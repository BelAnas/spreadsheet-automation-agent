"""Check the written summary on a real model: how often does it pass the number check?

    OPENAI_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.2-cpu:latest \\
        python -m evals.summary_check --provider openai --months 6

Each case is one generated month (a different seed gives different figures). The script
reports how many summaries were accepted at the first attempt, after the retry, or
replaced by the template, and which numbers the check refused.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from sheetagent import synth
from sheetagent.agent import Agent
from sheetagent.llm import make_llm
from sheetagent.summary import write_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--out", type=Path, default=Path("evals/results_summary.json"))
    args = parser.parse_args()

    llm = make_llm(args.provider, args.model)
    cases, started = [], time.perf_counter()
    for seed in range(21, 21 + args.months):
        root = Path("evals/tmp") / f"summary_{seed}"
        shutil.rmtree(root, ignore_errors=True)
        synth.write_demo(root, seed)
        history = {}
        rules_only = Agent()  # the figures do not depend on the model, so the cleaning runs without it
        for period in synth.DEMO_PERIODS:
            run = rules_only.report(rules_only.clean([root / "inbox" / period, root / "reference"], period), history)
            history[period] = {"revenue": run.facts["totals"]["revenue"], "units": run.facts["totals"]["units"],
                               "orders": run.facts["totals"]["orders"],
                               "regions": {r["region"]: r["revenue"] for r in run.facts["regions"]}}
        t0 = time.perf_counter()
        summary = write_summary(run.facts, llm)
        cases.append({"seed": seed, "source": summary.source, "attempts": summary.attempts,
                      "rejected_numbers": summary.rejected_numbers or [], "seconds": round(time.perf_counter() - t0, 1),
                      "text": summary.text})
        print(f"seed {seed}: {summary.source}, attempt {summary.attempts}, refused {summary.rejected_numbers or []}")
    first = sum(1 for c in cases if c["source"] == "model" and c["attempts"] == 1)
    retry = sum(1 for c in cases if c["source"] == "model" and c["attempts"] == 2)
    result = {"provider": llm.name, "model": llm.model, "months": len(cases), "accepted_first_attempt": first,
              "accepted_after_retry": retry, "replaced_by_template": len(cases) - first - retry,
              "model_calls": llm.usage.calls, "input_tokens": llm.usage.input_tokens,
              "output_tokens": llm.usage.output_tokens, "cost_usd": llm.usage.cost_usd(llm.model),
              "seconds": round(time.perf_counter() - started, 1), "cases": cases}
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=1))


if __name__ == "__main__":
    main()

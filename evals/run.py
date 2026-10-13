"""Benchmark: clean randomly spoiled sales files and compare the result with the known truth.

    python -m evals.run                      # 60 files, rules only (no model, no key)
    python -m evals.run --provider openai    # the same files, with the model naming leftover columns

Every file gets a random layout (see synth.random_spec): banner rows, renamed columns,
one of eight date formats and five number formats, typos in product codes, totals rows,
duplicates and unusable rows. About 40% of the column names are spellings the rules have
never been told about ("unseen"), so the benchmark does not only test the alias list.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import time
from pathlib import Path

import pandas as pd

from evals.baseline import naive_clean
from evals.scoring import score
from sheetagent import synth
from sheetagent.agent import Agent
from sheetagent.llm import make_llm
from sheetagent.readers import read_source
from sheetagent.schema import SALES
from sheetagent.structure import detect_header
from sheetagent.values import normalise_label

SEEN = {normalise_label(a) for f in SALES.fields for a in (*f.aliases, f.name)}
PERIOD = "2026-09"


def build_files(folder: Path, n: int, seed: int) -> list[dict]:
    """Write n messy files plus the reference files. Returns what each file really contains."""
    shutil.rmtree(folder, ignore_errors=True)
    synth.write_reference(folder / "reference")
    products = synth.make_products()
    cases = []
    for i in range(n):
        rng = random.Random(seed * 1000 + i)
        spec = synth.random_spec(rng)
        region = rng.choice(synth.REGIONS)
        truth = synth.make_sales(PERIOD, products, seed=seed * 1000 + i, orders_per_region={
            r: (rng.randint(25, 60) if r == region else 0) for r in synth.REGIONS})
        path = folder / "files" / f"{i:03d}" / f"sales_{region.lower()}_{i:03d}.{spec.fmt}"
        info = synth.write_messy(truth, spec, path, seed=seed * 1000 + i)
        cases.append({"path": path, "truth": truth, "region": region, "spec": spec, **info})
    return cases


def evaluate(cases: list[dict], reference: Path, llm) -> dict:
    header_hits = header_total = 0
    columns = {"seen": [0, 0], "unseen": [0, 0], "junk": [0, 0]}  # [correct, total]
    methods: dict[str, list[int]] = {}
    recovery, precision, revenue_error, base_recovery, base_error, region_ok = [], [], [], [], [], []
    bad_found = bad_total = wrongly_rejected = dup_removed = dup_total = 0
    perfect = failed = 0
    failures = []
    started = time.perf_counter()
    for case in cases:
        # Header row, checked on the raw sheets.
        for raw in read_source(case["path"]):
            expected = case["header_rows"].get(raw.sheet or "Export")
            if expected is None:
                continue  # the extra notes sheet has no table to find
            header_total += 1
            header_hits += detect_header(raw.grid) == expected

        run = Agent(llm).clean([case["path"], reference], PERIOD)
        s = score(run.sales, case["truth"])
        recovery.append(s["row_recovery"])
        precision.append(s["row_precision"])
        revenue_error.append(min(1.0, s["revenue_error"]))
        perfect += s["row_recovery"] == 1.0 and s["row_precision"] == 1.0
        failed += s["row_recovery"] == 0.0
        if len(run.sales):
            region_ok.append(float((run.sales["region"] == case["region"]).mean()))
        b = score(naive_clean(case["path"]), case["truth"])
        base_recovery.append(b["row_recovery"])
        base_error.append(min(1.0, b["revenue_error"]))

        # Column mapping: one vote per column per sheet.
        seen_sheets = set()
        for m in run.mappings:
            if m["table"] != "sales":
                continue
            seen_sheets.add(m["source"])
            truth_field = case["mapping"].get(m["column"])
            predicted = None if m["field"] == "(not used)" else m["field"]
            kind = "junk" if truth_field is None else ("seen" if normalise_label(m["column"]) in SEEN else "unseen")
            columns[kind][0] += predicted == truth_field
            columns[kind][1] += 1
            if predicted:
                tally = methods.setdefault(m["method"], [0, 0])
                tally[0] += predicted == truth_field
                tally[1] += 1
        if s["row_recovery"] < 1.0:
            failures.append({"file": case["path"].name, "row_recovery": round(s["row_recovery"], 3),
                             "headers": case["spec"].headers, "date_style": case["spec"].date_style,
                             "number_style": case["spec"].number_style,
                             "mapped": {m["column"]: m["field"] for m in run.mappings if m["table"] == "sales"}})

        # Unusable rows carry an ERR- order number in the generator, so they can be counted.
        quality = [r for r in run.rejected if r["reason"] != "duplicate line" and not r["reason"].startswith("outside")]
        caught = sum(1 for r in quality if "ERR-" in r["original"])
        bad_found += caught
        bad_total += case["bad_rows"]
        wrongly_rejected += len(quality) - caught
        if "order_id" in {m["field"] for m in run.mappings if m["table"] == "sales"}:
            dup_total += case["duplicates"]
            dup_removed += sum(1 for r in run.rejected if r["reason"] == "duplicate line")
    seconds = time.perf_counter() - started

    def share(pair: list[int]) -> float | None:
        return round(100 * pair[0] / pair[1], 1) if pair[1] else None

    n = len(cases)
    return {
        "files": n, "provider": llm.name, "model": llm.model, "model_calls": llm.usage.calls,
        "input_tokens": llm.usage.input_tokens, "output_tokens": llm.usage.output_tokens,
        "cost_usd": llm.usage.cost_usd(llm.model),
        "header_row_accuracy_pct": round(100 * header_hits / header_total, 1), "sheets": header_total,
        "column_accuracy_seen_pct": share(columns["seen"]), "columns_seen": columns["seen"][1],
        "column_accuracy_unseen_pct": share(columns["unseen"]), "columns_unseen": columns["unseen"][1],
        "junk_columns_left_alone_pct": share(columns["junk"]), "columns_junk": columns["junk"][1],
        "accuracy_by_method_pct": {k: {"correct_pct": share(v), "columns": v[1]} for k, v in sorted(methods.items())},
        "row_recovery_pct": round(100 * statistics.mean(recovery), 1),
        "row_precision_pct": round(100 * statistics.mean(precision), 1),
        "files_fully_correct": int(perfect), "files_not_readable": int(failed),
        "mean_revenue_error_pct": round(100 * statistics.mean(revenue_error), 2),
        "region_accuracy_pct": round(100 * statistics.mean(region_ok), 1) if region_ok else None,
        "bad_rows_caught_pct": round(100 * bad_found / bad_total, 1) if bad_total else None, "bad_rows": bad_total,
        "good_rows_wrongly_rejected": wrongly_rejected,
        "duplicates_removed_pct": round(100 * dup_removed / dup_total, 1) if dup_total else None, "duplicates": dup_total,
        "baseline_row_recovery_pct": round(100 * statistics.mean(base_recovery), 1),
        "baseline_files_fully_correct": int(sum(1 for x in base_recovery if x == 1.0)),
        "baseline_mean_revenue_error_pct": round(100 * statistics.mean(base_error), 2),
        "seconds_per_file": round(seconds / n, 3), "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=60)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model")
    parser.add_argument("--out", type=Path, default=Path("evals/results.json"))
    args = parser.parse_args()

    folder = Path("evals/tmp")
    cases = build_files(folder, args.files, args.seed)
    result = evaluate(cases, folder / "reference", make_llm(args.provider, args.model))
    result["command"] = f"python -m evals.run --files {args.files} --seed {args.seed}" + (
        f" --provider {args.provider}" if args.provider != "mock" else "")
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    rows = [("Files", result["files"]), ("Header row found", f"{result['header_row_accuracy_pct']}% of {result['sheets']} sheets"),
            ("Columns with a known name read correctly", f"{result['column_accuracy_seen_pct']}% of {result['columns_seen']}"),
            ("Columns with an unseen name read correctly", f"{result['column_accuracy_unseen_pct']}% of {result['columns_unseen']}"),
            ("Irrelevant columns left alone", f"{result['junk_columns_left_alone_pct']}% of {result['columns_junk']}"),
            ("Order lines recovered exactly", f"{result['row_recovery_pct']}%"),
            ("Files recovered with no error at all", f"{result['files_fully_correct']} of {result['files']}"),
            ("Mean revenue error", f"{result['mean_revenue_error_pct']}%"),
            ("Region correct", f"{result['region_accuracy_pct']}%"),
            ("Unusable rows caught", f"{result['bad_rows_caught_pct']}% of {result['bad_rows']}"),
            ("Good rows wrongly rejected", result["good_rows_wrongly_rejected"]),
            ("Duplicates removed", f"{result['duplicates_removed_pct']}% of {result['duplicates']}"),
            ("Baseline: order lines recovered", f"{result['baseline_row_recovery_pct']}%"),
            ("Baseline: files fully correct", f"{result['baseline_files_fully_correct']} of {result['files']}"),
            ("Baseline: mean revenue error", f"{result['baseline_mean_revenue_error_pct']}%"),
            ("Seconds per file", result["seconds_per_file"]), ("Model calls", result["model_calls"])]
    for label, value in rows:
        print(f"{label:<45} {value}")
    print("By method:", json.dumps(result["accuracy_by_method_pct"]))
    for failure in result["failures"][:12]:
        print("  FAIL", json.dumps(failure, ensure_ascii=False)[:420])


if __name__ == "__main__":
    main()

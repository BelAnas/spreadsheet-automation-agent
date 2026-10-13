"""Command line: `python -m sheetagent demo | run | make-data | serve`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import metrics, synth
from .agent import Agent, Run
from .llm import make_llm
from .report import publish


def _print_step(step: str, detail: str) -> None:
    print(f"  {step:<16} {detail}")


def run_report(inputs: list[str], period: str | None, out: Path, provider: str | None = None,
               model: str | None = None, title: str = "Monthly sales report", currency: str = "USD",
               plotly: str = "inline", quiet: bool = False) -> Run:
    """One full run: clean, measure, write, publish, and remember the month for the next comparison."""
    agent = Agent(make_llm(provider, model), memory_path=out / "mapping_memory.json", currency=currency,
                  on_step=None if quiet else _print_step)
    history_path = out / "history.json"
    history = metrics.load_history(history_path)
    run = agent.report(agent.clean(list(inputs), period), history)
    if not run.ok:
        return run
    publish(run, out, title, plotly)
    metrics.save_history(history_path, history, run.facts)
    if not quiet:
        _print_step("Publish", ", ".join(Path(p).name for p in run.outputs.values()) + f" in {out / run.period}")
    return run


def _report_tail(run: Run) -> None:
    for warning in run.warnings:
        print(f"  ! {warning}")
    print(f"\n  {run.summary.text}\n")
    print(f"  Excel report: {run.outputs['excel']}\n  Web report:   {run.outputs['html']}"
          f"\n  Done in {run.seconds:.1f} s (model: {run.llm['provider']}).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sheetagent", description="Turn messy spreadsheets into a monthly report.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--out", type=Path, default=Path("reports"), help="folder for the reports (default: reports)")
        p.add_argument("--provider", choices=["mock", "openai"], help="language model provider (default: LLM_PROVIDER or mock)")
        p.add_argument("--model", help="model name (default: LLM_MODEL or gpt-4o-mini)")
        p.add_argument("--plotly", choices=["inline", "cdn"], default="inline",
                       help="inline: one self-contained page of about 5 MB; cdn: small page that needs internet for the charts")

    demo = sub.add_parser("demo", help="generate the demo files and build the reports for two months")
    demo.add_argument("--data", type=Path, default=Path("data"))
    common(demo)

    run = sub.add_parser("run", help="build the report from files, folders or Google Sheets links")
    run.add_argument("inputs", nargs="+", help="files, folders, or links to a CSV / Google Sheet")
    run.add_argument("--period", help="report month as YYYY-MM (default: the month most rows belong to)")
    run.add_argument("--title", default="Monthly sales report")
    run.add_argument("--currency", default="USD")
    common(run)

    data = sub.add_parser("make-data", help="only generate the demo files")
    data.add_argument("--data", type=Path, default=Path("data"))
    data.add_argument("--seed", type=int, default=7)

    serve = sub.add_parser("serve", help="start the upload page")
    serve.add_argument("--port", type=int, default=8124)
    serve.add_argument("--host", default="127.0.0.1")

    args = parser.parse_args(argv)
    if args.command == "make-data":
        synth.write_demo(args.data, args.seed)
        print(f"Demo files written to {args.data}/inbox, {args.data}/reference and {args.data}/truth")
        return 0
    if args.command == "serve":
        import uvicorn
        uvicorn.run("sheetagent.web:app", host=args.host, port=args.port, log_level="warning")
        return 0
    try:
        if args.command == "demo":
            synth.write_demo(args.data)
            for period in synth.DEMO_PERIODS:
                print(f"\n{metrics.period_label(period)}")
                result = run_report([str(args.data / "inbox" / period), str(args.data / "reference")], period, args.out,
                                    args.provider, args.model, "Cedar & Stone - monthly sales report", plotly=args.plotly)
                _report_tail(result)
            return 0
        print(f"\nBuilding the report from {len(args.inputs)} input(s)")
        result = run_report(args.inputs, args.period, args.out, args.provider, args.model, args.title, args.currency,
                            args.plotly)
    except ValueError as exc:  # configuration mistakes, explained without a stack trace
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not result.ok:
        print("No report was built: no usable sales lines were found.", file=sys.stderr)
        for warning in result.warnings:
            print(f"  ! {warning}", file=sys.stderr)
        return 1
    _report_tail(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

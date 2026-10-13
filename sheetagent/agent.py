"""The agent: a fixed plan of steps, a record of every decision, and two checked model calls.

The plan never changes from run to run, which is what a recurring report needs: the same
files give the same report. The judgement calls (which row is the header, what a column
means, which date format a file uses) are made inside the steps and written to the trace,
so a person can read afterwards what was decided and why.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import metrics
from .cleaning import (SALES_COLUMNS, ProductIndex, clean_products, clean_sales, clean_targets, drop_duplicates)
from .llm import MockLLM
from .mapping import Mapping, MappingContext, detect_role, load_memory, remember
from .readers import ReadError, collect, read_source
from .structure import Table, extract_table
from .summary import Summary, write_summary
from .values import is_blank, sku_key


@dataclass
class Run:
    period: str | None = None
    sales: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=SALES_COLUMNS))
    products: pd.DataFrame | None = None
    targets: pd.DataFrame | None = None
    rejected: list[dict] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)
    mappings: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    facts: dict | None = None
    summary: Summary | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    llm: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.facts is not None


class Agent:
    def __init__(self, llm=None, memory_path: Path | None = None, currency: str = "USD",
                 on_step: Callable[[str, str], None] | None = None, summary_writer: str = "template") -> None:
        self.llm = llm or MockLLM()
        # The template is the default even when a model is configured: see the decision record on the
        # summary. A model-written summary has to be asked for.
        self.summary_writer = summary_writer
        self.memory_path = memory_path
        self.currency = currency
        self.on_step = on_step

    # ------------------------------------------------------------------ helpers

    def _step(self, run: Run, step: str, detail: str) -> None:
        run.trace.append({"step": step, "detail": detail})
        if self.on_step:
            self.on_step(step, detail)

    @staticmethod
    def _record_mapping(run: Run, table: Table, mapping: Mapping) -> None:
        for m in mapping.matches:
            run.mappings.append({"source": table.label, "table": mapping.role, "column": m.column,
                                 "field": m.field or "(not used)", "method": m.method or "-",
                                 "confidence": m.confidence if m.field else None, "note": m.note})

    # ------------------------------------------------------------------ the plan

    def clean(self, inputs: list[str | Path], period: str | None = None) -> Run:
        """Steps 1 to 5: collect, find the tables, map the columns, clean the values, join."""
        run = Run(period=period)
        started = time.perf_counter()

        # 1. Collect
        sources = collect(inputs)
        raws = []
        for source in sources:
            try:
                raws += read_source(source)
            except ReadError as exc:
                run.warnings.append(str(exc))
            except OSError as exc:
                run.warnings.append(f"{source}: {exc.strerror or exc}")
        self._step(run, "Collect", f"{len(sources)} files or links, {len(raws)} sheets read"
                   + (f", {len(run.warnings)} could not be opened" if run.warnings else ""))

        # 2. Find the table in each sheet
        tables: list[Table] = []
        for raw in raws:
            table = extract_table(raw)
            if table is None or table.df.empty:
                run.audit.append({"step": "structure", "source": raw.label, "action": "sheet skipped: no table found",
                                  "count": int(len(raw.grid)), "example": ""})
                continue
            tables.append(table)
            run.audit += table.audit
        skipped_rows = sum(a["count"] for a in run.audit if a["step"] == "structure" and "sheet skipped" not in a["action"])
        self._step(run, "Find the table", f"{len(tables)} tables found in {len(raws)} sheets; "
                   f"{skipped_rows} banner, blank, header and totals rows left out")

        # 3. Map the columns. Reference tables first: the product codes and the region names
        #    they contain help to recognise the columns of the sales files.
        ctx = MappingContext(memory=load_memory(self.memory_path), llm=self.llm)
        # Sheets of one workbook that share the same headers are one table cut into pieces (one sheet per
        # week, for instance). They are mapped together: more rows give the value-based rules more evidence,
        # and the sheets cannot end up read in different ways.
        families: dict[tuple, list[Table]] = {}
        for t in tables:
            families.setdefault((t.source, tuple(t.df.columns)), []).append(t)
        pooled = {key: pd.concat([t.df for t in group], ignore_index=True) for key, group in families.items()}

        def family(t: Table) -> pd.DataFrame:
            return pooled[(t.source, tuple(t.df.columns))]

        first_look = {key: detect_role(df, ctx, use_model=False) for key, df in pooled.items()}
        roles: list[tuple[Table, Mapping | None]] = [(t, first_look[(t.source, tuple(t.df.columns))]) for t in tables]
        product_parts, target_parts = [], []
        for table, mapping in roles:
            if mapping and mapping.role == "products":
                result = clean_products(table, mapping)
                product_parts.append(result.rows)
            elif mapping and mapping.role == "targets":
                result = clean_targets(table, mapping)
                target_parts.append(result.rows)
            else:
                continue
            self._record_mapping(run, table, mapping)
            run.rejected += result.rejected
            run.audit += result.audit
        if product_parts:
            run.products = pd.concat(product_parts, ignore_index=True).drop_duplicates("sku", keep="first")
            ctx.product_keys = {sku_key(s) for s in run.products["sku"]}
        if target_parts:
            run.targets = pd.concat(target_parts, ignore_index=True)
            ctx.regions = {r.lower() for r in run.targets["region"]}

        sales_tables: list[tuple[Table, Mapping]] = []
        second_look: dict[tuple, Mapping | None] = {}
        for table, first in roles:
            if first and first.role in ("products", "targets"):
                continue
            key = (table.source, tuple(table.df.columns))
            if key not in second_look:  # second look, now with the reference data and the model
                second_look[key] = detect_role(family(table), ctx)
            mapping = second_look[key]
            if mapping is None or mapping.role != "sales":
                run.audit.append({"step": "map", "source": table.label, "count": int(len(table.df)), "example":
                                  ", ".join(c for c in table.df.columns if c != "_row")[:120],
                                  "action": "table skipped: its columns do not form sales lines, products or targets"})
                run.warnings.append(f"{table.label}: table not recognised, skipped")
                continue
            self._record_mapping(run, table, mapping)
            remember(ctx.memory, mapping)
            sales_tables.append((table, mapping))
        methods = Counter(m["method"] for m in run.mappings if m["field"] != "(not used)")
        unused = sum(1 for m in run.mappings if m["field"] == "(not used)")
        self._step(run, "Map the columns", ", ".join(f"{n} by {method}" for method, n in methods.most_common())
                   + f"; {unused} columns not used" if methods else "no table recognised")

        # 4. Clean the values
        regions = sorted({r for r in (run.targets["region"] if run.targets is not None else [])})
        for table, mapping in sales_tables:  # regions named in the files themselves count as known too
            column = mapping.column_for("region")
            if column:
                regions = sorted(set(regions) | {str(v).strip().title() for v in table.df[column] if not is_blank(v)})
        index = ProductIndex(run.products) if run.products is not None else None
        parts, currencies = [], set()
        for table, mapping in sales_tables:
            result = clean_sales(table, mapping, index, regions, period)
            parts.append(result.rows)
            currencies |= result.currencies
            run.rejected += result.rejected
            run.audit += result.audit
        sales = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=SALES_COLUMNS)
        repairs = sum(a["count"] for a in run.audit if a["step"] == "clean" and ("converted" in a["action"]
                      or "computed" in a["action"] or "normalised" in a["action"] or "repaired" in a["action"]))
        self._step(run, "Clean the values", f"{len(sales)} sales lines typed, {repairs} cell repairs, "
                   f"{len(run.rejected)} rows set aside")
        if len(currencies) > 1:
            run.warnings.append(f"The files carry different currency marks ({', '.join(sorted(currencies))}): amounts are "
                                "added as written, no conversion is done.")
        if index is None and len(sales):
            run.warnings.append("No product list found: product codes were not checked, and there is no category or margin.")

        # 5. Join: stack, remove duplicates, keep the report month, add product data
        if len(sales):
            sales, duplicates, audit = drop_duplicates(sales)
            run.rejected += duplicates
            run.audit += audit
            months = sales["order_date"].map(lambda d: d.strftime("%Y-%m"))
            if run.period is None:
                run.period = months.mode().iloc[0]  # the month most lines belong to
            outside = sales[months != run.period]
            run.rejected += [{"source": r.source, "row": r.row, "reason": f"outside the report month {run.period}",
                              "original": f"{r.order_date}, {r.sku}, amount {r.amount}"} for r in outside.itertuples()]
            sales = sales[months == run.period].reset_index(drop=True)
            unknown = sales[~sales["sku_known"]]
            if len(unknown):
                n_codes = unknown['sku'].nunique()
                run.warnings.append(f"{n_codes} product code{'s are' if n_codes > 1 else ' is'} not in the product list "
                                    f"({len(unknown)} lines, kept in the totals under '{metrics.UNKNOWN_CATEGORY}').")
            if (sales["region"] == "Unassigned").any():
                run.warnings.append(f"{int((sales['region'] == 'Unassigned').sum())} lines have no region.")
            if sales["order_id"].isna().any():
                run.warnings.append(f"{int(sales['order_id'].isna().sum())} lines have no order number: they are "
                                    "not counted in the number of orders and duplicates among them cannot be detected.")
        run.sales = metrics.enrich(sales, run.products)
        reasons = Counter(r["reason"].split(":")[0] for r in run.rejected)
        self._step(run, "Join", f"{len(run.sales)} lines kept for {run.period or 'no period'}; set aside: "
                   + (", ".join(f"{n} {reason}" for reason, n in reasons.most_common()) or "none"))
        if self.memory_path and ctx.memory:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            import json
            self.memory_path.write_text(json.dumps(ctx.memory, indent=2, sort_keys=True), encoding="utf-8")
        run.seconds = time.perf_counter() - started
        return run

    def report(self, run: Run, history: dict) -> Run:
        """Steps 6 and 7: compute the figures, then write and check the summary."""
        started = time.perf_counter()
        if run.sales.empty:
            self._step(run, "Measure", "no usable sales lines: no report can be built")
            return run
        facts = metrics.compute(run.sales, run.targets, run.period, history, self.currency)
        rows_read = len(run.sales) + sum(1 for r in run.rejected)
        facts["quality"] = {"rows_read": rows_read, "rows_used": int(len(run.sales)), "rows_rejected": len(run.rejected),
                            "rejected_by_reason": dict(Counter(r["reason"].split(":")[0] for r in run.rejected)),
                            "warnings": list(run.warnings)}
        run.facts = facts
        t = facts["totals"]
        self._step(run, "Measure", f"revenue {t['revenue']:,.2f} {self.currency}, {t['lines']} lines, {t['orders']} orders, "
                   f"{len(facts['regions'])} regions, {len(facts['categories'])} categories"
                   + (f"; {facts['change']['revenue_pct']:+.1f}% on {facts['previous']['label']}" if facts["previous"] else
                      "; no previous month on file"))
        run.summary = write_summary(facts, self.llm if self.summary_writer == "model" else None)
        self._step(run, "Write", f"summary from the {run.summary.source}"
                   + (f"; numbers not on the fact sheet: {', '.join(run.summary.rejected_numbers)}"
                      if run.summary.rejected_numbers else ""))
        usage = self.llm.usage
        run.llm = {"provider": self.llm.name, "model": self.llm.model, "calls": usage.calls,
                   "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                   "cost_usd": usage.cost_usd(self.llm.model)}
        run.seconds += time.perf_counter() - started
        return run

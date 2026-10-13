"""Write the management summary, and check it before it is published.

The model receives the figures as a fact sheet and is told to copy numbers, not compute
them. Then code reads the text back: every number in it must be on the fact sheet. If one
is not, the model gets one retry with the offending numbers named, and after that the
template summary is used. A wrong number in a report costs more than a plain sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SYSTEM = ("You write the summary paragraph of a monthly sales report for managers. Use only the facts given. "
          "Copy every number exactly as written in the facts: do not round, convert, add, subtract or invent "
          "numbers. Write 4 to 6 plain sentences in one paragraph, no headings, no bullet points, no advice.")

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")


def money(x: float | None, currency: str = "") -> str:
    if x is None:
        return "n/a"
    return f"{x:,.0f}{' ' + currency if currency else ''}"


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}%"


def signed(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1f}%"


def fact_sheet(facts: dict) -> list[str]:
    """The figures as short sentences. The template and the model both work from these lines."""
    t, cur = facts["totals"], facts["currency"]
    lines = [f"Period: {facts['period_label']}.",
             f"Total revenue: {money(t['revenue'], cur)} from {t['lines']:,} order lines in {t['orders']:,} orders, "
             f"{t['units']:,.0f} units."]
    if facts["previous"]:
        lines.append(f"Previous month ({facts['previous']['label']}): revenue {money(facts['previous']['revenue'], cur)}; "
                     f"revenue changed by {signed(facts['change']['revenue_pct'])}, units by "
                     f"{signed(facts['change']['units_pct'])}.")
    if facts["target"]:
        lines.append(f"Company target: {money(facts['target']['total'], cur)}; reached {pct(facts['target']['attainment_pct'])} of it.")
    for r in facts["regions"]:
        line = f"Region {r['region']}: revenue {money(r['revenue'], cur)}, {pct(r['share_pct'])} of the total"
        if r["attainment_pct"] is not None:
            line += f", {pct(r['attainment_pct'])} of its target of {money(r['target'], cur)}"
        if r["change_pct"] is not None:
            line += f", {signed(r['change_pct'])} against the previous month"
        lines.append(line + ".")
    for c in facts["categories"][:3]:
        lines.append(f"Category {c['category']}: revenue {money(c['revenue'], cur)}, {pct(c['share_pct'])} of the total.")
    if facts["top_products"]:
        p = facts["top_products"][0]
        lines.append(f"Best-selling product: {p['product_name'] or p['sku']} ({p['sku']}), revenue {money(p['revenue'], cur)}.")
    if t["margin_pct"] is not None:
        lines.append(f"Gross margin: {money(t['gross_margin'], cur)}, {pct(t['margin_pct'])} of revenue.")
    if t["return_lines"]:
        lines.append(f"Returns: {t['return_lines']} lines worth {money(t['returns_value'], cur)}.")
    q = facts.get("quality")
    if q:
        lines.append(f"Data quality: {q['rows_used']:,} of {q['rows_read']:,} rows read were used; "
                     f"{q['rows_rejected']:,} rows were set aside.")
    return lines


def template_summary(facts: dict) -> str:
    """A deterministic summary built from the same facts. Always available, always correct."""
    t, cur = facts["totals"], facts["currency"]
    parts = [f"{facts['period_label']} closed with revenue of {money(t['revenue'], cur)} from {t['lines']:,} order "
             f"lines in {t['orders']:,} orders."]
    if facts["previous"] and facts["change"]["revenue_pct"] is not None:
        direction = "up" if facts["change"]["revenue_pct"] >= 0 else "down"
        parts.append(f"That is {direction} {abs(facts['change']['revenue_pct']):.1f}% on {facts['previous']['label']} "
                     f"({money(facts['previous']['revenue'], cur)}).")
    if facts["target"]:
        parts.append(f"The company reached {pct(facts['target']['attainment_pct'])} of its target of "
                     f"{money(facts['target']['total'], cur)}.")
    if facts["regions"]:
        lead = facts["regions"][0]
        parts.append(f"{lead['region']} was the largest region with {money(lead['revenue'], cur)}, "
                     f"{pct(lead['share_pct'])} of the total.")
        behind = [r for r in facts["regions"] if r["attainment_pct"] is not None and r["attainment_pct"] < 100]
        ahead = [r for r in facts["regions"] if r["attainment_pct"] is not None and r["attainment_pct"] >= 100]
        if behind:
            parts.append("Below target: " + ", ".join(f"{r['region']} at {pct(r['attainment_pct'])}" for r in behind) + ".")
        if ahead:
            parts.append("On or above target: " + ", ".join(f"{r['region']} at {pct(r['attainment_pct'])}" for r in ahead) + ".")
    if facts["categories"]:
        c = facts["categories"][0]
        parts.append(f"The strongest category was {c['category']} with {money(c['revenue'], cur)} "
                     f"({pct(c['share_pct'])} of revenue).")
    if facts["top_products"]:
        p = facts["top_products"][0]
        parts.append(f"The best-selling product was {p['product_name'] or p['sku']} at {money(p['revenue'], cur)}.")
    if t["margin_pct"] is not None:
        parts.append(f"Gross margin was {pct(t['margin_pct'])} of revenue.")
    q = facts.get("quality")
    if q and q["rows_rejected"]:
        parts.append(f"{q['rows_rejected']:,} of {q['rows_read']:,} rows could not be used and are listed with their reasons.")
    return " ".join(parts)


def _value(token: str) -> float:
    return float(token.rstrip("%").replace(",", ""))


def ungrounded_numbers(text: str, facts_text: str) -> list[str]:
    """Numbers in the text that do not appear on the fact sheet.

    Percentages must match a percentage on the sheet. Small bare whole numbers (up to 10)
    are allowed because they count things ("the 3 regions"), not money.
    """
    allowed = {(_value(tok), tok.endswith("%")) for tok in _NUMBER.findall(facts_text)}
    allowed_any = {v for v, _ in allowed}
    missing = []
    for tok in _NUMBER.findall(text):
        value, is_pct = _value(tok), tok.endswith("%")
        if is_pct:
            # "+4.2%" and "4.2%" are the same claim; the direction is carried by the wording.
            if (value, True) in allowed or (-value, True) in allowed:
                continue
        elif value in allowed_any or (abs(value) <= 10 and float(value).is_integer()):
            continue
        missing.append(tok)
    return missing


@dataclass
class Summary:
    text: str
    source: str  # "template" | "model" | "template (model answer rejected)" | "template (model unavailable)"
    attempts: int = 0
    rejected_numbers: list[str] | None = None


def write_summary(facts: dict, llm) -> Summary:
    fallback = template_summary(facts)
    if llm is None or getattr(llm, "is_mock", False):
        return Summary(fallback, "template")
    sheet = "\n".join(fact_sheet(facts))
    prompt = f"Facts:\n{sheet}\n\nWrite the summary paragraph."
    rejected: list[str] = []
    for attempt in (1, 2):
        try:
            text = " ".join(llm.complete(SYSTEM, prompt, max_tokens=260).split())
        except Exception:
            return Summary(fallback, "template (model unavailable)", attempt)
        bad = ungrounded_numbers(text, sheet)
        if text and not bad and len(text) >= 80:
            return Summary(text, "model", attempt)
        rejected += bad
        prompt = (f"Facts:\n{sheet}\n\nYour previous summary used numbers that are not in the facts: "
                  f"{', '.join(bad) or 'none, but it was too short'}. Write the summary paragraph again and copy numbers "
                  "exactly from the facts.")
    return Summary(fallback, "template (model answer rejected)", 2, rejected)

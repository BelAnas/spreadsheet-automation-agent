"""Convert mapped tables to clean, typed rows, and set aside what cannot be trusted.

Nothing is dropped silently. A row that cannot be used goes to the rejected list with its
file, its spreadsheet row number and the reason; every repair is counted in the audit log.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .mapping import Mapping
from .structure import Table
from .values import (CURRENCY_MARK, decimal_mark, infer_dayfirst, is_blank, parse_date, parse_month, parse_number, sku_confusable,
                     sku_key, sku_text)

SALES_COLUMNS = ["order_id", "order_date", "region", "sku", "quantity", "unit_price", "amount", "customer",
                 "sku_known", "source", "row"]


@dataclass
class ProductIndex:
    """The product list, indexed so that badly typed codes still find their product."""
    products: pd.DataFrame  # sku, product_name, category, unit_cost
    by_key: dict[str, str] = field(default_factory=dict)
    by_confusable: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for sku in self.products["sku"]:
            self.by_key.setdefault(sku_key(sku), sku)
            self.by_confusable.setdefault(sku_confusable(sku), []).append(sku)

    def resolve(self, raw: object) -> tuple[str, str]:
        """Return the official code and how it was found: exact, normalised, repaired or unknown."""
        text = sku_text(raw)
        official = self.by_key.get(sku_key(raw))
        if official:
            return official, "exact" if str(raw) == official else "normalised"
        # A look-alike repair is applied only when it points to one product, never to several.
        candidates = self.by_confusable.get(sku_confusable(raw), [])
        if len(candidates) == 1:
            return candidates[0], "repaired"
        return text, "unknown"


@dataclass
class CleanResult:
    rows: pd.DataFrame
    rejected: list[dict]
    audit: list[dict]
    currencies: set[str] = field(default_factory=set)  # currency marks seen in the money columns


def _text(value: object) -> str | None:
    if is_blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def region_from_name(label: str, regions: list[str]) -> str | None:
    """Find a known region name in a file or sheet name ('sales_north_2026-09.xlsx' gives 'North')."""
    words = set(re.findall(r"[a-z]+", label.lower()))
    hits = [r for r in regions if set(re.findall(r"[a-z]+", r.lower())) <= words]
    return hits[0] if len(hits) == 1 else None


def clean_products(table: Table, mapping: Mapping) -> CleanResult:
    df, audit, rejected = table.df, [], []
    cost_col = mapping.column_for("unit_cost")
    mark = decimal_mark(list(df[cost_col])) if cost_col else "."
    rows = []
    for record in df.to_dict("records"):
        sku = record[mapping.column_for("sku")]
        if is_blank(sku):
            rejected.append({"source": table.label, "row": record["_row"], "reason": "product without a code",
                             "original": _original(record)})
            continue
        category_col = mapping.column_for("category")
        rows.append({"sku": sku_text(sku), "product_name": _text(record[mapping.column_for("product_name")]),
                     "category": _text(record[category_col]) if category_col else None,
                     "unit_cost": parse_number(record[cost_col], mark) if cost_col else None})
    out = pd.DataFrame(rows, columns=["sku", "product_name", "category", "unit_cost"])
    before = len(out)
    out = out.drop_duplicates("sku", keep="first")
    if before != len(out):
        audit.append({"step": "clean", "source": table.label, "action": "repeated product code, first kept",
                      "count": before - len(out), "example": ""})
    return CleanResult(out, rejected, audit)


def clean_targets(table: Table, mapping: Mapping) -> CleanResult:
    df = table.df
    mark = decimal_mark(list(df[mapping.column_for("target_revenue")]))
    rows, rejected = [], []
    for record in df.to_dict("records"):
        region = _text(record[mapping.column_for("region")])
        month = parse_month(record[mapping.column_for("month")])
        target = parse_number(record[mapping.column_for("target_revenue")], mark)
        if not region or not month or target is None:
            rejected.append({"source": table.label, "row": record["_row"], "reason": "target line incomplete",
                             "original": _original(record)})
            continue
        rows.append({"region": region.title(), "month": month, "target_revenue": target})
    return CleanResult(pd.DataFrame(rows, columns=["region", "month", "target_revenue"]), rejected, [])


def _original(record: dict) -> str:
    """The row as it was in the file, for the person who has to fix it at the source."""
    return " | ".join(f"{k}={v}" for k, v in record.items() if k != "_row" and not is_blank(v))[:300]


def clean_sales(table: Table, mapping: Mapping, products: ProductIndex | None, regions: list[str],
                period: str | None = None) -> CleanResult:
    df, label = table.df, table.label
    audit: list[dict] = []
    rejected: list[dict] = []

    def log(action: str, count: int, example: str = "") -> None:
        if count:
            audit.append({"step": "clean", "source": label, "action": action, "count": int(count), "example": example})

    col = {name: mapping.column_for(name) for name in
           ("order_id", "order_date", "sku", "quantity", "unit_price", "amount", "customer", "region")}

    # Formats are settled once per column, from all its values.
    dates = list(df[col["order_date"]])
    dayfirst, why = infer_dayfirst(dates, period)
    if any(isinstance(v, str) for v in dates):
        log(f"date format decided: {why}", sum(1 for v in dates if isinstance(v, str)))
    marks = {name: decimal_mark(list(df[col[name]])) for name in ("quantity", "unit_price", "amount") if col[name]}
    for name, mark in marks.items():
        if mark == ",":
            log(f"{name}: decimal comma detected", sum(1 for v in df[col[name]] if isinstance(v, str)),
                str(next((v for v in df[col[name]] if isinstance(v, str)), "")))

    # The region comes from a column when there is one, otherwise from the file or sheet name.
    file_region = region_from_name(label, regions)
    by_lower = {r.lower(): r for r in regions}

    counts = {"code normalised": [], "code repaired": [], "code not in product list": [], "amount computed": 0,
              "unit price computed": 0, "quantity computed": 0, "amount differs from quantity x price": [],
              "text converted to number": 0, "text converted to date": 0}
    rows = []
    for record in df.to_dict("records"):
        def reject(reason: str) -> None:
            rejected.append({"source": label, "row": record["_row"], "reason": reason, "original": _original(record)})

        raw_sku = record[col["sku"]]
        if is_blank(raw_sku):
            reject("no product code")
            continue
        raw_date = record[col["order_date"]]
        day = parse_date(raw_date, dayfirst, allow_serial=True)
        if day is None:
            reject("date missing" if is_blank(raw_date) else f"date not understood: {str(raw_date)[:30]}")
            continue
        if isinstance(raw_date, str):
            counts["text converted to date"] += 1

        numbers = {}
        for name in ("quantity", "unit_price", "amount"):
            raw = record[col[name]] if col[name] else None
            numbers[name] = parse_number(raw, marks.get(name, "."))
            if isinstance(raw, str) and numbers[name] is not None:
                counts["text converted to number"] += 1
        qty, price, amount = numbers["quantity"], numbers["unit_price"], numbers["amount"]
        if amount is None and qty is not None and price is not None:
            amount = round(qty * price, 2)
            counts["amount computed"] += 1
        if amount is None:
            reject("no usable amount: " + ", ".join(
                f"{n}={str(record[col[n]])[:15]}" for n in ("quantity", "unit_price", "amount") if col[n]))
            continue
        if qty is None and price not in (None, 0):
            qty = round(amount / price, 3)
            counts["quantity computed"] += 1
        if price is None and qty not in (None, 0):
            price = round(amount / qty, 4)
            counts["unit price computed"] += 1
        if None not in (numbers["quantity"], numbers["unit_price"], numbers["amount"]) and \
                abs(qty * price - amount) > 0.011 + 0.002 * abs(amount):
            counts["amount differs from quantity x price"].append(record["_row"])

        if products is not None:
            sku, how = products.resolve(raw_sku)
            if how == "normalised":
                counts["code normalised"].append(f"{raw_sku!s} -> {sku}")
            elif how == "repaired":
                counts["code repaired"].append(f"{raw_sku!s} -> {sku}")
            elif how == "unknown":
                counts["code not in product list"].append(sku)
        else:
            sku, how = sku_text(raw_sku), "unchecked"

        region = _text(record[col["region"]]) if col["region"] else None
        region = by_lower.get(region.lower(), region.title()) if region else (file_region or "Unassigned")
        rows.append({"order_id": _text(record[col["order_id"]]) if col["order_id"] else None, "order_date": day,
                     "region": region, "sku": sku, "quantity": qty, "unit_price": price, "amount": round(amount, 2),
                     "customer": _text(record[col["customer"]]) if col["customer"] else None,
                     "sku_known": how != "unknown", "source": label, "row": record["_row"]})

    for action in ("text converted to date", "text converted to number", "amount computed", "quantity computed",
                   "unit price computed"):
        log(action, counts[action])
    for action in ("code normalised", "code repaired", "code not in product list"):
        log(f"product {action}", len(counts[action]), ", ".join(dict.fromkeys(counts[action]))[:120])
    log("amount differs from quantity x price (amount kept as written)",
        len(counts["amount differs from quantity x price"]),
        "rows " + ", ".join(map(str, counts["amount differs from quantity x price"][:5])))
    if not col["region"]:
        log(f"region taken from the file name: {file_region}" if file_region else
            "no region column and no region in the file name: rows marked Unassigned", len(rows))
    if not col["order_id"]:
        log("no order number column: duplicate lines cannot be detected in this file", len(rows))
    # Amounts are never converted. The marks are collected so that the agent can warn when files disagree.
    currencies = {m.group(0).upper() for name in ("unit_price", "amount") if col[name]
                  for v in df[col[name]] if isinstance(v, str) and (m := CURRENCY_MARK.search(v))}
    out = pd.DataFrame(rows, columns=SALES_COLUMNS)
    # A file with no quantity column leaves None here; as floats the gaps become NaN and sums still work.
    for name in ("quantity", "unit_price", "amount"):
        out[name] = pd.to_numeric(out[name], errors="coerce")
    return CleanResult(out, rejected, audit, currencies)


def drop_duplicates(sales: pd.DataFrame) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Remove lines that appear twice with the same order number, product, date, quantity and amount.

    Lines without an order number are left alone: two identical lines may then be two real sales.
    """
    keys = ["order_id", "sku", "order_date", "quantity", "amount"]
    has_id = sales["order_id"].notna()
    duplicate = has_id & sales.duplicated(keys, keep="first")
    rejected = [{"source": r.source, "row": r.row, "reason": "duplicate line",
                 "original": f"order {r.order_id}, {r.sku}, {r.order_date}, amount {r.amount}"}
                for r in sales[duplicate].itertuples()]
    audit = [{"step": "join", "source": source, "action": "duplicate line removed", "count": int(count), "example": ""}
             for source, count in sales[duplicate].groupby("source").size().items()]
    return sales[~duplicate].reset_index(drop=True), rejected, audit

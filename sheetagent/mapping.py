"""Decide what each column of a table means.

Five methods are tried in order of trust, and each column records which one placed it:

1. memory      a mapping confirmed in an earlier run (or written by a person)
2. name        the header is a known spelling, or close to one
3. arithmetic  three number columns where one is the product of the other two
4. profile     the values leave only one possibility (the only date column, the product codes)
5. model       the language model reads the header and five sample values

Whatever the method, a column is accepted for a field only if its values fit that field.
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from .schema import SALES, SCHEMAS, Field, Schema
from .values import (decimal_mark, is_blank, looks_like_sku, normalise_label, parse_date, parse_month, parse_number,
                     sku_key)

SIMILAR_ENOUGH = 0.84
MODEL_SYSTEM = ("You label spreadsheet columns. You are given one column (its header and sample values) and a list "
                "of field names with their meaning. Answer with exactly one field name from the list, or the word "
                "none if no field fits. Answer with that single word and nothing else.")


@dataclass
class ColumnMatch:
    column: str
    field: str | None
    method: str = ""
    confidence: float = 0.0
    note: str = ""


@dataclass
class Mapping:
    role: str
    matches: list[ColumnMatch]

    def column_for(self, field_name: str) -> str | None:
        return next((m.column for m in self.matches if m.field == field_name), None)

    @property
    def fields(self) -> set[str]:
        return {m.field for m in self.matches if m.field}

    def complete(self) -> bool:
        """True when the table has what its role needs to be used at all."""
        have = self.fields
        if self.role == "sales":
            return {"order_date", "sku"} <= have and ("amount" in have or {"quantity", "unit_price"} <= have)
        return all(f.name in have for f in SCHEMAS[self.role].fields if f.required)


@dataclass
class MappingContext:
    """What is known outside the table and helps to read it."""
    product_keys: set[str] = field(default_factory=set)  # sku keys from the product list
    regions: set[str] = field(default_factory=set)  # lower-case region names from the targets
    memory: dict[str, dict[str, str]] = field(default_factory=dict)  # role -> normalised header -> field
    llm: object | None = None


# ----------------------------------------------------------------------------- profiling

def profile(values: pd.Series, ctx: MappingContext) -> dict[str, float]:
    """Describe a column by what its cells look like, as shares between 0 and 1."""
    cells = [v for v in values if not is_blank(v)]
    n = len(cells)
    if n == 0:
        return {"n": 0, "date": 0, "number": 0, "integer": 0, "serial": 0, "sku": 0, "master": 0, "month": 0,
                "unique": 0, "region": 0}
    mark = decimal_mark(cells)
    numbers = [parse_number(v, mark) for v in cells]
    parsed = [x for x in numbers if x is not None]
    return {
        "n": n,
        "date": sum(1 for v in cells if not isinstance(v, (int, float)) and parse_date(v) is not None) / n,
        "number": len(parsed) / n,
        "integer": sum(1 for x in parsed if float(x).is_integer()) / n,
        "serial": sum(1 for x in parsed if 20000 <= x <= 80000 and float(x).is_integer()) / n,
        "sku": sum(1 for v in cells if looks_like_sku(v)) / n,
        "master": sum(1 for v in cells if sku_key(v) in ctx.product_keys) / n if ctx.product_keys else 0.0,
        "month": sum(1 for v in cells if parse_month(v) is not None) / n,
        "unique": len({str(v).strip().lower() for v in cells}) / n,
        "region": sum(1 for v in cells if str(v).strip().lower() in ctx.regions) / n if ctx.regions else 0.0,
    }


def fits(f: Field, p: dict[str, float], role: str) -> bool:
    """Can a column with this profile hold this field? The guard every mapping method must pass."""
    if p["n"] == 0:
        return False
    if f.kind == "date":
        return p["date"] >= 0.6 or p["serial"] >= 0.6
    if f.kind == "number":
        return p["number"] >= 0.6 and p["date"] < 0.6
    if f.kind == "month":
        return p["month"] >= 0.6
    if f.kind == "sku":
        if p["date"] >= 0.6:
            return False
        return p["master"] >= 0.5 or p["sku"] >= 0.5 or (role == "products" and p["unique"] >= 0.9)
    if f.name == "order_id":  # order numbers may be text or plain numbers, but never dates
        return p["date"] < 0.6
    return p["number"] < 0.5 and p["date"] < 0.5  # free text


# ----------------------------------------------------------------------------- the methods

def _name_score(header: str, f: Field) -> float:
    names = {normalise_label(a) for a in (*f.aliases, f.name)}
    if header in names:
        return 1.0
    tokens = set(header.split())
    best = 0.0
    for alias in names:
        alias_tokens = set(alias.split())
        if alias_tokens <= tokens:
            # "net amount usd" contains "net amount": longer contained names win over shorter ones.
            best = max(best, 0.85 + 0.02 * len(alias_tokens))
        best = max(best, SequenceMatcher(None, header, alias).ratio() if len(header) >= 4 else 0.0)
    return best if best >= SIMILAR_ENOUGH else 0.0


def _numbers(df: pd.DataFrame, column: str, limit: int = 300) -> list[float | None]:
    cells = list(df[column].head(limit))
    mark = decimal_mark(cells)
    return [parse_number(v, mark) for v in cells]


def _is_product(a: list, b: list, c: list) -> bool:
    """True when c equals a times b on nearly every row where the three are present."""
    rows = [(x, y, z) for x, y, z in zip(a, b, c) if None not in (x, y, z)]
    if len(rows) < 5:
        return False
    good = sum(1 for x, y, z in rows if abs(x * y - z) <= 0.011 + 0.002 * abs(z))
    return good / len(rows) >= 0.9


def _arithmetic(df: pd.DataFrame, assigned: dict[str, str], free: list[str], profiles: dict) -> dict[str, str]:
    """Find quantity, unit price and amount from the relation amount = quantity x unit price."""
    trio = ("quantity", "unit_price", "amount")
    missing = [f for f in trio if f not in assigned]
    numeric = [c for c in free if profiles[c]["number"] >= 0.9 and profiles[c]["date"] < 0.6]
    if not missing or len(numeric) < len(missing):
        return {}
    series = {c: _numbers(df, c) for c in numeric + [assigned[f] for f in trio if f in assigned]}
    found = []
    for columns in itertools.permutations(numeric, len(missing)):
        guess = {**{f: assigned[f] for f in trio if f in assigned}, **dict(zip(missing, columns))}
        if not _is_product(series[guess["quantity"]], series[guess["unit_price"]], series[guess["amount"]]):
            continue
        # Quantity and price are interchangeable in the product, so quantity must be the whole-number one.
        q = profiles[guess["quantity"]]
        if q["integer"] < 0.98 * q["number"]:
            continue
        found.append(guess)
    if len(found) != 1:
        return {}  # nothing fits, or two readings fit equally well and choosing would be a guess
    return {f: c for f, c in found[0].items() if f in missing}


def _price_or_amount(df: pd.DataFrame, sku_col: str, qty_col: str, col: str) -> str | None:
    """Tell a unit price from a line total when only one of them is in the file.

    A unit price stays about the same for one product whatever the quantity; a line total
    divided by the quantity does. Products sold at least twice in different quantities vote.
    """
    qty, value = _numbers(df, qty_col, 2000), _numbers(df, col, 2000)
    groups: dict[str, list[tuple[float, float]]] = {}
    for code, q, v in zip(df[sku_col].head(2000), qty, value):
        if q and v and not is_blank(code):
            groups.setdefault(sku_key(code), []).append((abs(q), abs(v)))
    price = amount = 0
    for rows in groups.values():
        if len({q for q, _ in rows}) < 2:
            continue
        flat = max(v for _, v in rows) / min(v for _, v in rows) <= 1.2
        per_unit = [v / q for q, v in rows]
        scaled = max(per_unit) / min(per_unit) <= 1.2
        price += flat and not scaled
        amount += scaled and not flat
    if price + amount < 5:
        return None
    if price >= 0.8 * (price + amount):
        return "unit_price"
    return "amount" if amount >= 0.8 * (price + amount) else None


def _ask_model(llm, header: str, samples: list[object], open_fields: list[Field]) -> str | None:
    listing = "\n".join(f"- {f.name}: {f.description}" for f in open_fields)
    user = (f"Column header: {header!r}\nSample values: {', '.join(repr(str(s)) for s in samples)}\n\n"
            f"Fields:\n{listing}\n- none: no field above fits\n\nAnswer:")
    try:
        answer = llm.complete(MODEL_SYSTEM, user, max_tokens=12)
    except Exception:  # a model outage must never stop the report
        return None
    words = re.findall(r"[a-z_]+", answer.lower())
    names = {f.name for f in open_fields}
    return next((w for w in words if w in names), None)


def map_columns(df: pd.DataFrame, schema: Schema, ctx: MappingContext, use_model: bool = True) -> Mapping:
    columns = [c for c in df.columns if c != "_row"]
    profiles = {c: profile(df[c], ctx) for c in columns}
    assigned: dict[str, ColumnMatch] = {}  # field -> match
    taken: set[str] = set()

    def place(column: str, f: Field, method: str, confidence: float, note: str = "") -> bool:
        if f.name in assigned or column in taken or not fits(f, profiles[column], schema.role):
            return False
        # Only an exact header may introduce a quantity with decimals (goods sold by weight). A header
        # that merely resembles one ("Unit $" looks like "Units") must hold whole numbers.
        p = profiles[column]
        if f.name == "quantity" and method not in ("name", "memory") and p["integer"] < 0.98 * p["number"]:
            return False
        assigned[f.name] = ColumnMatch(column, f.name, method, round(confidence, 2), note)
        taken.add(column)
        return True

    # 1. memory, 2. name. Candidates are placed best first so that a strong match is never displaced.
    candidates = []
    remembered = ctx.memory.get(schema.role, {})
    for c in columns:
        header = normalise_label(c)
        for order, f in enumerate(schema.fields):
            if remembered.get(header) == f.name:
                candidates.append((2.0, -order, c, f, "memory"))
            elif score := _name_score(header, f):
                candidates.append((score, -order, c, f, "name" if score == 1.0 else "similar name"))
    for score, _, c, f, method in sorted(candidates, key=lambda x: (-x[0], -x[1])):
        place(c, f, method, min(score, 1.0))

    free = [c for c in columns if c not in taken and profiles[c]["n"] > 0]

    # 3. arithmetic
    if schema.role == "sales":
        # A header that only resembles a known name ("Ext. Price" looks like "price" but is a total) is
        # treated as open here: if the numbers settle the question, the numbers win.
        trio = ("quantity", "unit_price", "amount")
        soft = {f: m for f, m in assigned.items() if f in trio and m.method == "similar name"}
        firm = {f: m.column for f, m in assigned.items() if f not in soft}
        solved = _arithmetic(df, firm, free + [m.column for m in soft.values()], profiles)
        if solved:
            for f, m in soft.items():
                del assigned[f]
                taken.discard(m.column)
            for name, column in solved.items():
                place(column, schema.field(name), "arithmetic", 0.95, "amount = quantity x unit price holds on the rows")
        free = [c for c in columns if c not in taken and profiles[c]["n"] > 0]

    # 4. profile: only when exactly one free column qualifies, otherwise it would be a guess.
    def only(test) -> str | None:
        hits = [c for c in free if c not in taken and test(profiles[c])]
        return hits[0] if len(hits) == 1 else None

    for f in schema.fields:
        if f.name in assigned:
            continue
        column = None
        if f.kind == "date":
            column = only(lambda p: p["date"] >= 0.8)
        elif f.kind == "sku":
            column = only(lambda p: p["master"] >= 0.5) if ctx.product_keys else only(lambda p: p["sku"] >= 0.9 and p["unique"] < 0.9)
        elif f.name == "region":
            column = only(lambda p: p["region"] >= 0.8)
        elif f.kind == "month":
            column = only(lambda p: p["month"] >= 0.8 and p["number"] < 0.5)
        if column:
            place(column, f, "profile", 0.8, "the only column whose values fit")

    if schema.role == "sales":
        def open_numbers() -> list[str]:
            return [c for c in free if c not in taken and profiles[c]["number"] >= 0.9 and profiles[c]["date"] < 0.6]

        # Files often carry only two of quantity, price and total, so the product test above cannot run.
        # With no anchor at all, two number columns of which one is whole numbers are read the same way.
        if "quantity" not in assigned and ({"unit_price", "amount"} & assigned.keys() or len(open_numbers()) == 2):
            whole = [c for c in open_numbers() if profiles[c]["integer"] >= 0.98 * profiles[c]["number"]]
            if len(whole) == 1:
                place(whole[0], schema.field("quantity"), "profile", 0.75,
                      "the only whole-number column next to a price or a total")
        # A price or total placed only because its header resembles a known name is re-examined with the
        # same per-product test: "Ext. Price" resembles "price" but is a line total.
        weak = [f for f in ("unit_price", "amount") if f in assigned and assigned[f].method == "similar name"]
        if len(weak) == 1 and {"quantity", "sku"} <= assigned.keys() and \
                len({"unit_price", "amount"} & assigned.keys()) == 1:
            match = assigned[weak[0]]
            kind = _price_or_amount(df, assigned["sku"].column, assigned["quantity"].column, match.column)
            if kind and kind != weak[0]:
                del assigned[weak[0]]
                taken.discard(match.column)
                place(match.column, schema.field(kind), "profile", 0.75,
                      "the header suggested otherwise, but the values behave like a "
                      + ("unit price" if kind == "unit_price" else "line total"))
        if {"quantity", "sku"} <= assigned.keys() and not {"unit_price", "amount"} & assigned.keys() \
                and len(open_numbers()) == 1:
            column = open_numbers()[0]
            kind = _price_or_amount(df, assigned["sku"].column, assigned["quantity"].column, column)
            if kind:
                place(column, schema.field(kind), "profile", 0.75,
                      "stable per product, so a unit price" if kind == "unit_price"
                      else "proportional to the quantity per product, so a line total")

    # 5. the language model, for what is still open
    llm = ctx.llm
    if use_model and llm is not None and not getattr(llm, "is_mock", False):
        for c in [c for c in free if c not in taken]:
            open_fields = [f for f in schema.fields if f.name not in assigned and fits(f, profiles[c], schema.role)]
            if not open_fields:
                continue
            samples = [v for v in df[c] if not is_blank(v)][:5]
            answer = _ask_model(llm, c, samples, open_fields)
            if answer:
                place(c, schema.field(answer), "model", 0.7, "named by the language model, values checked")

    by_column = {m.column: m for m in assigned.values()}
    return Mapping(schema.role, [by_column.get(c, ColumnMatch(c, None)) for c in columns])


def detect_role(df: pd.DataFrame, ctx: MappingContext, use_model: bool = True) -> Mapping | None:
    """Work out whether a table is sales lines, a product list or targets, and map its columns.

    The rules run first for every role. The model is called only when no role is complete
    without it, and only for the role the table resembles most.
    """
    drafts = [map_columns(df, schema, ctx, use_model=False) for schema in SCHEMAS.values()]
    complete = [m for m in drafts if m.complete()]
    if complete:
        best = max(complete, key=lambda m: len(m.fields))  # ties keep the schema order: sales first
        if best.role == "sales" and use_model:
            best = map_columns(df, SALES, ctx)  # let the model name the optional columns too
        return best
    closest = max(drafts, key=lambda m: len(m.fields))
    if len(closest.fields) < 1 or not use_model:
        return None
    retry = map_columns(df, SCHEMAS[closest.role], ctx)
    return retry if retry.complete() else None


# ----------------------------------------------------------------------------- memory

def load_memory(path: Path | None) -> dict[str, dict[str, str]]:
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def remember(memory: dict[str, dict[str, str]], mapping: Mapping) -> None:
    """Keep what the model decided, so next month the same header needs no model call."""
    for m in mapping.matches:
        if m.field and m.method == "model":
            memory.setdefault(mapping.role, {})[normalise_label(m.column)] = m.field

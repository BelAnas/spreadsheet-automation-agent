"""The naive baseline: what a quick script does with default pandas readers.

It reads the first sheet with the first row as the header, renames columns only when the
header is one of the known names, and lets pandas guess dates and numbers. It is the
honest "before" for the benchmark, not a straw man: most hand-written report scripts start
exactly like this.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from sheetagent.schema import SALES

_ALIASES = {alias: f.name for f in SALES.fields for alias in (*f.aliases, f.name.replace("_", " "))}


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def naive_clean(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path) if path.suffix == ".xlsx" else pd.read_csv(path, sep=None, engine="python",
                                                                          encoding_errors="replace")
    except Exception:
        return pd.DataFrame()
    df = df.rename(columns={c: _ALIASES[_norm(c)] for c in df.columns if _norm(c) in _ALIASES})
    if "order_date" not in df or "sku" not in df:
        return pd.DataFrame()
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    for col in ("quantity", "unit_price", "amount"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df and {"quantity", "unit_price"} <= set(df.columns):
        df["amount"] = (df["quantity"] * df["unit_price"]).round(2)
    if "quantity" not in df and {"amount", "unit_price"} <= set(df.columns):
        df["quantity"] = (df["amount"] / df["unit_price"]).round()
    if "amount" not in df or "quantity" not in df:
        return pd.DataFrame()
    return df.dropna(subset=["order_date", "sku", "amount"])

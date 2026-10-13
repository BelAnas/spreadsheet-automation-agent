"""Score a cleaned table against the known truth."""
from __future__ import annotations

from collections import Counter

import pandas as pd


def _keys(df: pd.DataFrame) -> Counter:
    """One key per order line. Counted as a multiset because two identical lines can be real."""
    if df.empty:
        return Counter()
    dates = pd.to_datetime(df["order_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return Counter(zip(df["sku"].astype(str), dates, pd.to_numeric(df["quantity"], errors="coerce").round(3),
                       pd.to_numeric(df["amount"], errors="coerce").round(2)))


def score(clean: pd.DataFrame, truth: pd.DataFrame) -> dict[str, float]:
    """Row recovery (share of true lines found with every value exact), precision and revenue error."""
    needed = ["sku", "order_date", "quantity", "amount"]
    if clean is None or clean.empty or any(c not in clean.columns for c in needed):
        return {"row_recovery": 0.0, "row_precision": 0.0, "revenue_error": 1.0, "rows": 0}
    found, wanted = _keys(clean), _keys(truth)
    matched = sum((found & wanted).values())
    true_revenue = float(truth["amount"].sum())
    revenue = float(pd.to_numeric(clean["amount"], errors="coerce").sum())
    return {"row_recovery": matched / max(1, sum(wanted.values())),
            "row_precision": matched / max(1, sum(found.values())),
            "revenue_error": abs(revenue - true_revenue) / abs(true_revenue), "rows": int(len(clean))}

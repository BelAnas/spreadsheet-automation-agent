"""Compute every figure of the report with pandas. The language model never does arithmetic."""
from __future__ import annotations

import calendar
import json
from pathlib import Path

import pandas as pd

UNKNOWN_CATEGORY = "Not in product list"


def period_label(period: str) -> str:
    year, month = period.split("-")
    return f"{calendar.month_name[int(month)]} {year}"


def previous_period(period: str) -> str:
    year, month = (int(x) for x in period.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _pct(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _change(now: float, before: float | None) -> float | None:
    return round(100 * (now - before) / abs(before), 1) if before else None


def enrich(sales: pd.DataFrame, products: pd.DataFrame | None) -> pd.DataFrame:
    """Add the product name, category and cost to each sales line."""
    if products is None or products.empty:
        out = sales.copy()
        out["product_name"], out["category"], out["unit_cost"] = None, UNKNOWN_CATEGORY, None
    else:
        out = sales.merge(products, on="sku", how="left")
        out["category"] = out["category"].fillna(UNKNOWN_CATEGORY)
    out["cost"] = (out["quantity"] * pd.to_numeric(out["unit_cost"], errors="coerce")).round(2)
    return out


def compute(sales: pd.DataFrame, targets: pd.DataFrame | None, period: str, history: dict,
            currency: str = "USD") -> dict:
    """Return the facts of the month as plain numbers, ready for the report and for the summary."""
    revenue = float(sales["amount"].sum())
    with_id = sales["order_id"].notna()
    orders = int(sales.loc[with_id, "order_id"].nunique())
    returns = sales[sales["amount"] < 0]
    costed = sales[sales["cost"].notna()]
    margin = float(costed["amount"].sum() - costed["cost"].sum()) if len(costed) else None

    totals = {
        "revenue": round(revenue, 2), "units": float(sales["quantity"].sum(skipna=True)), "orders": orders,
        "lines": int(len(sales)), "average_order_value": round(float(sales.loc[with_id, "amount"].sum()) / orders, 2) if orders else None,
        "returns_value": round(float(-returns["amount"].sum()), 2), "return_lines": int(len(returns)),
        "gross_margin": round(margin, 2) if margin is not None else None,
        "margin_pct": _pct(margin, float(costed["amount"].sum())) if margin is not None else None,
    }

    before = history.get(previous_period(period))
    previous = {"period": previous_period(period), "label": period_label(previous_period(period)),
                "revenue": before["revenue"], "units": before["units"], "orders": before["orders"]} if before else None
    change = {"revenue_pct": _change(revenue, before["revenue"]), "units_pct": _change(totals["units"], before["units"]),
              "orders_pct": _change(orders, before["orders"])} if before else None

    month_targets = {}
    if targets is not None and not targets.empty:
        month_targets = dict(targets[targets["month"] == period].groupby("region")["target_revenue"].sum())

    regions = []
    for region, group in sales.groupby("region"):
        amount = float(group["amount"].sum())
        target = month_targets.get(region)
        last = (before or {}).get("regions", {}).get(region)
        regions.append({"region": region, "revenue": round(amount, 2), "share_pct": _pct(amount, revenue),
                        "units": float(group["quantity"].sum(skipna=True)),
                        "orders": int(group["order_id"].nunique()),
                        "target": float(target) if target is not None else None,
                        "attainment_pct": _pct(amount, target) if target else None,
                        "previous_revenue": last, "change_pct": _change(amount, last)})
    regions.sort(key=lambda r: -r["revenue"])

    categories = []
    for category, group in sales.groupby("category"):
        amount = float(group["amount"].sum())
        with_cost = group[group["cost"].notna()]
        categories.append({"category": category, "revenue": round(amount, 2), "share_pct": _pct(amount, revenue),
                           "units": float(group["quantity"].sum(skipna=True)),
                           "margin_pct": _pct(float(with_cost["amount"].sum() - with_cost["cost"].sum()),
                                              float(with_cost["amount"].sum())) if len(with_cost) else None})
    categories.sort(key=lambda c: -c["revenue"])

    top = (sales.groupby(["sku", "product_name"], dropna=False)
           .agg(revenue=("amount", "sum"), units=("quantity", "sum")).reset_index()
           .sort_values(["revenue", "sku"], ascending=[False, True]).head(5))
    top_products = [{"sku": r.sku, "product_name": r.product_name if isinstance(r.product_name, str) else None,
                     "revenue": round(float(r.revenue), 2), "units": float(r.units)} for r in top.itertuples()]

    by_day = sales.groupby("order_date")["amount"].sum().sort_index()
    daily = [{"date": d.isoformat(), "revenue": round(float(v), 2)} for d, v in by_day.items()]
    best = max(daily, key=lambda d: d["revenue"]) if daily else None

    total_target = float(sum(month_targets.values())) if month_targets else None
    return {"period": period, "period_label": period_label(period), "currency": currency, "totals": totals,
            "previous": previous, "change": change, "regions": regions, "categories": categories,
            "top_products": top_products, "daily": daily, "best_day": best,
            "target": {"total": total_target, "attainment_pct": _pct(revenue, total_target)} if total_target else None}


# ----------------------------------------------------------------------------- history

def load_history(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(path: Path, history: dict, facts: dict) -> None:
    """Store this month's headline figures so that next month's report can compare with them."""
    history[facts["period"]] = {"revenue": facts["totals"]["revenue"], "units": facts["totals"]["units"],
                                "orders": facts["totals"]["orders"],
                                "regions": {r["region"]: r["revenue"] for r in facts["regions"]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(history.items())), indent=2), encoding="utf-8")

"""The three charts of the web report, built as Plotly figures.

Each chart shows one measure in one colour, so the colour never has to carry meaning by
itself: the target is a dark tick with a legend entry, and values are written at the bar
ends. Every figure is built twice (light and dark) because the report follows the
reader's system theme.
"""
from __future__ import annotations

from datetime import date

import plotly.graph_objects as go

THEMES = {
    "light": {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e", "grid": "#e7e6e2", "series": "#2a78d6"},
    "dark": {"surface": "#1a1a19", "text": "#f0efec", "muted": "#c3c2b7", "grid": "#33332f", "series": "#3987e5"},
}
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"


def _layout(theme: dict, height: int, **extra) -> dict:
    axis = {"gridcolor": theme["grid"], "linecolor": theme["grid"], "zerolinecolor": theme["grid"],
            "tickfont": {"color": theme["muted"], "size": 12}, "automargin": True}
    layout = {"height": height, "margin": {"l": 8, "r": 24, "t": 8, "b": 8}, "paper_bgcolor": "rgba(0,0,0,0)",
              "plot_bgcolor": "rgba(0,0,0,0)", "font": {"family": FONT, "color": theme["text"], "size": 13},
              "xaxis": dict(axis), "yaxis": dict(axis), "showlegend": False,
              "hoverlabel": {"bgcolor": theme["surface"], "bordercolor": theme["grid"],
                             "font": {"family": FONT, "color": theme["text"]}}}
    for key, value in extra.items():
        layout[key] = {**layout[key], **value} if isinstance(layout.get(key), dict) else value
    return layout


def _compact(x: float) -> str:
    return f"{x / 1000:,.1f}K" if abs(x) >= 1000 else f"{x:,.0f}"


def daily_revenue(facts: dict, theme: dict) -> go.Figure:
    """Change over time: a single 2px line, the best day marked and labelled."""
    days = [date.fromisoformat(d["date"]) for d in facts["daily"]]
    values = [d["revenue"] for d in facts["daily"]]
    fig = go.Figure(go.Scatter(
        x=days, y=values, mode="lines", line={"color": theme["series"], "width": 2, "shape": "linear"},
        hovertemplate="%{x|%a %d %b}<br><b>%{y:,.0f} " + facts["currency"] + "</b><extra></extra>"))
    best = facts["best_day"]
    if best:
        fig.add_trace(go.Scatter(
            x=[date.fromisoformat(best["date"])], y=[best["revenue"]], mode="markers+text",
            marker={"color": theme["series"], "size": 10, "line": {"color": theme["surface"], "width": 2}},
            text=[f"Best day {_compact(best['revenue'])}"], textposition="top center",
            textfont={"color": theme["text"], "size": 12}, hoverinfo="skip", cliponaxis=False))
    fig.update_layout(_layout(theme, 280, xaxis={"showgrid": False, "tickformat": "%d %b"},
                              yaxis={"rangemode": "tozero", "tickformat": "~s"}, hovermode="x",
                              margin={"l": 8, "r": 24, "t": 28, "b": 8}))
    return fig


def region_vs_target(facts: dict, theme: dict) -> go.Figure:
    """Magnitude against a goal: one bar per region, the target as a tick on the same axis."""
    regions = list(reversed(facts["regions"]))  # largest at the top of a horizontal chart
    names = [r["region"] for r in regions]
    fig = go.Figure(go.Bar(
        y=names, x=[r["revenue"] for r in regions], orientation="h", name="Revenue", width=0.42,
        marker={"color": theme["series"], "cornerradius": 4},
        # The value sits inside the bar end: outside, it would collide with the target tick.
        text=[_compact(r["revenue"]) for r in regions], textposition="inside", insidetextanchor="end",
        textfont={"color": "#ffffff", "size": 12}, cliponaxis=False,
        customdata=[[r["share_pct"], r["attainment_pct"] if r["attainment_pct"] is not None else float("nan")]
                    for r in regions],
        hovertemplate="%{y}<br><b>%{x:,.0f} " + facts["currency"] + "</b><br>%{customdata[0]:.1f}% of the total"
                      "<br>%{customdata[1]:.1f}% of target<extra></extra>"))
    with_target = [r for r in regions if r["target"]]
    if with_target:
        fig.add_trace(go.Scatter(
            y=[r["region"] for r in with_target], x=[r["target"] for r in with_target], mode="markers", name="Target",
            marker={"symbol": "line-ns", "size": 26, "line": {"color": theme["text"], "width": 2}},
            hovertemplate="%{y} target<br><b>%{x:,.0f} " + facts["currency"] + "</b><extra></extra>"))
    top = max([r["revenue"] for r in regions] + [r["target"] or 0 for r in regions])
    fig.update_layout(_layout(theme, 70 + 64 * len(regions), showlegend=bool(with_target),
                              legend={"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 0,
                                      "font": {"color": theme["muted"]}},
                              xaxis={"range": [0, top * 1.15], "tickformat": "~s"}, yaxis={"showgrid": False},
                              margin={"l": 8, "r": 24, "t": 36, "b": 8}))
    return fig


def category_revenue(facts: dict, theme: dict) -> go.Figure:
    """Magnitude by category: sorted horizontal bars, value at the tip."""
    categories = list(reversed(facts["categories"]))
    fig = go.Figure(go.Bar(
        y=[c["category"] for c in categories], x=[c["revenue"] for c in categories], orientation="h", width=0.5,
        marker={"color": theme["series"], "cornerradius": 4},
        text=[_compact(c["revenue"]) for c in categories], textposition="outside",
        textfont={"color": theme["text"], "size": 12}, cliponaxis=False,
        customdata=[[c["share_pct"]] for c in categories],
        hovertemplate="%{y}<br><b>%{x:,.0f} " + facts["currency"] + "</b><br>%{customdata[0]:.1f}% of the total<extra></extra>"))
    top = max((c["revenue"] for c in categories), default=1)
    fig.update_layout(_layout(theme, 60 + 40 * len(categories), xaxis={"range": [0, top * 1.15], "tickformat": "~s"},
                              yaxis={"showgrid": False}))
    return fig


def figures(facts: dict) -> dict[str, dict[str, dict]]:
    """All charts for both themes, as plain dictionaries ready to embed in the page."""
    out: dict[str, dict[str, dict]] = {}
    for name, theme in THEMES.items():
        out[name] = {"daily": daily_revenue(facts, theme).to_plotly_json(),
                     "regions": region_vs_target(facts, theme).to_plotly_json(),
                     "categories": category_revenue(facts, theme).to_plotly_json()}
    return out

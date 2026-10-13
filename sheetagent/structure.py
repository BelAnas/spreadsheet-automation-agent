"""Find the table inside a raw sheet: the header row, and the rows that are really data."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .readers import RawTable
from .schema import SCHEMAS
from .values import is_blank, normalise_label, parse_date, parse_number

_TOTAL = re.compile(r"(?i)^\s*(grand\s+|sub\s*-?\s*)?tota(l|ls|ux)\b|^\s*sum\b|^\s*somme\b")
_KNOWN_LABELS = {normalise_label(a) for s in SCHEMAS.values() for f in s.fields for a in (*f.aliases, f.name)}


@dataclass
class Table:
    """A sheet reduced to its data rows, with headers as column names."""
    source: str
    sheet: str | None
    header_row: int  # 0-based position in the sheet
    df: pd.DataFrame  # object dtype; the extra column `_row` is the 1-based spreadsheet row
    audit: list[dict] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.source} [{self.sheet}]" if self.sheet else self.source


def _is_label(value: object) -> bool:
    """True for a cell that can be a column title: short text that is not a number or a date."""
    return (isinstance(value, str) and len(value.strip()) <= 40
            and parse_number(value) is None and parse_number(value, ",") is None and parse_date(value) is None)


def _has_value(cells: list[object]) -> bool:
    """True when a row holds at least one number or date, which is what data rows look like."""
    return any(not isinstance(c, bool) and (isinstance(c, (int, float)) or parse_number(c) is not None
                                             or parse_number(c, ",") is not None or parse_date(c) is not None)
               for c in cells)


def detect_header(grid: pd.DataFrame, max_scan: int = 40) -> int | None:
    """Return the position of the header row, or None when the sheet holds no table.

    A header is the first wide row made of labels only, with data rows under it. Title
    banners fail on width (one or two cells); data rows fail because they hold numbers.
    """
    rows = [[c for c in row if not is_blank(c)] for row in grid.itertuples(index=False, name=None)]
    if not rows:
        return None
    widest = max(len(r) for r in rows)
    best, best_score = None, 0.0
    for i, filled in enumerate(rows[:max_scan]):
        if len(filled) < 2 or len(filled) < 0.5 * widest:
            continue
        labels = [c for c in filled if _is_label(c)]
        # One stray non-label is tolerated in wide headers (a year used as a column title).
        if len(filled) - len(labels) > (1 if len(filled) >= 6 else 0):
            continue
        below = [r for r in rows[i + 1:i + 12] if r][:8]
        if not below:
            continue
        data_like = sum(1 for r in below if _has_value(r) and len(r) >= 0.5 * len(filled)) / len(below)
        if data_like == 0:
            continue
        names = [normalise_label(c) for c in labels]
        known = sum(1 for n in names if n in _KNOWN_LABELS)
        score = (0.5 * len(filled) / widest + 0.4 * data_like + (0.1 if len(set(names)) == len(names) else 0)
                 + min(0.2, 0.05 * known))
        if score > best_score + 1e-9:  # strict: on a tie the earlier row wins
            best, best_score = i, score
    return best


def extract_table(raw: RawTable) -> Table | None:
    """Cut the data out of a raw sheet and log everything that was left behind."""
    header_row = detect_header(raw.grid)
    if header_row is None:
        return None
    header = list(raw.grid.iloc[header_row])
    body = raw.grid.iloc[header_row + 1:]

    # Keep a column when it has a title, or when it has data under an empty title.
    keep, names, seen = [], [], {}
    for position, title in enumerate(header):
        has_data = not body.iloc[:, position].map(is_blank).all()
        if is_blank(title) and not has_data:
            continue
        name = str(title).strip() if not is_blank(title) else f"column_{position + 1}"
        seen[name] = seen.get(name, 0) + 1
        keep.append(position)
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")

    header_key = [normalise_label(header[p]) for p in keep]
    skipped = {"blank row": [], "repeated header": [], "totals row": [], "note line": []}
    rows, row_numbers = [], []
    for offset, cells in enumerate(body.itertuples(index=False, name=None)):
        sheet_row = header_row + offset + 2  # 1-based, as the user sees it in Excel
        cells = [cells[p] for p in keep]
        filled = [c for c in cells if not is_blank(c)]
        if not filled:
            skipped["blank row"].append(sheet_row)
        elif sum(1 for c, h in zip(cells, header_key) if not is_blank(c) and normalise_label(c) == h) >= 0.6 * len(keep):
            skipped["repeated header"].append(sheet_row)
        elif any(isinstance(c, str) and _TOTAL.match(c) for c in filled):
            skipped["totals row"].append(sheet_row)
        elif len(filled) == 1 and len(keep) >= 4:
            skipped["note line"].append(sheet_row)
        else:
            rows.append(cells)
            row_numbers.append(sheet_row)

    df = pd.DataFrame(rows, columns=names, dtype=object)
    df["_row"] = row_numbers
    table = Table(raw.source, raw.sheet, header_row, df)
    if header_row > 0:
        table.audit.append({"step": "structure", "source": table.label, "action": "rows above the header skipped",
                            "count": header_row, "example": f"header found on row {header_row + 1}"})
    for action, where in skipped.items():
        if where:
            table.audit.append({"step": "structure", "source": table.label, "action": f"{action} skipped",
                                "count": len(where), "example": "rows " + ", ".join(map(str, where[:5]))})
    return table

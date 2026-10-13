"""Read spreadsheets as raw grids of cells, without assuming where the table starts.

A default table reader takes the first row as the header. Real exports put a title, a
date line or a logo row above it, so every reader here returns the untouched grid and
leaves finding the table to structure.py.
"""
from __future__ import annotations

import csv
import io
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

SUPPORTED = {".xlsx", ".xlsm", ".csv", ".tsv", ".txt"}
_SHEETS_URL = re.compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)")


@dataclass
class RawTable:
    source: str  # file name or link, shown in the audit log
    sheet: str | None
    grid: pd.DataFrame  # object dtype, no header, one row per spreadsheet row

    @property
    def label(self) -> str:
        return f"{self.source} [{self.sheet}]" if self.sheet else self.source


class ReadError(Exception):
    """A file that cannot be opened. The run continues with the other files."""


def google_sheet_csv_url(url: str) -> str:
    """Turn a Google Sheets browser link into its CSV export link (the sheet must be shared by link)."""
    match = _SHEETS_URL.search(url)
    if not match:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv" + (
        f"&gid={gid.group(1)}" if gid else "")


def _decode(data: bytes) -> str:
    # utf-8-sig removes the byte-order mark Excel adds; cp1252 never fails, so it is the last resort.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def sniff_delimiter(text: str) -> str:
    """Pick the delimiter that splits the most lines into the same number of columns."""
    lines = [ln for ln in text.splitlines()[:60] if ln.strip()]
    best, best_score = ",", -1.0
    for delimiter in (",", ";", "\t", "|"):
        counts = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        # Agreement first, then width: a wrong delimiter gives few, irregular columns.
        score = counts.count(mode) / len(counts) + mode / 1000
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read_csv_bytes(data: bytes, source: str) -> RawTable:
    text = _decode(data)
    rows = list(csv.reader(io.StringIO(text), delimiter=sniff_delimiter(text)))
    width = max((len(r) for r in rows), default=0)
    grid = pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=object)
    return RawTable(source, None, grid.replace("", None))


def read_xlsx(path: Path) -> list[RawTable]:
    try:
        # data_only gives the last value Excel calculated for a formula, not the formula text.
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types for broken files
        raise ReadError(f"{path.name}: cannot open the workbook ({exc.__class__.__name__})") from exc
    tables = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if rows:
            width = max(len(r) for r in rows)
            grid = pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object)
            tables.append(RawTable(path.name, ws.title, grid))
    wb.close()
    return tables


def fetch(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(google_sheet_csv_url(url), headers={"User-Agent": "sheetagent/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (http/https only, checked by caller)
            return response.read()
    except Exception as exc:
        raise ReadError(f"{url}: download failed ({exc})") from exc


def read_source(source: str | Path) -> list[RawTable]:
    """Read one file or link. Raises ReadError with a message a non-developer can act on."""
    text = str(source)
    if text.startswith(("http://", "https://")):
        data = fetch(text)
        if data[:2] == b"PK":  # an .xlsx file is a zip archive
            raise ReadError(f"{text}: links must point to a CSV export, not a workbook")
        name = "google-sheet" if _SHEETS_URL.search(text) else text.rsplit("/", 1)[-1]
        return [read_csv_bytes(data, name)]
    path = Path(text)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    if suffix in (".csv", ".tsv", ".txt"):
        return [read_csv_bytes(path.read_bytes(), path.name)]
    raise ReadError(f"{path.name}: unsupported file type {suffix or '(none)'}")


def collect(inputs: list[str | Path]) -> list[str | Path]:
    """Expand folders into their spreadsheet files, in a stable order; keep links as they are."""
    found: list[str | Path] = []
    for item in inputs:
        if str(item).startswith(("http://", "https://")):
            found.append(str(item))
            continue
        path = Path(item)
        if path.is_dir():
            # '~$name.xlsx' is the lock file Excel leaves next to an open workbook.
            found += sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED and not p.name.startswith(("~$", ".")))
        else:
            found.append(path)
    return found

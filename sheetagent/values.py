"""Turn one spreadsheet cell into a date, a number or a product-code key.

Formats are decided per column, not per cell: "03/04/2026" cannot be read alone, but a
column that also contains "25/04/2026" can only be day-first. The same goes for "1.234",
which is one thousand two hundred in a column that writes "1.234,50" elsewhere.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta

CURRENCY_MARK = re.compile(r"(?i)\b(usd|eur|gbp|mad|cad|aud|chf|aed|sar)\b|[$€£¥]")
_UNIT = re.compile(r"(?i)(?<=\d)\s*(pcs?|pieces?|units?|ea|each|pc|x)\.?$")
_SPACES = re.compile(r"[\s  ']")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")

_MONTHS = {name: i for i, names in enumerate([
    ("jan", "january", "janv", "janvier"), ("feb", "february", "fev", "fevr", "fevrier"),
    ("mar", "march", "mars"), ("apr", "april", "avr", "avril"), ("may", "mai"),
    ("jun", "june", "juin"), ("jul", "july", "juil", "juillet"), ("aug", "august", "aout"),
    ("sep", "sept", "september", "septembre"), ("oct", "october", "octobre"),
    ("nov", "november", "novembre"), ("dec", "december", "decembre")], start=1) for name in names}

_TIME = re.compile(r"[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*([APap][Mm])?Z?$")
_YMD = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_ABY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$")
_D_MON_Y = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[ -]([^\W\d]{3,9})\.?,?[ -](\d{2}|\d{4})$")
_MON_D_Y = re.compile(r"^([^\W\d]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})$")
_MONTH_ONLY = re.compile(r"^(?:(\d{4})[-/](\d{1,2})|(\d{1,2})[-/](\d{4})|([^\W\d]{3,9})\.?[ -](\d{2}|\d{4}))$")

# Characters people and scanners swap in product codes.
_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8"})


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalise_label(text: object) -> str:
    """'Qté ' and 'QTE' become 'qte'; 'Order No.' becomes 'order no'."""
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(str(text)).lower()).strip()


# ----------------------------------------------------------------------------- numbers

def _numeric_core(text: str) -> tuple[str, bool] | None:
    """Strip currency, units, spaces and the sign. Returns the digits-and-separators part."""
    s = text.strip()
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = CURRENCY_MARK.sub("", s.strip("()"))
    s = _SPACES.sub("", _UNIT.sub("", s.strip())).strip("+-")
    if not s or not re.fullmatch(r"[\d.,]*\d[\d.,]*", s):
        return None
    return s, negative


def decimal_mark(values: list[object]) -> str:
    """Decide whether a column writes decimals with a comma or a point, by majority."""
    comma = dot = 0
    for value in values:
        if not isinstance(value, str):
            continue
        core = _numeric_core(value)
        if core is None:
            continue
        s = core[0]
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                comma += 1
            else:
                dot += 1
        elif "," in s and not _THOUSANDS_COMMA.match(s):
            comma += 1
        elif "." in s and not _THOUSANDS_DOT.match(s):
            dot += 1
    return "," if comma > dot else "."


def parse_number(value: object, decimal: str = ".") -> float | None:
    if isinstance(value, bool) or is_blank(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    core = _numeric_core(value)
    if core is None:
        return None
    s, negative = core
    if decimal == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
        if s.count(".") > 1:
            if not _THOUSANDS_DOT.match(s):
                return None  # "03.09.2026" is a date, not a number
            s = s.replace(".", "")
    try:
        number = float(s)
    except ValueError:
        return None
    return -number if negative else number


# ----------------------------------------------------------------------------- dates

def _build(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_text(value: str) -> str:
    return _TIME.sub("", value.strip())


def parse_date(value: object, dayfirst: bool = True, allow_serial: bool = False) -> date | None:
    if is_blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # Excel stores dates as days since 1899-12-30; 20000-80000 covers 1954 to 2119.
        if allow_serial and 20000 <= value <= 80000:
            return date(1899, 12, 30) + timedelta(days=int(value))
        return None
    if not isinstance(value, str):
        return None
    s = _date_text(value)
    if m := _YMD.match(s):
        return _build(int(m[1]), int(m[2]), int(m[3]))
    if m := _ABY.match(s):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        first = _build(year, b, a) if dayfirst else _build(year, a, b)
        # A value such as 25/04 has only one valid reading, whatever the column default is.
        return first or (_build(year, a, b) if dayfirst else _build(year, b, a))
    if m := _D_MON_Y.match(s):
        month = _MONTHS.get(strip_accents(m[2]).lower())
        return _build(int(m[3]), month, int(m[1])) if month else None
    if m := _MON_D_Y.match(s):
        month = _MONTHS.get(strip_accents(m[1]).lower())
        return _build(int(m[3]), month, int(m[2])) if month else None
    return None


def infer_dayfirst(values: list[object], period: str | None = None) -> tuple[bool, str]:
    """Decide day-first or month-first for a column. Returns the choice and the reason, for the audit log."""
    day_first = month_first = ambiguous = 0
    for value in values:
        if not isinstance(value, str):
            continue
        m = _ABY.match(_date_text(value))
        if not m:
            continue
        a, b = int(m[1]), int(m[2])
        if a > 12 >= b:
            day_first += 1
        elif b > 12 >= a:
            month_first += 1
        else:
            ambiguous += 1
    if day_first or month_first:
        if day_first >= month_first:
            return True, f"day first: {day_first} values have a day above 12 in first position"
        return False, f"month first: {month_first} values have a day above 12 in second position"
    if ambiguous and period:
        hits = {flag: sum(1 for v in values if (d := parse_date(v, flag)) and d.strftime("%Y-%m") == period)
                for flag in (True, False)}
        if hits[False] > hits[True]:
            return False, f"month first: this reading puts {hits[False]} dates inside {period}"
        return True, f"day first: this reading puts {hits[True]} dates inside {period}"
    if ambiguous:
        return True, "day first assumed: no value settles the order"
    return True, "no ambiguous dates"


def parse_month(value: object) -> str | None:
    """'2026-09', 'Sep 2026', '09/2026' or any full date become '2026-09'."""
    if is_blank(value):
        return None
    if isinstance(value, str):
        if m := _MONTH_ONLY.match(value.strip()):
            if m[1]:
                year, month = int(m[1]), int(m[2])
            elif m[3]:
                year, month = int(m[4]), int(m[3])
            else:
                year, month = int(m[6]), _MONTHS.get(strip_accents(m[5]).lower(), 0)
            year += 2000 if year < 100 else 0
            return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else None
    d = parse_date(value)
    return d.strftime("%Y-%m") if d else None


# ----------------------------------------------------------------------------- product codes

def sku_text(value: object) -> str:
    """The code as upper-case text. Excel turns numeric codes into floats (10045.0), so undo that."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip().upper()


def sku_key(value: object) -> str:
    """A spelling-proof key: 'ht_0101', ' HT-101 ' and 'HT0101' all give 'HT101'."""
    plain = re.sub(r"[^A-Z0-9]", "", sku_text(value))
    m = re.fullmatch(r"([A-Z]*)0*(\d+)", plain)
    return f"{m[1]}{m[2]}" if m else plain


def sku_confusable(value: object) -> str:
    """The code with look-alike characters merged (O and 0, I and 1), to repair typing slips."""
    return re.sub(r"[^A-Z0-9]", "", sku_text(value)).translate(_CONFUSABLE)


def looks_like_sku(value: object) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    return 3 <= len(s) <= 24 and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _\-./]*", s)) and \
        any(c.isdigit() for c in s) and any(c.isalpha() for c in s) and s.count(" ") <= 1

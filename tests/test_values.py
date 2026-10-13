from datetime import date, datetime

import pytest

from sheetagent.values import (decimal_mark, infer_dayfirst, looks_like_sku, normalise_label, parse_date, parse_month,
                               parse_number, sku_confusable, sku_key)


@pytest.mark.parametrize("raw, mark, expected", [
    ("$1,234.50", ".", 1234.5), ("($45.00)", ".", -45.0), ("1.234,50", ",", 1234.5), ("1 234,50 €", ",", 1234.5),
    ("12 pcs", ".", 12.0), ("1234.50 USD", ".", 1234.5), ("-12,5", ",", -12.5), ("1,234", ".", 1234.0),
    ("1.234", ",", 1234.0), ("1.234", ".", 1.234), (7, ".", 7.0), (2.5, ".", 2.5), ("45-", ".", -45.0),
    ("1.234.567", ".", 1234567.0),
])
def test_parse_number_formats(raw, mark, expected):
    assert parse_number(raw, mark) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["n/a", "-", "", None, "pending", "Unit $", "03.09.2026", "2026-09-03", True, "12 boxes of 6"])
def test_parse_number_rejects_non_numbers(raw):
    assert parse_number(raw) is None


def test_decimal_mark_is_decided_by_the_whole_column():
    assert decimal_mark(["32,25 €", "1 258,00 €"]) == ","
    assert decimal_mark(["$1,234.50", "$5.00"]) == "."
    assert decimal_mark(["1,234", "12"]) == "."  # thousands separators only
    assert decimal_mark(["1.234,50", "1.234"]) == ","


@pytest.mark.parametrize("raw", ["2026-09-03", "03/09/2026", "03.09.2026", "3 Sep 2026", "Sep 3, 2026", "03/09/26",
                                 "2026-09-03 00:00:00", "3 septembre 2026", "3-Sep-26", "2026/09/03"])
def test_parse_date_formats_day_first(raw):
    assert parse_date(raw) == date(2026, 9, 3)


def test_parse_date_month_first_and_unambiguous_values():
    assert parse_date("09/03/2026", dayfirst=False) == date(2026, 9, 3)
    assert parse_date("25/04/2026", dayfirst=False) == date(2026, 4, 25)  # only one valid reading
    assert parse_date(datetime(2026, 9, 3, 14, 0)) == date(2026, 9, 3)
    assert parse_date("1 août 2026") == date(2026, 8, 1)


def test_parse_date_excel_serial_only_when_allowed():
    assert parse_date(46268) is None
    assert parse_date(46268, allow_serial=True) == date(2026, 9, 3)


@pytest.mark.parametrize("raw", ["TBC", "see note", "??", "31/02/2026", "", None, "12", 12.5])
def test_parse_date_rejects_non_dates(raw):
    assert parse_date(raw) is None


def test_infer_dayfirst_from_values_above_twelve():
    assert infer_dayfirst(["03/09/2026", "25/09/2026"])[0] is True
    assert infer_dayfirst(["09/03/2026", "09/25/2026"])[0] is False


def test_infer_dayfirst_uses_the_report_month_when_every_date_is_ambiguous():
    values = ["09/03/2026", "09/04/2026", "09/11/2026"]
    assert infer_dayfirst(values, "2026-09")[0] is False
    assert infer_dayfirst(values, "2026-03")[0] is True
    choice, reason = infer_dayfirst(values)
    assert choice is True and "assumed" in reason


def test_parse_month():
    assert [parse_month(v) for v in ("2026-09", "Sep 2026", "09/2026", "01/09/2026", "September 2026")] == ["2026-09"] * 5
    assert parse_month("soon") is None


def test_sku_keys_survive_spelling():
    assert {sku_key(v) for v in ("HT-0101", "ht_0101", " HT-101 ", "HT0101", "ht 0101")} == {"HT101"}
    assert sku_key(10045.0) == "10045"
    assert sku_confusable("OL-O603") == sku_confusable("OL-0603")
    assert looks_like_sku("HT-0101") and not looks_like_sku("Greenleaf Nursery") and not looks_like_sku("12")


def test_normalise_label():
    assert normalise_label(" Qté ") == "qte"
    assert normalise_label("Order No.") == "order no"

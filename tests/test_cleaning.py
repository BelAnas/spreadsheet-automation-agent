from datetime import date

import pandas as pd

from sheetagent.cleaning import ProductIndex, clean_sales, drop_duplicates, region_from_name
from sheetagent.mapping import MappingContext, map_columns
from sheetagent.schema import SALES
from sheetagent.structure import extract_table

from .conftest import grid

PRODUCTS = ProductIndex(pd.DataFrame({"sku": ["HT-0101", "HT-0102", "OL-0603", "SB-0001", "5B-0001"],
                                      "product_name": list("abcde"), "category": ["x"] * 5, "unit_cost": [1.0] * 5}))


def clean(rows: list[list], header=None, source="sales_north.xlsx", period=None, regions=("North", "South")):
    header = header or ["Order No.", "Order Date", "Product Code", "Qty", "Unit Price", "Line Total"]
    raw = grid([header] + rows)
    raw.source = source
    table = extract_table(raw)
    mapping = map_columns(table.df, SALES, MappingContext(product_keys=set(PRODUCTS.by_key)))
    return clean_sales(table, mapping, PRODUCTS, list(regions), period)


GOOD = [["A-1", "03/09/2026", "HT-0101", 2, "$5.00", "$10.00"], ["A-2", "25/09/2026", "HT-0102", 3, "$4.00", "$12.00"],
        ["A-3", "14/09/2026", "OL-0603", 1, "$9.50", "$9.50"]]


def test_product_codes_are_normalised_repaired_or_reported():
    assert PRODUCTS.resolve("HT-0101") == ("HT-0101", "exact")
    assert PRODUCTS.resolve(" ht_101 ") == ("HT-0101", "normalised")
    assert PRODUCTS.resolve("OL-O603") == ("OL-0603", "repaired")
    assert PRODUCTS.resolve("ZZ-9999") == ("ZZ-9999", "unknown")
    # 'SB-OOO1' could be SB-0001 or 5B-0001 once look-alikes are merged: no repair is made.
    assert PRODUCTS.resolve("SB-OOO1")[1] == "unknown"


def test_values_are_typed_and_rows_keep_their_origin():
    result = clean(GOOD)
    row = result.rows.iloc[0]
    assert (row.order_date, row.sku, row.quantity, row.unit_price, row.amount) == (date(2026, 9, 3), "HT-0101", 2, 5, 10)
    assert row.region == "North" and row.source == "sales_north.xlsx [Sheet1]" and row["row"] == 2
    assert not result.rejected
    assert any("region taken from the file name: North" in a["action"] for a in result.audit)


def test_unusable_rows_are_set_aside_with_a_reason():
    result = clean(GOOD + [["A-4", "03/09/2026", None, 1, "$1.00", "$1.00"], ["A-5", "TBC", "HT-0101", 1, "$1.00", "$1.00"],
                           ["A-6", "04/09/2026", "HT-0101", "n/a", "-", "pending"], ["A-7", None, "HT-0101", 1, 1, 1]])
    assert len(result.rows) == 3
    assert [(r["row"], r["reason"].split(":")[0]) for r in result.rejected] == [
        (5, "no product code"), (6, "date not understood"), (7, "no usable amount"), (8, "date missing")]
    assert "A-6" in result.rejected[2]["original"]  # the original content is kept for whoever fixes the source


def test_missing_total_price_or_quantity_is_computed():
    header = ["Order Date", "SKU", "Qty", "Price"]
    rows = [["03/09/2026", "HT-0101", 2, 5.25], ["25/09/2026", "HT-0102", 3, 4], ["14/09/2026", "OL-0603", 4, 1.5]] * 4
    assert clean(rows, header).rows["amount"].tolist()[:3] == [10.5, 12.0, 6.0]
    header = ["Order Date", "SKU", "Qty", "Amount"]
    rows = [["03/09/2026", "HT-0101", 2, 10.5], ["25/09/2026", "HT-0102", 3, 12], ["14/09/2026", "OL-0603", 4, 6]]
    assert clean(rows, header).rows["unit_price"].tolist() == [5.25, 4.0, 1.5]


def test_amount_that_contradicts_quantity_times_price_is_kept_and_flagged():
    result = clean(GOOD + [["A-9", "05/09/2026", "HT-0101", 2, "$5.00", "$99.00"]])
    assert result.rows["amount"].tolist()[-1] == 99.0
    assert any("amount differs" in a["action"] and a["count"] == 1 for a in result.audit)


def test_unknown_product_is_kept_and_marked():
    result = clean(GOOD + [["A-8", "05/09/2026", "ZZ-9999", 1, "$1.00", "$1.00"]])
    assert result.rows["sku_known"].tolist() == [True, True, True, False]


def test_region_column_wins_over_the_file_name_and_missing_region_is_marked():
    header = ["Order Date", "SKU", "Qty", "Amount", "Branch"]
    rows = [["03/09/2026", "HT-0101", 2, 10, "south"], ["25/09/2026", "HT-0102", 3, 12, " SOUTH "]]
    assert clean(rows, header).rows["region"].tolist() == ["South", "South"]
    assert clean(GOOD, source="export.xlsx").rows["region"].tolist() == ["Unassigned"] * 3


def test_region_from_name():
    regions = ["North", "South", "West"]
    assert region_from_name("sales_north_2026-09.xlsx [Export]", regions) == "North"
    assert region_from_name("West weekly sales.xlsx", regions) == "West"
    assert region_from_name("north_and_south.xlsx", regions) is None  # two regions: no guess
    assert region_from_name("northern.xlsx", regions) is None  # whole words only


def test_ambiguous_dates_follow_the_report_month():
    rows = [["A-1", "09/03/2026", "HT-0101", 2, 5, 10], ["A-2", "09/11/2026", "HT-0102", 3, 4, 12]]
    assert clean(rows, period="2026-09").rows["order_date"].tolist() == [date(2026, 9, 3), date(2026, 9, 11)]
    assert clean(rows, period="2026-03").rows["order_date"].tolist()[0] == date(2026, 3, 9)


def test_duplicates_are_removed_only_when_there_is_an_order_number():
    result = clean(GOOD + [list(GOOD[0])])
    kept, rejected, audit = drop_duplicates(result.rows)
    assert len(kept) == 3 and rejected[0]["reason"] == "duplicate line" and rejected[0]["row"] == 5
    header = ["Order Date", "SKU", "Qty", "Amount"]
    rows = [["03/09/2026", "HT-0101", 2, 10]] * 2 + [["04/09/2026", "HT-0102", 1, 4]]
    no_id = clean(rows, header)
    assert len(drop_duplicates(no_id.rows)[0]) == 3  # identical lines may be two real sales
    assert any("no order number column" in a["action"] for a in no_id.audit)

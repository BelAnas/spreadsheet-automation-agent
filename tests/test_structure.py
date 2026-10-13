from sheetagent.structure import detect_header, extract_table

from .conftest import grid

HEADER = ["Order No.", "Order Date", "Product Code", "Qty", "Line Total"]


def rows(n: int = 4) -> list[list]:
    return [[f"A-{i}", "03/09/2026", "HT-0101", i + 1, f"${(i + 1) * 10}.00"] for i in range(n)]


def test_header_on_first_row():
    assert detect_header(grid([HEADER] + rows()).grid) == 0


def test_header_under_banner_rows():
    raw = grid([["Sales export - North branch"], ["Generated 01/10/2026 by ERP"], [None], HEADER] + rows())
    table = extract_table(raw)
    assert table.header_row == 3
    assert list(table.df.columns) == HEADER + ["_row"]
    assert table.df["_row"].tolist() == [5, 6, 7, 8]  # spreadsheet row numbers, 1-based
    assert any("rows above the header" in a["action"] for a in table.audit)


def test_totals_blank_repeated_header_and_notes_are_left_out():
    body = rows(6)
    raw = grid([HEADER] + body[:3] + [[None] * 5, list(HEADER)] + body[3:] +
               [["TOTAL", None, None, 21, "$210.00"], ["Prepared by the north branch office"]])
    table = extract_table(raw)
    assert len(table.df) == 6
    actions = {a["action"]: a["count"] for a in table.audit}
    assert actions == {"blank row skipped": 1, "repeated header skipped": 1, "totals row skipped": 1,
                       "note line skipped": 1}


def test_sheet_without_a_table_is_refused():
    assert extract_table(grid([["Notes for head office"], ["Region", "Comment"], ["All", "Two deliveries slipped"]])) is None
    assert extract_table(grid([["just a title"]])) is None


def test_empty_columns_are_dropped_and_untitled_data_columns_named():
    raw = grid([["Order Date", None, "SKU", None, "Amount"],
                ["03/09/2026", None, "HT-0101", "x", 10], ["04/09/2026", None, "HT-0102", "y", 12]])
    table = extract_table(raw)
    assert list(table.df.columns) == ["Order Date", "SKU", "column_4", "Amount", "_row"]


def test_duplicate_header_names_are_made_unique():
    raw = grid([["Date", "Total", "Total"], ["03/09/2026", 1, 2], ["04/09/2026", 3, 4]])
    assert list(extract_table(raw).df.columns) == ["Date", "Total", "Total (2)", "_row"]

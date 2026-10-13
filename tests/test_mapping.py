import pandas as pd

from sheetagent.mapping import MappingContext, detect_role, load_memory, map_columns, remember
from sheetagent.schema import SALES

from .conftest import ScriptedLLM

PRODUCTS = ["HT-0101", "HT-0102", "WT-0201", "WT-0202", "SD-0501", "SD-0502"]
PRICES = dict(zip(PRODUCTS, [4.5, 12.0, 30.25, 8.4, 2.99, 15.5]))


def sales(headers: dict[str, str], n: int = 36, extra: dict | None = None) -> pd.DataFrame:
    """A small clean sales table with the given header for each field."""
    rows = []
    for i in range(n):
        sku = PRODUCTS[i % 6]
        qty = [1, 3, 5, 12, 2, 8, 20][i % 7]
        full = {"order_id": f"A-{1000 + i // 2}", "order_date": f"{1 + i % 28:02d}/09/2026", "sku": sku,
                "quantity": qty, "unit_price": PRICES[sku], "amount": round(qty * PRICES[sku], 2),
                "customer": ["Oak Nursery", "Fern Growers", "Hill Garden Centre"][i % 3], "region": "North"}
        row = {header: full[name] for name, header in headers.items()}
        row.update({k: v[i % len(v)] for k, v in (extra or {}).items()})
        rows.append(row)
    return pd.DataFrame(rows, dtype=object)


def fields(mapping) -> dict[str, tuple[str, str]]:
    return {m.column: (m.field, m.method) for m in mapping.matches}


def test_known_names_and_close_spellings():
    df = sales({"order_id": "Order No.", "order_date": "Invoice Date", "sku": "SKU", "quantity": "Quantité",
                "unit_price": "Unit Price", "amount": "Total USD", "customer": "Client"})
    got = fields(map_columns(df, SALES, MappingContext()))
    assert got["Order No."] == ("order_id", "name")
    # Close spellings are placed by name, then confirmed by the arithmetic test when it can run.
    assert got["Quantité"][0] == "quantity" and got["Total USD"][0] == "amount"
    no_price = df.drop(columns=["Unit Price"])
    assert fields(map_columns(no_price, SALES, MappingContext()))["Quantité"] == ("quantity", "similar name")
    assert got["Client"] == ("customer", "name")


def test_arithmetic_finds_quantity_price_and_total_under_unknown_names():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Pieces", "unit_price": "Each", "amount": "Value"})
    got = fields(map_columns(df, SALES, MappingContext()))
    assert got["Pieces"] == ("quantity", "arithmetic")
    assert got["Each"] == ("unit_price", "arithmetic")
    assert got["Value"] == ("amount", "arithmetic")


def test_arithmetic_overrules_a_misleading_header():
    """'Ext. Price' resembles 'price', but the numbers show it is the line total."""
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "unit_price": "Each", "amount": "Ext. Price"})
    got = fields(map_columns(df, SALES, MappingContext()))
    assert got["Ext. Price"] == ("amount", "arithmetic")
    assert got["Each"] == ("unit_price", "arithmetic")
    assert got["Qty"] == ("quantity", "name")


def test_misleading_header_is_caught_without_a_third_column():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Ext. Price"})
    got = fields(map_columns(df, SALES, MappingContext()))
    assert got["Ext. Price"] == ("amount", "profile")


def test_price_and_total_are_told_apart_by_behaviour_per_product():
    price = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "unit_price": "PU"})
    total = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Montant"})
    assert fields(map_columns(price, SALES, MappingContext()))["PU"] == ("unit_price", "profile")
    assert fields(map_columns(total, SALES, MappingContext()))["Montant"] == ("amount", "profile")


def test_a_header_resembling_units_is_not_taken_as_quantity_when_it_holds_prices():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Nb", "unit_price": "Unit $"})
    got = fields(map_columns(df, SALES, MappingContext()))
    assert got["Nb"] == ("quantity", "profile")
    assert got["Unit $"] == ("unit_price", "profile")


def test_profile_places_the_only_date_and_the_product_codes():
    df = sales({"order_date": "Booked", "sku": "Material", "quantity": "Qty", "amount": "Amount", "order_id": "Ref."})
    ctx = MappingContext(product_keys={"HT101", "HT102", "WT201", "WT202", "SD501", "SD502"})
    got = fields(map_columns(df, SALES, ctx))
    assert got["Booked"] == ("order_date", "profile")
    assert got["Material"] == ("sku", "profile")
    assert got["Ref."] == (None, "")  # nothing proves it is the order number: left for the model


def test_irrelevant_columns_are_left_alone():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Amount"},
               extra={"Sales rep": ["A. Reyes", "M. Okafor"], "Notes": ["", "urgent", "pallet"]})
    got = fields(map_columns(df, SALES, MappingContext()))
    assert got["Sales rep"] == (None, "") and got["Notes"] == (None, "")


def test_model_names_a_leftover_column_and_the_answer_is_checked():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Amount", "order_id": "Doc Ref",
                "customer": "Store"})
    llm = ScriptedLLM(rule=lambda prompt: "order_id" if "Doc Ref" in prompt else "customer")
    got = fields(map_columns(df, SALES, MappingContext(llm=llm)))
    assert got["Doc Ref"] == ("order_id", "model") and got["Store"] == ("customer", "model")
    assert len(llm.prompts) == 2  # only the two columns the rules could not place
    assert "'A-1000'" in llm.prompts[0]  # sample values are shown to the model


def test_model_answer_that_does_not_fit_the_values_is_refused():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Amount", "customer": "Store"})
    # A customer name column cannot be a unit price: the field is not even offered, and the answer is ignored.
    llm = ScriptedLLM(rule=lambda prompt: "unit_price")
    got = fields(map_columns(df, SALES, MappingContext(llm=llm)))
    assert got["Store"] == (None, "")
    assert "unit_price" not in llm.prompts[0]


def test_model_failure_or_nonsense_never_breaks_the_mapping():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Amount", "customer": "Store"})
    for llm in (ScriptedLLM(answers=[RuntimeError("timeout")]), ScriptedLLM(answers=["I think it could be many things"])):
        mapping = map_columns(df, SALES, MappingContext(llm=llm))
        assert mapping.complete() and fields(mapping)["Store"] == (None, "")


def test_memory_is_used_before_anything_else_and_saved_from_model_answers(tmp_path):
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Amount", "order_id": "Doc Ref"})
    llm = ScriptedLLM(rule=lambda prompt: "order_id")
    ctx = MappingContext(llm=llm)
    remember(ctx.memory, map_columns(df, SALES, ctx))
    assert ctx.memory == {"sales": {"doc ref": "order_id"}}
    again = ScriptedLLM()
    got = fields(map_columns(df, SALES, MappingContext(memory=ctx.memory, llm=again)))
    assert got["Doc Ref"] == ("order_id", "memory") and again.prompts == []
    assert load_memory(tmp_path / "missing.json") == {}


def test_role_detection():
    products = pd.DataFrame({"Item No.": PRODUCTS, "Description": list("abcdef"), "Family": ["x"] * 6,
                             "Cost": [1.5, 2, 3, 4, 5, 6]}, dtype=object)
    targets = pd.DataFrame({"Region": ["North", "South"], "Month": ["2026-09", "2026-09"], "Target": [100, 200]}, dtype=object)
    notes = pd.DataFrame({"Topic": ["a", "b"], "Count": [1, 2]}, dtype=object)
    ctx = MappingContext()
    assert detect_role(products, ctx).role == "products"
    assert detect_role(targets, ctx).role == "targets"
    assert detect_role(sales({"order_date": "Date", "sku": "SKU", "amount": "Amount"}), ctx).role == "sales"
    assert detect_role(notes, ctx) is None


def test_model_cannot_turn_an_unrelated_column_into_the_region():
    df = sales({"order_date": "Date", "sku": "SKU", "quantity": "Qty", "amount": "Amount"},
               extra={"Warehouse": ["W1", "W2", "W3"]})
    llm = ScriptedLLM(rule=lambda prompt: "region")
    known = MappingContext(llm=llm, regions={"north", "south"})
    assert fields(map_columns(df, SALES, known))["Warehouse"] == (None, "")
    # With no list of regions to check against, the model's answer is the best information there is.
    assert fields(map_columns(df, SALES, MappingContext(llm=llm)))["Warehouse"] == ("region", "model")

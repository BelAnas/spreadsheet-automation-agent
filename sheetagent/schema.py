"""The standard fields every input file is mapped to.

Each kind of table (sales lines, product list, targets) has a small schema. The aliases
are the header spellings the rules recognise without help; anything else goes through
similar-spelling matching, value profiling and, last, the language model.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # text | date | number | integer | sku | month
    required: bool
    description: str  # shown to the language model when it is asked about a column
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schema:
    role: str  # sales | products | targets
    fields: tuple[Field, ...]

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]


SALES = Schema("sales", (
    Field("order_id", "text", False, "the order, invoice or document number",
          ("order id", "order no", "order number", "order", "invoice", "invoice no", "invoice number")),
    Field("order_date", "date", True, "the date of the order or invoice",
          ("order date", "date", "invoice date", "sale date")),
    Field("sku", "sku", True, "the product code (SKU, item number, article number)",
          ("sku", "product code", "item code", "item no", "article", "product id")),
    Field("quantity", "number", False, "how many units were sold on this line",
          ("quantity", "qty", "units", "units sold")),
    Field("unit_price", "number", False, "the price of one unit",
          ("unit price", "price", "price per unit")),
    Field("amount", "number", False, "the line total: quantity multiplied by unit price",
          ("amount", "total", "line total", "net amount", "revenue", "sales")),
    Field("customer", "text", False, "the customer or account name",
          ("customer", "customer name", "client", "account")),
    Field("region", "text", False, "the sales region or branch",
          ("region", "branch", "territory")),
))

PRODUCTS = Schema("products", (
    Field("sku", "sku", True, "the product code", ("sku", "product code", "item code", "item no", "article")),
    Field("product_name", "text", True, "the product name or description",
          ("product name", "product", "name", "description", "item name")),
    Field("category", "text", False, "the product category or family",
          ("category", "product category", "family", "group")),
    Field("unit_cost", "number", False, "what one unit costs the company to buy",
          ("unit cost", "cost", "cost price", "purchase price")),
))

TARGETS = Schema("targets", (
    Field("region", "text", True, "the sales region or branch", ("region", "branch", "territory")),
    Field("month", "month", True, "the month the target applies to", ("month", "period")),
    Field("target_revenue", "number", True, "the revenue target for that region and month",
          ("target", "target revenue", "revenue target", "budget", "goal")),
))

SCHEMAS: dict[str, Schema] = {s.role: s for s in (SALES, PRODUCTS, TARGETS)}

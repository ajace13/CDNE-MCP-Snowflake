"""The Week 4 star schema the tools read: SAKILA.DW (Assignment 4).

Everything that can end up in SQL text (table names, metrics, groupings, categories,
store ids) is listed here. Tools look values up in these lists; nothing a user types is
pasted into SQL.
"""
from __future__ import annotations

DATABASE = "SAKILA"
SCHEMA = "DW"
DW = f"{DATABASE}.{SCHEMA}"

# What one row of each table represents (from the GRAIN comments in 07_sakila_dw_ddl.sql).
GRAIN: dict[str, str] = {
    "FACT_RENTAL": "One row = one rental: one copy of one film rented by one customer on one date, handled by one "
                   "staff member. Counting rows counts rentals, NOT customers.",
    "FACT_PAYMENT": "One row = one payment: one amount paid by one customer to one staff member on one date, against "
                    "one rental. SUM(amount) = revenue.",
    "DIM_CUSTOMER": "One row = one VERSION of a customer (Type 2). Count customers with COUNT(DISTINCT customer_id), "
                    "never COUNT(*) or customer_key.",
    "DIM_FILM": "One row = one VERSION of a film (Type 2), with its category and rental rate at the time.",
    "DIM_STORE": "One row = one store (Type 1). There are two stores.",
    "DIM_STAFF": "One row = one staff member (Type 1).",
    "DIM_DATE": "One row = one calendar day, 2005-01-01 to 2030-12-31, plus -1 for 'no date yet'.",
}

# Sakila's 16 film categories and 2 stores.
CATEGORIES = (
    "Action", "Animation", "Children", "Classics", "Comedy", "Documentary", "Drama", "Family",
    "Foreign", "Games", "Horror", "Music", "New", "Sci-Fi", "Sports", "Travel",
)
STORE_IDS = (1, 2)

# Metrics: each one belongs to exactly one fact table, so a total never joins the two facts.
#   fact     -> which fact table and its event timestamp
#   expr     -> the SQL for the number (fixed text, never user input)
#   counts   -> what the number counts, in plain words
FACTS = {
    "payment": {"table": f"{DW}.FACT_PAYMENT", "ts": "payment_ts", "row_word": "payments"},
    "rental": {"table": f"{DW}.FACT_RENTAL", "ts": "rental_ts", "row_word": "rentals"},
}
METRICS = {
    "revenue": {"fact": "payment", "expr": "SUM(x.amount)",
                "label": "Revenue ($)", "means": "total of payment amounts"},
    "payment_count": {"fact": "payment", "expr": "COUNT(*)",
                      "label": "Payments", "means": "number of payments (one row each)"},
    "avg_payment": {"fact": "payment", "expr": "AVG(x.amount)",
                    "label": "Average payment ($)", "means": "average payment amount"},
    "rental_count": {"fact": "rental", "expr": "SUM(x.rental_count)",
                     "label": "Rentals", "means": "number of rentals (one row each), NOT number of customers"},
    "distinct_customers": {"fact": "rental", "expr": "COUNT(DISTINCT c.customer_id)",
                           "label": "Customers who rented", "means": "number of different customers who rented"},
    "avg_days_out": {"fact": "rental", "expr": "SUM(x.days_out) / NULLIF(COUNT(x.days_out), 0)",
                     "label": "Average days out", "means": "average days a copy was out, returned rentals only"},
}

# Groupings: the label column and how the result rows are ordered.
GROUP_BY = {
    "none": {"expr": None, "one_row_is": "one row = the whole period"},
    "month": {"expr": "TO_CHAR(DATE_TRUNC('month', x.{ts}), 'YYYY-MM')", "one_row_is": "one row = one month"},
    "category": {"expr": "f.category", "one_row_is": "one row = one film category"},
    "store": {"expr": "'Store ' || s.store_id", "one_row_is": "one row = one store"},
    "country": {"expr": "c.country", "one_row_is": "one row = one customer country (as of the event)"},
    "rating": {"expr": "f.rating", "one_row_is": "one row = one film rating (G, PG, ...)"},
}


def table_kind(name: str) -> str:
    n = name.upper()
    if n.startswith("FACT"):
        return "fact"
    if "BRIDGE" in n:
        return "bridge"
    if n.startswith("DIM"):
        return "dimension"
    return "other"


def grain_for(name: str) -> str:
    return GRAIN.get(name.upper(), "Grain not documented. Check the Week 4 DDL before trusting counts from this table.")

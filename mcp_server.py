"""MCP server over the Week 4 Sakila star schema in Snowflake (CNDE Assignment 5, Part 1).

Runs locally over stdio. Claude Desktop or Claude Code starts it as a subprocess, so
nothing may be printed to stdout: all logging goes to stderr.

Run by hand to check it starts:   python mcp_server.py
Browse the tools in a browser:     mcp dev mcp_server.py
"""
from __future__ import annotations

from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

from warehouse import tools

Category = Literal["Action", "Animation", "Children", "Classics", "Comedy", "Documentary", "Drama", "Family",
                   "Foreign", "Games", "Horror", "Music", "New", "Sci-Fi", "Sports", "Travel"]
Metric = Literal["revenue", "payment_count", "avg_payment", "rental_count", "distinct_customers", "avg_days_out"]
GroupBy = Literal["none", "month", "category", "store", "country", "rating"]

mcp = FastMCP(
    "sakila-warehouse",
    instructions=(
        "Read-only tools over the Sakila DVD-rental star schema (SAKILA.DW) in Snowflake. "
        "The data only covers May 2005 to February 2006; check dates with describe_schema before explaining a trend. "
        "If someone asks how many CUSTOMERS or PEOPLE, use metric distinct_customers, never rental_count. "
        "Zero rows means 'no data for that', not 'the answer is zero'. If no tool fits the question "
        "(costs, profit, reviews, demographics, anything outside these tables), say plainly that these tools can't answer it. "
        "Always tell the user which tool ran, the SQL it returned, and how many rows came back."
    ),
)


@mcp.tool()
def describe_schema(table: Optional[str] = None) -> dict:
    """Describe the warehouse: which tables exist, what ONE ROW of each represents, row counts, columns,
    the date range the data covers, and what is NOT in the data.

    Use this FIRST when unsure whether a question can be answered at all, when a question mentions a
    year or period (to check the data covers it), or before counting anything (to check the grain).
    Do NOT use it for business numbers; use aggregate for those.

    Args:
        table: Optional exact table name, one of FACT_RENTAL, FACT_PAYMENT, DIM_CUSTOMER, DIM_FILM,
               DIM_STORE, DIM_STAFF, DIM_DATE. Leave empty for all tables. Anything else is rejected.
    """
    return tools.describe_schema(table)


@mcp.tool()
def find_rentals(start_date: str, end_date: str, category: Optional[Category] = None,
                 store_id: Optional[Literal[1, 2]] = None, limit: int = 50) -> dict:
    """List individual rentals (one row = one rental) in a date range, optionally for one film category and/or store.

    Use this when someone wants to SEE specific rentals: which films were rented, by whom, when, and whether
    they came back. Do NOT use it to count or total anything (it returns at most 200 rows); use aggregate.

    Args:
        start_date: First day to include, YYYY-MM-DD (e.g. "2005-07-01").
        end_date: Last day to include, YYYY-MM-DD. Must not be before start_date.
        category: Optional film category, exactly one of the 16 Sakila categories.
        store_id: Optional store, 1 or 2.
        limit: Rows to return, 1 to 200 (default 50). Larger values are capped at 200.

    Returns rows plus total_matching_rentals, so you can say "showing 50 of 1,112".
    """
    return tools.find_rentals(start_date, end_date, category, store_id, limit)


@mcp.tool()
def aggregate(metric: Metric, start_date: str, end_date: str, group_by: GroupBy = "none",
              category: Optional[Category] = None, store_id: Optional[Literal[1, 2]] = None) -> dict:
    """Compute one business number over a period, optionally broken down by month, category, store,
    customer country or film rating. Use this for ANY total, count or average.

    Pick the metric that matches the question's words:
      revenue            - dollars taken (sum of payments)
      payment_count      - number of payments
      avg_payment        - average payment in dollars
      rental_count       - number of RENTALS (one per transaction). Not the number of customers.
      distinct_customers - number of different CUSTOMERS / people who rented. Use for "how many customers".
      avg_days_out       - average days a copy was kept (returned rentals only)

    Every result also returns the rows matched and distinct customers, so a mismatch is visible.
    If nothing matched, the result says no_data: report "no data for that", never "zero".
    The data covers 2005-05-24 to 2006-02-14 only.

    Args:
        metric: One of the six metrics above.
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD.
        group_by: none (one total), month, category, store, country, or rating.
        category: Optional film category filter.
        store_id: Optional store filter, 1 or 2.
    """
    return tools.aggregate(metric, start_date, end_date, group_by, category, store_id)


@mcp.tool()
def query_cost(query_id: str) -> dict:
    """Report what a query cost: elapsed time, bytes scanned and credits, from Snowflake QUERY_HISTORY.

    Use this after another tool has run, passing the query_id it returned, or the word "last" for the most
    recent query this server ran. Use it when someone asks how long a query took or what it cost.
    Warehouse credits are an ESTIMATE (execution time x the warehouse size's hourly rate); say so when you
    report them. Cloud-services credits come straight from QUERY_HISTORY. Not for business questions.

    Args:
        query_id: A Snowflake query id such as "01b2c3d4-0000-1234-0000-0001a2b3c4d5", or "last".
    """
    return tools.query_cost(query_id)


if __name__ == "__main__":
    mcp.run()  # stdio transport

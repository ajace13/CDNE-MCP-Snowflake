"""The warehouse tools, shared by the MCP server and the Streamlit app.

Rules every tool follows:
- Arguments are typed and validated before any SQL runs.
- Anything that becomes part of the SQL text (a table, a metric, a GROUP BY) must come
  from an allow-list. User values are bound as parameters.
- Every result says which SQL ran, how many rows came back, what one row is, and the
  query id, so cost can be looked up.

The warehouse is the Week 4 Sakila star, SAKILA.DW (see catalog.py).
"""
from __future__ import annotations

import datetime as dt
import re
import time

from . import db
from . import catalog
from .catalog import grain_for, table_kind

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
EARLIEST_DATE = dt.date(1990, 1, 1)
LATEST_DATE = dt.date(2100, 12, 31)

# Credits per hour for a standard warehouse, by size.
CREDITS_PER_HOUR = {
    "X-SMALL": 1, "SMALL": 2, "MEDIUM": 4, "LARGE": 8, "X-LARGE": 16,
    "2X-LARGE": 32, "3X-LARGE": 64, "4X-LARGE": 128, "5X-LARGE": 256, "6X-LARGE": 512,
}

QUERY_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ToolInputError(ValueError):
    """An argument failed validation. The message says what was allowed."""


# ---------- validation helpers (also used by the next two tools) ----------

def parse_date(value: str, name: str) -> dt.date:
    """Accept only YYYY-MM-DD, within a sane range."""
    if isinstance(value, dt.date):
        d = value
    else:
        try:
            d = dt.date.fromisoformat(str(value).strip())
        except ValueError:
            raise ToolInputError(f"{name} must be a date written YYYY-MM-DD, got {value!r}.") from None
    if not (EARLIEST_DATE <= d <= LATEST_DATE):
        raise ToolInputError(f"{name} {d} is outside {EARLIEST_DATE} to {LATEST_DATE}.")
    return d


def parse_date_range(start: str, end: str) -> tuple[dt.date, dt.date]:
    s, e = parse_date(start, "start_date"), parse_date(end, "end_date")
    if s > e:
        raise ToolInputError(f"start_date {s} is after end_date {e}.")
    return s, e


def cap_limit(limit: int | None) -> tuple[int, bool]:
    """Return (limit to use, whether the request was capped)."""
    if limit is None:
        return DEFAULT_LIMIT, False
    try:
        n = int(limit)
    except (TypeError, ValueError):
        raise ToolInputError(f"limit must be a whole number, got {limit!r}.") from None
    if n < 1:
        raise ToolInputError("limit must be at least 1.")
    return (MAX_LIMIT, True) if n > MAX_LIMIT else (n, False)


def check_allowed(value: str, allowed: dict | list | set | tuple, name: str) -> str:
    """Exact match against an allow-list (case-insensitive). Returns the canonical key."""
    lookup = {str(k).lower(): k for k in allowed}
    key = lookup.get(str(value).strip().lower())
    if key is None:
        raise ToolInputError(f"{name} {value!r} is not allowed. Choose one of: {', '.join(map(str, allowed))}.")
    return key


# ---------- tool 1: describe the schema ----------

DESCRIBE_SQL = """
SELECT t.TABLE_NAME,
       t.TABLE_TYPE,
       t.ROW_COUNT,
       LISTAGG(c.COLUMN_NAME || ' ' || c.DATA_TYPE, ', ')
         WITHIN GROUP (ORDER BY c.ORDINAL_POSITION) AS COLUMNS
FROM {db}.INFORMATION_SCHEMA.TABLES t
JOIN {db}.INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
WHERE t.TABLE_SCHEMA = %(schema)s
GROUP BY t.TABLE_NAME, t.TABLE_TYPE, t.ROW_COUNT
ORDER BY t.TABLE_NAME
""".strip().format(db=catalog.DATABASE)


def describe_schema(table: str | None = None) -> dict:
    result = db.run(DESCRIBE_SQL, {"schema": catalog.SCHEMA})
    tables = [
        {
            "table": r["TABLE_NAME"],
            "kind": table_kind(r["TABLE_NAME"]),
            "one_row_is": grain_for(r["TABLE_NAME"]),
            "row_count": r["ROW_COUNT"],
            "columns": r["COLUMNS"],
        }
        for r in result.rows
    ]
    if table:
        name = check_allowed(table, [t["table"] for t in tables], "table")
        tables = [t for t in tables if t["table"] == name]
    cov = coverage()
    return {
        "database": catalog.DATABASE,
        "schema": catalog.SCHEMA,
        "data_covers": f"Rentals {cov['first_rental']} to {cov['last_rental']}; payments {cov['first_payment']} to {cov['last_payment']}. "
                       "Questions about other dates have no data.",
        "not_in_the_data": "Costs, profit, inventory purchase prices, reviews or ratings by customers, web traffic, "
                           "customer demographics (age, gender, income), anything after the last date above.",
        "tables": tables,
        "rows_returned": len(tables),
        "one_row_is": "One row of this answer = one table in the warehouse.",
        "sql": result.display_sql,
        "query_id": result.query_id,
    }


# ---------- tool 4: report what a query cost ----------

COST_SQL = """
SELECT QUERY_ID, EXECUTION_STATUS, START_TIME,
       TOTAL_ELAPSED_TIME, COMPILATION_TIME, EXECUTION_TIME, QUEUED_OVERLOAD_TIME,
       BYTES_SCANNED, ROWS_PRODUCED, WAREHOUSE_NAME, WAREHOUSE_SIZE,
       CREDITS_USED_CLOUD_SERVICES
FROM TABLE({db}.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(RESULT_LIMIT => 1000))
WHERE QUERY_ID = %(query_id)s
""".strip().format(db=catalog.DATABASE)


def query_cost(query_id: str) -> dict:
    if str(query_id).strip().lower() == "last":
        if not db.last_query_id:
            raise ToolInputError("No query has run yet in this session, so there is no 'last' query to cost.")
        query_id = db.last_query_id
    query_id = str(query_id).strip().lower()
    if not QUERY_ID_RE.match(query_id):
        raise ToolInputError(f"query_id must look like 01b2c3d4-0000-1234-0000-0001a2b3c4d5, got {query_id!r}.")

    row = None
    for attempt in range(4):  # history can take a moment to show a query that just finished
        found = db.run(COST_SQL, {"query_id": query_id})
        if found.rows:
            row = found.rows[0]
            break
        time.sleep(1.5)
    if row is None:
        return {
            "query_id": query_id,
            "found": False,
            "message": "That query isn't in this user's query history from the last 7 days.",
            "sql": found.display_sql,
            "cost_query_id": found.query_id,
        }

    size = (row["WAREHOUSE_SIZE"] or "").upper()
    exec_ms = row["EXECUTION_TIME"] or 0
    if size in CREDITS_PER_HOUR:
        est = exec_ms / 3_600_000 * CREDITS_PER_HOUR[size]
        note = (f"Estimate = execution time x {CREDITS_PER_HOUR[size]} credits/hour for a {size.title()} warehouse. "
                "Snowflake actually bills the warehouse per second with a 60-second minimum each time it resumes, "
                "shared by all queries running on it.")
    else:
        est = 0.0
        note = "No warehouse time was used (answered from the result cache or metadata)."
    return {
        "query_id": query_id,
        "found": True,
        "status": row["EXECUTION_STATUS"],
        "started": row["START_TIME"],
        "elapsed_seconds": round((row["TOTAL_ELAPSED_TIME"] or 0) / 1000, 3),
        "compile_seconds": round((row["COMPILATION_TIME"] or 0) / 1000, 3),
        "execution_seconds": round(exec_ms / 1000, 3),
        "queued_seconds": round((row["QUEUED_OVERLOAD_TIME"] or 0) / 1000, 3),
        "bytes_scanned": row["BYTES_SCANNED"],
        "rows_produced": row["ROWS_PRODUCED"],
        "warehouse": row["WAREHOUSE_NAME"],
        "warehouse_size": row["WAREHOUSE_SIZE"],
        "cloud_services_credits": row["CREDITS_USED_CLOUD_SERVICES"],
        "estimated_warehouse_credits": round(est, 8),
        "credits_note": note,
        "sql": found.display_sql,
        "cost_query_id": found.query_id,
    }


# ---------- data coverage (cached: the sample data doesn't change) ----------

COVERAGE_SQL = f"""
SELECT (SELECT MIN(rental_ts)::DATE  FROM {catalog.DW}.FACT_RENTAL)  AS FIRST_RENTAL,
       (SELECT MAX(rental_ts)::DATE  FROM {catalog.DW}.FACT_RENTAL)  AS LAST_RENTAL,
       (SELECT MIN(payment_ts)::DATE FROM {catalog.DW}.FACT_PAYMENT) AS FIRST_PAYMENT,
       (SELECT MAX(payment_ts)::DATE FROM {catalog.DW}.FACT_PAYMENT) AS LAST_PAYMENT
""".strip()

_coverage: dict | None = None


def coverage() -> dict:
    global _coverage
    if _coverage is None:
        r = db.run(COVERAGE_SQL).rows[0]
        _coverage = {k.lower(): (v if isinstance(v, str) else str(v)) for k, v in r.items()}
    return _coverage


def coverage_note(start: dt.date, end: dt.date, fact: str) -> str | None:
    """Say plainly when the requested dates fall partly or wholly outside the data."""
    cov = coverage()
    first = dt.date.fromisoformat(cov[f"first_{fact}"])
    last = dt.date.fromisoformat(cov[f"last_{fact}"])
    if end < first or start > last:
        return (f"The data has {fact}s only from {first} to {last}. The requested period {start} to {end} "
                f"is entirely outside it, so there is nothing to report for it (not zero: no data).")
    if start < first or end > last:
        return f"Note: {fact} data only runs from {first} to {last}; the part of {start} to {end} outside that has no data."
    return None


def _filters(category: str | None, store_id: int | None, params: dict) -> tuple[str, dict]:
    """Optional filters. Their SQL text is fixed; only the checked values are bound."""
    sql, shown = "", {}
    if category:
        params["category"] = check_allowed(category, catalog.CATEGORIES, "category")
        sql += "\n  AND f.category = %(category)s"
        shown["category"] = params["category"]
    if store_id is not None:
        try:
            sid = int(store_id)
        except (TypeError, ValueError):
            raise ToolInputError(f"store_id must be 1 or 2, got {store_id!r}.") from None
        if sid not in catalog.STORE_IDS:
            raise ToolInputError(f"store_id must be 1 or 2, got {store_id!r}. Sakila has two stores.")
        params["store_id"] = sid
        sql += "\n  AND s.store_id = %(store_id)s"
        shown["store_id"] = sid
    return sql, shown


# ---------- tool 2: parameterized query (individual rentals) ----------

def find_rentals(start_date: str, end_date: str, category: str | None = None,
                 store_id: int | None = None, limit: int | None = None) -> dict:
    start, end = parse_date_range(start_date, end_date)
    n, capped = cap_limit(limit)
    params: dict = {"start_date": start, "end_date": end, "limit": n}
    where, shown = _filters(category, store_id, params)
    sql = f"""
SELECT r.rental_id,
       TO_CHAR(r.rental_ts, 'YYYY-MM-DD HH24:MI') AS rented_at,
       f.title, f.category, s.store_id,
       c.customer_id, c.first_name || ' ' || c.last_name AS customer,
       TO_CHAR(r.return_ts, 'YYYY-MM-DD HH24:MI') AS returned_at,
       r.days_out,
       COUNT(*) OVER () AS total_matching
FROM {catalog.DW}.FACT_RENTAL r
JOIN {catalog.DW}.DIM_FILM f     ON f.film_key = r.film_key
JOIN {catalog.DW}.DIM_STORE s    ON s.store_key = r.store_key
JOIN {catalog.DW}.DIM_CUSTOMER c ON c.customer_key = r.customer_key
WHERE r.rental_ts >= %(start_date)s
  AND r.rental_ts < DATEADD(day, 1, %(end_date)s::DATE){where}
ORDER BY r.rental_ts, r.rental_id
LIMIT %(limit)s""".strip()
    result = db.run(sql, params)
    total = result.rows[0]["TOTAL_MATCHING"] if result.rows else 0
    rows = [{k.lower(): v for k, v in r.items() if k != "TOTAL_MATCHING"} for r in result.rows]
    out = {
        "tool": "find_rentals",
        "filters": {"start_date": str(start), "end_date": str(end), **shown, "limit": n},
        "rows": rows,
        "rows_returned": len(rows),
        "total_matching_rentals": total,
        "one_row_is": catalog.GRAIN["FACT_RENTAL"],
        "sql": result.display_sql,
        "query_id": result.query_id,
    }
    notes = []
    if capped:
        notes.append(f"You asked for {limit} rows; the limit is capped at {MAX_LIMIT}.")
    if total > len(rows):
        notes.append(f"Showing {len(rows)} of {total} matching rentals.")
    if total == 0:
        notes.append("No rentals matched these filters. That means no data for this slice, not that the answer is zero.")
        cov = coverage_note(start, end, "rental")
        if cov:
            notes.append(cov)
    out["notes"] = notes
    return out


# ---------- tool 3: aggregate over a period ----------

def aggregate(metric: str, start_date: str, end_date: str, group_by: str = "none",
              category: str | None = None, store_id: int | None = None) -> dict:
    metric = check_allowed(metric, catalog.METRICS, "metric")
    group_by = check_allowed(group_by or "none", catalog.GROUP_BY, "group_by")
    start, end = parse_date_range(start_date, end_date)
    m, g = catalog.METRICS[metric], catalog.GROUP_BY[group_by]
    fact = catalog.FACTS[m["fact"]]
    params: dict = {"start_date": start, "end_date": end}
    where, shown = _filters(category, store_id, params)

    measures = (f"{m['expr']} AS value,\n       COUNT(*) AS fact_rows,\n"
                f"       COUNT(DISTINCT c.customer_id) AS distinct_customers")
    if g["expr"]:
        select = f"SELECT {g['expr'].format(ts=fact['ts'])} AS group_value,\n       {measures}"
        tail = "\nGROUP BY 1\nORDER BY " + ("1" if group_by == "month" else "2 DESC NULLS LAST, 1") + f"\nLIMIT {MAX_LIMIT}"
    else:
        select, tail = f"SELECT {measures}", ""
    sql = f"""
{select}
FROM {fact['table']} x
JOIN {catalog.DW}.DIM_FILM f     ON f.film_key = x.film_key
JOIN {catalog.DW}.DIM_STORE s    ON s.store_key = x.store_key
JOIN {catalog.DW}.DIM_CUSTOMER c ON c.customer_key = x.customer_key
WHERE x.{fact['ts']} >= %(start_date)s
  AND x.{fact['ts']} < DATEADD(day, 1, %(end_date)s::DATE){where}{tail}""".strip()
    result = db.run(sql, params)

    def tidy(v):
        return round(v, 2) if isinstance(v, float) else v

    rows = [{k.lower(): tidy(v) for k, v in r.items()} for r in result.rows]
    total_fact_rows = sum(r["fact_rows"] or 0 for r in rows)
    out = {
        "tool": "aggregate",
        "metric": metric,
        "metric_label": m["label"],
        "metric_means": m["means"],
        "counted_from": f"{fact['table']} ({fact['row_word']})",
        "group_by": group_by,
        "filters": {"start_date": str(start), "end_date": str(end), **shown},
        "rows": rows if total_fact_rows else [],
        "rows_returned": len(rows) if total_fact_rows else 0,
        "one_row_is": g["one_row_is"],
        f"{fact['row_word']}_matched": total_fact_rows,
        "sql": result.display_sql,
        "query_id": result.query_id,
        "no_data": total_fact_rows == 0,
    }
    notes = []
    if total_fact_rows == 0:
        notes.append(f"No {fact['row_word']} matched these filters. That means there is no data for this slice, "
                     "not that the answer is zero.")
    if metric == "rental_count":
        notes.append("This counts rentals (rows). For the number of different customers, use distinct_customers; "
                     "each row also carries distinct_customers for comparison.")
    cov = coverage_note(start, end, m["fact"])
    if cov:
        notes.append(cov)
    out["notes"] = notes
    return out

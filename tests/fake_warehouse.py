"""A stand-in for db.run that answers by the shape of the SQL, so the real tools, router,
answer builder and app can be tested end to end without Snowflake.

The numbers are shaped like Sakila's (16,044 rentals, 599 customers, data 2005-05-24..2006-02-14)
but are not the live values.
"""
from __future__ import annotations

import datetime as dt
import itertools

from warehouse import db

QID = itertools.count(1)
MONTHS = [("2005-05", 4824.43), ("2005-06", 9631.88), ("2005-07", 28373.89), ("2005-08", 24072.13),
          ("2006-02", 514.18)]
CATS = [("Sports", 5314.21), ("Sci-Fi", 4756.98), ("Animation", 4656.30), ("Music", 3417.72)]


def _qid() -> str:
    return f"01b2c3d4-0000-1234-0000-{next(QID):012d}"


def _in_data(params: dict) -> bool:
    start, end = params.get("start_date"), params.get("end_date")
    return not (start and (end < dt.date(2005, 5, 24) or start > dt.date(2006, 2, 14)))


def fake_run(sql: str, params: dict | None = None) -> db.QueryResult:
    params = dict(params or {})
    if "QUERY_HISTORY" in sql:
        rows = [{"QUERY_ID": params["query_id"], "EXECUTION_STATUS": "SUCCESS", "START_TIME": "2026-11-02 10:00",
                 "TOTAL_ELAPSED_TIME": 412, "COMPILATION_TIME": 120, "EXECUTION_TIME": 280,
                 "QUEUED_OVERLOAD_TIME": 0, "BYTES_SCANNED": 524288, "ROWS_PRODUCED": 1,
                 "WAREHOUSE_NAME": "CLASS_WH", "WAREHOUSE_SIZE": "X-Small", "CREDITS_USED_CLOUD_SERVICES": 0.000012}]
    elif "INFORMATION_SCHEMA.TABLES" in sql:
        rows = [{"TABLE_NAME": t, "TABLE_TYPE": "BASE TABLE", "ROW_COUNT": n, "COLUMNS": "ID NUMBER"}
                for t, n in [("DIM_CUSTOMER", 599), ("DIM_FILM", 1000), ("DIM_STORE", 2),
                             ("FACT_PAYMENT", 16044), ("FACT_RENTAL", 16044)]]
    elif "TOTAL_MATCHING" in sql.upper():
        rows = [] if not _in_data(params) else [
            {"RENTAL_ID": i, "RENTED_AT": f"2005-08-01 10:{i:02d}", "TITLE": f"FILM {i}",
             "CATEGORY": params.get("category", "Comedy"), "STORE_ID": 1, "CUSTOMER_ID": 100 + i,
             "CUSTOMER": f"CUSTOMER {i}", "RETURNED_AT": "2005-08-04 09:00", "DAYS_OUT": 3, "TOTAL_MATCHING": 671}
            for i in range(min(params["limit"], 50))]
    elif "AS group_value" in sql:
        if not _in_data(params):
            rows = []
        elif "DATE_TRUNC('month'" in sql:
            rows = [{"GROUP_VALUE": m, "VALUE": v, "FACT_ROWS": int(v / 4.2), "DISTINCT_CUSTOMERS": 590}
                    for m, v in MONTHS]
        else:
            rows = [{"GROUP_VALUE": c, "VALUE": v, "FACT_ROWS": int(v / 4.2), "DISTINCT_CUSTOMERS": 500}
                    for c, v in CATS]
    else:  # scalar aggregate: one row even when nothing matched (SUM over nothing is NULL)
        if not _in_data(params):
            rows = [{"VALUE": None, "FACT_ROWS": 0, "DISTINCT_CUSTOMERS": 0}]
        elif "COUNT(DISTINCT c.customer_id) AS value" in sql:
            rows = [{"VALUE": 519, "FACT_ROWS": 1112, "DISTINCT_CUSTOMERS": 519}]
        elif "SUM(x.rental_count) AS value" in sql:
            rows = [{"VALUE": 16044, "FACT_ROWS": 16044, "DISTINCT_CUSTOMERS": 599}]
        else:
            rows = [{"VALUE": 67406.56, "FACT_ROWS": 16044, "DISTINCT_CUSTOMERS": 599}]
    qid = _qid()
    db.last_query_id = qid
    return db.QueryResult(sql=sql, params=params, display_sql=db.render_for_display(sql, params), query_id=qid,
                          columns=list(rows[0]) if rows else [], rows=rows)


COVERAGE = {"first_rental": "2005-05-24", "last_rental": "2006-02-14",
            "first_payment": "2005-05-24", "last_payment": "2006-02-14"}

"""Tests that need no Snowflake connection:  python -m pytest -q"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warehouse import config, db, tools  # noqa: E402
from warehouse.catalog import grain_for, table_kind  # noqa: E402


# ---- dates ----
def test_parse_date_accepts_iso():
    assert tools.parse_date("2005-07-01", "start_date") == dt.date(2005, 7, 1)


@pytest.mark.parametrize("bad", ["07/01/2005", "last summer", "2005-13-01", "2005-07-01; DROP TABLE X", ""])
def test_parse_date_rejects_non_dates(bad):
    with pytest.raises(tools.ToolInputError):
        tools.parse_date(bad, "start_date")


def test_parse_date_rejects_out_of_range():
    with pytest.raises(tools.ToolInputError):
        tools.parse_date("1850-01-01", "start_date")


def test_date_range_order():
    with pytest.raises(tools.ToolInputError):
        tools.parse_date_range("2006-01-01", "2005-01-01")


# ---- limit ----
def test_limit_default_and_cap():
    assert tools.cap_limit(None) == (tools.DEFAULT_LIMIT, False)
    assert tools.cap_limit(10) == (10, False)
    assert tools.cap_limit(1_000_000) == (tools.MAX_LIMIT, True)


@pytest.mark.parametrize("bad", [0, -5, "ten", "10; DROP TABLE X"])
def test_limit_rejects_bad(bad):
    with pytest.raises(tools.ToolInputError):
        tools.cap_limit(bad)


# ---- allow-list ----
def test_allow_list_exact_match_only():
    allowed = ["month", "category", "store"]
    assert tools.check_allowed("Month", allowed, "group_by") == "month"
    for attack in ["category; DROP TABLE FACT_RENTAL", "customer_email", "1=1", "month--"]:
        with pytest.raises(tools.ToolInputError):
            tools.check_allowed(attack, allowed, "group_by")


# ---- query id ----
@pytest.mark.parametrize("bad", ["abc", "' OR 1=1 --", "01b2c3d4-0000-1234-0000-0001a2b3c4d5x"])
def test_query_cost_rejects_bad_ids_before_any_sql(bad, monkeypatch):
    monkeypatch.setattr(db, "run", lambda *a, **k: pytest.fail("SQL should not run for a bad id"))
    with pytest.raises(tools.ToolInputError):
        tools.query_cost(bad)


def test_query_cost_last_with_nothing_run(monkeypatch):
    monkeypatch.setattr(db, "last_query_id", None)
    with pytest.raises(tools.ToolInputError):
        tools.query_cost("last")


# ---- display SQL ----
def test_display_sql_quotes_values():
    shown = db.render_for_display("SELECT * FROM T WHERE c = %(c)s AND d >= %(d)s LIMIT %(n)s",
                                  {"c": "Sci-Fi's", "d": dt.date(2005, 7, 1), "n": 50})
    assert shown == "SELECT * FROM T WHERE c = 'Sci-Fi''s' AND d >= '2005-07-01' LIMIT 50"


# ---- catalog ----
def test_grain_and_kind():
    assert "one rental" in grain_for("fact_rental")
    assert "VERSION of a customer" in grain_for("DIM_CUSTOMER")
    assert table_kind("FACT_PAYMENT") == "fact"
    assert "not documented" in grain_for("SOMETHING_ELSE")


# ---- settings come from the Week 4 config.toml, env overrides win ----
def test_settings_from_config_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[connections.claude]\naccount = "org-acct"\nuser = "CLAUDE_AGENT"\nauthenticator = "SNOWFLAKE_JWT"\n'
        'private_key_file = "C:/Users/x/.snowflake/claude_agent.p8"\nrole = "CLAUDE_RW"\n'
        'warehouse = "CLASS_WH"\ndatabase = "CLASSICMODELS"\nschema = "DW"\n'
    )
    for k in list(config.ENV_OVERRIDES) + ["SNOWFLAKE_CONNECTION"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SNOWFLAKE_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("SNOWFLAKE_ROLE", "A5_APP_RO")
    s = config.load_settings()
    assert s["account"] == "org-acct" and s["role"] == "A5_APP_RO" and s["schema"] == "DW"


def test_settings_missing_says_what(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_CONFIG_FILE", str(tmp_path / "nope.toml"))
    for k in config.ENV_OVERRIDES:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="Missing"):
        config.load_settings()


# ---- the MCP server exposes the tools with their docstrings ----
def test_mcp_lists_tools():
    from mcp.shared.memory import create_connected_server_and_client_session

    import mcp_server

    async def go():
        async with create_connected_server_and_client_session(mcp_server.mcp._mcp_server) as client:
            return (await client.list_tools()).tools

    listed = {t.name: t for t in asyncio.run(go())}
    assert set(listed) == {"describe_schema", "find_rentals", "aggregate", "query_cost"}
    agg = listed["aggregate"].inputSchema["properties"]
    assert "distinct_customers" in str(agg["metric"]) and "Sci-Fi" in str(agg["category"])
    assert "ONE ROW" in listed["describe_schema"].description
    assert "ESTIMATE" in listed["query_cost"].description


# ---- aggregate / find_rentals with a fake warehouse ----
class FakeDB:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, sql, params=None):
        self.calls.append((sql, dict(params or {})))
        return db.QueryResult(sql=sql, params=params or {}, display_sql=db.render_for_display(sql, params or {}),
                              query_id="01b2c3d4-0000-1234-0000-0001a2b3c4d5",
                              columns=list(self.rows[0]) if self.rows else [], rows=list(self.rows))


@pytest.fixture
def fake_cov(monkeypatch):
    monkeypatch.setattr(tools, "_coverage", {"first_rental": "2005-05-24", "last_rental": "2006-02-14",
                                             "first_payment": "2005-05-24", "last_payment": "2006-02-14"})


def test_aggregate_builds_fixed_sql_and_binds_values(monkeypatch, fake_cov):
    fake = FakeDB([{"GROUP_VALUE": "Action", "VALUE": 1112, "FACT_ROWS": 1112, "DISTINCT_CUSTOMERS": 510}])
    monkeypatch.setattr(db, "run", fake)
    out = tools.aggregate("rental_count", "2005-01-01", "2005-12-31", "category", category="action", store_id=1)
    sql, params = fake.calls[0]
    assert "FROM SAKILA.DW.FACT_RENTAL x" in sql and "f.category = %(category)s" in sql
    assert "action" not in sql.lower().replace("transaction", "")  # value is bound, not pasted
    assert params["category"] == "Action" and params["store_id"] == 1
    assert out["rentals_matched"] == 1112 and not out["no_data"]
    assert any("distinct_customers" in n for n in out["notes"])  # rows-vs-customers warning


def test_aggregate_counts_customers_by_natural_key(monkeypatch, fake_cov):
    fake = FakeDB([{"VALUE": 590, "FACT_ROWS": 15000, "DISTINCT_CUSTOMERS": 590}])
    monkeypatch.setattr(db, "run", fake)
    tools.aggregate("distinct_customers", "2005-05-24", "2006-02-14")
    assert "COUNT(DISTINCT c.customer_id) AS value" in fake.calls[0][0]  # not customer_key (Type 2 versions)


def test_aggregate_zero_rows_is_no_data(monkeypatch, fake_cov):
    # SUM over nothing still returns one row: value NULL, fact_rows 0.
    monkeypatch.setattr(db, "run", FakeDB([{"VALUE": None, "FACT_ROWS": 0, "DISTINCT_CUSTOMERS": 0}]))
    out = tools.aggregate("revenue", "2007-01-01", "2007-12-31")
    assert out["no_data"] and out["rows"] == []
    assert any("not that the answer is zero" in n for n in out["notes"])
    assert any("entirely outside" in n for n in out["notes"])


@pytest.mark.parametrize("kwargs", [
    {"metric": "profit"},
    {"metric": "revenue; DROP TABLE SAKILA.DW.FACT_RENTAL"},
    {"group_by": "category; DROP TABLE X"},
    {"group_by": "customer_email"},
    {"category": "Horror' OR '1'='1"},
    {"store_id": 3},
    {"start_date": "last year"},
])
def test_aggregate_rejects_before_sql(monkeypatch, kwargs):
    monkeypatch.setattr(db, "run", lambda *a, **k: pytest.fail("SQL must not run"))
    args = {"metric": "revenue", "start_date": "2005-06-01", "end_date": "2005-06-30", "group_by": "none"}
    args.update(kwargs)
    with pytest.raises(tools.ToolInputError):
        tools.aggregate(**args)


def test_find_rentals_caps_limit_and_reports_total(monkeypatch, fake_cov):
    rows = [{"RENTAL_ID": i, "RENTED_AT": "2005-07-01 10:00", "TITLE": "X", "CATEGORY": "Horror", "STORE_ID": 2,
             "CUSTOMER_ID": 1, "CUSTOMER": "A B", "RETURNED_AT": None, "DAYS_OUT": None, "TOTAL_MATCHING": 999}
            for i in range(200)]
    fake = FakeDB(rows)
    monkeypatch.setattr(db, "run", fake)
    out = tools.find_rentals("2005-07-01", "2005-07-31", "Horror", 2, limit=1_000_000)
    assert fake.calls[0][1]["limit"] == 200
    assert out["total_matching_rentals"] == 999 and "total_matching" not in out["rows"][0]
    assert any("capped" in n for n in out["notes"]) and any("200 of 999" in n for n in out["notes"])

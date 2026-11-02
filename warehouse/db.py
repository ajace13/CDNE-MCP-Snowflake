"""One Snowflake connection, opened lazily, used by every tool.

Every query goes through run(): values are passed as bind parameters (never pasted
into the SQL text), the Snowflake query id is captured for the cost tool, and a
display copy of the SQL with the values filled in is kept for the app's panel.
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from .config import load_settings

QUERY_TAG = "cnde_a5_mcp"
STATEMENT_TIMEOUT_SECONDS = 60

_conn = None
_settings: dict | None = None
last_query_id: str | None = None


@dataclass
class QueryResult:
    sql: str                      # SQL as sent, with %(name)s placeholders
    params: dict                  # bound values
    display_sql: str              # SQL with values filled in, for people to read
    query_id: str | None
    columns: list[str]
    rows: list[dict] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _load_private_key(path: str) -> bytes:
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def settings() -> dict:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def connection():
    """Open (or reuse) the connection. Logs go to stderr: stdout belongs to MCP."""
    global _conn
    if _conn is not None and not _conn.is_closed():
        return _conn
    import snowflake.connector

    s = settings()
    _conn = snowflake.connector.connect(
        account=s["account"],
        user=s["user"],
        authenticator="SNOWFLAKE_JWT",
        private_key=_load_private_key(s["private_key_file"]),
        role=s["role"],
        warehouse=s["warehouse"],
        database=s["database"],
        schema=s["schema"],
        session_parameters={
            "QUERY_TAG": QUERY_TAG,
            "STATEMENT_TIMEOUT_IN_SECONDS": STATEMENT_TIMEOUT_SECONDS,
        },
        client_session_keep_alive=True,
    )
    # Only the configured (read-only) role may act in this session, even if the user also holds
    # other roles and has DEFAULT_SECONDARY_ROLES = ALL.
    _conn.cursor().execute("USE SECONDARY ROLES NONE")
    print(f"[warehouse] connected as {s['user']} / role {s['role']} "
          f"({s['database']}.{s['schema']}, settings from {s['_source']})", file=sys.stderr)
    return _conn


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def render_for_display(sql: str, params: dict) -> str:
    """Fill in bound values so a person can read the query. Display only, never executed."""
    shown = sql
    for name, value in params.items():
        shown = shown.replace(f"%({name})s", _literal(value))
    return shown


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return value


def run(sql: str, params: dict | None = None) -> QueryResult:
    """Run one read query with bound parameters and return rows as dicts."""
    global last_query_id
    params = params or {}
    cur = connection().cursor()
    try:
        cur.execute(sql, params)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [{c: _jsonable(v) for c, v in zip(columns, r)} for r in cur.fetchall()]
        last_query_id = cur.sfqid
        return QueryResult(sql=sql, params=params, display_sql=render_for_display(sql, params),
                           query_id=cur.sfqid, columns=columns, rows=rows)
    finally:
        cur.close()

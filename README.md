# CDNE Assignment 5: MCP server over the Sakila star schema

A local MCP server that makes the Week 4 Sakila warehouse (`SAKILA.DW` in Snowflake) answerable in plain English through four bounded, read-only tools. Claude Desktop or Claude Code starts it on this laptop over stdio. It signs in to Snowflake as `CLAUDE_AGENT` with a key pair and the SELECT-only role `A5_APP_RO`. No password is stored anywhere.

## The four tools

| Tool | What it answers | Arguments (all validated) |
| --- | --- | --- |
| `describe_schema` | Which tables exist, what one row of each is, row counts, columns, the dates the data covers, and what is *not* in the data | optional `table` (must exist in `SAKILA.DW`) |
| `find_rentals` | Individual rentals in a date range | `start_date`, `end_date` (real `YYYY-MM-DD` dates), optional `category` (one of 16), `store_id` (1 or 2), `limit` (1 to 200, capped) |
| `aggregate` | Revenue, counts and averages over a period | `metric` (6 allowed), `group_by` (none, month, category, store, country, rating), dates, optional category and store |
| `query_cost` | Elapsed time, bytes scanned and credits for a query, from `QUERY_HISTORY` | `query_id` (Snowflake id format, or `last`) |

Every result carries the SQL that ran (values filled in for reading), the number of rows, what one row represents, and the query id. Where it applies, a result also says there was no data rather than zero, or that the dates fall outside the data's range.

Validation, in `warehouse/tools.py`:
- Anything that becomes SQL text (metric, GROUP BY, table) is looked up in an allow-list in `warehouse/catalog.py`.
- User values (dates, category, store) are bound as parameters, never pasted into SQL.
- Dates are parsed as real dates, and the start must come before the end.
- `LIMIT` is capped at 200.
- The session runs `USE SECONDARY ROLES NONE`, so only the read-only role can act, even if the user holds other roles.

## Why four bounded tools instead of one "run any SQL" tool

*(Part 1 paragraph, draft)* A single tool that runs whatever SQL the model writes turns every question into a security, cost and correctness gamble. It can express `DROP TABLE`, a scan of a billion rows, or `COUNT(*)` on a Type 2 customer dimension. None of those can be validated, because SQL is a language, not a value. Four bounded tools invert that. Each argument is typed and checked against an allow-list before any SQL exists, so injection and runaway scans can't be expressed at all. The grain is built into the tools, so "how many customers" runs `COUNT(DISTINCT customer_id)` instead of counting rental rows. Every answer comes from SQL a person can read and rerun, and its cost can be looked up. The price is coverage: a fair question that doesn't fit the four tools gets "I can't answer that" instead of an improvised query. For a warehouse that non-SQL users will trust, that honest refusal is the behavior we want. The read-only Snowflake role is the backstop: even a tool talked into misbehaving cannot write.

## Run it

See [SETUP_WINDOWS.md](SETUP_WINDOWS.md). In short:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q          # offline tests, no Snowflake needed
.venv\Scripts\python check_connection.py   # live: identity, tables, a real aggregate, its cost, read-only proof
```

Then register the server in Claude Desktop (`claude_desktop_config.example.json`) or Claude Code (`.mcp.json.example`).

## Files

```
mcp_server.py              FastMCP server: the four tools and their docstrings
warehouse/catalog.py       SAKILA.DW tables, grain sentences, allow-lists (metrics, groupings, categories, stores)
warehouse/tools.py         the tools, validation, honesty notes (no data / outside coverage / rows vs customers)
warehouse/db.py            Snowflake connection (key pair), bound queries, display SQL, query ids
warehouse/config.py        reads [connections.claude] from the Snowflake CLI config; env vars override
check_connection.py        one-shot live check
sql/register_key_and_role.sql   one-time Snowsight setup (key + read-only role)
tests/test_offline.py      validation and SQL-building tests with a fake warehouse
```

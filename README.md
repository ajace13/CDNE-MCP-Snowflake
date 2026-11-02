# CNDE Assignment 5: MCP server and Streamlit app over the Sakila star schema

Two ways to ask the Week 4 warehouse a question in plain English, built on the same four tools:

- **MCP server** (`mcp_server.py`, Part 1): Claude Desktop or Claude Code calls the tools.
- **Streamlit app** (`app.py`, Parts 2 and 3): a person types a question and gets an answer they can check, with the tool, SQL, rows, grain and cost beside it.

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

A single tool that runs whatever SQL the model writes turns every question into a security, cost and correctness gamble. It can express `DROP TABLE`, a scan of a billion rows, or `COUNT(*)` on a Type 2 customer dimension. None of those can be validated, because SQL is a language, not a value. Four bounded tools invert that. Each argument is typed and checked against an allow-list before any SQL exists, so injection and runaway scans can't be expressed at all. The grain is built into the tools, so "how many customers" runs `COUNT(DISTINCT customer_id)` instead of counting rental rows. Every answer comes from SQL a person can read and rerun, and its cost can be looked up. The price is coverage: a fair question that doesn't fit the four tools gets "I can't answer that" instead of an improvised query. For a warehouse that non-SQL users will trust, that honest refusal is the behavior we want. The read-only Snowflake role is the backstop: even a tool talked into misbehaving cannot write.

## The Streamlit app

```powershell
.venv\Scripts\python -m streamlit run app.py
```

There is no language model in the app (no API key needed). `qa/router.py` reads the question with keyword rules and picks one tool and its arguments, or decides the tools can't answer it, or refuses it. `qa/answer.py` turns the result into a sentence, a chart and a table, and applies the honesty checks:

- **Customers vs rows:** "how many customers" counts `DISTINCT customer_id`, and every count shows rows and different customers side by side (16,044 rentals vs 599 customers over the whole data).
- **No data is not zero:** an empty result says "No data for that", explains whether the dates are outside the data or fall in one of its gaps (no payments from September 2005 to January 2006), and never shows 0.
- **Can't answer:** profit, demographics, percentages, per-film rankings and other things the tools can't do get a plain "can't answer" with the reason, and nothing runs. Words the router didn't understand are named, not silently dropped.
- **False premises:** "why did X drop" questions are checked against the data before anything else.

Beside every answer, "How this was answered" shows the tool, its arguments, the SQL, rows back, what one row is, and the query's cost from `QUERY_HISTORY`. The sidebar filters (dates, store, category) keep their values across reruns.

`docs/requirements_map.md` maps every line of the assignment to the code and evidence. `docs/part4_findings.md` is the break-it log. `run_break_tests.py` runs the Part 4 questions live and writes `docs/part4_live_results.md`.

## Run it

See [SETUP_WINDOWS.md](SETUP_WINDOWS.md). In short:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q          # offline tests, no Snowflake needed
.venv\Scripts\python check_connection.py   # live: identity, tables, a real aggregate, its cost, read-only proof
.venv\Scripts\python -m streamlit run app.py
.venv\Scripts\python run_break_tests.py     # live Part 4 questions -> docs/part4_live_results.md
```

Then register the server in Claude Desktop (`claude_desktop_config.example.json`) or Claude Code (`.mcp.json.example`).

## Files

```
mcp_server.py              FastMCP server: the four tools and their docstrings
warehouse/catalog.py       SAKILA.DW tables, grain sentences, allow-lists (metrics, groupings, categories, stores)
warehouse/tools.py         the tools, validation, honesty notes (no data / outside coverage / rows vs customers)
warehouse/db.py            Snowflake connection (key pair), bound queries, display SQL, query ids
warehouse/config.py        reads [connections.claude] from the Snowflake CLI config; env vars override
app.py                     Streamlit app: question box, filters, chart, table, "How this was answered" panel
qa/router.py               question -> one tool call (keyword rules, no LLM); can't-answer and refuse rules
qa/answer.py               tool result -> headline, warnings, chart, table; the honesty checks
run_break_tests.py         Part 4 questions through the app's code, logged to docs/
check_connection.py        one-shot live check
sql/register_key_and_role.sql   one-time Snowsight setup (key + read-only role)
tests/test_offline.py      validation and SQL-building tests with a fake warehouse
tests/test_app.py          router, answers and the app itself (Streamlit AppTest), incl. the Part 4 questions
tests/fake_warehouse.py    offline stand-in for Snowflake, used by the tests
docs/                      requirement map, Part 4 findings and live results
```

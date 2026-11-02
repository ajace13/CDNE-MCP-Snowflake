# Assignment 5: where each requirement is met

Every line of the assignment, mapped to the code that meets it and the evidence that it works. "Test" names refer to `tests/test_offline.py` (tools) and `tests/test_app.py` (router, answers, app). Screenshot slots are filled from the live run on the Windows PC.

## Part 1: MCP server (30 pts)

| Requirement | Where | Evidence |
| --- | --- | --- |
| 1a Describe the schema: tables and what one row is | `describe_schema` in `mcp_server.py`, `warehouse/tools.py`; grain sentences in `warehouse/catalog.py` (`GRAIN`) | Live: 7 tables in SAKILA.DW with row counts and "one row is". Test `test_mcp_lists_tools` (exactly four) |
| 1b Parameterized query with typed args (dates, category, store), no SQL string | `find_rentals(start_date, end_date, category, store_id, limit)` | Live: a start_date of `2005-07-01' OR 1=1 --` was rejected as not a date. Values are bound parameters (tests `test_aggregate_builds_fixed_sql_and_binds_values`, `test_find_rentals_caps_limit_and_reports_total`) |
| 1c Aggregate: revenue, counts, averages over a period | `aggregate(metric, start_date, end_date, group_by, category, store_id)`, 6 metrics | Live: revenue by store 33,726.77 + 33,679.79 = 67,406.56 (matches Week 4) |
| 1d Query cost: elapsed time and credits from QUERY_HISTORY | `query_cost(query_id)` reads `INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER` | Live: elapsed, compile, execution, bytes scanned, cloud-services credits, warehouse-credit estimate (labelled as an estimate) |
| 2a Docstrings clear enough to pick the right tool | Each tool's docstring in `mcp_server.py` says when to use it and when not to (e.g. "how many customers" means `distinct_customers`, never `rental_count`) | Claude Desktop picks `aggregate` with `distinct_customers` for "how many customers..." |
| 2b Allow-list for anything that reaches GROUP BY | `check_allowed()` against `catalog.GROUP_BY`, `METRICS`, `CATEGORIES`, table names | Live: `group_by="store; DROP TABLE ..."` rejected. Test `test_allow_list_exact_match_only` |
| 2b Parsed dates | `parse_date()` / `parse_date_range()`: real dates, start before end | Tests `test_parse_date_*`, `test_date_range_order` |
| 2b Capped LIMIT | `cap_limit()`: default 50, max 200, and the result says it was capped | Live: limit 100000 capped to 200. Test `test_limit_default_and_cap` |
| 3 README paragraph: why four bounded tools | `README.md`, section "Why four bounded tools..." | |
| Backstop | Read-only role `A5_APP_RO` (SELECT + USAGE only); `USE SECONDARY ROLES NONE` | Live: `UPDATE ... WHERE 1 = 0` refused by Snowflake (`check_connection.py`) |

## Part 2: Streamlit app (30 pts)

| Requirement | Where | Evidence |
| --- | --- | --- |
| User types a question and gets an answer | `app.py`: question form, then `qa/router.py` picks a tool and `qa/answer.py` writes the answer. No LLM and no API key: keyword rules map the question to one of the four tools | Test `test_app_answers_and_shows_tool_sql_rows_cost`. Screenshot: `writeup/screenshots/a_customers_action_july2005.png` |
| Local is fine (SiS preferred) | Runs locally: `.venv\Scripts\python -m streamlit run app.py`. It uses the same `warehouse/` tools as the MCP server | |
| A chart that changes with the answer | `draw_chart()` in `app.py`. Monthly answers get a line chart; breakdowns by category, store, country or rating get a sorted bar chart; single numbers get a "rows vs. different customers" bar chart; a no-data answer gets a timeline of the dates asked for against the dates the data covers; a list gets rentals by category; a cost answer gets compile/queue/execute time | Screenshots: `a_customers_action_july2005.png` (rows vs customers), `e_revenue_by_month.png` (monthly line, gap called out) |
| A filter that keeps its value when the script reruns | Sidebar `Dates`, `Store` and `Film category` use `key=` (`f_dates`, `f_store`, `f_category`), so Streamlit keeps them in `session_state` across reruns. The answer shows which filters were in effect, and offers "Ask again with current filters" if they change | Test `test_app_filter_keeps_value_across_reruns` |
| Usable without a tutorial, no raw JSON | Example-question buttons, a "What I can answer" list in the sidebar, a one-sentence headline, `st.metric`, a formatted table (dollars, thousands separators). Tool results are never printed as dicts | Test asserts no `{` in the headline. Screenshots: all of `writeup/screenshots/` |

## Part 3: Show the user what happened (25 pts)

| Requirement | Where | Evidence |
| --- | --- | --- |
| Which tool ran and the SQL it executed | "How this was answered" panel beside every answer: tool name, the arguments it was called with, and the SQL (`st.code`), with bound values filled in for reading | Test `test_app_answers_and_shows_tool_sql_rows_cost` |
| How many rows came back and what one row represents | "Rows back" metric and "One row is: ..." from the tool's grain; for aggregates, "Computed from N rentals/payments in SAKILA.DW.FACT_..." | Same test |
| What the query cost | After every tool call the app calls `query_cost` with that query's id and shows elapsed seconds, bytes scanned and credits. "What did that query cost?" reports the previous question's query, not the cost lookup itself | Test `test_cost_answer` |
| Honest: question and query don't match (customers vs rows, more than 20x in Sakila) | Router maps people words to `distinct_customers` (COUNT DISTINCT customer_id, not customer_key, because DIM_CUSTOMER is Type 2). Every single-number answer shows both numbers, and the chart puts rows next to customers. If a customers question ever runs a row count, a warning says so with the customer count | Live: 16,044 rentals vs 599 customers (26.8x). Tests `test_break_2_*`, `test_rows_vs_customers_mismatch_is_flagged` |
| Honest: nothing came back means "no data", not zero | `_no_data()` in `qa/answer.py`: headline "No data for that...", "It does not mean the answer is zero", the coverage note when the dates are outside the data, and a gap note when they are inside it but empty (no payments from Sep 2005 to Jan 2006). Monthly charts say that a skipped month is a gap, not a drop | Tests `test_break_4_*`, `test_month_gap_is_called_a_gap_not_a_drop`, `test_empty_month_inside_range_explains_gaps` |
| Honest: the tools can't answer it | Router returns `cannot` with the reason (no profit data, no per-film ranking, no percentages, holiday names, return status...). The app shows no number, lists what it can answer, and sends nothing to Snowflake. Words the router didn't understand are named in a warning instead of being dropped silently | Tests `test_break_3_*`, `test_probe_gaps_now_handled`, `test_unknown_words_are_reported_not_silently_dropped` |

## Part 4 and submission

See `docs/part4_findings.md` (what broke and what changed) and `docs/part4_live_results.md` (written by `run_break_tests.py` on the live warehouse). The PDF combines the Part 1 paragraph from the README with the Part 4 write-up.

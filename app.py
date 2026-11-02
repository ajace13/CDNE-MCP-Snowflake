"""Ask the Sakila warehouse: a Streamlit app over the same four tools as the MCP server.

Run:  .venv\\Scripts\\python -m streamlit run app.py

A question goes through three steps, all visible on screen:
1. qa.router.route() reads it with keyword rules and picks one tool and its arguments
   (or decides the tools can't answer it, or refuses it).
2. The tool in warehouse/tools.py builds bound SQL from its allow-lists and runs it under
   the read-only role. Its cost is then looked up in QUERY_HISTORY.
3. qa.answer turns the result into a sentence, a chart and a table, and applies the honesty
   checks (customers vs rows, no data vs zero, can't answer).
"""
from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

from qa import answer as ans
from qa.router import DATA_END, DATA_START, WHAT_THE_TOOLS_CAN_ANSWER, Defaults, route
from warehouse import catalog

st.set_page_config(page_title="Ask the Sakila warehouse", page_icon="🎬", layout="wide")

EXAMPLES = [
    "How many customers rented Action films in July 2005?",
    "Revenue by category in 2005",
    "Revenue by month at store 2",
    "List rentals of Comedy films on 2005-08-01",
    "What tables are in the warehouse?",
    "What did that query cost?",
]

# ---------------- state that survives reruns ----------------
ss = st.session_state
ss.setdefault("history", [])          # list[Answer], newest last
ss.setdefault("question", "")


def use_example(q: str) -> None:
    ss.question = q
    ss.pending = q


def reset_filters() -> None:
    ss.f_dates = (DATA_START, DATA_END)
    ss.f_store = "Both stores"
    ss.f_category = "All categories"


# ---------------- sidebar: filters (keyed, so they keep their values) ----------------
with st.sidebar:
    st.header("Filters")
    st.caption("Used when your question doesn't say. Words in the question win.")
    st.date_input("Dates", value=(DATA_START, DATA_END), min_value=dt.date(2005, 1, 1),
                  max_value=dt.date(2006, 12, 31), key="f_dates",
                  help=f"Sakila's data runs from {DATA_START:%d %b %Y} to {DATA_END:%d %b %Y}.")
    st.selectbox("Store", ["Both stores", "Store 1", "Store 2"], key="f_store")
    st.selectbox("Film category", ["All categories", *catalog.CATEGORIES], key="f_category")
    st.button("Reset filters", on_click=reset_filters)

    st.divider()
    st.subheader("What I can answer")
    st.markdown("\n".join(f"- {line}" for line in WHAT_THE_TOOLS_CAN_ANSWER))
    st.caption("No profit, costs, reviews, inventory, actors or demographics: that data isn't in the warehouse.")


def current_defaults() -> Defaults:
    dates = ss.get("f_dates") or (DATA_START, DATA_END)
    if not isinstance(dates, (tuple, list)):
        dates = (dates, dates)
    start, end = (dates[0], dates[1]) if len(dates) == 2 else (dates[0], dates[0])
    store = {"Store 1": 1, "Store 2": 2}.get(ss.get("f_store"))
    category = ss.get("f_category")
    return Defaults(start=start, end=end, store_id=store,
                    category=None if category in (None, "All categories") else category)


def filters_label(d: Defaults) -> str:
    return (f"{d.start} to {d.end} · {('store ' + str(d.store_id)) if d.store_id else 'both stores'} · "
            f"{d.category or 'all categories'}")


# ---------------- main: question ----------------
st.title("🎬 Ask the Sakila warehouse")
st.write("Type a question about rentals, payments or customers of the Sakila DVD stores "
         "(May 2005 to February 2006). Every answer shows the tool that ran, its SQL, the rows that came "
         "back and what the query cost.")

st.caption("Try one:")
cols = st.columns(3)
for i, ex in enumerate(EXAMPLES):
    cols[i % 3].button(ex, key=f"ex_{i}", on_click=use_example, args=(ex,), width="stretch")

with st.form("ask", clear_on_submit=False):
    st.text_input("Your question", key="question",
                  placeholder="e.g. How many different customers rented Sports films in August 2005?")
    asked = st.form_submit_button("Ask", type="primary")

question = None
if asked and ss.question.strip():
    question = ss.question.strip()
elif ss.get("pending"):
    question = ss.pop("pending")


def ask(q: str) -> None:
    defaults = current_defaults()
    plan = route(q, defaults)
    # "What did that cost?" means the previous answer's query, not the cost lookup that followed it.
    if plan.kind == "tool" and plan.tool == "query_cost":
        prev = next((a for a in reversed(ss.history) if a.result and a.result.get("query_id")
                     and a.plan.tool != "query_cost"), None)
        if prev is None:
            a = ans.Answer(q, "no_data", "No earlier query to report on yet. Ask a question first, then ask "
                                         "what it cost.", plan, checks=[("ok", "Nothing was sent to Snowflake.")])
            a.filters = filters_label(defaults)
            ss.history.append(a)
            return
        plan.args = {"query_id": prev.result["query_id"]}
        plan.assumptions.append(f"Reporting the cost of the previous question: \"{prev.question}\".")
    with st.spinner("Asking Snowflake..."):
        a = ans.answer_question(q, plan)
    a.filters = filters_label(defaults)
    ss.history.append(a)


if question:
    ask(question)


# ---------------- rendering ----------------

def md(text: str) -> str:
    """Streamlit reads $...$ as LaTeX, which garbles dollar amounts. Escape it."""
    return str(text).replace("$", "\\$")


def draw_chart(c: dict) -> None:
    df = c["df"]
    if c["type"] == "line":
        chart = alt.Chart(df).mark_line(point=True).encode(
            x=alt.X(field=c["x"], type="ordinal", title=c["x"].capitalize(), axis=alt.Axis(labelAngle=-40)),
            y=alt.Y(field=c["y"], type="quantitative", title=c["y_title"]),
            tooltip=[alt.Tooltip(field=c["x"], type="ordinal"),
                     alt.Tooltip(field=c["y"], type="quantitative", format=",.2f")],
        )
    elif c["type"] == "timeline":  # no-data answers: the period asked for next to the period the data covers
        chart = alt.Chart(df).mark_bar(height=22).encode(
            y=alt.Y("what:N", title=None, sort=None),
            x=alt.X("start:T", title=None, axis=alt.Axis(format="%b %Y")),
            x2="end:T",
            color=alt.Color("what:N", legend=None,
                            scale=alt.Scale(domain=["Data's first to last date", "You asked for"], range=["#2E7D32", "#C62828"])),
            tooltip=[alt.Tooltip("what:N", title=""), alt.Tooltip("start:T", title="From"),
                     alt.Tooltip("end:T", title="To")],
        )
    else:
        chart = alt.Chart(df).mark_bar().encode(
            y=alt.Y(field=c["y"], type="nominal", sort="-x", title=c["y_title"] or None),
            x=alt.X(field=c["x"], type="quantitative", title=c["x"]),
            tooltip=[alt.Tooltip(field=c["y"], type="nominal"),
                     alt.Tooltip(field=c["x"], type="quantitative", format=",.2f")],
        )
    height = 320 if c["type"] == "line" else 90 + 36 * len(df)
    st.altair_chart(chart.properties(title=c["title"]), width="stretch", height=height)


ICON = {"ok": "✅", "warn": "⚠️", "info": "ℹ️"}


def how_panel(a: ans.Answer) -> None:
    """Part 3: which tool, its SQL, rows and grain, cost, and the checks that ran."""
    st.subheader("How this was answered")
    r, plan = a.result, a.plan
    if plan.kind != "tool" or r is None:
        why = {"refuse": "Refused before any tool ran.", "cannot": "No tool fits this question, so none ran."}
        st.markdown(f"**Tool:** none. {why.get(plan.kind, 'The tool did not return a result.')}")
        st.markdown("**SQL:** none. **Rows:** none. **Cost:** nothing ran, so nothing was spent.")
    else:
        tool = plan.tool
        if tool == "query_cost":
            rows, one_row = (1 if r.get("found") else 0), "one query in Snowflake's QUERY_HISTORY."
        else:
            rows, one_row = r.get("rows_returned", 0), r.get("one_row_is", "")
        cost = a.cost or {}
        st.markdown(f"**Tool:** `{tool}` ({ans.TOOL_NAMES[tool]})")
        m1, m2 = st.columns(2)
        m1.metric("Rows back", f"{rows:,}")
        m2.metric("Time in Snowflake", f"{cost['elapsed_seconds']:.2f} s" if cost.get("found") else "n/a")
        st.markdown("**Arguments:** " + ", ".join(f"`{k}` = `{v}`" for k, v in plan.args.items() if v is not None))
        st.markdown(f"**One row is:** {md(one_row)}")
        if tool == "aggregate":
            fact = catalog.METRICS[r["metric"]]["fact"]
            word, table = catalog.FACTS[fact]["row_word"], catalog.FACTS[fact]["table"]
            st.markdown(md(f"**Measured:** {r['metric_label']}, the {r['metric_means']}. Computed from "
                           f"{r.get(word + '_matched', 0):,} {word} in {table}."))
        st.markdown(md(f"**Cost:** {ans.cost_line(cost)}"))
        st.caption("SQL that ran. Values are shown filled in for reading; the tool sends them as bound parameters.")
        st.code(r.get("sql", ""), language="sql", wrap_lines=True)
        if r.get("query_id"):
            st.caption(f"Snowflake query id: {r['query_id']}")
    if a.checks:
        st.markdown("**Checks and assumptions**")
        for level, text in a.checks:
            st.markdown(f"{ICON.get(level, '•')} {md(text)}")


def show(a: ans.Answer) -> None:
    st.markdown(f"#### {md(a.question)}")
    st.caption(f"Sidebar filters when asked: {a.filters}")
    box = {"answer": st.success, "no_data": st.info, "cannot": st.warning, "refuse": st.error,
           "error": st.error}[a.kind]
    box(md(a.headline))
    for w in a.warnings:
        st.warning(md(w))
    left, right = st.columns([3, 2], gap="large")
    with left:
        if a.kind in ("cannot", "refuse"):
            st.markdown("**What these tools can answer:**\n" + "\n".join(f"- {x}" for x in WHAT_THE_TOOLS_CAN_ANSWER))
            st.caption("Or click one of the example questions above.")
        if a.metric_value:
            st.metric(a.result.get("metric_label", "Answer") if a.result else "Answer", a.metric_value)
        if a.chart is not None and len(a.chart["df"]):
            draw_chart(a.chart)
        if a.table is not None and len(a.table):
            st.dataframe(a.table, hide_index=True, width="stretch")
    with right:
        how_panel(a)


if ss.history:
    latest = ss.history[-1]
    if latest.filters != filters_label(current_defaults()) \
            and latest.plan.kind == "tool":
        st.info("The filters changed since this answer. Ask again to use the new filters.")
        st.button("Ask again with current filters", on_click=use_example, args=(latest.question,))
    show(latest)
    if len(ss.history) > 1:
        with st.expander(f"Earlier questions ({len(ss.history) - 1})"):
            for i, old in reversed(list(enumerate(ss.history[:-1]))):
                st.markdown(md(f"**{old.question}**  \n{old.headline}"))
                st.button("Ask again", key=f"again_{i}", on_click=use_example, args=(old.question,))
else:
    st.info("Ask a question above, or click one of the examples.")

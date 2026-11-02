"""Run a routed plan through the warehouse tools and turn the result into an honest answer.

The three honesty rules from the assignment live here, as code rather than prompts:
1. Question/query mismatch: asked about customers but counted rows (or the reverse) -> say so,
   with both numbers.
2. Nothing came back -> "no data for that", never "zero".
3. The tools can't answer -> say so plainly, no number shown.
A fourth check follows from the false-premise test: a "why did X drop" question first checks
whether the data covers the period and whether it shows a drop at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from warehouse import catalog, tools

from .router import DATA_END, DATA_START, WHAT_THE_TOOLS_CAN_ANSWER, Plan

TOOL_NAMES = {
    "describe_schema": "Describe the schema",
    "find_rentals": "Find rentals (parameterized query)",
    "aggregate": "Aggregate over a period",
    "query_cost": "Report query cost",
}
GROUP_WORD = {"month": "month", "category": "film category", "store": "store", "country": "customer country",
              "rating": "film rating"}


@dataclass
class Answer:
    question: str
    kind: str                                   # answer | no_data | cannot | refuse | error
    headline: str
    plan: Plan
    result: dict | None = None
    cost: dict | None = None
    warnings: list[str] = field(default_factory=list)   # shown prominently (mismatch, premise)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (ok|warn|info, text) for the panel
    table: pd.DataFrame | None = None
    chart: dict | None = None                   # {"type": line|bar|hbar, "df", "x", "y", "title", "y_title"}
    metric_value: str | None = None
    filters: str = ""                           # the sidebar filters when it was asked


# ---------------- formatting ----------------

def fmt(metric: str, v) -> str:
    if v is None:
        return "no data"
    if metric in ("revenue", "avg_payment"):
        return f"${v:,.2f}"
    if metric == "avg_days_out":
        return f"{v:,.1f} days"
    return f"{int(v):,}"


def scope_words(args: dict) -> str:
    parts = [f"{args['category']} films" if args.get("category") else "all films"]
    if args.get("store_id"):
        parts.append(f"store {args['store_id']}")
    if args.get("start_date"):
        parts.append(f"{args['start_date']} to {args['end_date']}")
    return ", ".join(parts)


def _bytes(n) -> str:
    n = n or 0
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def cost_line(cost: dict | None) -> str:
    if not cost:
        return "Cost not available."
    if not cost.get("found"):
        return cost.get("message", "Cost not found in query history yet.")
    return (f"{cost['elapsed_seconds']:.2f} s elapsed, {_bytes(cost['bytes_scanned'])} scanned, "
            f"~{cost['estimated_warehouse_credits']:.6f} warehouse credits (estimate) + "
            f"{(cost['cloud_services_credits'] or 0):.6f} cloud-services credits")


# ---------------- running ----------------

def run_plan(plan: Plan) -> tuple[dict, dict | None]:
    fn = getattr(tools, plan.tool)
    result = fn(**plan.args)
    cost = None
    if plan.tool != "query_cost" and result.get("query_id"):
        try:
            cost = tools.query_cost(result["query_id"])
        except Exception as exc:  # cost is extra information; never block the answer on it
            cost = {"found": False, "message": f"Cost lookup failed: {exc}"}
    return result, cost


def answer_question(question: str, plan: Plan) -> Answer:
    if plan.kind == "refuse":
        return Answer(question, "refuse", "I can't do that.", plan, warnings=[plan.reason],
                      checks=[("ok", "Nothing was sent to Snowflake.")])
    if plan.kind == "cannot":
        return Answer(question, "cannot", "These tools can't answer that question.", plan,
                      warnings=[plan.reason + " Rather than guess, no number is shown."],
                      checks=[("ok", "Nothing was sent to Snowflake.")])
    try:
        result, cost = run_plan(plan)
    except tools.ToolInputError as exc:
        return Answer(question, "cannot", "The tool rejected that request.", plan, warnings=[str(exc)],
                      checks=[("ok", "Arguments failed validation, so no SQL ran.")])
    except Exception as exc:  # connection problems etc.
        return Answer(question, "error", "Couldn't reach the warehouse.", plan, warnings=[str(exc)])
    return build(question, plan, result, cost)


# ---------------- building the answer ----------------

def build(question: str, plan: Plan, result: dict, cost: dict | None) -> Answer:
    a = Answer(question, "answer", "", plan, result=result, cost=cost)
    builder = {"aggregate": _aggregate, "find_rentals": _rentals, "describe_schema": _schema,
               "query_cost": _cost}[plan.tool]
    builder(a)
    if plan.ignored_words:
        words = ", ".join(f"'{w}'" for w in plan.ignored_words)
        a.warnings.insert(0, f"I didn't use {words} from your question. The tools have no filter for it, so the "
                             "answer below ignores it. Check that this is still what you meant.")
        a.checks.append(("warn", f"Words not used by the router: {words}."))
    for note in result.get("notes", []):
        if "not that the answer is zero" in note or "entirely outside" in note:
            continue  # already turned into the headline/warnings
        a.checks.append(("info", note))
    for assumption in plan.assumptions:
        a.checks.append(("info", "Assumption: " + assumption))
    return a


def _no_data(a: Answer, what: str) -> None:
    r = a.result
    a.kind = "no_data"
    a.headline = f"No data for that. No {what} matched these filters: {scope_words(r.get('filters', {}))}."
    a.warnings.append("This means the warehouse has nothing for that slice. It does not mean the answer is zero.")
    outside = [n for n in r.get("notes", []) if "entirely outside" in n]
    if not outside:
        a.warnings.append("These dates are inside the data's overall range, but the data has gaps (for example, no "
                          "payments from September 2005 to January 2006). Ask for 'rentals by month' to see which "
                          "months have data.")
    if outside:
        a.warnings.append(outside[0])
        if a.plan.asks_why:
            a.warnings.append("So there is nothing to explain: the premise doesn't hold for this data.")
    a.checks.append(("ok", "Zero matching rows reported as 'no data', not as 0."))
    f = r.get("filters", {})
    if f.get("start_date"):
        a.chart = {"type": "timeline", "title": "What you asked for vs. the dates the data covers", "y_title": "",
                   "x": "start", "y": "what",
                   "df": pd.DataFrame({"what": ["Data's first to last date", "You asked for"],
                                       "start": pd.to_datetime([str(DATA_START), f["start_date"]]),
                                       "end": pd.to_datetime([str(DATA_END), f["end_date"]])})}


def _aggregate(a: Answer) -> None:
    r, plan = a.result, a.plan
    metric, group_by = r["metric"], r["group_by"]
    row_word = catalog.FACTS[catalog.METRICS[metric]["fact"]]["row_word"]
    matched = r.get(f"{row_word}_matched", 0)
    if r["no_data"]:
        _no_data(a, row_word)
        return
    scope = scope_words(r["filters"])
    label = r["metric_label"]
    rows = r["rows"]
    df = pd.DataFrame(rows)

    if group_by == "none":
        row = rows[0]
        v, dc = row["value"], row["distinct_customers"]
        a.metric_value = fmt(metric, v)
        a.headline = {
            "distinct_customers": f"{int(v):,} different customers rented {scope}. Together they made {matched:,} rentals.",
            "rental_count": f"{int(v):,} rentals of {scope}, made by {dc:,} different customers.",
            "revenue": f"Revenue was {fmt(metric, v)} for {scope}, from {matched:,} payments.",
            "payment_count": f"{int(v):,} payments for {scope}, from {dc:,} different customers.",
            "avg_payment": f"The average payment was {fmt(metric, v)} for {scope}, across {matched:,} payments.",
            "avg_days_out": f"Copies were kept {fmt(metric, v)} on average for {scope} (returned rentals only).",
        }[metric]
        # One number doesn't make a chart, so show what it was computed from: rows vs. different customers.
        # This is also the comparison the customers-vs-rows honesty check is about.
        chart_df = pd.DataFrame({"Measure": [f"{row_word.title()} (rows)", "Different customers"],
                                 "Count": [matched, dc]})
        a.chart = {"type": "hbar", "df": chart_df, "x": "Count", "y": "Measure", "y_title": "",
                   "title": "What the answer was computed from"}
        a.table = pd.DataFrame([{label: fmt(metric, v), f"{row_word.title()} matched": f"{matched:,}",
                                 "Different customers": f"{dc:,}"}])
    else:
        top = max(rows, key=lambda x: x["value"] or 0)
        low = min(rows, key=lambda x: x["value"] or 0)
        if group_by == "month":
            a.headline = (f"{label} by month for {scope}: highest in {top['group_value']} ({fmt(metric, top['value'])}), "
                          f"lowest in {low['group_value']} ({fmt(metric, low['value'])}).")
            chart_type = "line"
        else:
            a.headline = (f"{label} by {GROUP_WORD[group_by]} for {scope}: {top['group_value']} is highest "
                          f"({fmt(metric, top['value'])}), {low['group_value']} lowest ({fmt(metric, low['value'])}), "
                          f"across {len(rows)} groups.")
            chart_type = "hbar"
        a.chart = {"type": chart_type, "df": df.rename(columns={"group_value": GROUP_WORD[group_by], "value": label}),
                   "x": GROUP_WORD[group_by] if chart_type == "line" else label,
                   "y": label if chart_type == "line" else GROUP_WORD[group_by],
                   "title": f"{label} by {GROUP_WORD[group_by]}",
                   "y_title": label if chart_type == "line" else ""}
        a.table = pd.DataFrame([{GROUP_WORD[group_by].capitalize(): x["group_value"], label: fmt(metric, x["value"]),
                                 row_word.title(): f"{x['fact_rows']:,}",
                                 "Different customers": f"{x['distinct_customers']:,}"} for x in rows])
        if group_by == "month":
            missing = _missing_months([x["group_value"] for x in rows])
            if missing:
                span = missing[0] if len(missing) == 1 else f"{missing[0]} to {missing[-1]}"
                a.warnings.append(f"There is no data at all for {span}. The chart jumps over "
                                  f"{'that month' if len(missing) == 1 else 'those months'}: that is a gap in the "
                                  "data, not a drop to zero.")
            partial = [m for m in ("2005-05", "2006-02") if m in {x["group_value"] for x in rows}]
            if partial:
                a.checks.append(("info", "May 2005 (data starts on the 24th) and February 2006 (one day of data) are "
                                         "partial months, so they look low: " + ", ".join(partial) + "."))
            if len(rows) >= 2:
                _premise(a, rows, metric)

    # ---- honesty rule 1: does the count match the question? ----
    dc_total = None if group_by != "none" else rows[0]["distinct_customers"]
    if plan.asks_for_people and metric in ("rental_count", "payment_count"):
        extra = f" The number of different customers is {dc_total:,}." if dc_total is not None else \
            " The table also shows different customers per group."
        a.warnings.insert(0, f"You asked how many customers, but this counted {row_word} (one row per {row_word[:-1]})." + extra)
        a.checks.append(("warn", "Question asks for people; the measure counts rows."))
    elif plan.asks_for_people and metric == "distinct_customers":
        a.checks.append(("ok", f"You asked about customers, so it counted DISTINCT customer_id ({matched:,} rental rows "
                               "would have been the wrong number). customer_id, not customer_key, because DIM_CUSTOMER "
                               "keeps one row per version of a customer."))
    elif metric in ("rental_count", "payment_count") and dc_total is not None:
        a.checks.append(("info", f"This counts {row_word}, not customers: {dc_total:,} different customers made them."))


def _missing_months(months: list[str]) -> list[str]:
    """Months between the first and last returned month that have no rows at all."""
    if len(months) < 2:
        return []
    y, m = map(int, months[0].split("-"))
    have, out = set(months), []
    while f"{y:04d}-{m:02d}" < months[-1]:
        if f"{y:04d}-{m:02d}" not in have:
            out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


DOWN = r"\b(drop\w*|declin\w*|fall|fell|decreas\w*|down|dip\w*|slump\w*|lower|collaps\w*|crash\w*|plummet\w*|plung\w*|tank\w*)\b"
UP = r"\b(ris\w*|rose|increas\w*|grow\w*|grew|jump\w*|up|spike\w*|higher)\b"


def _premise(a: Answer, rows: list[dict], metric: str) -> None:
    """For 'why did X drop/rise' questions: does the data even show that?"""
    q = a.question.lower()
    expects = "down" if re.search(DOWN, q) else "up" if re.search(UP, q) else None
    if not expects:
        return
    label = catalog.METRICS[metric]["label"].lower()
    if a.plan.focus:
        # Compare the first month asked about with the month before it.
        fstart = a.plan.focus[0][:7]
        idx = next((i for i, r in enumerate(rows) if r["group_value"] >= fstart), None)
        if idx is None or idx == 0:
            a.checks.append(("info", "Premise not checked: there is no earlier month in the data to compare with."))
            return
        if rows[idx]["group_value"] > fstart:
            a.warnings.append(f"There is no data at all for {fstart}, so the premise can't be checked. A month with "
                              "no rows is missing data, not a collapse to zero.")
            return
        first, last = rows[idx - 1], rows[idx]
        if last["group_value"] in ("2005-05", "2006-02") or first["group_value"] in ("2005-05", "2006-02"):
            a.checks.append(("warn", "One of the months compared is partial, so the comparison is weak."))
    else:
        full = [r for r in rows if r["group_value"] not in ("2005-05", "2006-02")] or rows
        first, last = full[0], full[-1]
    went = "down" if (last["value"] or 0) < (first["value"] or 0) else "up"
    if went == expects:
        a.checks.append(("ok", f"Premise checked: {catalog.METRICS[metric]['label']} did go {went}, from "
                               f"{fmt(metric, first['value'])} in {first['group_value']} to {fmt(metric, last['value'])} "
                               f"in {last['group_value']}."))
    else:
        a.warnings.insert(0, f"The data doesn't support the premise: {label} went {went}, "
                          f"not {expects}, from {fmt(metric, first['value'])} in {first['group_value']} to "
                          f"{fmt(metric, last['value'])} in {last['group_value']}.")


def _rentals(a: Answer) -> None:
    r = a.result
    if r["total_matching_rentals"] == 0:
        _no_data(a, "rentals")
        return
    a.headline = f"Showing {r['rows_returned']:,} of {r['total_matching_rentals']:,} rentals of {scope_words(r['filters'])}."
    df = pd.DataFrame(r["rows"])
    a.table = df.rename(columns={"rental_id": "Rental", "rented_at": "Rented", "title": "Film", "category": "Category",
                                 "store_id": "Store", "customer_id": "Customer id", "customer": "Customer",
                                 "returned_at": "Returned", "days_out": "Days out"})
    by_cat = df.groupby("category").size().reset_index(name="Rentals shown").rename(columns={"category": "Category"})
    a.chart = {"type": "hbar", "df": by_cat, "x": "Rentals shown", "y": "Category",
               "title": "Rentals shown, by category", "y_title": ""}
    if r["total_matching_rentals"] > r["rows_returned"]:
        a.checks.append(("info", f"The list is capped; {r['total_matching_rentals']:,} rentals matched in total. "
                                 "Ask a 'how many' question for counts."))


def _schema(a: Answer) -> None:
    r = a.result
    a.headline = f"The warehouse has {len(r['tables'])} tables in {r['database']}.{r['schema']}. {r['data_covers']}"
    df = pd.DataFrame(r["tables"])
    a.table = df.rename(columns={"table": "Table", "kind": "Kind", "one_row_is": "One row is", "row_count": "Rows",
                                 "columns": "Columns"})
    a.chart = {"type": "hbar", "df": df[["table", "row_count"]].rename(columns={"table": "Table", "row_count": "Rows"}),
               "x": "Rows", "y": "Table", "title": "Rows per table", "y_title": ""}
    a.checks.append(("info", "Not in the data: " + r["not_in_the_data"]))


def _cost(a: Answer) -> None:
    r = a.result
    a.cost = r
    if not r.get("found"):
        a.kind = "no_data"
        a.headline = "No earlier query to report on yet. Ask a question first, then ask what it cost."
        return
    a.headline = "The last query: " + cost_line(r) + "."
    a.metric_value = f"{r['elapsed_seconds']:.2f} s"
    a.chart = {"type": "hbar", "df": pd.DataFrame({"Phase": ["Compile", "Queued", "Execute"],
                                                   "Seconds": [r["compile_seconds"], r["queued_seconds"], r["execution_seconds"]]}),
               "x": "Seconds", "y": "Phase", "title": "Where the time went", "y_title": ""}
    a.checks.append(("info", r["credits_note"]))


def can_answer_list() -> list[str]:
    return WHAT_THE_TOOLS_CAN_ANSWER

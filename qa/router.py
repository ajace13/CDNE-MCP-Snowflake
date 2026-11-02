"""Turn a typed question into one tool call, without a language model.

The router reads the question with plain keyword rules and picks one of the four
warehouse tools plus its arguments. Anything it can't map to those tools becomes a
"can't answer" plan, and anything that tries to change data or reach outside the tools
becomes a "refuse" plan. It never writes SQL: the tools do that from their allow-lists.

Every guess the router makes (a default measure, a year it filled in, a sidebar filter
it used) is written into Plan.assumptions so the app can show it.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field

from warehouse import catalog

# Sakila's data runs 2005-05-24 .. 2006-02-14 (checked live by describe_schema).
DATA_START = dt.date(2005, 5, 24)
DATA_END = dt.date(2006, 2, 14)

WHAT_THE_TOOLS_CAN_ANSWER = [
    "Revenue, number of payments or average payment",
    "Number of rentals, or number of different customers who rented",
    "Average days a copy was kept",
    "Broken down by month, film category, store, customer country or film rating",
    "Filtered by dates (May 2005 to Feb 2006), one film category, or store 1 or 2",
    "A list of individual rentals",
    "What tables exist, and what a query cost",
]


@dataclass
class Plan:
    kind: str                          # "tool" | "cannot" | "refuse"
    tool: str | None = None            # describe_schema | find_rentals | aggregate | query_cost
    args: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    reason: str = ""                   # for cannot / refuse
    asks_for_people: bool = False      # the question asks how many customers/people
    asks_why: bool = False             # the question asks for an explanation
    ignored_words: list[str] = field(default_factory=list)  # words the router didn't understand
    focus: tuple[str, str] | None = None  # for "why" questions: the period asked about, inside a wider query


@dataclass
class Defaults:
    """Sidebar filters, used when the question doesn't say."""
    start: dt.date = DATA_START
    end: dt.date = DATA_END
    store_id: int | None = None
    category: str | None = None


# ---------------- patterns ----------------

def _any(patterns: list[str], text: str) -> re.Match | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m
    return None


MANIPULATION = [
    r"\bdrop\s+(table|schema|database|view)\b", r"\btruncate\b", r"\bdelete\s+from\b", r"\binsert\s+into\b",
    r"\bupdate\s+\w+(\.\w+)*\s+set\b", r"\balter\s+(table|user|role|warehouse)\b", r"\b(grant|revoke)\s+\w+",
    r"\bcreate\s+(or\s+replace\s+)?(table|user|role|view|stage)\b",
    r";", r"--", r"/\*", r"\bselect\b.+\bfrom\b", r"\bunion\b\s+select",
    r"\bignore\s+(all\s+|your\s+|the\s+|previous\s+|prior\s+)*(rules|instructions|limits|restrictions)\b",
    r"\b(system|developer)\s+prompt\b", r"\bjailbreak\b", r"\bact as\b",
    r"\b(password|passwords|private key|api key|credentials?|secret)\b",
    r"\brun (this|the following|my|any|raw)\s+(sql|query)\b", r"\braw sql\b",
    r"\b(as|use|switch to)\s+(role\s+)?(accountadmin|sysadmin|securityadmin)\b",
    r"\bpretend\b", r"\byou are now\b", r"\b(as|you're|you are) (an? |the )?admin(istrator)?\b",
    r"\baccount_usage\b", r"\binformation_schema\b", r"\bsnowflake\.\w+", r"\b(bypass|override|disable)\b",
    r"\b(other|another|different) (database|schema|account)s?\b",
]

NOT_IN_DATA = {
    r"\bprofits?\b|\bmargins?\b|\bnet income\b": "profit or margin (Sakila records what customers paid, not what anything cost)",
    r"\bexpenses?\b|\bsalar(y|ies)\b|\bwages?\b|\bpayroll\b": "costs, expenses or salaries",
    r"\bcost of\b|\bcosts\b|\bcogs\b": "business costs",
    r"\breviews?\b|\bstars?\b|\bsatisfaction\b|\bnps\b|\bfeedback\b": "customer reviews or satisfaction",
    r"\bhow old\b|\bage\b|\bages\b|\bbirthdays?\b|\bgender\b|\bincome\b|\bdemographics?\b|\bethnicity\b": "customer demographics",
    r"\bforecast\b|\bpredict\w*\b|\bnext (year|month|quarter|week)\b|\bwill\b.*\b(be|grow|drop)\b": "forecasts (the tools report what happened, not what will)",
    r"\bactors?\b|\bactress\w*\b|\bcast\b": "actors (the star schema has no actor table)",
    r"\binventory\b|\bstock\b|\bcopies on hand\b|\bin stock\b": "inventory levels",
    r"\bemails?\b|\bphone\b|\baddress(es)?\b": "customer contact details (not exposed by these tools)",
    r"\blate fees?\b|\bfines?\b": "late fees (payments aren't split into rental and late fee)",
    r"\bweb(site)?\b|\bclicks?\b|\btraffic\b|\bvisits?\b": "web traffic",
    r"\bweather\b": "weather",
    r"\b(christmas|thanksgiving|halloween|easter|valentine'?s|new year'?s|holiday\w*)\b":
        "!The tools don't know holiday names. Ask with the date instead, e.g. 'revenue on 2005-07-06'.",
    r"\bpercent(age)?s?\b|%|\bshare of\b|\bratios?\b|\b(churn|retention|conversion|growth) rates?\b|\bchurn\b|\bretention\b":
        "!These tools return counts, totals and averages, not rates or percentages. Ask for the two numbers "
        "you would divide (e.g. 'how many customers rented Action films' and 'how many customers rented').",
    r"\bmore than (once|\d+ times?)\b|\brepeat (customers|renters)\b|\breturning customers\b|\bloyal\w*\b|"
    r"\bonly once\b|\bfirst[- ]time\b|\bat least \d+ (rentals|times)\b":
        "!The tools count customers, but can't filter on how often each customer rented (repeat or first-time renters).",
    r"\bnever (been )?returned\b|\bnot (been )?returned\b|\bunreturned\b|\boverdue\b|\bstill out\b|\boutstanding\b":
        "!No tool filters on whether a rental came back. (Average days out uses returned rentals only.)",
    r"\b(which|what) (single |one )?(film|movie|title|customer|actor|staff member|employee)s?\b(?!\s+(categor|genre|rating|were rented|rented))":
        "that breakdown. The tools group only by month, category, store, country or rating; there is no per-customer, per-film or per-staff ranking",
    r"\bstreaming\b|\bnetflix\b|\bcompetitors?\b": "competitors or streaming",
    r"\bby (staff|employee|clerk|actor|customer|film|title|day of week|weekday|hour|city)\b|\bper (staff|employee|customer|film|title)\b|\btop \d*\s*(customers|films|titles|movies)\b|\b(best|top)[- ]selling\b|\bmost (popular|rented) (films?|titles?|movies?)\b":
        "that breakdown. The tools group only by month, category, store, country or rating; there is no per-customer, per-film or per-staff ranking",
}

UNKNOWN_GENRES = ["romance", "romantic", "thriller", "thrillers", "western", "westerns", "fantasy", "mystery",
                  "crime", "adventure", "superhero", "anime", "romcom", "war", "musical", "musicals", "biography"]

CATEGORY_ALIASES = {c.lower(): c for c in catalog.CATEGORIES if c != "New"}
CATEGORY_ALIASES.update({"scifi": "Sci-Fi", "sci fi": "Sci-Fi", "science fiction": "Sci-Fi", "kids": "Children",
                         "children's": "Children", "childrens": "Children", "documentaries": "Documentary",
                         "comedies": "Comedy", "dramas": "Drama", "classic": "Classics", "horror": "Horror",
                         "animated": "Animation", "sport": "Sports", "game": "Games", "foreign-language": "Foreign"})

PEOPLE = r"(customers?|people|persons|renters|members|users|clients|households|individuals|borrowers)"
METRIC_RULES = [  # first match wins
    ("avg_days_out", [r"\bdays?\s+(out|kept)\b", r"\bhow long\b.*\b(kept|keep|out|borrow\w*|rent(al|ed)? (last|lasts?))\b",
                      r"\b(rental|loan) (length|duration)\b", r"\breturn (time|speed)\b"]),
    ("avg_payment", [r"\b(average|avg|mean|typical)\s+(payment|amount|spend|transaction|ticket|charge)\b",
                     r"\baverage\b.*\bpa(id|y)\b"]),
    ("distinct_customers", [rf"\b(how many|number of|count of|# of|total)\s+(\w+\s+){{0,3}}{PEOPLE}\b",
                            rf"\b(unique|distinct|different|individual|active)\s+{PEOPLE}\b",
                            rf"\b{PEOPLE}\s+(count|counted)\b",
                            rf"\b(most|more|fewest|fewer|many)\s+{PEOPLE}\b"]),
    ("payment_count", [r"\b(how many|number of|count of)\s+payments\b", r"\bpayment count\b",
                       r"\bpayments\s+(were\s+)?(made|taken)\b"]),
    ("rental_count", [r"\b(how many|number of|count of|total)\s+(\w+\s+){0,2}(rentals|times|transactions|movies|films|titles)\b",
                      r"\brental (count|volume|activity)\b", r"\b(how many|number of)\b.*\brent(ed|als?)\b",
                      r"\brentals\b", r"\bbusiest\b", r"\bvolume\b"]),
    ("revenue", [r"\brevenue\b", r"\bsales\b", r"\bmoney\b", r"\bearn\w*\b", r"\bmade\b", r"\bincome\b",
                 r"\btakings\b", r"\$", r"\bdollars?\b", r"\bpaid\b", r"\bspen[dt]\b", r"\bturnover\b",
                 r"\bbring in\b|\bbrought in\b", r"\bgross\b"]),
]

GROUP_RULES = [
    ("month", [r"\bby month\b", r"\bper month\b", r"\bmonthly\b", r"\bmonth[- ]by[- ]month\b", r"\bover time\b",
               r"\btrend\w*\b", r"\beach month\b", r"\bwhich month\b", r"\bbusiest month\b", r"\bdrop\w*\b",
               r"\bdecline\w*\b", r"\bfall\b|\bfell\b", r"\b(collaps|crash|plummet|plung|tank|slump)\w*\b", r"\bgrow\w*\b", r"\bgrew\b", r"\bchange\w*\b"]),
    ("category", [r"\bby (film )?(category|categories|genre|genres)\b", r"\bper (category|genre)\b",
                  r"\b(each|every|which|what) (film )?(category|genre)\b", r"\bcategories\b", r"\bgenres\b"]),
    ("store", [r"\bby store\b", r"\bper store\b", r"\b(each|which|both|two) stores?\b", r"\bstores\b",
               r"\bcompare\b.*\bstore", r"\bstore\b.*\bcompare"]),
    ("country", [r"\bby country\b", r"\bper country\b", r"\b(which|what|each) countr(y|ies)\b", r"\bcountries\b"]),
    ("rating", [r"\bby (film )?rating\b", r"\bper rating\b", r"\b(which|each) rating\b", r"\bratings\b",
                r"\bpg-?13\b|\bnc-?17\b|\brated\b"]),
]

SCHEMA_RULES = [r"\b(what|which) tables\b", r"\bschema\b", r"\bdata dictionary\b", r"\bwhat data\b",
                r"\bwhat can (you|i) (answer|ask)\b", r"\bwhat does (a|one|each) row\b", r"\bdescribe\b.*\b(data|warehouse|tables?)\b",
                r"\bwhat('s| is) in (the|this) (warehouse|database|data)\b", r"\bhelp\b$",
                r"\b(fact|dim)_\w+\b", r"\bhow many rows\b", r"\brows (are )?in\b", r"\btables?\b"]
COST_RULES = [r"\bquery cost\b", r"\bcredits?\b", r"\bhow long did (that|the|this|my|it)\b",
              r"\bwhat did (that|the|this|it)\s*(query)?\s*cost\b", r"\bhow (expensive|much did) (that|the|this) query\b",
              r"\bbytes scanned\b", r"\bcost of (that|the|this|last|my) query\b", r"\bwhat did that cost\b"]
LIST_RULES = [r"\blist\b", r"\bshow me (the |some |all )?(individual |actual |specific )?rentals\b",
              r"\bwhich (films|movies|titles) were rented\b", r"\bwho rented\b", r"\bexamples? of\b",
              r"\b(individual|specific|each) rentals?\b", r"\brental (details|records|log)\b", r"\blatest rentals\b"]
VAGUE_RULES = [r"\bhow (are|is|'re) (we|it|things|business)\b", r"\bhow'?s (business|it going|everything)\b",
               r"\bhow (are|did) we do(ing)?\b", r"\boverview\b", r"\bsummary\b", r"\bdashboard\b",
               r"\bperformance\b", r"\bdoing\b", r"\bkpis?\b", r"\banything interesting\b", r"\bstatus\b"]
WHY_RULES = [r"^\s*why\b", r"\bwhy (did|do|does|is|was|are|were)\b", r"\bwhat caused\b", r"\bexplain (the|why)\b",
             r"\breason for\b"]
RELATIVE_DATES = [r"\blast (month|year|quarter|week)\b", r"\bthis (month|year|quarter|week)\b", r"\brecent(ly)?\b",
                  r"\blately\b", r"\btoday\b", r"\byesterday\b", r"\bytd\b", r"\byear to date\b"]

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9
MONTH_RE = r"(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"


# ---------------- date parsing ----------------

def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def _infer_year(month: int) -> int:
    """A month with no year: pick the year the data covers that month."""
    return 2006 if month <= 2 else 2005


def parse_dates(q: str) -> tuple[dt.date, dt.date, list[str]] | None:
    """Return (start, end, assumptions) if the question names a period, else None."""
    notes: list[str] = []
    iso = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", q)
    if iso:
        try:
            days = sorted(dt.date.fromisoformat(d) for d in iso)
        except ValueError:
            return None
        return days[0], days[-1], notes

    m = re.search(rf"\b(?:from|between)\s+{MONTH_RE}\s*(\d{{4}})?\s+(?:to|and|through|until|-)\s+{MONTH_RE}\s*(\d{{4}})?", q)
    if m:
        m1, y1, m2, y2 = MONTHS[m.group(1)], m.group(2), MONTHS[m.group(3)], m.group(4)
        y2 = int(y2) if y2 else None
        y1 = int(y1) if y1 else (y2 if y2 and m1 <= m2 else (y2 - 1 if y2 else _infer_year(m1)))
        y2 = y2 or (y1 if m2 >= m1 else y1 + 1)
        if not (m.group(2) and m.group(4)):
            notes.append(f"Read the period as {calendar.month_name[m1]} {y1} to {calendar.month_name[m2]} {y2}.")
        return _month_range(y1, m1)[0], _month_range(y2, m2)[1], notes

    # a season needs a year after it or "in/during/last/this/over" before it, so "rentals fall" isn't autumn
    season = re.search(r"\b(spring|summer|fall|autumn|winter)\s+(?:of\s+)?(\d{4})\b", q) or \
        re.search(r"\b(?:in|during|last|this|over)\s+(?:the\s+)?(spring|summer|fall|autumn|winter)\b()", q)
    if season:
        name, yr = season.group(1), season.group(2)
        first = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}[name]
        year = int(yr) if yr else 2005
        if not yr:
            notes.append(f"Read '{name}' as {name} {year}, the one the data covers.")
        end_year, end_month = (year + 1, 2) if first == 12 else (year, first + 2)
        return dt.date(year, first, 1), _month_range(end_year, end_month)[1], notes

    q_match = re.search(r"\bq([1-4])\s*(?:of\s+)?(\d{4})\b|\b(\d{4})\s*q([1-4])\b", q)
    if q_match:
        quarter = int(q_match.group(1) or q_match.group(4))
        year = int(q_match.group(2) or q_match.group(3))
        first = 3 * (quarter - 1) + 1
        return dt.date(year, first, 1), _month_range(year, first + 2)[1], notes

    months = re.findall(rf"\b{MONTH_RE}(?:\s+(?:of\s+)?(\d{{4}}))?\b", q)
    # ignore "may" used as a verb ("may I", "may have") unless a year follows
    months = [(mo, yr) for mo, yr in months if not (mo == "may" and not yr and re.search(r"\bmay (i|we|you|have|be)\b", q))]
    if months:
        ranges = []
        for mo, yr in months:
            month = MONTHS[mo]
            year = int(yr) if yr else None
            if year is None:
                standalone_year = re.search(r"\b(19|20)\d{2}\b", q)
                year = int(standalone_year.group(0)) if standalone_year else _infer_year(month)
                if not standalone_year:
                    notes.append(f"No year given for {calendar.month_name[month]}; used {year}, the year the data covers it.")
            ranges.append(_month_range(year, month))
        return min(r[0] for r in ranges), max(r[1] for r in ranges), notes

    years = sorted({int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", q)})
    if years:
        return dt.date(years[0], 1, 1), dt.date(years[-1], 12, 31), notes
    return None


# ---------------- words the router understands ----------------

STOPWORDS = set("""
a about above after again all also am an and any are as at be because been before being between both but by can
could did do does doing done down during each either else ever every few for from get gets give got had has have
having he her here hers him his how i if in into is it its it's itself just let lets let's like me mine more most
much my myself no nor not now of off on once one only or other our ours out over own per please same she should
show shown so some such tell than that that's the their theirs them then there these they this those through to
too under until up us very want was we were what what's when where which while who whom whose why will with would
you your yours whats hows thats didn't doesn't wasn't weren't kind sort see look find know
number count total overall amount many much compare compared comparison versus vs difference against
best worst high higher highest low lower lowest big biggest small smallest top bottom least less greater
film films movie movies title titles store stores customer customers rental rentals rented rent renting renter
payment payments paid pay paying revenue month months monthly year years yearly day days daily week weeks
quarter quarters date dates period time times category categories genre genres rating ratings rated country
countries average averages avg mean typical data warehouse table tables query queries cost costs dvd dvds sakila
business doing going happen happened happening things breakdown split broken whole entire
ok okay hey hi thanks thank list lists listed showing giving getting individual better worse make makes making
""".split())


def _vocabulary() -> set[str]:
    words = set(STOPWORDS)
    sources = [MANIPULATION, SCHEMA_RULES, COST_RULES, LIST_RULES, VAGUE_RULES, WHY_RULES, RELATIVE_DATES,
               list(NOT_IN_DATA), UNKNOWN_GENRES, [PEOPLE]]
    sources += [[p for _, pats in METRIC_RULES for p in pats], [p for _, pats in GROUP_RULES for p in pats]]
    for group in sources:
        for pattern in group:
            words.update(re.findall(r"[a-z]{3,}", re.sub(r"\\[a-zA-Z]", " ", pattern)))
    words.update(a for alias in CATEGORY_ALIASES for a in alias.split())
    words.update(MONTHS)
    words.update({"new", "releases", "release", "spring", "summer", "fall", "autumn", "winter", "first", "second",
                  "two", "limit", "rows", "records", "results", "different", "unique", "distinct", "trend", "change",
                  "collapse", "collapsed", "crash", "crashed", "plummet", "plummeted", "plunge", "plunged", "tank",
                  "tanked", "slump", "slumped", "spike", "spiked", "rise", "rose", "jump", "jumped"})
    return words


VOCAB: set[str] | None = None


def _flag_unknown_words(plan: Plan, q: str) -> None:
    """Note words that nothing in the router used, so the answer can say it ignored them."""
    global VOCAB
    if VOCAB is None:
        VOCAB = _vocabulary()
    unknown = []
    for t in re.findall(r"[a-z][a-z'_-]*", q):
        base = t.strip("'").removesuffix("'s")
        if len(base) < 3 or base in VOCAB or base.rstrip("s") in VOCAB or base + "s" in VOCAB:
            continue
        if base.endswith(("ed", "ing")) and (base.removesuffix("ed") in VOCAB or base.removesuffix("ing") in VOCAB):
            continue
        if base not in unknown:
            unknown.append(base)
    plan.ignored_words = unknown


# ---------------- the router ----------------

def route(question: str, defaults: Defaults | None = None) -> Plan:
    defaults = defaults or Defaults()
    q = " ".join(question.lower().split())
    if not q:
        return Plan("cannot", reason="Type a question first.")

    if _any(MANIPULATION, q):
        return Plan("refuse", reason=(
            "That asks for something these tools are built not to do: run SQL you write, change data, "
            "skip their limits, or reveal credentials. Nothing was run. The app can only call four "
            "read-only tools with checked arguments."))

    plan = Plan("tool", asks_for_people=bool(re.search(rf"\b(how many|number of|count of|unique|distinct|different)\b.*\b{PEOPLE}\b|\bwho\b", q)),
                asks_why=bool(_any(WHY_RULES, q)))

    if _any(COST_RULES, q):
        plan.tool, plan.args = "query_cost", {"query_id": "last"}
        return plan
    if _any(SCHEMA_RULES, q):
        plan.tool, plan.args = "describe_schema", {}
        return plan

    for pattern, what in NOT_IN_DATA.items():
        if re.search(pattern, q):
            reason = what[1:] if what.startswith("!") else f"These tools can't answer that: there is no data on {what}."
            return Plan("cannot", reason=reason, asks_why=plan.asks_why)
    genre = next((g for g in UNKNOWN_GENRES if re.search(rf"\b{g}\b", q)), None)
    if genre:
        return Plan("cannot", reason=f"Sakila has no '{genre}' category. Its 16 categories are: "
                                     + ", ".join(catalog.CATEGORIES) + ".")
    bad_store = re.search(r"\bstore\s*(?:#|number|no\.?)?\s*(\d+)\b", q)
    if bad_store and int(bad_store.group(1)) not in catalog.STORE_IDS:
        return Plan("cannot", reason=f"There is no store {bad_store.group(1)}. Sakila has two stores: 1 and 2.")

    # ---- filters: question first, sidebar second ----
    named = []
    rest = q
    for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
        pattern = rf"(?<![\w-]){re.escape(alias)}(?![\w-])"
        if re.search(pattern, rest):
            if CATEGORY_ALIASES[alias] not in named:
                named.append(CATEGORY_ALIASES[alias])
            rest = re.sub(pattern, " ", rest)
    if re.search(r"\bnew (releases?|category|films|movies)\b|['\"]new['\"]", q):
        named.append("New")
    category = named[0] if len(named) == 1 else None
    stores = []
    for sm in re.finditer(r"\bstore\s*(?:#|number|no\.?)?\s*(1|2|one|two)\b|\b(first|second) store\b", q):
        token = sm.group(1) or sm.group(2)
        n = {"1": 1, "one": 1, "first": 1, "2": 2, "two": 2, "second": 2}[token]
        if n not in stores:
            stores.append(n)
    store = stores[0] if len(stores) == 1 else None
    compare_groups = None
    if len(named) > 1:
        compare_groups = "category"
        plan.assumptions.append(f"Only one category can be filtered at a time, so this shows every category. "
                                f"Compare {' and '.join(named)} in the table.")
    elif len(stores) > 1:
        compare_groups = "store"
        plan.assumptions.append("Both stores were named, so this shows the two side by side.")

    if category is None and defaults.category:
        category = defaults.category
        plan.assumptions.append(f"Used the sidebar's category filter: {category}.")
    if store is None and defaults.store_id:
        store = defaults.store_id
        plan.assumptions.append(f"Used the sidebar's store filter: store {store}.")

    parsed = parse_dates(q)
    if parsed:
        start, end, notes = parsed
        plan.assumptions += notes
    else:
        start, end = defaults.start, defaults.end
        if _any(RELATIVE_DATES, q):
            plan.assumptions.append(
                f"Sakila's data is from {DATA_START:%b %Y} to {DATA_END:%b %Y}, so 'recent' or 'last month' has no "
                f"meaning here. Used the sidebar dates instead: {start} to {end}.")
        elif (start, end) != (DATA_START, DATA_END):
            plan.assumptions.append(f"No dates in the question; used the sidebar dates {start} to {end}.")

    # ---- list of rentals ----
    if _any(LIST_RULES, q) and not re.search(r"\bhow many\b", q):
        plan.tool = "find_rentals"
        lim = re.search(r"\b(?:limit|first|top|last)\s*(?:to|of|=)?\s*(\d+)\b|\b(\d+)\s+(?:rows|rentals|records|results)\b", q)
        plan.args = {"start_date": str(start), "end_date": str(end), "category": category,
                     "store_id": store, "limit": int(lim.group(1) or lim.group(2)) if lim else 50}
        _flag_unknown_words(plan, q)
        return plan

    # ---- aggregate ----
    metric = next((name for name, pats in METRIC_RULES if _any(pats, q)), None)
    group_by = next((name for name, pats in GROUP_RULES if _any(pats, q)), "none")
    if group_by == "category" and category and not re.search(r"\bby (category|genre)\b", q):
        group_by = "none"   # "Action films" is a filter, not a breakdown
    if group_by == "store" and store:
        group_by = "none"
    if compare_groups:
        group_by = compare_groups
    years = {int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", q)}
    if group_by == "none" and len(years) > 1:
        group_by = "month"
        plan.assumptions.append(f"More than one year was named ({', '.join(map(str, sorted(years)))}), so this "
                                "shows each month to compare them.")

    vague = metric is None and (_any(VAGUE_RULES, q) or (group_by == "none" and not category and not store and not parsed))
    _flag_unknown_words(plan, q)
    if metric is None and plan.ignored_words and not _any(VAGUE_RULES, q):
        return Plan("cannot", reason="I couldn't match " + ", ".join(f"'{w}'" for w in plan.ignored_words)
                    + " to anything these tools measure, so rather than guess, nothing was run.")
    if vague:
        metric, group_by = "revenue", compare_groups or "month"
        shown = "revenue" if compare_groups else "revenue by month"
        plan.assumptions.insert(0, f"The question doesn't say what to measure, so this shows {shown} as a starting "
                                   "point. Try asking for something specific, e.g. 'revenue by category in July 2005' "
                                   "or 'how many customers rented Comedy films in 2005'.")
    elif metric is None:
        metric = "revenue"
        plan.assumptions.append("No measure named; showing revenue.")
    if plan.asks_why and group_by == "none":
        group_by = "month"
    if plan.asks_why:
        plan.assumptions.append("These tools can show what happened, not why. The answer first checks whether the data even shows it.")
        overlaps = not (end < DATA_START or start > DATA_END)
        if group_by == "month" and parsed and overlaps and (start, end) != (DATA_START, DATA_END):
            # Widen to every month, so the period asked about can be compared with the month before it.
            if re.search(r"\b(after|since)\b", q):   # "why did revenue collapse after August?"
                start, end = end + dt.timedelta(days=1), DATA_END
            plan.focus = (str(start), str(end))
            start, end = DATA_START, DATA_END
            plan.assumptions.append(f"To check the premise, this shows every month, so {plan.focus[0]} to "
                                    f"{plan.focus[1]} can be compared with the month before.")

    plan.tool = "aggregate"
    plan.args = {"metric": metric, "start_date": str(start), "end_date": str(end), "group_by": group_by,
                 "category": category, "store_id": store}
    return plan

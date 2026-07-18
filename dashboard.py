import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

from flask import Flask, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from alerting import ALERT_WORTHY_SOURCES
from correlate import (
    find_correlations, correlation_summary,
    find_teco_correlations, teco_correlation_summary,
    find_duke_correlations, duke_correlation_summary,
    find_jea_correlations, find_tallahassee_correlations,
    find_talquin_correlations, find_fpuc_incident_correlations, _alert_identity,
    find_preco_correlations, find_fkec_correlations, find_tcec_correlations,
    find_erec_correlations, find_chelco_correlations, find_gcec_correlations, _county_in_alert,
    find_lwbu_correlations,
    find_ouc_correlations,
    find_lcec_correlations,
)
from county_status import (
    COUNTY_PICKER_CHOICES, _duration_since, _percentage_tier,
    _normalize_open_events, _real_per_county_open_events,
    _combined_territory_open_events, _rows_for_county,
    humanize_timestamp as _humanize_timestamp,
    explain_missing_historical_data, fpl_ordinary_restoration_stats,
    teco_etr_accuracy, TECO_UTILITY_NAME,
    duke_restoration_precedent, DUKE_UTILITY_NAME,
)
from storm_history import (
    HISTORICAL_DB_PATH,
    available_history_counties as _available_history_counties,
    all_storms as _all_storms,
    load_history_for_county as _load_history_for_county,
    fpl_restoration_precedent as _fpl_restoration_precedent,
    fpl_restoration_precedent_by_wind_severity as _fpl_restoration_precedent_by_wind_severity,
)
from fetch_fpl_outages import UTILITY_NAME as FPL_UTILITY_NAME
from fetch_jea_outages import UTILITY_NAME as JEA_UTILITY_NAME
from fetch_talquin_outages import UTILITY_NAME as TALQUIN_UTILITY_NAME
from fetch_tallahassee_outages import UTILITY_NAME as TALLAHASSEE_UTILITY_NAME
from fetch_fpuc_outages import UTILITY_NAME as FPUC_UTILITY_NAME
from fetch_preco_outages import UTILITY_NAME as PRECO_UTILITY_NAME
from fetch_fkec_outages import UTILITY_NAME as FKEC_UTILITY_NAME
from fetch_tcec_outages import UTILITY_NAME as TCEC_UTILITY_NAME
from fetch_erec_outages import UTILITY_NAME as EREC_UTILITY_NAME
from fetch_chelco_outages import UTILITY_NAME as CHELCO_UTILITY_NAME
from fetch_gcec_outages import UTILITY_NAME as GCEC_UTILITY_NAME
from fetch_lwbu_outages import UTILITY_NAME as LWBU_UTILITY_NAME
from fetch_ouc_outages import UTILITY_NAME as OUC_UTILITY_NAME
from fetch_lcec_outages import UTILITY_NAME as LCEC_UTILITY_NAME


app = Flask(__name__)

# find_correlations()/find_teco_correlations()/find_duke_correlations()/
# find_jea_correlations() each nested-loop the raw history of their
# source table against every weather alert in plain Python - measured
# 2026-07-12 at ~35s combined once outages/teco_incidents/duke_incidents
# grew into the tens of thousands of rows (a fresh row gets logged
# every 15-min poll cycle per county/incident, forever, so this only
# gets slower over time). The underlying data only actually changes
# once per poll cycle, but the dashboard auto-refreshes every 60s (see
# the <meta http-equiv="refresh"> in dashboard.html), so most reloads
# were recomputing an answer that couldn't have changed. Cached here
# with a short TTL rather than rewriting the matching into SQL - a much
# smaller, lower-risk fix for the same practical problem.
CORRELATION_CACHE_TTL_SECONDS = 300

# Default window for the correlation tables - added 2026-07-12 alongside
# a real over-counting bug fix (see correlate.py). Without a window
# these counts are all-time since the poller first started and only
# ever grow less meaningful; the dashboard also offers a 7-day toggle
# (see the /?window=7 query param below). Cache is keyed by window
# since 7-day and 30-day results genuinely differ.
DEFAULT_CORRELATION_WINDOW_DAYS = 30
CORRELATION_WINDOW_CHOICES = (7, 30)
_correlation_cache = {}

# Shared between the pipeline-health strip on the main dashboard and the
# /pipeline-errors drill-down page, so a source's display name can't
# drift between the two.
PIPELINE_SOURCE_DISPLAY_NAMES = {
    "fpl": "FPL",
    "weather": "NWS Weather",
    "teco": "TECO",
    "duke": "Duke Energy",
    "jea": "JEA",
    "tallahassee": "City of Tallahassee",
    "talquin": "Talquin Electric Cooperative",
    "fpuc": "Florida Public Utilities Corporation",
    "preco": "Peace River Electric Cooperative",
    "fkec": "Florida Keys Electric Cooperative",
    "tcec": "Tri-County Electric Cooperative",
    "erec": "Escambia River Electric Cooperative",
    "chelco": "Choctawhatchee Electric Cooperative",
    "gcec": "Gulf Coast Electric Cooperative",
    "lwbu": "Lake Worth Beach Utilities",
    "ouc": "Orlando Utilities Commission",
    "lcec": "Lee County Electric Cooperative",
    "correlation": "Correlation",
    "historical_tally": "Historical Confidence Tally",
}

# COUNTY_PICKER_CHOICES now lives in county_status.py (imported above).

# Plain-English translations for the raw exception text landing in
# pipeline_errors.error_message (str(e) from main.py's try/except
# blocks - always a real Python/requests exception message, e.g.
# "database is locked" or "HTTPSConnectionPool(...): Read timed out.").
# Never replaces the raw message on the /pipeline-errors page - this is
# a derived explanation shown alongside it, same non-destructive
# principle as fetch_teco_outages.py's reason/status categorization.
# Order matters: first matching pattern wins, most specific first.
PIPELINE_ERROR_EXPLANATIONS = [
    ("fetch-failed", [r"fetch returned no (records|data)"],
     "A request to this data source failed this cycle - it normally "
     "reports fresh numbers every single check, so getting nothing back "
     "at all is treated as a real problem rather than a quiet day. It "
     "will try again automatically on the next scheduled check.",
     "warn"),
    ("database-lock", [r"database is locked"],
     "Two parts of our own system tried to write to the local database at "
     "the exact same instant. Not related to the utility's feed at all - "
     "it resolves itself automatically on the very next check.",
     "info"),
    ("rate-limited", [r"\b429\b", r"\b420\b", r"too many requests"],
     "The data source temporarily blocked repeated requests, a common "
     "anti-abuse measure most sites use. It backs off and tries again on "
     "the next scheduled check.",
     "warn"),
    ("server-error", [r"\b50[0234]\b", r"server error", r"bad gateway", r"service unavailable"],
     "The data source's own server reported a problem on its end - not "
     "something wrong with our system. Usually brief.",
     "warn"),
    ("timeout", [r"timed? ?out", r"\btimeout\b"],
     "The request to the data source took too long and gave up waiting. "
     "Usually a brief network hiccup, not a sign anything is actually "
     "broken - it tries again on the next scheduled check.",
     "warn"),
    ("connection", [r"connection refused", r"failed to establish a new connection",
                     r"name or service not known", r"connection aborted", r"connection reset"],
     "Couldn't reach the data source's server at all for a moment - a "
     "brief network outage on one end or the other. Resolves on its own "
     "once the connection path is available again.",
     "crit"),
    ("unexpected-format", [r"expecting value", r"jsondecodeerror", r"not valid json", r"unexpected.*shape"],
     "The data source sent back something in a shape we didn't expect. "
     "Worth a second look if this keeps happening - it can mean the "
     "source changed how it formats its data.",
     "crit"),
]


def _explain_pipeline_error(message):
    """
    Best-effort plain-English explanation for a raw pipeline error
    message, for display on /pipeline-errors. Returns
    (label, explanation, severity) - severity is a rough "how worried
    should a reader be" hint (info/warn/crit), not the same thing as
    get_pipeline_health()'s healthy/warning/critical status (that one
    tracks sustained failure over time; this one is about a single
    message's own nature - a lone "connection refused" is still crit-
    flavored even if it's the source's only failure all week).

    Falls back to an honest "not recognized" label rather than
    guessing - better to admit a message wasn't understood than
    to mislabel it.
    """
    if not message:
        return ("unknown", "No error message was recorded.", "info")

    lowered = message.lower()
    for label, patterns, explanation, severity in PIPELINE_ERROR_EXPLANATIONS:
        if any(re.search(pattern, lowered) for pattern in patterns):
            return (label, explanation, severity)

    return ("other", "An uncommon error - the raw message above is the best available detail.", "warn")


def _get_cached_correlations(db_path, days):
    now = time.time()
    cached = _correlation_cache.get(days)
    if cached is not None and (now - cached["computed_at"]) < CORRELATION_CACHE_TTL_SECONDS:
        return cached["data"]

    data = (
        find_correlations(db_path, days=days),
        find_teco_correlations(db_path, days=days),
        find_duke_correlations(db_path, days=days),
        find_jea_correlations(db_path, days=days),
        find_tallahassee_correlations(db_path, days=days),
        find_talquin_correlations(db_path, days=days),
        find_fpuc_incident_correlations(db_path, days=days),
        find_preco_correlations(db_path, days=days),
        find_fkec_correlations(db_path, days=days),
        find_tcec_correlations(db_path, days=days),
        find_erec_correlations(db_path, days=days),
        find_chelco_correlations(db_path, days=days),
        find_gcec_correlations(db_path, days=days),
        find_lwbu_correlations(db_path, days=days),
        find_ouc_correlations(db_path, days=days),
        find_lcec_correlations(db_path, days=days),
    )
    _correlation_cache[days] = {"data": data, "computed_at": now}
    return data


# _duration_since now lives in county_status.py (imported above).


# _humanize_timestamp now lives in county_status.py as
# humanize_timestamp (imported above under the old private name).
app.jinja_env.filters['humanize'] = _humanize_timestamp


def _incident_label(incident_id):
    """
    Duke's incident_id is literally YYYYMMDD + a 6-digit sequence number
    that resets daily (confirmed 2026-07-12 against real first-seen
    dates - an incident first seen on 2026-07-03 has id
    "20260703000275", one from 2026-07-12 starts "20260712..."). The
    date half is pure redundancy here since the row's own "Started"
    column already shows it - the sequence number is the only actually
    new information, so that's all this shows: "Incident #423".

    TECO's incident_id (e.g. "A202619308291") does NOT decode to a date -
    it's a large, steadily-incrementing counter (grew by roughly 100,000
    over 10 real days, checked directly against the data), almost
    certainly TECO's shared enterprise ticket sequence rather than
    anything outage-specific. There's no real structure to translate,
    so it's left exactly as TECO sends it rather than faking a
    transformation - detected by shape (14 digits, first 8 a valid
    date), not by utility name, so this stays correct if either source's
    format ever changes.
    """
    if incident_id and len(incident_id) == 14 and incident_id.isdigit():
        date_part, seq_part = incident_id[:8], incident_id[8:]
        try:
            datetime.strptime(date_part, "%Y%m%d")
            return f"Incident #{int(seq_part)}"
        except ValueError:
            pass
    return incident_id


app.jinja_env.filters['incident_label'] = _incident_label


def _format_alert_types(alert_types):
    """
    Turn {"Flood Advisory": 32, "Tornado Warning": 2} into
    "Flood Advisory ×32, Tornado Warning ×2"
    """
    return ", ".join(f"{name} ×{count}" for name, count in alert_types.items())


def _format_confidence(confidence_breakdown):
    """
    Turn {"high": 3, "medium": 5, "low": 2} into "high ×3, medium ×5, low ×2",
    always in high/medium/low order regardless of dict insertion order.
    """
    order = ["high", "medium", "low"]
    return ", ".join(
        f"{tier} ×{confidence_breakdown[tier]}" for tier in order if tier in confidence_breakdown
    )


def _confidence_bar_segments(confidence_breakdown):
    """
    Convert a {"high": 5, "medium": 19, "low": 81} breakdown into a list
    of {"tier", "count", "pct"} dicts for a stacked bar's segment widths
    (pct of the total), always in high/medium/low order so the bar reads
    best-to-worst left to right. Empty list if there's nothing to show.
    """
    order = ["high", "medium", "low"]
    total = sum(confidence_breakdown.values())
    if total == 0:
        return []
    return [
        {"tier": tier, "count": confidence_breakdown[tier], "pct": confidence_breakdown[tier] / total * 100}
        for tier in order if tier in confidence_breakdown
    ]


def _combine_confidence_breakdowns(*match_lists):
    """
    Merge confidence counts across multiple correlation match lists
    (FPL + TECO + Duke + JEA) into one combined breakdown, for a single
    state-wide summary bar.

    Deduplicated by distinct alert (2026-07-12) - same fix as
    correlation_summary()/teco_correlation_summary()/
    duke_correlation_summary() in correlate.py, caught right after
    shipping those: this function still counted every matched pair, so
    the combined KPI strip kept showing an inflated "low x27118" even
    after the per-county tables were fixed. Confidence is a pure
    function of the alert's own event_type + severity, not of which
    outage/incident it happened to match, so it needs the same per-alert
    deduplication - reuses correlate.py's own _alert_identity() rather
    than re-deriving the same synthetic-key logic here, so the two stay
    in sync if that logic ever changes.
    """
    matched_alerts = {}
    for matches in match_lists:
        for match in matches:
            matched_alerts[_alert_identity(match["alert"])] = match["confidence"]

    combined = {}
    for confidence in matched_alerts.values():
        combined[confidence] = combined.get(confidence, 0) + 1
    return combined


# _percentage_tier now lives in county_status.py (imported above).


def _build_unified_view(open_events, teco_open_events, duke_open_events, jea_open_events, tallahassee_open_events, talquin_open_events, fpuc_open_events, fpuc_open_incidents, preco_open_events, fkec_open_events, tcec_open_events, erec_open_events, chelco_open_events, gcec_open_events, lwbu_open_events, ouc_open_events, lcec_open_events):
    """
    Normalize FPL's/JEA's county-level outage_events-shaped tables and
    TECO's/Duke's incident-level *_incident_events into one common shape
    for an at-a-glance, all-utilities table. Deliberately keeps only the
    fields all sources actually have (utility, county, customers
    affected, when it started, how long it's been going) - the richer
    per-source fields (TECO's/Duke's cause/ETR, FPL's/JEA's percentage-
    of-county) stay in their own detailed sections below, not squeezed
    in here.

    Both "customers" (the live count right now) and "peak_customers" (the
    high-water mark for the whole ongoing episode) are kept - sorted and
    summed by "customers", since this feeds the "right now" KPI strip,
    not a peak-of-episode one. Added 2026-07-12 after comparing a peak
    reading against poweroutage.us's live count for Palm Beach and
    realizing the two numbers are legitimately different things.
    """
    unified = []

    for e in open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in teco_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customer_count"],
            "peak_customers": e["peak_customer_count"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in duke_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customer_count"],
            "peak_customers": e["peak_customer_count"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in jea_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in tallahassee_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in talquin_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in fpuc_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    # Real regression, found 2026-07-18 by comparing this page's own
    # "Customers Out Right Now" KPI against the public site's - FPUC's
    # real per-incident view (distinct from its combined-territory total
    # just above) was never folded into this statewide total, even
    # though county_status._real_per_county_open_events() (which the
    # public site and this same dashboard's own /county page both use)
    # already counts it as real per-county data. Silent until now only
    # because FPUC's incident-level view happened to have zero open
    # incidents every time this was checked before.
    for e in fpuc_open_incidents:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customer_count"],
            "peak_customers": e["peak_customer_count"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in preco_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in fkec_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in tcec_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in erec_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in chelco_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in gcec_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in lwbu_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in ouc_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in lcec_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["current_customers_out"],
            "peak_customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    unified.sort(key=lambda row: row["customers"] or 0, reverse=True)
    return unified


# _normalize_open_events, _real_per_county_open_events,
# _combined_territory_open_events, and _rows_for_county now live in
# county_status.py (imported above) - shared with public_site.py so
# both apps read live per-county status the same way without either
# one importing from the other.


@app.route("/")
def index():
    try:
        window_days = int(request.args.get("window", DEFAULT_CORRELATION_WINDOW_DAYS))
    except ValueError:
        window_days = DEFAULT_CORRELATION_WINDOW_DAYS
    if window_days not in CORRELATION_WINDOW_CHOICES:
        window_days = DEFAULT_CORRELATION_WINDOW_DAYS

    db = OutageDatabase()
    db_path = db.db_path

    snapshot = db.get_latest_snapshot()
    open_events = db.get_open_events()
    closed_events = db.get_recent_closed_events(limit=10)
    weather_alerts = db.get_recent_weather_alerts(limit=10)
    teco_open_events = db.get_teco_open_events()
    teco_closed_events = db.get_teco_recent_closed_events(limit=10)
    duke_open_events = db.get_duke_open_events()
    duke_closed_events = db.get_duke_recent_closed_events(limit=10)
    jea_open_events = db.get_jea_open_events()
    jea_closed_events = db.get_jea_recent_closed_events(limit=10)
    tallahassee_open_events = db.get_tallahassee_open_events()
    tallahassee_closed_events = db.get_tallahassee_recent_closed_events(limit=10)
    talquin_open_events = db.get_talquin_open_events()
    talquin_closed_events = db.get_talquin_recent_closed_events(limit=10)
    fpuc_open_events = db.get_fpuc_open_events()
    fpuc_closed_events = db.get_fpuc_recent_closed_events(limit=10)
    fpuc_open_incidents = db.get_fpuc_open_incidents()
    fpuc_closed_incidents = db.get_fpuc_recent_closed_incidents(limit=10)
    preco_open_events = db.get_preco_open_events()
    preco_closed_events = db.get_preco_recent_closed_events(limit=10)
    fkec_open_events = db.get_fkec_open_events()
    fkec_closed_events = db.get_fkec_recent_closed_events(limit=10)
    tcec_open_events = db.get_tcec_open_events()
    tcec_closed_events = db.get_tcec_recent_closed_events(limit=10)
    erec_open_events = db.get_erec_open_events()
    erec_closed_events = db.get_erec_recent_closed_events(limit=10)
    chelco_open_events = db.get_chelco_open_events()
    chelco_closed_events = db.get_chelco_recent_closed_events(limit=10)
    gcec_open_events = db.get_gcec_open_events()
    gcec_closed_events = db.get_gcec_recent_closed_events(limit=10)
    lwbu_open_events = db.get_lwbu_open_events()
    lwbu_closed_events = db.get_lwbu_recent_closed_events(limit=10)
    lwbu_open_incidents = db.get_lwbu_open_incidents()
    lwbu_closed_incidents = db.get_lwbu_recent_closed_incidents(limit=10)
    ouc_open_events = db.get_ouc_open_events()
    ouc_closed_events = db.get_ouc_recent_closed_events(limit=10)
    lcec_open_events = db.get_lcec_open_events()
    lcec_closed_events = db.get_lcec_recent_closed_events(limit=10)

    pipeline_health = db.get_pipeline_health(sources=["fpl", "weather", "teco", "duke", "jea", "tallahassee", "talquin", "fpuc", "preco", "fkec", "tcec", "erec", "chelco", "gcec", "lwbu", "ouc", "lcec", "correlation", "historical_tally"])
    heat_summary = db.get_heat_advisory_summary()

    db.close()

    for event in open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in teco_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in teco_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in duke_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in duke_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in jea_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in jea_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in tallahassee_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in tallahassee_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in talquin_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in talquin_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in fpuc_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in fpuc_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in fpuc_open_incidents:
        event["duration"] = _duration_since(event["start_time"])
    for event in fpuc_closed_incidents:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in preco_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in preco_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in fkec_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in fkec_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in tcec_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in tcec_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in erec_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in erec_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in chelco_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in chelco_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in gcec_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in gcec_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in lwbu_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in lwbu_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in lwbu_open_incidents:
        event["duration"] = _duration_since(event["start_time"])
    for event in lwbu_closed_incidents:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in ouc_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in ouc_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])
    for event in lcec_open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in lcec_closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])

    matches, teco_matches, duke_matches, jea_matches, tallahassee_matches, talquin_matches, fpuc_matches, preco_matches, fkec_matches, tcec_matches, erec_matches, chelco_matches, gcec_matches, lwbu_matches, ouc_matches, lcec_matches = _get_cached_correlations(db_path, window_days)

    correlation = correlation_summary(matches)
    for stats in correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    teco_correlation = teco_correlation_summary(teco_matches)
    for stats in teco_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    duke_correlation = duke_correlation_summary(duke_matches)
    for stats in duke_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    jea_correlation = correlation_summary(jea_matches)
    for stats in jea_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    tallahassee_correlation = correlation_summary(tallahassee_matches)
    for stats in tallahassee_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    talquin_correlation = correlation_summary(talquin_matches)
    for stats in talquin_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    fpuc_correlation = duke_correlation_summary(fpuc_matches)
    for stats in fpuc_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    preco_correlation = correlation_summary(preco_matches)
    for stats in preco_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    fkec_correlation = correlation_summary(fkec_matches)
    for stats in fkec_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    tcec_correlation = correlation_summary(tcec_matches)
    for stats in tcec_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    erec_correlation = correlation_summary(erec_matches)
    for stats in erec_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    chelco_correlation = correlation_summary(chelco_matches)
    for stats in chelco_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    gcec_correlation = correlation_summary(gcec_matches)
    for stats in gcec_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    lwbu_correlation = correlation_summary(lwbu_matches)
    for stats in lwbu_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    ouc_correlation = correlation_summary(ouc_matches)
    for stats in ouc_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    lcec_correlation = correlation_summary(lcec_matches)
    for stats in lcec_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    unified_open = _build_unified_view(open_events, teco_open_events, duke_open_events, jea_open_events, tallahassee_open_events, talquin_open_events, fpuc_open_events, fpuc_open_incidents, preco_open_events, fkec_open_events, tcec_open_events, erec_open_events, chelco_open_events, gcec_open_events, lwbu_open_events, ouc_open_events, lcec_open_events)

    for event in open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in jea_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in jea_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in talquin_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in talquin_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in fpuc_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in fpuc_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in preco_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in preco_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in fkec_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in fkec_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in tcec_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in tcec_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in erec_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in erec_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in chelco_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in chelco_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in gcec_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in gcec_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in lwbu_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in lwbu_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in ouc_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in ouc_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in lcec_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in lcec_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])

    # Pipeline health strip - surfaces caught fetch/correlation failures
    # (see OutageDatabase.log_pipeline_error/get_pipeline_health) that
    # used to only ever exist as a print() line in a growing text log
    # file nobody was watching.
    for source, info in pipeline_health.items():
        info["source"] = source
        info["display_name"] = PIPELINE_SOURCE_DISPLAY_NAMES.get(source, source.title())
        info["last_error_ago"] = _duration_since(info["last_error_time"]) if info["last_error_time"] else None
    pipeline_status_order = {"critical": 0, "warning": 1, "healthy": 2}
    pipeline_health_list = sorted(
        pipeline_health.values(),
        key=lambda info: pipeline_status_order[info["status"]],
    )
    any_pipeline_issue = any(info["status"] != "healthy" for info in pipeline_health_list)

    # KPI summary strip at the top of the page - a fast, at-a-glance
    # read before scrolling into the detailed per-utility tables below.
    total_customers_affected = sum(row["customers"] or 0 for row in unified_open)
    worst_row = unified_open[0] if unified_open else None
    combined_confidence = _combine_confidence_breakdowns(matches, teco_matches, duke_matches, jea_matches, tallahassee_matches, talquin_matches, fpuc_matches, preco_matches, fkec_matches, tcec_matches, erec_matches, chelco_matches, gcec_matches, lwbu_matches, ouc_matches, lcec_matches)
    combined_confidence_bar = _confidence_bar_segments(combined_confidence)
    combined_confidence_display = _format_confidence(combined_confidence)

    return render_template(
        "dashboard.html",
        snapshot=snapshot,
        open_events=open_events,
        closed_events=closed_events,
        weather_alerts=weather_alerts,
        correlation=correlation,
        teco_open_events=teco_open_events,
        teco_closed_events=teco_closed_events,
        teco_correlation=teco_correlation,
        duke_open_events=duke_open_events,
        duke_closed_events=duke_closed_events,
        duke_correlation=duke_correlation,
        jea_open_events=jea_open_events,
        jea_closed_events=jea_closed_events,
        jea_correlation=jea_correlation,
        tallahassee_open_events=tallahassee_open_events,
        tallahassee_closed_events=tallahassee_closed_events,
        tallahassee_correlation=tallahassee_correlation,
        talquin_open_events=talquin_open_events,
        talquin_closed_events=talquin_closed_events,
        talquin_correlation=talquin_correlation,
        fpuc_open_events=fpuc_open_events,
        fpuc_closed_events=fpuc_closed_events,
        fpuc_correlation=fpuc_correlation,
        fpuc_open_incidents=fpuc_open_incidents,
        fpuc_closed_incidents=fpuc_closed_incidents,
        preco_open_events=preco_open_events,
        preco_closed_events=preco_closed_events,
        preco_correlation=preco_correlation,
        fkec_open_events=fkec_open_events,
        fkec_closed_events=fkec_closed_events,
        fkec_correlation=fkec_correlation,
        tcec_open_events=tcec_open_events,
        tcec_closed_events=tcec_closed_events,
        tcec_correlation=tcec_correlation,
        erec_open_events=erec_open_events,
        erec_closed_events=erec_closed_events,
        erec_correlation=erec_correlation,
        chelco_open_events=chelco_open_events,
        chelco_closed_events=chelco_closed_events,
        chelco_correlation=chelco_correlation,
        gcec_open_events=gcec_open_events,
        gcec_closed_events=gcec_closed_events,
        gcec_correlation=gcec_correlation,
        lwbu_open_events=lwbu_open_events,
        lwbu_closed_events=lwbu_closed_events,
        lwbu_correlation=lwbu_correlation,
        lwbu_open_incidents=lwbu_open_incidents,
        lwbu_closed_incidents=lwbu_closed_incidents,
        ouc_open_events=ouc_open_events,
        ouc_closed_events=ouc_closed_events,
        ouc_correlation=ouc_correlation,
        lcec_open_events=lcec_open_events,
        lcec_closed_events=lcec_closed_events,
        lcec_correlation=lcec_correlation,
        unified_open=unified_open,
        total_customers_affected=total_customers_affected,
        worst_row=worst_row,
        combined_confidence_bar=combined_confidence_bar,
        combined_confidence_display=combined_confidence_display,
        pipeline_health_list=pipeline_health_list,
        any_pipeline_issue=any_pipeline_issue,
        heat_summary=heat_summary,
        window_days=window_days,
        window_choices=CORRELATION_WINDOW_CHOICES,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


# HISTORICAL_DB_PATH, _available_history_counties, _all_storms, and
# _load_history_for_county now live in storm_history.py (imported
# above) - shared with public_site.py so both apps read historical
# storm data the same way without either one importing from the other.


@app.route("/history")
def history():
    """
    Query a single Florida county's real historical storm pattern across
    the 17 independently-verified storms backfilled so far (2018-2025).

    Internal tool for now, not a public-facing feature - see
    docs/ROADMAP.md Phase 4 for what actually opening this up publicly
    would require (it's a real, separate, not-yet-met gate, not just a
    UI decision).
    """
    available_counties = _available_history_counties()
    selected_county = request.args.get("county", "").strip()

    storms = None
    storms_with_data_count = 0
    if selected_county and available_counties:
        storms = _load_history_for_county(selected_county)
        storms_with_data_count = sum(1 for s in storms if s["has_data"])

    return render_template(
        "history.html",
        available_counties=available_counties,
        selected_county=selected_county,
        storms=storms,
        storms_with_data_count=storms_with_data_count,
        db_missing=not available_counties,
    )


@app.route("/county")
def county_detail():
    """
    Live, per-county drill-down - pick one of Florida's 67 real counties
    and see everything currently relevant to it in one place: real
    per-county outages from every source that actually reports
    per-county (including FPUC's real incident-level markers, not just
    its combined total), weather alerts active right now that name this
    county, and - shown separately, since their number covers more
    territory than just this one county - combined-territory sources
    (FPUC's original combined view, TCEC, EREC, CHELCO, GCEC) whose
    label happens to mention it.

    Deliberately live/current-status only, not historical - see
    /history for real multi-year storm data per county.
    """
    selected_county = request.args.get("county", "").strip()

    real_events = []
    combined_events = []
    active_alerts = []
    historical_confidence = None
    historical_gap_reason = None
    major_storm_precedent = None
    major_storm_by_severity = None
    everyday_precedent = None
    teco_accuracy = None
    duke_precedent = None

    if selected_county:
        db = OutageDatabase()
        real_events = _rows_for_county(_real_per_county_open_events(db), selected_county)
        combined_events = _rows_for_county(_combined_territory_open_events(db), selected_county)
        all_active_alerts = db.get_active_weather_alerts()

        tally = db.get_historical_confidence_tally()
        historical_confidence = next(
            (stats for county, stats in tally.items() if county.upper() == selected_county.upper()), None
        )
        if historical_confidence is None:
            historical_gap_reason = explain_missing_historical_data(selected_county, db)

        real_events.sort(key=lambda r: r["customers"] or 0, reverse=True)
        combined_events.sort(key=lambda r: r["customers"] or 0, reverse=True)

        # Restoration precedent (Phase 3) - two deliberately separate,
        # distinctly-labeled numbers, only shown when there's a real,
        # currently-open FPL outage in this county right now, same
        # gating as the public site. FPL's live feed can never support
        # real incident-level restoration modeling, so these are the
        # honest substitute - "Major Storms" from the 17-storm PSC
        # archive (see storm_history.fpl_restoration_precedent()) and
        # "Everyday Outages" from this project's own live tracking (see
        # county_status.fpl_ordinary_restoration_stats()). Computed
        # before db.close() below - the everyday version needs the live
        # connection, unlike the storm one's own separate archive db.
        fpl_open_now = any(r["utility"] == FPL_UTILITY_NAME for r in real_events)
        major_storm_precedent = _fpl_restoration_precedent(selected_county) if fpl_open_now else None
        major_storm_by_severity = _fpl_restoration_precedent_by_wind_severity(selected_county) if fpl_open_now else None
        everyday_precedent = fpl_ordinary_restoration_stats(selected_county, db) if fpl_open_now else None

        # A genuinely different Phase 3 signal for TECO - it already
        # reports a real per-incident ETR (unlike FPL, which has no
        # per-incident data at all), so instead of inventing a precedent
        # range, this checks how trustworthy TECO's own existing number
        # has actually been. Same "only when directly relevant right
        # now" gating - see county_status.teco_etr_accuracy().
        teco_open_now = any(r["utility"] == TECO_UTILITY_NAME for r in real_events)
        teco_accuracy = teco_etr_accuracy(selected_county, db) if teco_open_now else None

        # A third Phase 3 shape for Duke - real, individually-tracked
        # incidents like TECO's, but no restoration-estimate field to
        # check accuracy against, so it gets a plain duration precedent
        # like FPL's "Everyday Outages" instead, without FPL's outlier
        # filter (Duke's incidents are already real and individually
        # clean) and without a "Major Storms" sibling (no storm archive
        # counterpart for Duke). See county_status.duke_restoration_precedent().
        duke_open_now = any(r["utility"] == DUKE_UTILITY_NAME for r in real_events)
        duke_precedent = duke_restoration_precedent(selected_county, db) if duke_open_now else None

        db.close()

        active_alerts = [a for a in all_active_alerts if _county_in_alert(selected_county, a["areas"])]
        for a in active_alerts:
            a["is_heat"] = a["event_type"] in ("Heat Advisory", "Excessive Heat Warning")

    return render_template(
        "county.html",
        available_counties=COUNTY_PICKER_CHOICES,
        selected_county=selected_county,
        real_events=real_events,
        combined_events=combined_events,
        active_alerts=active_alerts,
        historical_confidence=historical_confidence,
        historical_gap_reason=historical_gap_reason,
        major_storm_precedent=major_storm_precedent,
        major_storm_by_severity=major_storm_by_severity,
        everyday_precedent=everyday_precedent,
        teco_accuracy=teco_accuracy,
        duke_precedent=duke_precedent,
    )


@app.route("/heat")
def heat():
    """
    Detail view for the dashboard's "heat this month" strip - which
    specific NWS forecast zones are under an active Heat Advisory /
    Excessive Heat Warning right now, plus this month's frequency so
    far. See OutageDatabase.get_heat_advisory_summary().
    """
    db = OutageDatabase()
    heat_summary = db.get_heat_advisory_summary()
    db.close()

    return render_template("heat.html", heat_summary=heat_summary)


# A poll cycle runs every ~15 min, so a gap under this between two
# failures from the same source is almost certainly the same underlying
# episode continuing, not two unrelated blips - used to group raw
# pipeline_errors rows into "streaks" for display (see
# _group_pipeline_errors below), rather than showing each individual
# instant a source failed as its own separate line.
PIPELINE_ERROR_GROUP_GAP_MINUTES = 20


def _group_pipeline_errors(errors):
    """
    Collapse consecutive same-source pipeline_errors rows into streaks,
    so /pipeline-errors can show how long a source was actually failing
    for (first occurrence -> last occurrence) instead of a redundant
    pair of "when this row happened" timestamps repeated once per row.

    errors: rows from OutageDatabase.get_pipeline_error_history(),
    any order. Returns a list of dicts (source, first_timestamp,
    last_timestamp, count, latest_message), most recent streak first.
    """
    by_source = {}
    for e in errors:
        by_source.setdefault(e["source"], []).append(e)

    gap = timedelta(minutes=PIPELINE_ERROR_GROUP_GAP_MINUTES)
    groups = []
    for source, rows in by_source.items():
        rows = sorted(rows, key=lambda r: r["timestamp"])
        current = None
        current_dt = None
        for row in rows:
            row_dt = datetime.fromisoformat(row["timestamp"])
            if current and (row_dt - current_dt) <= gap:
                current["last_timestamp"] = row["timestamp"]
                current["count"] += 1
                current["latest_message"] = row["error_message"]
            else:
                if current:
                    groups.append(current)
                current = {
                    "source": source,
                    "first_timestamp": row["timestamp"],
                    "last_timestamp": row["timestamp"],
                    "count": 1,
                    "latest_message": row["error_message"],
                }
            current_dt = row_dt
        if current:
            groups.append(current)

    groups.sort(key=lambda g: g["last_timestamp"], reverse=True)
    return groups


def _is_pipeline_error_ongoing(last_timestamp, now=None):
    """
    True if a pipeline-error streak (see _group_pipeline_errors) is
    still actively happening, as opposed to a resolved, historical one -
    i.e. its most recent failure is recent enough that another real
    failure right now would still extend this same streak rather than
    start a new one. Same PIPELINE_ERROR_GROUP_GAP_MINUTES threshold
    _group_pipeline_errors() itself uses to decide that, just measured
    against "now" instead of another row in the same table.

    Without this distinction, a streak that's still actively failing
    (e.g. Talquin/PRECO mid-outage) reads exactly like a long-past,
    fully-resolved one ("occurred over 6h24m") - same past-tense wording
    either way, no way to tell which one you're looking at.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=PIPELINE_ERROR_GROUP_GAP_MINUTES)
    return datetime.fromisoformat(last_timestamp) >= cutoff


PIPELINE_ERRORS_PER_PAGE = 20

# The "known chronic issues" section isn't paginated like the main
# list - it's meant as a quick, bounded "yes, still the same known
# thing" glance, not a full history (use the source=talquin/preco
# filter for that).
CHRONIC_ISSUE_DISPLAY_LIMIT = 10


def _paginate(items, page, per_page):
    """
    Slice an already-sorted list into one page's worth of items, plus
    enough context to render prev/next controls - used to keep
    /pipeline-errors from rendering every streak on one unbounded page
    as the error-history table keeps growing over the project's
    lifetime.

    page is 1-indexed and clamped into range rather than erroring or
    silently returning nothing - a stale bookmarked link to a page
    number that no longer exists (e.g. the list got shorter) lands on
    the nearest real page instead of an empty/broken result.
    """
    total = len(items)
    total_pages = max(1, -(-total // per_page))  # ceiling division
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return {
        "items": items[start:start + per_page],
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }


def _split_chronic_errors(errors, chronic_sources, limit):
    """
    Pull streaks from known-chronic sources (Talquin/PRECO's rotating
    credential - see alerting.ALERT_WORTHY_SOURCES) out of the main
    /pipeline-errors list into their own bounded, unpaginated group -
    otherwise their real but already-understood recurring failures
    would dominate the combined view and bury genuinely rare failures
    from every other source.

    Returns (chronic, other) - chronic capped at `limit` (a quick "yes,
    still the same known thing" glance, not a full history; the
    source=talquin/preco filter already shows that), other unchanged
    apart from the removal.
    """
    chronic = [e for e in errors if e["source"] in chronic_sources][:limit]
    other = [e for e in errors if e["source"] not in chronic_sources]
    return chronic, other


def _summarize_chronic_errors(chronic_errors):
    """
    One compact line per known-chronic source (total streak count, most
    recent streak's own timing/status) - even a handful of individual
    streak cards reads as clutter for a source everyone already knows
    is a recurring, understood issue. chronic_errors is assumed already
    sorted most-recent-first (same order _group_pipeline_errors
    produces), so the first streak seen per source is its latest.
    """
    summaries = []
    seen_sources = set()
    for e in chronic_errors:
        if e["source"] in seen_sources:
            continue
        seen_sources.add(e["source"])
        streak_count = sum(1 for other in chronic_errors if other["source"] == e["source"])
        summaries.append({
            "display_name": e["display_name"],
            "streak_count": streak_count,
            "latest": e,
        })
    return summaries


@app.route("/pipeline-errors")
def pipeline_errors():
    """
    Detail view behind the main dashboard's pipeline-health strip - the
    strip only ever shows a count and the single latest message per
    source (see OutageDatabase.get_pipeline_health()); this shows the
    actual raw history so a real pattern (recurring at the same time of
    day, several sources failing at once, one message repeating) is
    visible instead of just "something failed once."

    Consecutive same-source failures are collapsed into one streak (see
    _group_pipeline_errors) - shows when a streak started and how long
    it actually lasted, rather than a redundant "date + time ago" pair
    repeated on every individual row.

    source=<name> filters to just that source (matches the same keys
    used in main.py's log_pipeline_error() calls, e.g. "fpl"/"preco");
    omitted shows every source combined, most recent first.

    page=<n> paginates the resulting streak list (see _paginate) -
    added once a real sustained failure (Talquin/PRECO) made the
    all-on-one-page view keep growing without bound.
    """
    selected_source = request.args.get("source", "").strip().lower()
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1

    db = OutageDatabase()
    all_sources = sorted(PIPELINE_SOURCE_DISPLAY_NAMES.keys())
    # Fetched well above any realistic history size so streaks are
    # grouped from the complete raw record, not a truncated recent
    # window - pagination for display happens afterward, on the
    # grouped streaks themselves, not by cutting off raw rows early.
    raw_errors = db.get_pipeline_error_history(source=selected_source or None, limit=10000)
    db.close()

    errors = _group_pipeline_errors(raw_errors)
    for e in errors:
        e["display_name"] = PIPELINE_SOURCE_DISPLAY_NAMES.get(e["source"], e["source"].title())
        e["duration"] = _duration_since(e["first_timestamp"], e["last_timestamp"])
        e["explanation_label"], e["explanation_text"], e["explanation_severity"] = _explain_pipeline_error(e["latest_message"])
        e["is_ongoing"] = _is_pipeline_error_ongoing(e["last_timestamp"])

    # Split known-chronic sources (Talquin/PRECO) into their own
    # section, but only on the combined "all sources" view - a direct
    # source=talquin filter already shows exactly what was asked for.
    # Same source list alerting.py uses for its email cooldown, so both
    # stay in sync automatically.
    chronic_errors = []
    if not selected_source:
        chronic_errors, errors = _split_chronic_errors(errors, ALERT_WORTHY_SOURCES, CHRONIC_ISSUE_DISPLAY_LIMIT)
    chronic_summaries = _summarize_chronic_errors(chronic_errors)

    pagination = _paginate(errors, page, PIPELINE_ERRORS_PER_PAGE)

    return render_template(
        "pipeline_errors.html",
        errors=pagination["items"],
        chronic_errors=chronic_errors,
        chronic_summaries=chronic_summaries,
        page=pagination["page"],
        total_pages=pagination["total_pages"],
        total_errors=pagination["total"],
        has_prev=pagination["has_prev"],
        has_next=pagination["has_next"],
        selected_source=selected_source,
        all_sources=all_sources,
        source_display_names=PIPELINE_SOURCE_DISPLAY_NAMES,
    )


@app.route("/incident")
def incident():
    """
    Detail view for one specific outage/incident, reached by clicking a
    row in one of the "Currently Open"/"Recently Resolved" tables on the
    main dashboard (one pair per utility) - not meant to be reached by
    typing an id from memory.

    TECO/Duke/FPUC's per-incident view have a real incident_id, so one
    id is enough to find everything on file for it (every lifecycle
    episode, plus the full raw snapshot timeline - both tables log a
    fresh row every poll cycle while active, so this is a real
    timeline, not just a start/end pair). FPL/JEA/Talquin/City of
    Tallahassee/FPUC's combined-territory view/PRECO never give us a
    discrete incident identity, only a county-level rollup, so a
    specific occurrence there is identified by (county, start_time)
    instead - the same natural key their own outage_events-shaped
    tables' unique index already enforces. Tallahassee moved from the
    first group to this one 2026-07-18 - see fetch_tallahassee_outages.
    get_rollup_summary() for why its old incident-level design never
    actually worked.
    """
    source = request.args.get("source", "").strip().lower()
    db = OutageDatabase()

    detail = None
    if source in ("teco", "duke", "fpuc_incident", "lwbu_incident"):
        incident_id = request.args.get("incident_id", "").strip()
        if incident_id:
            detail_fns = {
                "teco": db.get_teco_incident_detail,
                "duke": db.get_duke_incident_detail,
                "fpuc_incident": db.get_fpuc_incident_detail,
                "lwbu_incident": db.get_lwbu_incident_detail,
            }
            raw_detail = detail_fns[source](incident_id)
            if raw_detail["events"] or raw_detail["history"]:
                detail = raw_detail
    elif source in ("fpl", "jea", "talquin", "tallahassee", "fpuc", "preco", "fkec", "tcec", "erec", "chelco", "gcec", "lwbu", "ouc", "lcec"):
        county = request.args.get("county", "").strip()
        start_time = request.args.get("start_time", "").strip()
        if county and start_time:
            utility_fns = {
                "fpl": (FPL_UTILITY_NAME, db.get_fpl_outage_detail),
                "jea": (JEA_UTILITY_NAME, db.get_jea_outage_detail),
                "talquin": (TALQUIN_UTILITY_NAME, db.get_talquin_outage_detail),
                "tallahassee": (TALLAHASSEE_UTILITY_NAME, db.get_tallahassee_outage_detail),
                "fpuc": (FPUC_UTILITY_NAME, db.get_fpuc_outage_detail),
                "preco": (PRECO_UTILITY_NAME, db.get_preco_outage_detail),
                "fkec": (FKEC_UTILITY_NAME, db.get_fkec_outage_detail),
                "tcec": (TCEC_UTILITY_NAME, db.get_tcec_outage_detail),
                "erec": (EREC_UTILITY_NAME, db.get_erec_outage_detail),
                "chelco": (CHELCO_UTILITY_NAME, db.get_chelco_outage_detail),
                "gcec": (GCEC_UTILITY_NAME, db.get_gcec_outage_detail),
                "lwbu": (LWBU_UTILITY_NAME, db.get_lwbu_outage_detail),
                "ouc": (OUC_UTILITY_NAME, db.get_ouc_outage_detail),
                "lcec": (LCEC_UTILITY_NAME, db.get_lcec_outage_detail),
            }
            utility, get_fn = utility_fns[source]
            detail = get_fn(utility, county, start_time)

    db.close()

    if detail:
        if source in ("teco", "duke", "fpuc_incident", "lwbu_incident"):
            for ev in detail["events"]:
                ev["duration"] = _duration_since(ev["start_time"], ev["end_time"])
        else:
            detail["event"]["duration"] = _duration_since(detail["event"]["start_time"], detail["event"]["end_time"])

    return render_template(
        "incident.html",
        source=source,
        detail=detail,
        incident_id=request.args.get("incident_id", "").strip(),
        county=request.args.get("county", "").strip(),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)

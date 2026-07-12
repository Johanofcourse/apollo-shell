import os
import sqlite3
import sys
import time
from datetime import datetime

from flask import Flask, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from correlate import (
    find_correlations, correlation_summary,
    find_teco_correlations, teco_correlation_summary,
    find_duke_correlations, duke_correlation_summary,
    find_jea_correlations,
)


app = Flask(__name__)

# find_correlations()/find_teco_correlations()/find_duke_correlations()/
# find_jea_correlations() each nested-loop the *entire* raw history of
# their source table against every weather alert in plain Python -
# measured 2026-07-12 at ~35s combined once outages/teco_incidents/
# duke_incidents grew into the tens of thousands of rows (a fresh row
# gets logged every 15-min poll cycle per county/incident, forever, so
# this only gets slower over time). The underlying data only actually
# changes once per poll cycle, but the dashboard auto-refreshes every
# 60s (see the <meta http-equiv="refresh"> in dashboard.html), so most
# reloads were recomputing an answer that couldn't have changed. Cached
# here with a short TTL rather than rewriting the matching into SQL -
# a much smaller, lower-risk fix for the same practical problem.
CORRELATION_CACHE_TTL_SECONDS = 300
_correlation_cache = {"computed_at": 0.0, "data": None}


def _get_cached_correlations(db_path):
    now = time.time()
    if _correlation_cache["data"] is not None and (now - _correlation_cache["computed_at"]) < CORRELATION_CACHE_TTL_SECONDS:
        return _correlation_cache["data"]

    data = (
        find_correlations(db_path),
        find_teco_correlations(db_path),
        find_duke_correlations(db_path),
        find_jea_correlations(db_path),
    )
    _correlation_cache["data"] = data
    _correlation_cache["computed_at"] = now
    return data


def _duration_since(start_iso, end_iso=None):
    """
    Human-readable duration between two ISO timestamps (or start_iso and
    now, if end_iso is omitted).
    """
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso) if end_iso else datetime.now()
    total_minutes = int((end - start).total_seconds() // 60)

    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _humanize_timestamp(ts):
    """
    Turn a raw ISO timestamp ("2026-07-02T01:19:57.483375" or, for
    weather alerts, "2026-07-04T02:01:00-04:00") into plain prose
    ("July 2, 2026, 1:19 AM") for display. The duration/"ago" columns
    elsewhere (_duration_since) are unaffected - this is only for the
    absolute-time columns that used to show the raw ISO string as-is.
    """
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    return dt.strftime("%B %-d, %Y, %-I:%M %p")


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
    (FPL + TECO + Duke) into one combined breakdown, for a single
    state-wide summary bar.
    """
    combined = {}
    for matches in match_lists:
        for match in matches:
            confidence = match["confidence"]
            combined[confidence] = combined.get(confidence, 0) + 1
    return combined


def _percentage_tier(percentage_out):
    """
    Bucket a peak-percentage-out value into a severity tier for a
    colored badge. Real Florida outage percentages are rarely above
    20-30% outside of a major hurricane, so a plain 0-100 linear bar
    would look nearly empty for almost every real row - a discrete tier
    badge reads much better than a proportional bar at these scales.
    """
    if percentage_out is None:
        return "unknown"
    if percentage_out >= 30:
        return "critical"
    if percentage_out >= 10:
        return "high"
    if percentage_out >= 2:
        return "medium"
    return "low"


def _build_unified_view(open_events, teco_open_events, duke_open_events, jea_open_events):
    """
    Normalize FPL's/JEA's county-level outage_events-shaped tables and
    TECO's/Duke's incident-level *_incident_events into one common shape
    for an at-a-glance, all-utilities table. Deliberately keeps only the
    fields all sources actually have (utility, county, customers
    affected, when it started, how long it's been going) - the richer
    per-source fields (TECO's/Duke's cause/ETR, FPL's/JEA's percentage-
    of-county) stay in their own detailed sections below, not squeezed
    in here.
    """
    unified = []

    for e in open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in teco_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["peak_customer_count"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in duke_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["peak_customer_count"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    for e in jea_open_events:
        unified.append({
            "utility": e["utility"],
            "county": e["county"],
            "customers": e["peak_customers_out"],
            "start_time": e["start_time"],
            "duration": e["duration"],
        })

    unified.sort(key=lambda row: row["customers"] or 0, reverse=True)
    return unified


@app.route("/")
def index():
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

    pipeline_health = db.get_pipeline_health(sources=["fpl", "weather", "teco", "duke", "jea", "correlation"])
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

    matches, teco_matches, duke_matches, jea_matches = _get_cached_correlations(db_path)

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

    unified_open = _build_unified_view(open_events, teco_open_events, duke_open_events, jea_open_events)

    for event in open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in jea_open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in jea_closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])

    # Pipeline health strip - surfaces caught fetch/correlation failures
    # (see OutageDatabase.log_pipeline_error/get_pipeline_health) that
    # used to only ever exist as a print() line in a growing text log
    # file nobody was watching.
    source_display_names = {
        "fpl": "FPL",
        "weather": "NWS Weather",
        "teco": "TECO",
        "duke": "Duke Energy",
        "jea": "JEA",
        "correlation": "Correlation",
    }
    for source, info in pipeline_health.items():
        info["display_name"] = source_display_names.get(source, source.title())
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
    combined_confidence = _combine_confidence_breakdowns(matches, teco_matches, duke_matches, jea_matches)
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
        unified_open=unified_open,
        total_customers_affected=total_customers_affected,
        worst_row=worst_row,
        combined_confidence_bar=combined_confidence_bar,
        combined_confidence_display=combined_confidence_display,
        pipeline_health_list=pipeline_health_list,
        any_pipeline_issue=any_pipeline_issue,
        heat_summary=heat_summary,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


HISTORICAL_DB_PATH = "historical_consolidated.db"


def _available_history_counties():
    if not os.path.exists(HISTORICAL_DB_PATH):
        return []
    conn = sqlite3.connect(HISTORICAL_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT county FROM historical_outage_events ORDER BY county")
    counties = [row[0] for row in cursor.fetchall()]
    conn.close()
    return counties


def _all_storms():
    """
    Every storm this project has real data for, across all 67 counties -
    used so a single county's history lists every storm explicitly, even
    the ones where that county has nothing, rather than silently
    omitting them (see _load_history_for_county).
    """
    conn = sqlite3.connect(HISTORICAL_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT storm_name, storm_year FROM historical_outage_events
        ORDER BY storm_year, storm_name
    ''')
    storms = [{"storm_name": row[0], "storm_year": row[1]} for row in cursor.fetchall()]
    conn.close()
    return storms


def _load_history_for_county(county):
    """
    Real historical storm data for one Florida county, from the
    consolidated historical database (see
    apollo_shell/consolidate_historical.py) - built from the 17
    independently-verified per-storm databases, never the raw per-storm
    files directly. County names in this table are stored upper-case (an
    artifact of the PSC report parser), so the lookup is case-insensitive -
    a user typing "Miami-Dade" still matches the stored "MIAMI-DADE".

    Returns every storm this project has data for (see _all_storms()),
    not just the ones with a report for this specific county - a storm
    with nothing for this county gets an explicit has_data=False entry
    instead of being silently left out. "No report for this storm" and
    "confirmed unaffected by this storm" are different claims, and only
    listing storms with data blurred that distinction (this is the same
    lesson the Miami-Dade bug hunt turned up - see docs/documentation.md).
    """
    conn = sqlite3.connect(HISTORICAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
        SELECT storm_name, storm_year, utility, start_time, end_time,
               peak_customers_out, peak_percentage_out, customers_served
        FROM historical_outage_events
        WHERE UPPER(county) = UPPER(?)
        ORDER BY storm_year, peak_percentage_out DESC
    ''', (county,))
    outage_rows = [dict(row) for row in cursor.fetchall()]

    cursor.execute('''
        SELECT storm_name, storm_year, event_type, reported_wind_mph,
               snow_inches, ice_inches, wind_chill_f
        FROM historical_storm_severity
        WHERE UPPER(county) = UPPER(?)
        ORDER BY storm_year
    ''', (county,))
    severity_rows = [dict(row) for row in cursor.fetchall()]

    conn.close()

    # Group both tables by storm so the page can show, per storm: which
    # utilities reported an outage here and how bad it got, plus whatever
    # independent NOAA severity readings exist for the same county/storm -
    # two different sources, shown side by side, never merged into one
    # number.
    storms_by_key = {}

    def _storm_bucket(storm_name, storm_year):
        key = (storm_name, storm_year)
        return storms_by_key.setdefault(key, {
            "storm_name": storm_name,
            "storm_year": storm_year,
            "utilities": [],
            "severity": [],
            "has_data": False,
        })

    for row in outage_rows:
        bucket = _storm_bucket(row["storm_name"], row["storm_year"])
        bucket["utilities"].append(row)
        bucket["has_data"] = True
    for row in severity_rows:
        bucket = _storm_bucket(row["storm_name"], row["storm_year"])
        bucket["severity"].append(row)
        bucket["has_data"] = True

    # Every storm gets a row - _storm_bucket() is a no-op for storms
    # already populated above, and creates an honest has_data=False
    # entry for the rest.
    for storm in _all_storms():
        _storm_bucket(storm["storm_name"], storm["storm_year"])

    return sorted(storms_by_key.values(), key=lambda s: s["storm_year"])


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)

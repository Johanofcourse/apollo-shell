import os
import sqlite3
import sys
from datetime import datetime

from flask import Flask, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from correlate import (
    find_correlations, correlation_summary,
    find_teco_correlations, teco_correlation_summary,
    find_duke_correlations, duke_correlation_summary,
)


app = Flask(__name__)


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


def _build_unified_view(open_events, teco_open_events, duke_open_events):
    """
    Normalize FPL's county-level outage_events and TECO's/Duke's
    incident-level *_incident_events into one common shape for an at-a-
    glance, all-utilities table. Deliberately keeps only the fields all
    three sources actually have (utility, county, customers affected,
    when it started, how long it's been going) - the richer per-source
    fields (TECO's/Duke's cause/ETR, FPL's percentage-of-county) stay in
    their own detailed sections below, not squeezed in here.
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

    matches = find_correlations(db_path)
    correlation = correlation_summary(matches)
    for stats in correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    teco_matches = find_teco_correlations(db_path)
    teco_correlation = teco_correlation_summary(teco_matches)
    for stats in teco_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    duke_matches = find_duke_correlations(db_path)
    duke_correlation = duke_correlation_summary(duke_matches)
    for stats in duke_correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])
        stats["confidence_display"] = _format_confidence(stats["confidence_breakdown"])
        stats["confidence_bar"] = _confidence_bar_segments(stats["confidence_breakdown"])

    unified_open = _build_unified_view(open_events, teco_open_events, duke_open_events)

    for event in open_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])
    for event in closed_events:
        event["severity_tier"] = _percentage_tier(event["peak_percentage_out"])

    # KPI summary strip at the top of the page - a fast, at-a-glance
    # read before scrolling into the detailed per-utility tables below.
    total_customers_affected = sum(row["customers"] or 0 for row in unified_open)
    worst_row = unified_open[0] if unified_open else None
    combined_confidence = _combine_confidence_breakdowns(matches, teco_matches, duke_matches)
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
        unified_open=unified_open,
        total_customers_affected=total_customers_affected,
        worst_row=worst_row,
        combined_confidence_bar=combined_confidence_bar,
        combined_confidence_display=combined_confidence_display,
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


def _load_history_for_county(county):
    """
    Real historical storm data for one Florida county, from the
    consolidated historical database (see
    apollo_shell/consolidate_historical.py) - built from the 17
    independently-verified per-storm databases, never the raw per-storm
    files directly. County names in this table are stored upper-case (an
    artifact of the PSC report parser), so the lookup is case-insensitive -
    a user typing "Miami-Dade" still matches the stored "MIAMI-DADE".
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

    def _storm_bucket(row):
        key = (row["storm_name"], row["storm_year"])
        return storms_by_key.setdefault(key, {
            "storm_name": row["storm_name"],
            "storm_year": row["storm_year"],
            "utilities": [],
            "severity": [],
        })

    for row in outage_rows:
        _storm_bucket(row)["utilities"].append(row)
    for row in severity_rows:
        _storm_bucket(row)["severity"].append(row)

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
    if selected_county and available_counties:
        storms = _load_history_for_county(selected_county)

    return render_template(
        "history.html",
        available_counties=available_counties,
        selected_county=selected_county,
        storms=storms,
        db_missing=not available_counties,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)

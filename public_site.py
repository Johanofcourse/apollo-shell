import json
import os
import sys

from flask import Flask, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from correlate import _county_in_alert
from county_status import (
    COUNTY_PICKER_CHOICES, _real_per_county_open_events,
    _combined_territory_open_events, _rows_for_county, humanize_timestamp,
    _row_tier,
)
from storm_history import available_history_counties, load_history_for_county
import florida_county_paths as county_map

app = Flask(__name__, template_folder="templates_public")
app.jinja_env.filters['humanize'] = humanize_timestamp
app.jinja_env.filters['row_tier'] = _row_tier


def _severity_icon(severity):
    """
    Map a raw NWS alert severity string (Extreme/Severe/Moderate/Minor,
    or missing) to the same critical/high/medium/low/nodata icon
    vocabulary _row_tier() already uses for live outages - one shared
    hazard-icon language across the whole page instead of two.
    """
    mapping = {"extreme": "critical", "severe": "high", "moderate": "medium", "minor": "low"}
    return mapping.get((severity or "").lower(), "nodata")


app.jinja_env.filters['severity_icon'] = _severity_icon

# Genuinely separate from dashboard.py's app - its own Flask instance,
# its own template folder, its own port when run directly. Reads the
# same live/historical databases (via the shared apollo_shell/ data
# layer), but never imports anything from dashboard.py itself, so the
# internal ops tool and this public-facing page can change
# independently of each other. See docs/ROADMAP.md's Phase 4 entry for
# why this exists as a second, separate thing rather than a mode of the
# internal dashboard.


def _statewide_rows(db):
    """
    One pass over every currently-open event, real and combined-
    territory, statewide - the common input for the map data, the hero
    hover, and the narrative summary, so all three describe the exact
    same snapshot rather than three separate queries that could
    disagree with each other by a poll cycle.
    """
    return _real_per_county_open_events(db) + _combined_territory_open_events(db)


def _county_map_data(db, all_rows):
    """
    Per-county data for the client-side map: current live customers/
    served (for the "Live Severity" view) plus the all-time historical
    weather-match confidence tally (for the "Historical Pattern" view -
    precomputed once per poll cycle by main.py's own cycle function and
    just read back here, see database.get_historical_confidence_tally()
    for why this isn't computed at page-load time). Matches
    COUNTY_RINGS's real county names so the client can join the two
    directly.
    """
    tally = db.get_historical_confidence_tally()
    # historical_confidence_tally()'s keys are whatever raw casing each
    # source's own county field happens to use (already-proper-case for
    # some live sources, all-caps for others) - never assume one casing,
    # match case-insensitively instead.
    tally_by_upper = {county.upper(): stats for county, stats in tally.items()}

    # Same case-insensitivity concern for live customer counts - a real
    # per-county source's own casing shouldn't have to match
    # FLORIDA_COUNTY_RINGS's canonical title-case names exactly.
    by_county_customers = {}
    by_county_served = {}
    for r in all_rows:
        county_key = r["county"].upper()
        by_county_customers[county_key] = by_county_customers.get(county_key, 0) + (r.get("customers") or 0)
        if r.get("customers_served"):
            by_county_served[county_key] = by_county_served.get(county_key, 0) + r["customers_served"]

    counties = []
    for name in county_map.FLORIDA_COUNTY_RINGS:
        confidence = tally_by_upper.get(name.upper(), {})
        counties.append({
            "name": name,
            "customers": by_county_customers.get(name.upper(), 0),
            "served": by_county_served.get(name.upper(), 0),
            "high": confidence.get("high", 0),
            "medium": confidence.get("medium", 0),
            "low": confidence.get("low", 0),
        })
    return counties


def _narrative_stats(all_rows):
    """
    Real statewide numbers for the plain-language summary paragraph -
    total customers currently out, the single worst county/utility by
    raw count, and (only among counties/utilities with a real known
    customer base) the worst by percentage. Never invents a percentage
    for a source that doesn't carry a real customer base (TECO, Duke,
    City of Tallahassee, FPUC's incident-level view) - those are
    counted in every raw total but deliberately left out of every
    percentage figure, same honest split the map's own "what counts as
    a customer" note already draws.
    """
    total_current = sum(r.get("customers") or 0 for r in all_rows)

    by_county = {}
    by_utility = {}
    for r in all_rows:
        c = by_county.setdefault(r["county"], {"customers": 0, "known_customers": 0, "known_served": 0})
        u = by_utility.setdefault(r["utility"], {"customers": 0, "known_customers": 0, "known_served": 0})
        customers = r.get("customers") or 0
        c["customers"] += customers
        u["customers"] += customers
        if r.get("customers_served"):
            c["known_customers"] += customers
            c["known_served"] += r["customers_served"]
            u["known_customers"] += customers
            u["known_served"] += r["customers_served"]

    def _worst_by_count(groups):
        if not groups:
            return None, 0
        name, stats = max(groups.items(), key=lambda kv: kv[1]["customers"])
        return name, stats["customers"]

    def _worst_by_pct(groups):
        known = {k: v for k, v in groups.items() if v["known_served"] > 0}
        if not known:
            return None, None
        name, stats = max(known.items(), key=lambda kv: kv[1]["known_customers"] / kv[1]["known_served"])
        return name, stats["known_customers"] / stats["known_served"] * 100

    worst_county_name, worst_county_customers = _worst_by_count(by_county)
    worst_pct_county_name, worst_pct_value = _worst_by_pct(by_county)
    top_utility_name, top_utility_customers = _worst_by_count(by_utility)
    top_pct_utility_name, top_pct_utility_value = _worst_by_pct(by_utility)

    known_rows = [r for r in all_rows if r.get("customers_served")]
    total_known_served = sum(r["customers_served"] for r in known_rows)
    total_current_known_base = sum(r.get("customers") or 0 for r in known_rows)
    overall_pct = (total_current_known_base / total_known_served * 100) if total_known_served else 0.0

    return {
        "total_current": total_current,
        "total_known_served": total_known_served,
        "overall_pct": overall_pct,
        "worst_county_name": worst_county_name,
        "worst_county_customers": worst_county_customers,
        "worst_pct_county_name": worst_pct_county_name,
        "worst_pct_value": worst_pct_value,
        "top_utility_name": top_utility_name,
        "top_utility_customers": top_utility_customers,
        "top_pct_utility_name": top_pct_utility_name,
        "top_pct_utility_value": top_pct_utility_value,
    }


@app.route("/")
def index():
    """
    The public-facing page: a real, isometric Florida county map
    (toggle between its all-time historical weather-match pattern and
    current live severity), a plain-language narrative summary built
    from the same live snapshot, this month's heat advisories, current
    weather alerts, and - once a county is picked, by clicking the map
    or the search box - that county's live status plus its real
    historical storm pattern.

    Deliberately shows only derived/aggregated data, never a raw
    utility feed pass-through - same standing policy as the internal
    dashboard, see docs/ROADMAP.md's "Explicitly not planned" section.
    """
    db = OutageDatabase()
    all_rows = _statewide_rows(db)
    counties_data = _county_map_data(db, all_rows)
    narrative = _narrative_stats(all_rows)
    heat_summary = db.get_heat_advisory_summary()

    all_active_alerts = db.get_active_weather_alerts()
    # areas is a raw "Area One; Area Two; ..." string in the underlying
    # table - _county_in_alert() matches directly against that string,
    # but display needs a real list, not Jinja iterating character by
    # character over an un-split string.
    for a in all_active_alerts:
        a["areas_list"] = [part.strip() for part in a["areas"].split(";") if part.strip()]

    selected_county = request.args.get("county", "").strip()
    county_detail = None

    if selected_county:
        real_events = _rows_for_county(_real_per_county_open_events(db), selected_county)
        combined_events = _rows_for_county(_combined_territory_open_events(db), selected_county)
        active_alerts = [a for a in all_active_alerts if _county_in_alert(selected_county, a["areas"])]
        for a in active_alerts:
            a["is_heat"] = a["event_type"] in ("Heat Advisory", "Excessive Heat Warning")

        real_events.sort(key=lambda r: r["customers"] or 0, reverse=True)
        combined_events.sort(key=lambda r: r["customers"] or 0, reverse=True)

        has_history = selected_county.upper() in {c.upper() for c in available_history_counties()}
        storms = load_history_for_county(selected_county) if has_history else []
        storms_with_data_count = sum(1 for s in storms if s["has_data"])

        county_detail = {
            "real_events": real_events,
            "combined_events": combined_events,
            "active_alerts": active_alerts,
            "storms": storms,
            "storms_with_data_count": storms_with_data_count,
        }

    db.close()

    return render_template(
        "index.html",
        counties_json=json.dumps(counties_data),
        county_rings_json=json.dumps(county_map.FLORIDA_COUNTY_RINGS),
        narrative=narrative,
        heat_summary=heat_summary,
        active_alerts=all_active_alerts,
        available_counties=COUNTY_PICKER_CHOICES,
        selected_county=selected_county,
        county_detail=county_detail,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PUBLIC_SITE_PORT", 5051))
    app.run(debug=False, port=port)

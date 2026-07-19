import json
import os
import subprocess
import sys

from flask import Flask, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from correlate import _county_in_alert
from county_status import (
    COUNTY_PICKER_CHOICES, _real_per_county_open_events,
    _combined_territory_open_events, _real_per_county_closed_events,
    _combined_territory_closed_events, _rows_for_county, humanize_timestamp,
    _row_tier, fpl_ordinary_restoration_stats,
    teco_etr_accuracy, TECO_UTILITY_NAME,
    duke_restoration_precedent, DUKE_UTILITY_NAME,
    lwbu_etr_accuracy,
)
from storm_history import (
    available_history_counties, load_history_for_county,
    fpl_restoration_precedent, fpl_restoration_precedent_by_wind_severity, FPL_UTILITY_NAME,
    jea_restoration_precedent, JEA_UTILITY_NAME,
)
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

# Most recent resolved outages shown per county in the Outage History
# section - a high-churn county's full history could otherwise be an
# unbounded scroll; this is a display cap, not a data limitation (see
# county_status._CLOSED_EVENTS_LIMIT for the query-side cap).
OUTAGE_HISTORY_DISPLAY_LIMIT = 15

# Real count of independently-integrated live utility sources, for the
# narrative summary's "We track N utilities" line. Kept as an explicit
# constant here rather than inferred from live data (a utility with
# zero currently-open outages would otherwise silently undercount) -
# deliberately not imported from dashboard.py's own
# PIPELINE_SOURCE_DISPLAY_NAMES, since the two apps share only the
# read-only apollo_shell/ data layer, never each other's code.
TRACKED_UTILITY_COUNT = 16


# Real semver, not decorative - 0.x specifically means "pre-1.0, no
# stability contract yet," which is honestly true right now (no domain,
# no HTTPS, dashboard is SSH-tunnel-only, no real prod/test split - see
# Phase 6 in docs/ROADMAP.md). The patch number below stays fully
# automatic forever; this prefix is the one piece meant to be bumped by
# hand, once, deliberately, the day this project actually goes live -
# semver's 0.x -> 1.0 jump is supposed to be a real decision everywhere,
# never an automatic one, so this isn't a limitation of the scheme.
SENTINEL_VERSION_PREFIX = "0.1"


def _get_sentinel_version():
    """
    Real build identifier for the page footer/header - SENTINEL_VERSION_PREFIX
    plus the total commit count on this checkout as the patch number,
    computed once at process start (not per request, since it only
    changes on a fresh deploy, which already requires a restart). Falls
    back to "dev" if git isn't available (e.g. a stripped deployment
    with no .git directory), rather than showing a fabricated number.
    """
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5, check=True,
        )
        return f"{SENTINEL_VERSION_PREFIX}.{result.stdout.strip()}"
    except (subprocess.SubprocessError, OSError):
        return "dev"


SENTINEL_VERSION = _get_sentinel_version()

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
        # A source's county can legitimately come back missing for a single
        # event (e.g. Duke's reverse-geocode occasionally can't resolve a
        # lat/lon) - the event still counts in every other total elsewhere,
        # it just can't be placed on this per-county map.
        if not r.get("county"):
            continue
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
        # A missing county (e.g. Duke's reverse-geocode occasionally can't
        # resolve a lat/lon) still counts in total_current above, but must
        # not become its own fake "county" bucket here - it could otherwise
        # win "worst county" and print None in the public narrative.
        if not r.get("county"):
            continue
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

        # Restoration precedent (Phase 3) - two deliberately separate,
        # distinctly-labeled numbers, only shown when there's a real,
        # currently-open FPL outage in this county right now, not as a
        # standalone historical curiosity. FPL's live feed can never
        # support real incident-level restoration modeling, so these are
        # the honest substitute - "Major Storms" from the 17-storm PSC
        # archive (see storm_history.fpl_restoration_precedent()) and
        # "Everyday Outages" from this project's own live tracking (see
        # county_status.fpl_ordinary_restoration_stats()). Never merged
        # into one number - they honestly answer different questions.
        fpl_open_now = any(r["utility"] == FPL_UTILITY_NAME for r in real_events)
        major_storm_precedent = fpl_restoration_precedent(selected_county) if fpl_open_now else None
        major_storm_by_severity = fpl_restoration_precedent_by_wind_severity(selected_county) if fpl_open_now else None
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
        # like FPL's "Everyday Outages" instead. Not paired with a
        # "Major Storms" sibling the way FPL's is - Duke has no storm
        # archive counterpart - and doesn't need FPL's outlier filter,
        # since its incidents are already real and individually clean
        # (checked directly: 7,195 real closed incidents statewide, only
        # 1 over 48 hours). See county_status.duke_restoration_precedent().
        duke_open_now = any(r["utility"] == DUKE_UTILITY_NAME for r in real_events)
        duke_precedent = duke_restoration_precedent(selected_county, db) if duke_open_now else None

        # JEA gets FPL's shape, not TECO's/Duke's - same structural limit
        # as FPL (jea_outage_events is a county-wide rollup, no
        # per-incident data at all). Unlike FPL, JEA's live event volume
        # is too thin right now for an "Everyday Outages" companion (2
        # closed events statewide, checked 2026-07-18), so this ships
        # historical-only for now. See storm_history.jea_restoration_precedent().
        jea_open_now = any(r["utility"] == JEA_UTILITY_NAME for r in real_events)
        jea_precedent = jea_restoration_precedent(selected_county) if jea_open_now else None

        # LWBU gets the same accuracy-check shape as TECO - real,
        # individually-tracked incidents with a real ETR field. Judged
        # too thin to build when TECO's version shipped (8 closed
        # incidents); revisited once it reached 12. See
        # county_status.lwbu_etr_accuracy().
        #
        # Gating deliberately checks db.get_lwbu_open_incidents() directly,
        # NOT real_events - real_events is built from get_lwbu_open_events()
        # (the county-rollup table), which _real_per_county_open_events()
        # uses for LWBU by design (see its own docstring), while this
        # feature is about the SEPARATE per-incident table. The two can
        # genuinely disagree (confirmed live 2026-07-18: an open incident
        # existed with zero open rollup rows at the same moment) - gating
        # on the wrong one would have silently hidden this feature every
        # time that happened.
        lwbu_open_now = any(
            (i["county"] or "").upper() == selected_county.upper()
            for i in db.get_lwbu_open_incidents()
        )
        lwbu_accuracy = lwbu_etr_accuracy(selected_county, db) if lwbu_open_now else None

        # This project's own directly-observed outage history for this
        # county (real start/end pairs from the live poller, running
        # since 2026-04) - a genuinely different dataset from Storm
        # History below (independently-sourced, backfilled 2018-2025).
        # Capped to the most recent OUTAGE_HISTORY_DISPLAY_LIMIT per
        # group so a high-churn county's page doesn't turn into an
        # unbounded scroll.
        closed_events = _rows_for_county(_real_per_county_closed_events(db), selected_county)
        combined_closed_events = _rows_for_county(_combined_territory_closed_events(db), selected_county)
        closed_events.sort(key=lambda r: r["end_time"] or "", reverse=True)
        combined_closed_events.sort(key=lambda r: r["end_time"] or "", reverse=True)
        closed_events_total = len(closed_events)
        combined_closed_events_total = len(combined_closed_events)
        closed_events = closed_events[:OUTAGE_HISTORY_DISPLAY_LIMIT]
        combined_closed_events = combined_closed_events[:OUTAGE_HISTORY_DISPLAY_LIMIT]

        has_history = selected_county.upper() in {c.upper() for c in available_history_counties()}
        storms = load_history_for_county(selected_county) if has_history else []
        storms_with_data_count = sum(1 for s in storms if s["has_data"])

        county_detail = {
            "real_events": real_events,
            "combined_events": combined_events,
            "active_alerts": active_alerts,
            "closed_events": closed_events,
            "closed_events_total": closed_events_total,
            "combined_closed_events": combined_closed_events,
            "combined_closed_events_total": combined_closed_events_total,
            "storms": storms,
            "storms_with_data_count": storms_with_data_count,
            "major_storm_precedent": major_storm_precedent,
            "major_storm_by_severity": major_storm_by_severity,
            "everyday_precedent": everyday_precedent,
            "teco_accuracy": teco_accuracy,
            "duke_precedent": duke_precedent,
            "jea_precedent": jea_precedent,
            "lwbu_accuracy": lwbu_accuracy,
        }

    db.close()

    return render_template(
        "index.html",
        counties_json=json.dumps(counties_data),
        county_rings_json=json.dumps(county_map.FLORIDA_COUNTY_RINGS),
        narrative=narrative,
        tracked_utility_count=TRACKED_UTILITY_COUNT,
        sentinel_version=SENTINEL_VERSION,
        heat_summary=heat_summary,
        active_alerts=all_active_alerts,
        available_counties=COUNTY_PICKER_CHOICES,
        available_counties_json=json.dumps(COUNTY_PICKER_CHOICES),
        selected_county=selected_county,
        county_detail=county_detail,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PUBLIC_SITE_PORT", 5051))
    app.run(debug=False, port=port)

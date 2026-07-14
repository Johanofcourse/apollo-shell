import os
import sys

from flask import Flask, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from correlate import _county_in_alert
from county_status import (
    COUNTY_PICKER_CHOICES, county_verdict, _real_per_county_open_events,
    _combined_territory_open_events, _rows_for_county, humanize_timestamp,
)
from storm_history import available_history_counties, load_history_for_county
import florida_county_paths as county_map

app = Flask(__name__, template_folder="templates_public")
app.jinja_env.filters['humanize'] = humanize_timestamp

# Genuinely separate from dashboard.py's app - its own Flask instance,
# its own template folder, its own port when run directly. Reads the
# same live/historical databases (via the shared apollo_shell/ data
# layer), but never imports anything from dashboard.py itself, so the
# internal ops tool and this public-facing page can change
# independently of each other. See docs/ROADMAP.md's Phase 4 entry for
# why this exists as a second, separate thing rather than a mode of the
# internal dashboard.

VERDICT_ORDER = ["clear", "low", "medium", "high", "critical"]

# Map fill colors per verdict tier - a restrained, semantic palette
# (not the same accent color used for anything decorative elsewhere on
# the page), consistent with this project's "form encodes state, not
# just the number" principle already established on the internal tool.
VERDICT_COLORS = {
    "clear": "#1b2a3a",
    "low": "#2c6e49",
    "medium": "#c9a227",
    "high": "#d97b29",
    "critical": "#c0392b",
    "no-source": "#232f3d",
}


def _statewide_snapshot(db):
    """
    One pass over every currently-open event, real and combined-
    territory, statewide - used to build both the per-county map
    coloring and the hero KPIs from the same fetched data, rather than
    querying twice.
    """
    real_rows = _real_per_county_open_events(db)
    combined_rows = _combined_territory_open_events(db)

    verdicts = {}
    for county in COUNTY_PICKER_CHOICES:
        verdicts[county] = county_verdict(
            _rows_for_county(real_rows, county),
            _rows_for_county(combined_rows, county),
        )

    total_customers_affected = sum(
        (r.get("customers") or 0) for r in real_rows + combined_rows
    )
    counties_with_issue = sum(1 for v in verdicts.values() if v != "clear")

    return {
        "verdicts": verdicts,
        "total_customers_affected": total_customers_affected,
        "counties_with_issue": counties_with_issue,
        "counties_clear": len(verdicts) - counties_with_issue,
        "total_counties": len(verdicts),
    }


@app.route("/")
def index():
    """
    The public-facing page: a real Florida county map colored by
    current live status, a plain-language hero summary, this month's
    heat advisories, and - once a county is picked, by clicking the map
    or the search box - that county's live status plus its real
    historical storm pattern.

    Deliberately shows only derived/aggregated data, never a raw
    utility feed pass-through - same standing policy as the internal
    dashboard, see docs/ROADMAP.md's "Explicitly not planned" section.
    """
    db = OutageDatabase()
    snapshot = _statewide_snapshot(db)
    heat_summary = db.get_heat_advisory_summary()

    selected_county = request.args.get("county", "").strip()
    county_detail = None

    if selected_county:
        real_events = _rows_for_county(_real_per_county_open_events(db), selected_county)
        combined_events = _rows_for_county(_combined_territory_open_events(db), selected_county)
        all_active_alerts = db.get_active_weather_alerts()
        active_alerts = [a for a in all_active_alerts if _county_in_alert(selected_county, a["areas"])]
        for a in active_alerts:
            a["is_heat"] = a["event_type"] in ("Heat Advisory", "Excessive Heat Warning")

        real_events.sort(key=lambda r: r["customers"] or 0, reverse=True)
        combined_events.sort(key=lambda r: r["customers"] or 0, reverse=True)

        has_history = selected_county.upper() in {c.upper() for c in available_history_counties()}
        storms = load_history_for_county(selected_county) if has_history else []
        storms_with_data_count = sum(1 for s in storms if s["has_data"])

        county_detail = {
            "verdict": snapshot["verdicts"].get(selected_county, "clear"),
            "real_events": real_events,
            "combined_events": combined_events,
            "active_alerts": active_alerts,
            "storms": storms,
            "storms_with_data_count": storms_with_data_count,
        }

    db.close()

    return render_template(
        "index.html",
        snapshot=snapshot,
        heat_summary=heat_summary,
        available_counties=COUNTY_PICKER_CHOICES,
        selected_county=selected_county,
        county_detail=county_detail,
        county_paths=county_map.FLORIDA_COUNTY_PATHS,
        map_bounds=county_map.FLORIDA_MAP_BOUNDS,
        verdict_colors=VERDICT_COLORS,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PUBLIC_SITE_PORT", 5051))
    app.run(debug=False, port=port)

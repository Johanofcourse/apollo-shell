import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

# Fully public, unauthenticated static JSON file - no tracking code, no
# session token, no special headers required, same platform LCEC runs
# on (see LCEC_API_URL in .env.example), just Clay's own instance slug
# in the path. Kept in .env anyway for consistency with every other
# utility here, not because it's secret.
CLAY_API_URL = os.environ.get("CLAY_API_URL")

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data
# ("Clay Electric Cooperative").
UTILITY_NAME = "Clay Electric Cooperative"


def fetch_clay_outages():
    """
    Fetches live outage data from Clay's outage-map endpoint. Returns
    the parsed JSON data, or None on failure/missing config.
    """
    if not CLAY_API_URL:
        print("CLAY_API_URL not set - skipping Clay fetch")
        return None

    try:
        print("Fetching Clay Electric outage data...")
        response = requests.get(CLAY_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Clay data: {e}")
        return None


def outages_to_records(data):
    """
    Convert Clay's raw JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect
    (county/customers_out/customers_served). The real per-county
    numbers live in regionDataSets, under the entry with
    id == "Counties" (LCEC's identical platform uses "County",
    singular - confirmed by checking Clay's own real response rather
    than assuming the same key name). The same response also breaks
    the exact same totals down by internal "district" name - richer
    detail not currently used here.

    The response also includes a top-level "outages" array with real
    per-incident detail - see incidents_to_records() below, which reads
    the same raw response this function does.
    """
    records = []
    region_datasets = (data or {}).get("regionDataSets", [])
    county_dataset = next((d for d in region_datasets if d.get("id") == "Counties"), None)
    if not county_dataset:
        return records

    for region in county_dataset.get("regions", []):
        records.append({
            "county": region.get("id") or "",
            "customers_out": region.get("numberOut") or 0,
            "customers_served": region.get("numberServed") or 0,
        })
    return records


def _epoch_ms_to_iso(value):
    """
    Clay's own timeOff/estimateTime fields are epoch milliseconds, not
    ISO strings like every other timestamp in this project - converted
    here once, at the source, rather than carrying raw epoch-ms deeper
    into the codebase. Returns None for a missing/invalid value rather
    than raising, since estimateTime in particular isn't always present
    (no crew assigned yet, no estimate to give).
    """
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def incidents_to_records(data):
    """
    Convert Clay's raw "outages" array into real per-incident records:
    incident_id, customer_count, start_time (Clay's own real timeOff -
    more accurate than the "first time we happened to poll it"
    fallback TECO/Duke need, since their feeds don't give a true start
    time at all), estimated_restoration, crew_assigned, planned.

    Deliberately NO county field. Clay's raw x/y turned out not to be
    resolvable to a real county - checked directly (a real ground-truth
    point, a live coordinate transform, and a dig through the site's
    own JS for the actual rendering logic), not assumed impossible.
    Carrying raw_x/raw_y through anyway, unused for now, so a future
    session that does solve the transform doesn't need to re-fetch
    anything - the raw values are already sitting in the database.
    """
    records = []
    for o in (data or {}).get("outages", []):
        records.append({
            "incident_id": o.get("id"),
            "utility": UTILITY_NAME,
            "customer_count": o.get("nbrOut") or 0,
            "start_time": _epoch_ms_to_iso(o.get("timeOff")),
            "estimated_restoration": _epoch_ms_to_iso(o.get("estimateTime")),
            "crew_assigned": bool(o.get("crewAssigned")),
            "planned": bool(o.get("planned")),
            "raw_x": o.get("x"),
            "raw_y": o.get("y"),
        })
    return records


def get_clay_records():
    """
    Fetch and parse current Clay county-level outage records in one
    call.
    """
    return outages_to_records(fetch_clay_outages())


def get_clay_incident_records():
    """
    Fetch and parse current Clay per-incident records in one call.
    """
    return incidents_to_records(fetch_clay_outages())


def main():
    """
    Test function - displays current Clay Electric outage data
    """
    print("=" * 70)
    print("CLAY ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_clay_records()
    if not records:
        print("\nNo Clay data fetched.")
    else:
        total_out = sum(r["customers_out"] for r in records)
        total_served = sum(r["customers_served"] for r in records)
        print(f"\n{len(records)} counties tracked, {total_out:,} of {total_served:,} customers affected\n")
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

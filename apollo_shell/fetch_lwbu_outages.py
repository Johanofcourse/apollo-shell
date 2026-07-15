import os

import requests
from dotenv import load_dotenv

from fetch_teco_outages import categorize_reason

load_dotenv()

# A set of plain static JSON files under one base path - not an
# officially documented public API, kept out of the committed code (this
# repo is public), same as every other utility here. No auth, no
# cookie, no special headers needed at all - confirmed against a real
# response using nothing but a plain GET.
LWBU_API_BASE = os.environ.get("LWBU_API_BASE")

UTILITY_NAME = "Lake Worth Beach Utilities"

# Lake Worth Beach Utilities' entire real service territory is a small
# coastal footprint confirmed to sit inside a single real county -
# verified two ways: the service-area boundary's own coordinates never
# leave a ~0.1-degree box around 26.6N/-80.08W, and reverse-geocoding a
# real live outage point through the same FCC lookup_county() every
# other utility here uses returned Palm Beach directly. One real
# county, so this is a hardcoded constant, not a per-record geocode.
SERVICE_COUNTY = "Palm Beach"


def _get(path):
    if not LWBU_API_BASE:
        return None
    url = f"{LWBU_API_BASE}/{path}?v=2"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Lake Worth Beach Utilities data ({path}): {e}")
        return None


def fetch_lwbu_summary():
    """
    Fetch LWBU's real city-wide outage summary (current customers out,
    total customers served) - a genuine top-level total, not something
    summed from parts. Returns the parsed JSON, or None on
    failure/missing config.
    """
    if not LWBU_API_BASE:
        print("LWBU_API_BASE not set - skipping Lake Worth Beach Utilities fetch")
        return None
    print("Fetching Lake Worth Beach Utilities outage summary...")
    return _get("outageSummary.json")


def fetch_lwbu_incidents():
    """
    Fetch LWBU's raw individual outage incidents. Returns a list of raw
    dicts, or an empty list on failure/missing config.
    """
    if not LWBU_API_BASE:
        return []
    data = _get("outages.json")
    if data is None:
        return []
    print(f"Found {len(data)} active Lake Worth Beach Utilities outage incidents")
    return data


def summary_to_records(data):
    """
    Convert LWBU's real outageSummary.json into the same list-of-dicts
    shape OutageDatabase.log_multiple_outages()/sync_outage_events()
    expect - always exactly one record (SERVICE_COUNTY). A real-
    percentage county-rollup source like FKEC/PRECO, not a raw-count
    fallback like Tallahassee/TECO/Duke, since this feed's own
    "customersServed" total is always present, not just during a
    declared event.
    """
    if not data:
        return []

    return [{
        "county": SERVICE_COUNTY,
        "customers_out": data.get("customersOutNow") or 0,
        "customers_served": data.get("customersServed") or 0,
    }]


def parse_incidents(raw_incidents):
    """
    Convert raw LWBU outage incidents into a flat list of dicts, same
    incident shape TECO/Duke/Tallahassee use (utility, incident_id,
    customer_count, lat, lon, county, cause, cause_category) plus real
    fields unique to this source that those don't have: crew_assigned,
    work_status, and streets_affected (joined into one string for
    storage) - all reported directly, no categorization needed the way
    the free-text cause field needs.
    """
    records = []
    for incident in raw_incidents:
        point = incident.get("outagePoint") or {}
        cause = incident.get("cause")

        records.append({
            "utility": UTILITY_NAME,
            "incident_id": incident.get("outageRecID"),
            "customer_count": incident.get("customersOutNow"),
            "lat": point.get("lat"),
            "lon": point.get("lng"),
            "county": SERVICE_COUNTY,
            "cause": cause,
            "cause_category": categorize_reason(cause),
            "crew_assigned": bool(incident.get("crewAssigned")),
            "work_status": incident.get("outageWorkStatus"),
            "streets_affected": ", ".join(incident.get("streetsAffected") or []),
            "is_planned": bool(incident.get("isPlanned")),
            "verified": bool(incident.get("verified")),
            "reported_start_time": incident.get("outageStartTime"),
            "estimated_restoration": incident.get("estimatedTimeOfRestoral"),
        })
    return records


def get_lwbu_records():
    """
    Fetch and parse the current LWBU county-level (real-percentage)
    outage record in one call.
    """
    return summary_to_records(fetch_lwbu_summary())


def get_incidents_summary():
    """
    Fetch and parse current LWBU individual outage incidents in one
    call. Incidents missing a real id are dropped - incident_id is the
    whole basis for lifecycle tracking, same as an unidentifiable
    TECO/Duke/Tallahassee incident would be.
    """
    return [r for r in parse_incidents(fetch_lwbu_incidents()) if r["incident_id"]]


def main():
    """
    Test function - displays current Lake Worth Beach Utilities outage
    data
    """
    print("=" * 70)
    print("LAKE WORTH BEACH UTILITIES LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_lwbu_records()
    if not records:
        print("\nNo LWBU summary data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    incidents = get_incidents_summary()
    if not incidents:
        print("\nNo active LWBU outage incidents.")
    else:
        print(f"\n{len(incidents)} active incidents\n")
        for incident in incidents:
            print(f"  {incident['incident_id']}: {incident['customer_count']} customers on {incident['streets_affected']}")
            print(f"    Cause: {incident['cause']} ({incident['cause_category']}) | Crew assigned: {incident['crew_assigned']} | Status: {incident['work_status']}")
            print()

    print("=" * 70)


if __name__ == "__main__":
    main()

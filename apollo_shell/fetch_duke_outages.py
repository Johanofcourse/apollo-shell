import os

import requests
from dotenv import load_dotenv

from fetch_teco_outages import lookup_county, categorize_reason

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public) the same way the auth token below is,
# loaded from .env instead of hardcoded as a literal string.
DUKE_API_BASE = os.environ.get("DUKE_API_BASE")
DUKE_API_ORIGIN = os.environ.get("DUKE_API_ORIGIN")
JURISDICTION = "DEF"  # Duke's internal code for their Florida jurisdiction
UTILITY_NAME = "Duke Energy"


def _headers():
    token = os.environ.get("DUKE_ENERGY_API_AUTH")
    if not token or not DUKE_API_BASE or not DUKE_API_ORIGIN:
        raise RuntimeError(
            "DUKE_ENERGY_API_AUTH / DUKE_API_BASE / DUKE_API_ORIGIN are not "
            "set. Copy .env.example to .env and fill in the real values."
        )
    return {
        "Accept": "application/json, text/plain, */*",
        "Origin": DUKE_API_ORIGIN,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5.2 Safari/605.1.15",
        "Authorization": token,
    }


def _get(path):
    url = f"{DUKE_API_BASE}/{path}"
    try:
        response = requests.get(url, headers=_headers(), timeout=15)
        response.raise_for_status()
        body = response.json()
        return body.get("data", body)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Duke Energy data ({path}): {e}")
        return None


def fetch_duke_outages():
    """
    Fetch raw individual outage incidents for Duke's FL jurisdiction.
    Returns a list of raw dicts, or an empty list on failure.
    """
    print("Fetching Duke Energy outage incidents...")
    data = _get(f"outages?jurisdiction={JURISDICTION}")
    if data is None:
        return []
    print(f"Found {len(data)} active Duke outage incidents")
    return data


def fetch_duke_counties():
    """
    Fetch per-county rollup data (customers served, ETR/cause/crew
    overrides when a real event is active) for Duke's FL jurisdiction.
    """
    data = _get(f"counties?jurisdiction={JURISDICTION}")
    return data or []


def fetch_duke_system_alerts():
    """
    Fetch system notifications about the outage map's own data
    reliability (e.g. "data may be delayed") - not weather alerts.
    """
    data = _get(f"alerts?jurisdiction={JURISDICTION}")
    return data or []


def fetch_duke_map_status():
    """
    Fetch Duke's own map settings, including the real stormMode flag -
    a direct operational signal for whether Duke is currently treating
    this as a significant event, not something we have to infer.
    """
    return _get(f"mapsettings?jurisdiction={JURISDICTION}") or {}


def parse_incidents(raw_outages):
    """
    Convert raw Duke outage incidents into a flat list of dicts, with a
    reverse-geocoded county and a categorized cause, same helpers TECO
    already uses (Duke's outageCause has only shown "unplanned" so far,
    but the same free-text categorization applies if that ever changes).
    """
    records = []
    for outage in raw_outages:
        lat = outage.get("deviceLatitudeLocation")
        lon = outage.get("deviceLongitudeLocation")
        cause = outage.get("outageCause")

        records.append({
            "utility": UTILITY_NAME,
            "incident_id": outage.get("sourceEventNumber"),
            "customer_count": outage.get("customersAffectedNumber"),
            "lat": lat,
            "lon": lon,
            "county": lookup_county(lat, lon),
            "cause": cause,
            "cause_category": categorize_reason(cause),
        })
    return records


def parse_counties(raw_counties):
    """
    Convert raw Duke county rollup records into a flat list of dicts.
    """
    records = []
    for county in raw_counties:
        summary = county.get("areaOfInterestSummary") or {}
        records.append({
            "utility": UTILITY_NAME,
            "county": county.get("countyName"),
            "area_of_interest_id": county.get("areaOfInterestId"),
            "customers_served": county.get("customersServed"),
            "etr_override": county.get("etrOverride"),
            "cause_code_override": county.get("causeCodeOverride"),
            "crew_status_override": county.get("crewStatusOverride"),
            "customers_affected_override": county.get("customersAffectedOverride"),
            "max_customers_affected": summary.get("maxCustomersAffected"),
            "active_events_count": summary.get("activeEventsCount"),
            "restored_events_count": summary.get("restoredEventsCount"),
            "last_updated": county.get("lastUpdated"),
        })
    return records


def parse_system_alerts(raw_alerts):
    """
    Convert raw Duke system alerts into a flat list of dicts.
    """
    records = []
    for alert in raw_alerts:
        records.append({
            "duke_alert_id": alert.get("id"),
            "title": alert.get("titleText"),
            "description": alert.get("description"),
            "active_indicator": bool(alert.get("activeIndicator")),
            "alert_type": alert.get("alertType"),
            "start_time": alert.get("startTime"),
            "end_time": alert.get("endTime"),
        })
    return records


def get_incidents_summary():
    """
    Fetch and parse current Duke outage incidents in one call.
    """
    return parse_incidents(fetch_duke_outages())


def get_counties_summary():
    """
    Fetch and parse current Duke county rollup data in one call.
    """
    return parse_counties(fetch_duke_counties())


def get_system_alerts_summary():
    """
    Fetch and parse current Duke system alerts in one call.
    """
    return parse_system_alerts(fetch_duke_system_alerts())


def main():
    """
    Test function - displays current Duke Energy outage data
    """
    print("=" * 70)
    print("DUKE ENERGY LIVE OUTAGE DATA (Florida)")
    print("=" * 70)

    status = fetch_duke_map_status()
    print(f"\nStorm mode: {status.get('stormMode')} | {status.get('remarks')}")

    alerts = get_system_alerts_summary()
    active_alerts = [a for a in alerts if a["active_indicator"]]
    if active_alerts:
        print(f"\n{len(active_alerts)} active system alert(s):")
        for a in active_alerts:
            print(f"  {a['title']}")

    incidents = get_incidents_summary()
    if not incidents:
        print("\nNo active Duke outage incidents.")
    else:
        total = sum(i["customer_count"] or 0 for i in incidents)
        print(f"\n{len(incidents)} active incidents, {total} customers affected\n")
        for incident in incidents:
            print(f"  {incident['incident_id']}: {incident['customer_count']} customers")
            print(f"    Cause: {incident['cause']} ({incident['cause_category']})")
            print(f"    Location: {incident['lat']}, {incident['lon']} ({incident['county']} County)")
            print()

    print("=" * 70)


if __name__ == "__main__":
    main()

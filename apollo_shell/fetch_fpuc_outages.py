import os
from datetime import datetime

import requests
from dotenv import load_dotenv

from fetch_teco_outages import lookup_county

load_dotenv()

# Found via Safari Web Inspector, not officially documented - kept out
# of the committed code (this repo is public), same as every other
# utility here. Runs on a fourth distinct vendor platform (DataVoice's
# "Apprise" outage system, outageentry.com - different from FPL/TECO's
# custom builds, Tallahassee's ArcGIS, JEA's Kubra, and Talquin's Siena).
FPUC_API_URL = os.environ.get("FPUC_API_URL")

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data
# ("Florida Public Utilities Corporation").
UTILITY_NAME = "Florida Public Utilities Corporation"

# FPUC's live feed reports ONE combined total across its whole Florida
# electric territory in the same response's top-level stats/customersAffected
# fields - kept as its own tracked entity (see outages_to_records()) since
# it's the one real, authoritative number for the whole utility. Real
# per-county detail lives in this same response's "markers" array instead
# (see markers_to_incidents()) - confirmed real 2026-07-13 once a live
# outage finally populated it (it had only ever been seen empty before
# that, which looked identical to "no per-county data exists at all").
# Historically (PSC storm reports), this utility's real territory spans
# five non-adjacent counties: Calhoun, Jackson, Liberty, Nassau, Wakulla.
# Using a placeholder "county" that deliberately can't match any real
# NWS alert area for the combined total is an honest way to fit it into
# the existing per-source pipeline without fabricating geography for a
# number that is genuinely combined - the incident-level markers get
# their county the same way Duke's incidents do, by reverse-geocoding a
# real lat/lon.
COMBINED_TERRITORY_LABEL = "Multiple Counties (NW FL & Nassau)"

# The multipart form fields the live app itself sends - reproduced
# exactly from a captured real request (2026-07-13), not guessed.
_FORM_BASE = {
    "action": "get",
    "client": "fpuc",
    "target": "cfa_device_markers",
    "serviceIndex": "1",
    "port": "8818",
    "includePrecictions": "false",  # sic - matches the app's own (misspelled) field name
    "includeIndividual": "false",
    "includeComments": "false",
    "devicesToPolygonize": "[]",
    "dataUrl": "null",
}


def fetch_fpuc_outage_summary():
    """
    Fetch FPUC's live combined-territory outage summary. Returns the
    parsed JSON data, or None on failure/missing config.
    """
    if not FPUC_API_URL:
        print("FPUC_API_URL not set - skipping FPUC fetch")
        return None

    try:
        print("Fetching FPUC outage data...")
        response = requests.post(FPUC_API_URL, data=_FORM_BASE, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching FPUC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert FPUC's raw Apprise JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect -
    always exactly one record (see COMBINED_TERRITORY_LABEL), since this
    source has no real per-county breakdown.
    """
    if not data or data.get("result") != "true":
        return []

    service = data.get("0") or {}
    stats = service.get("stats") or {}

    return [{
        "county": COMBINED_TERRITORY_LABEL,
        "customers_out": service.get("customersAffected") or 0,
        "customers_served": stats.get("NumConsumers") or 0,
    }]


def get_fpuc_records():
    """
    Fetch and parse FPUC's current combined-territory outage record in
    one call.
    """
    return outages_to_records(fetch_fpuc_outage_summary())


def _parse_fpuc_start_date(raw):
    """
    FPUC's marker start_date has no year ("07/13 12:52 pm") - assumes
    the current year, which would only be wrong for a report spanning a
    New Year's boundary (not worth over-engineering for). Returns an
    ISO string, or None if the format doesn't match what's been
    observed.
    """
    if not raw:
        return None
    try:
        year = datetime.now().year
        return datetime.strptime(f"{year} {raw}", "%Y %m/%d %I:%M %p").isoformat()
    except ValueError:
        return None


def markers_to_incidents(data):
    """
    Convert FPUC's raw incident markers into the same list-of-dicts
    shape TECO/Duke use (utility, incident_id, customer_count, lat, lon,
    county) - confirmed real 2026-07-13 once a live outage finally
    populated this field (it had only ever been observed empty before).
    County is reverse-geocoded from each marker's own lat/lon, the same
    way Duke's fetch module already does - FPUC's raw feed has no
    county field of its own either.

    IMPORTANT CAVEAT, straight from the app's own config
    (prmCustAppIndividualMsg): "Individual outages are included in the
    total but may not be reflected on the map for privacy." This list
    is real and usable, but not necessarily complete - some real
    outages counted in the combined total (see outages_to_records())
    are deliberately withheld here. Never assume summing this list
    reproduces the combined total exactly.
    """
    if not data or data.get("result") != "true":
        return []

    service = data.get("0") or {}
    incidents = []

    for marker in service.get("markers", []):
        incident_id = marker.get("incident_id")
        if not incident_id:
            continue

        try:
            lat = float(marker["lat"]) if marker.get("lat") is not None else None
            lon = float(marker["lon"]) if marker.get("lon") is not None else None
        except (TypeError, ValueError):
            lat = lon = None

        try:
            customer_count = int(marker.get("consumers_affected") or 0)
        except (TypeError, ValueError):
            customer_count = 0

        incidents.append({
            "utility": UTILITY_NAME,
            "incident_id": incident_id,
            "customer_count": customer_count,
            "lat": lat,
            "lon": lon,
            "county": lookup_county(lat, lon) if lat is not None and lon is not None else None,
            "substation": marker.get("substation"),
            "feeder": marker.get("feeder"),
            "reported_start_time": _parse_fpuc_start_date(marker.get("start_date")),
            "estimated_restoration": marker.get("formatted_ert") or marker.get("estimated_restore_time"),
        })

    return incidents


def get_fpuc_incidents():
    """
    Fetch and parse FPUC's current per-incident markers in one call.
    """
    return markers_to_incidents(fetch_fpuc_outage_summary())


def main():
    """
    Test function - displays current FPUC outage data
    """
    print("=" * 70)
    print("FLORIDA PUBLIC UTILITIES CORPORATION LIVE OUTAGE DATA")
    print("=" * 70)

    data = fetch_fpuc_outage_summary()
    records = outages_to_records(data)
    if not records:
        print("\nNo FPUC data fetched.")
    else:
        r = records[0]
        pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
        print(f"\n{r['customers_out']:,} of {r['customers_served']:,} customers affected ({pct:.2f}%)")
        print("(Combined statewide total - authoritative, but not broken out by county.)")

    incidents = markers_to_incidents(data)
    if not incidents:
        print("\nNo per-incident markers right now (real, but not guaranteed complete - see markers_to_incidents()).")
    else:
        print(f"\n{len(incidents)} real incident(s) with location data:\n")
        for i in incidents:
            print(f"  {i['incident_id']}: {i['customer_count']} customers in {i['county'] or 'unknown county'}")
            print(f"    Substation {i['substation']}, feeder {i['feeder']} - started {i['reported_start_time']}")

    print("=" * 70)


if __name__ == "__main__":
    main()

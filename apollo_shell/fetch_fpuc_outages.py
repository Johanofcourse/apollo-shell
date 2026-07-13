import os

import requests
from dotenv import load_dotenv

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
# electric territory - unlike every other source here, there is no real
# per-county breakdown available (confirmed 2026-07-13: the serviceIndex
# param in the request does nothing, no per-region/substation endpoint
# was found despite a real search of the app's own JS bundle and its
# config - a "Substation" view exists in that config but no active
# outage existed to trigger capturing its actual request). Historically
# (PSC storm reports), this utility's real territory spans five
# non-adjacent counties: Calhoun, Jackson, Liberty, Nassau, Wakulla.
# Using a placeholder "county" that deliberately can't match any real
# NWS alert area is an honest way to fit this into the existing
# per-source pipeline without fabricating geography we don't have -
# weather correlation for this source will always come back empty as a
# direct, self-documenting result, not a special-cased skip.
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


def main():
    """
    Test function - displays current FPUC outage data
    """
    print("=" * 70)
    print("FLORIDA PUBLIC UTILITIES CORPORATION LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_fpuc_records()
    if not records:
        print("\nNo FPUC data fetched.")
    else:
        r = records[0]
        pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
        print(f"\n{r['customers_out']:,} of {r['customers_served']:,} customers affected ({pct:.2f}%)")
        print("(Combined statewide-ish total - no per-county breakdown available from this feed.)")

    print("=" * 70)


if __name__ == "__main__":
    main()

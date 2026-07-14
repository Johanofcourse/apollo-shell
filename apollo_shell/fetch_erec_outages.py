import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Found via Safari Web Inspector, not officially documented - kept out
# of the committed code (this repo is public), same as every other
# utility here. Runs on the exact same vendor platform as TCEC
# (identical outageSummary.json/outagePolygons.json shape, same
# Microsoft IIS server) - just hosted directly off a raw IP:port over
# plain HTTP rather than a proper domain/TLS, which is simply how this
# particular deployment happens to be set up, not something to work
# around. No trackingCode or auth needed, plain GET.
EREC_API_URL = os.environ.get("EREC_API_URL")

# The canonical utility name ("Escambia River Electric Cooperative,
# Inc.") - not present in historical_import.py's PSC-report data (EREC
# wasn't named individually in the 17 backfilled storms), but kept
# consistent with the naming convention every other utility here uses.
UTILITY_NAME = "Escambia River Electric Cooperative, Inc."

# EREC's live feed (outageSummary.json) reports ONE combined total
# across its whole real territory, with no per-county breakdown in that
# response - identical limitation to TCEC (same vendor platform). Real
# per-region detail lives in a sibling endpoint, outagePolygons.json,
# confirmed to exist but seen empty every time so far (zero active
# outages during discovery, 2026-07-13) - its real field shape is
# unknown until a genuine outage populates it, same "wait for a real
# event" situation FPUC's markers array and TCEC's own outagePolygons.json
# were in before/still are.
#
# Real territory confirmed by the user directly: Escambia and Santa
# Rosa counties - a clean two-county case, no partial/uncertain
# coverage the way TCEC's territory had. Using a combined "county"
# label that names the real counties involved (not a placeholder like
# FPUC's non-adjacent-territory one) fits the existing per-source
# pipeline honestly. Weather correlation for this combined view will
# always come back empty (a multi-county label can never match a
# single-county NWS alert's areaDesc string) - same known, disclosed
# limitation TCEC's and FPUC's original combined-territory trackers had.
COMBINED_TERRITORY_LABEL = "Escambia/Santa Rosa"


def fetch_erec_outage_summary():
    """
    Fetch EREC's live combined-territory outage summary. Returns the
    parsed JSON data, or None on failure/missing config.
    """
    if not EREC_API_URL:
        print("EREC_API_URL not set - skipping EREC fetch")
        return None

    try:
        print("Fetching EREC outage data...")
        response = requests.get(EREC_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching EREC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert EREC's raw outageSummary.json into the same list-of-dicts
    shape OutageDatabase.log_multiple_outages()/sync_outage_events()
    expect - always exactly one record (see COMBINED_TERRITORY_LABEL),
    since this source has no real per-county breakdown yet.
    """
    if not data:
        return []

    return [{
        "county": COMBINED_TERRITORY_LABEL,
        "customers_out": data.get("customersOutNow") or 0,
        "customers_served": data.get("customersServed") or 0,
    }]


def get_erec_records():
    """
    Fetch and parse EREC's current combined-territory outage record in
    one call.
    """
    return outages_to_records(fetch_erec_outage_summary())


def main():
    """
    Test function - displays current EREC outage data
    """
    print("=" * 70)
    print("ESCAMBIA RIVER ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_erec_records()
    if not records:
        print("\nNo EREC data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

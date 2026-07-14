import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Found via Safari Web Inspector, not officially documented - kept out
# of the committed code (this repo is public), same as every other
# utility here. Runs on the exact same vendor platform as TCEC/EREC
# (identical outageSummary.json/outagePolygons.json shape, same
# Microsoft IIS server) - hosted on a proper domain over HTTPS but on a
# non-standard port (:8080). No trackingCode or auth needed, plain GET.
CHELCO_API_URL = os.environ.get("CHELCO_API_URL")

# The canonical utility name ("Choctawhatchee Electric Cooperative,
# Inc.") - not present in historical_import.py's PSC-report data
# (CHELCO wasn't named individually in the 17 backfilled storms), but
# kept consistent with the naming convention every other utility here
# uses.
UTILITY_NAME = "Choctawhatchee Electric Cooperative, Inc."

# CHELCO's live feed (outageSummary.json) reports ONE combined total
# across its whole real territory, with no per-county breakdown in that
# response - identical limitation to TCEC/EREC (same vendor platform).
# Real per-region detail lives in a sibling endpoint,
# outagePolygons.json, confirmed to exist but seen empty every time so
# far (zero active outages during discovery, 2026-07-14) - its real
# field shape is unknown until a genuine outage populates it, same
# "wait for a real event" situation FPUC's markers array and TCEC's/
# EREC's own outagePolygons.json were in before/still are.
#
# Real territory confirmed by the user directly: Santa Rosa, Okaloosa,
# Walton, and Holmes counties - a clean four-county case (no "partial
# coverage" caveat the way TCEC's territory had). Closes the real
# Holmes County coverage gap flagged during the 2026-07-13/14 county
# audit (previously only a tiny 2,835-customer FPL sliver, no other
# utility on file). Using a combined "county" label that names the real
# counties involved (not a placeholder like FPUC's non-adjacent-
# territory one) fits the existing per-source pipeline honestly.
# Weather correlation for this combined view will always come back
# empty (a multi-county label can never match a single-county NWS
# alert's areaDesc string) - same known, disclosed limitation TCEC's/
# EREC's and FPUC's original combined-territory trackers had.
COMBINED_TERRITORY_LABEL = "Santa Rosa/Okaloosa/Walton/Holmes"


def fetch_chelco_outage_summary():
    """
    Fetch CHELCO's live combined-territory outage summary. Returns the
    parsed JSON data, or None on failure/missing config.
    """
    if not CHELCO_API_URL:
        print("CHELCO_API_URL not set - skipping CHELCO fetch")
        return None

    try:
        print("Fetching CHELCO outage data...")
        response = requests.get(CHELCO_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching CHELCO data: {e}")
        return None


def outages_to_records(data):
    """
    Convert CHELCO's raw outageSummary.json into the same list-of-dicts
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


def get_chelco_records():
    """
    Fetch and parse CHELCO's current combined-territory outage record in
    one call.
    """
    return outages_to_records(fetch_chelco_outage_summary())


def main():
    """
    Test function - displays current CHELCO outage data
    """
    print("=" * 70)
    print("CHOCTAWHATCHEE ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_chelco_records()
    if not records:
        print("\nNo CHELCO data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

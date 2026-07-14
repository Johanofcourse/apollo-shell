import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Found via Safari Web Inspector, not officially documented - kept out
# of the committed code (this repo is public), same as every other
# utility here. Runs on a sixth distinct vendor platform (a custom
# Microsoft IIS-hosted build, outage.tcec.com) - no trackingCode or
# auth needed, plain GET with standard ETag/If-Modified-Since caching.
TCEC_API_URL = os.environ.get("TCEC_API_URL")

# The canonical utility name ("Tri-County Electric Cooperative, Inc.") -
# not present in historical_import.py's PSC-report data (TCEC wasn't
# named individually in the 17 backfilled storms), but kept consistent
# with the naming convention every other utility here uses.
UTILITY_NAME = "Tri-County Electric Cooperative, Inc."

# TCEC's live feed (outageSummary.json) reports ONE combined total
# across its whole real territory, with no per-county breakdown in that
# response. Real per-region detail lives in a sibling endpoint,
# outagePolygons.json, confirmed to exist but seen empty every time so
# far (zero active outages during discovery, 2026-07-13) - its real
# field shape is unknown until a genuine outage populates it, same
# "wait for a real event" situation FPUC's markers array was in before
# one finally did. Counties.zip (the map's own Esri shapefile,
# NAME field: Dixie/Jefferson/Lafayette/Hamilton/Suwannee/Taylor/
# Madison/Brooks/Thomas/Leon) turned out to be the map's background
# county-line reference layer, not real service territory - it
# includes two Georgia counties (Brooks, Thomas), a dead end for real
# coverage the same way PRECO's grid-geometry endpoint was.
#
# Real territory confirmed instead by the user's own visual read of the
# live map: Jefferson, Madison, and Taylor counties, plus small parts
# of Dixie, Lafayette, and Leon. Using a combined "county" label that
# names the real counties involved (not a placeholder like FPUC's
# non-adjacent-territory one, since these genuinely are the real
# counties, just not splittable from this response alone) fits the
# existing per-source pipeline honestly. Weather correlation for this
# combined view will always come back empty (a multi-county label can
# never match a single-county NWS alert's areaDesc string) - same known,
# disclosed limitation FPUC's original combined-territory tracker had.
COMBINED_TERRITORY_LABEL = "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)"


def fetch_tcec_outage_summary():
    """
    Fetch TCEC's live combined-territory outage summary. Returns the
    parsed JSON data, or None on failure/missing config.
    """
    if not TCEC_API_URL:
        print("TCEC_API_URL not set - skipping TCEC fetch")
        return None

    try:
        print("Fetching TCEC outage data...")
        response = requests.get(TCEC_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching TCEC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert TCEC's raw outageSummary.json into the same list-of-dicts
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


def get_tcec_records():
    """
    Fetch and parse TCEC's current combined-territory outage record in
    one call.
    """
    return outages_to_records(fetch_tcec_outage_summary())


def main():
    """
    Test function - displays current TCEC outage data
    """
    print("=" * 70)
    print("TRI-COUNTY ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_tcec_records()
    if not records:
        print("\nNo TCEC data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

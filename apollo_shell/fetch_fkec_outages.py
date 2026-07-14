import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public), loaded from .env instead of hardcoded as
# a literal string, same as every other utility here. A plain static
# JSON file, no tracking code or auth needed at all, wide-open CORS.
FKEC_API_URL = os.environ.get("FKEC_API_URL")

# The canonical utility name ("Florida Keys Electric Cooperative,
# Inc.") - not present in historical_import.py's PSC-report data (FKEC
# wasn't named individually in the 17 backfilled storms), but kept
# consistent with the naming convention every other utility here uses.
UTILITY_NAME = "Florida Keys Electric Cooperative, Inc."

# FKEC's entire real service territory is confirmed to be Monroe County
# (the Florida Keys) - verified by reverse-geocoding a real coordinate
# from the map's own ZIP-boundary geometry through the same FCC Census
# API lookup_county() in fetch_teco_outages.py already uses. Unlike JEA
# (which spans many counties and needs a real per-ZIP reverse-geocode),
# FKEC's six ZIP codes never need that - they all collapse to this one
# real county, so this is a hardcoded constant, not a placeholder like
# FPUC's combined-territory label.
SERVICE_COUNTY = "Monroe"


def fetch_fkec_outages():
    """
    Fetches live outage data from FKEC's outage-summary endpoint.
    Returns the parsed JSON data, or None on failure/missing config.
    """
    if not FKEC_API_URL:
        print("FKEC_API_URL not set - skipping FKEC fetch")
        return None

    try:
        print("Fetching FKEC outage data...")
        response = requests.get(FKEC_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching FKEC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert FKEC's raw outage-summary JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect
    (county/customers_out/customers_served) - a county-rollup source
    like FPL/JEA/Talquin/PRECO, not an incident list. Always returns
    exactly one record (SERVICE_COUNTY), since FKEC's whole territory
    is one real county - same "always exactly one row" shape as FPUC's
    combined-territory view, except this county name is real, not a
    placeholder.

    customers_out is summed across all ZIP-level regions (there's no
    top-level "total out" field in the response). customers_served
    uses the response's own top-level "totalServed" directly rather
    than summing the per-ZIP "numberServed" figures - checked 2026-07-13
    against a real response and found the two don't quite agree (summing
    the six ZIPs came to 34,667 vs. the authoritative totalServed of
    34,475, a ~0.6% gap) - config.json's own
    outageSettings.summaryTableSettings.useConsumerCountForTotalServed
    flag confirms totalServed is the intended authoritative figure, not
    a value derived by re-summing the per-region rollup here.
    """
    if not data:
        return []

    regions = []
    for dataset in data.get("regionDataSets", []):
        regions.extend(dataset.get("regions", []))

    if not regions:
        return []

    customers_out = sum(r.get("numberOut") or 0 for r in regions)
    customers_served = data.get("totalServed") or 0

    return [{
        "county": SERVICE_COUNTY,
        "customers_out": customers_out,
        "customers_served": customers_served,
    }]


def get_fkec_records():
    """
    Fetch and parse the current FKEC county-level outage record in one
    call.
    """
    return outages_to_records(fetch_fkec_outages())


def main():
    """
    Test function - displays current FKEC outage data
    """
    print("=" * 70)
    print("FLORIDA KEYS ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_fkec_records()
    if not records:
        print("\nNo FKEC data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

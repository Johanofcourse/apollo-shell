import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Fully public, unauthenticated static JSON file (S3/CloudFront) - no
# tracking code, no session token, no special headers required, unlike
# Talquin/PRECO's Siena-hosted endpoints. Kept in .env anyway for
# consistency with every other utility here, not because it's secret.
LCEC_API_URL = os.environ.get("LCEC_API_URL")

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data
# ("Lee County Electric Cooperative").
UTILITY_NAME = "Lee County Electric Cooperative"


def fetch_lcec_outages():
    """
    Fetches live outage data from LCEC's outage-map endpoint. Returns
    the parsed JSON data, or None on failure/missing config.
    """
    if not LCEC_API_URL:
        print("LCEC_API_URL not set - skipping LCEC fetch")
        return None

    try:
        print("Fetching LCEC outage data...")
        response = requests.get(LCEC_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching LCEC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert LCEC's raw JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect
    (county/customers_out/customers_served). The real per-county
    numbers live in regionDataSets, under the entry with
    id == "County" - the same response also breaks the exact same
    totals down by Zip Code and by internal "Region" name (LCEC's own
    service-area names like "Cape Coral SW") - richer detail not
    currently used here.

    The response also includes a top-level "outages" array with real
    per-incident detail (id, nbrOut, timeOff/estimateTime as epoch-ms
    timestamps, crewAssigned) - not read here either. Unlike OUC's
    unpopulated cluster layer, this one genuinely has live data, so
    it's a real candidate for incident-level tracking later - just
    out of scope for this pass, which sticks to the same
    rollup-only shape as FKEC/GCEC/OUC.
    """
    records = []
    region_datasets = (data or {}).get("regionDataSets", [])
    county_dataset = next((d for d in region_datasets if d.get("id") == "County"), None)
    if not county_dataset:
        return records

    for region in county_dataset.get("regions", []):
        records.append({
            "county": region.get("id") or "",
            "customers_out": region.get("numberOut") or 0,
            "customers_served": region.get("numberServed") or 0,
        })
    return records


def get_lcec_records():
    """
    Fetch and parse current LCEC county-level outage records in one
    call.
    """
    return outages_to_records(fetch_lcec_outages())


def main():
    """
    Test function - displays current LCEC outage data
    """
    print("=" * 70)
    print("LEE COUNTY ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_lcec_records()
    if not records:
        print("\nNo LCEC data fetched.")
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

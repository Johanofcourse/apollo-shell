import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public), loaded from .env instead of hardcoded as
# a literal string, same as every other utility here.
#
# The trackingCode query param embedded in this URL is a live, rotating
# credential tied to a real browser visit, not a permanently-stable
# string - it needs periodic refreshing (see TALQUIN_API_URL in
# .env.example for how to obtain a current one), same as
# TALQUIN_REQUEST_HEADERS below needs to match a real browser visit.
TALQUIN_API_URL = os.environ.get("TALQUIN_API_URL")

# Required alongside a current trackingCode for the request to succeed.
TALQUIN_REQUEST_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://my.talquinelectric.com",
    "Referer": "https://my.talquinelectric.com/",
    "Client": "talquin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/26.5.2 Safari/605.1.15"
    ),
}

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data
# ("Talquin Electric Cooperative, Inc.").
UTILITY_NAME = "Talquin Electric Cooperative, Inc."


def fetch_talquin_outages():
    """
    Fetches live outage data from Talquin's Siena-hosted outage-map
    endpoint. Returns the parsed JSON data, or None on failure/missing
    config.
    """
    if not TALQUIN_API_URL:
        print("TALQUIN_API_URL not set - skipping Talquin fetch")
        return None

    try:
        print("Fetching Talquin outage data...")
        response = requests.get(TALQUIN_API_URL, headers=TALQUIN_REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Talquin data: {e}")
        return None


def outages_to_records(data):
    """
    Convert Talquin's raw Siena JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect
    (county/customers_out/customers_served) - same shape as
    fetch_fpl_outages.outages_to_records(), since this is a county-level
    rollup source like FPL/JEA, not an incident list like TECO/Duke/
    Tallahassee. The real per-county numbers live in reportData.reports,
    under the entry with id == "County" (the same response also breaks
    the exact same totals down by substation and by ZIP code - richer
    detail not currently used here, but on file in the raw response if
    ever wanted later).

    County names come back all-caps ("GADSDEN") - .title()'d to match
    the natural-case convention every other source already uses.
    """
    records = []
    reports = (data or {}).get("reportData", {}).get("reports", [])
    county_report = next((r for r in reports if r.get("id") == "County"), None)
    if not county_report:
        return records

    for polygon in county_report.get("polygons", []):
        records.append({
            "county": (polygon.get("name") or "").title(),
            "customers_out": polygon.get("affected") or 0,
            "customers_served": polygon.get("accounts") or 0,
        })
    return records


def get_talquin_records():
    """
    Fetch and parse current Talquin county-level outage records in one
    call.
    """
    return outages_to_records(fetch_talquin_outages())


def main():
    """
    Test function - displays current Talquin outage data
    """
    print("=" * 70)
    print("TALQUIN ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_talquin_records()
    if not records:
        print("\nNo Talquin data fetched.")
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

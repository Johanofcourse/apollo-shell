import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public), loaded from .env instead of hardcoded as
# a literal string, same as every other utility here. Peace River
# Electric Cooperative (PRECO) runs on the same underlying platform as
# Talquin, and exposes the identical endpoint shape - just PRECO's own
# tracking code in place of Talquin's.
#
# Like Talquin's, this trackingCode is a live, rotating credential tied
# to a real browser visit, not a permanently-stable string - it needs
# periodic refreshing, same as PRECO_REQUEST_HEADERS below needs to
# match a real browser visit to outages.preco.coop.
PRECO_API_URL = os.environ.get("PRECO_API_URL")

# Required alongside a current trackingCode for the request to succeed.
PRECO_REQUEST_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://outages.preco.coop",
    "Referer": "https://outages.preco.coop/",
    "Client": "preco",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/26.5.2 Safari/605.1.15"
    ),
}

# The canonical utility name, matching how this entity is formally
# known ("Peace River Electric Cooperative, Inc.") - not yet present
# in historical_import.py's PSC-report data (PRECO wasn't one of the
# original 17 storms), but kept consistent with the naming convention
# every other utility here uses.
UTILITY_NAME = "Peace River Electric Cooperative, Inc."


def fetch_preco_outages():
    """
    Fetches live outage data from PRECO's outage-map endpoint. Returns
    the parsed JSON data, or None on failure/missing config.
    """
    if not PRECO_API_URL:
        print("PRECO_API_URL not set - skipping PRECO fetch")
        return None

    try:
        print("Fetching PRECO outage data...")
        response = requests.get(PRECO_API_URL, headers=PRECO_REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching PRECO data: {e}")
        return None


def outages_to_records(data):
    """
    Convert PRECO's raw Siena JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect
    (county/customers_out/customers_served) - identical parsing
    approach to fetch_talquin_outages.outages_to_records(), since both
    utilities share the exact same Siena reportData.reports shape. The
    same response also breaks the totals down by "District" (PRECO's
    3 internal service regions: CENTRAL/EASTERN/WESTERN) - not a real
    Florida county grouping, so only the "County" report is used here.

    Unlike Talquin, PRECO's county names already come back correctly
    cased ("Brevard", "DeSoto") straight from the source - NOT
    .title()'d like Talquin's all-caps feed, since .title() would
    mangle "DeSoto" into "Desoto" (it capitalizes only the first
    letter of each whitespace-separated word, not the internal "S").
    """
    records = []
    reports = (data or {}).get("reportData", {}).get("reports", [])
    county_report = next((r for r in reports if r.get("id") == "County"), None)
    if not county_report:
        return records

    for polygon in county_report.get("polygons", []):
        records.append({
            "county": polygon.get("name") or "",
            "customers_out": polygon.get("affected") or 0,
            "customers_served": polygon.get("accounts") or 0,
        })
    return records


def get_preco_records():
    """
    Fetch and parse current PRECO county-level outage records in one
    call.
    """
    return outages_to_records(fetch_preco_outages())


def main():
    """
    Test function - displays current PRECO outage data
    """
    print("=" * 70)
    print("PEACE RIVER ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_preco_records()
    if not records:
        print("\nNo PRECO data fetched.")
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

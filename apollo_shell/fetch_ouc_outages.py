import os

import requests
from dotenv import load_dotenv

load_dotenv()

# OUC's outage map runs on the same shared vendor platform JEA's does -
# the host below is the vendor's own shared domain (used by many
# utilities, not OUC-specific), so it's fine to hardcode, same
# reasoning as fetch_jea_outages.JEA_PLATFORM_HOST. OUC's own instance
# id is the real secret, kept out of the committed code.
OUC_PLATFORM_HOST = "https://kubra.io"
OUC_INSTANCE_ID = os.environ.get("OUC_INSTANCE_ID")

# Matches the exact string this same real entity is stored as in
# historical_import.py's PSC-report data ("Orlando (Orlando Utilities
# Commission - OUC)") - one canonical name across both tables, same
# principle as every other utility here.
UTILITY_NAME = "Orlando (Orlando Utilities Commission - OUC)"

# OUC's entire real service territory is confirmed to be Orange County
# only - verified against real historical PSC storm reports (2026-07-16):
# across all 17 backfilled storms, OUC has only ever been recorded
# under Orange County, never any other. One real county, so this is a
# hardcoded constant, not a per-record geocode - same reasoning as
# fetch_fkec_outages.SERVICE_COUNTY (Monroe) and
# fetch_tallahassee_outages.COUNTY (Leon).
SERVICE_COUNTY = "Orange"

# OUC's live feed also has a real per-incident/per-cluster layer
# (confirmed to exist: a tile-based "cluster-data" endpoint, grouping
# outage pins by zoom level and map tile) - but it was only ever
# observed as a 404 the day this was captured, because the summary
# endpoint showed zero active outages at that exact moment
# ("total_outages": 0, page_mode "BLUESKY"). Its real populated shape
# is still unknown until a genuine outage populates it - same "wait for
# a real event" situation FPUC's markers array and GCEC's per-region
# endpoint were in before/still are. Only the summary rollup is
# integrated here for now.


def fetch_ouc_summary():
    """
    Fetch OUC's live city-wide outage summary (current customers
    affected, total customers served) from the shared vendor platform's
    static data endpoint. Returns the parsed JSON data, or None on
    failure/missing config.
    """
    if not OUC_INSTANCE_ID:
        print("OUC_INSTANCE_ID not set - skipping OUC fetch")
        return None

    url = f"{OUC_PLATFORM_HOST}/data/{OUC_INSTANCE_ID}/public/summary-1/data.json"
    try:
        print("Fetching OUC outage data...")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching OUC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert OUC's raw summary JSON into the same list-of-dicts shape
    OutageDatabase.log_multiple_outages()/sync_outage_events() expect -
    always exactly one record (SERVICE_COUNTY), a real-percentage
    county-rollup source like FKEC/PRECO, not a raw-count fallback.
    """
    if not data:
        return []

    totals = (data.get("summaryFileData") or {}).get("totals") or []
    if not totals:
        return []

    total = totals[0]
    customers_out = (total.get("total_cust_a") or {}).get("val") or 0
    customers_served = total.get("total_cust_s") or 0

    return [{
        "county": SERVICE_COUNTY,
        "customers_out": customers_out,
        "customers_served": customers_served,
    }]


def get_ouc_records():
    """
    Fetch and parse the current OUC county-level outage record in one
    call.
    """
    return outages_to_records(fetch_ouc_summary())


def main():
    """
    Test function - displays current OUC outage data
    """
    print("=" * 70)
    print("ORLANDO UTILITIES COMMISSION LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_ouc_records()
    if not records:
        print("\nNo OUC data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

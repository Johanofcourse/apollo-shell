import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public), same as every other utility here. Runs on
# the exact same platform as TCEC/EREC/CHELCO (identical feed shape).
# No tracking code or auth needed, plain GET.
GCEC_API_URL = os.environ.get("GCEC_API_URL")

# The canonical utility name ("Gulf Coast Electric Cooperative, Inc.") -
# matches the exact string this same real entity is stored as in
# historical_import.py's PSC-report data.
UTILITY_NAME = "Gulf Coast Electric Cooperative, Inc."

# GCEC's live feed reports ONE combined total across its whole real
# territory, with no per-county breakdown in that response - identical
# limitation to TCEC/EREC/CHELCO (same underlying platform). Real
# per-region detail lives in a sibling endpoint, confirmed to exist but
# seen empty every time so far (zero active outages during discovery) -
# its real field shape is unknown until a genuine outage populates it,
# same "wait for a real event" situation FPUC's markers array and
# TCEC's/EREC's/CHELCO's own sibling endpoints were in before/still are.
#
# Real territory confirmed against historical PSC storm-report data
# (this utility appears consistently across 7 of 17 backfilled storms):
# Bay, Calhoun, Gulf, Jackson, Walton, and Washington counties. Closes
# the real Calhoun County coverage gap - the last Florida county with
# zero live per-county coverage as of this integration (previously only
# covered as part of FPUC's separate, unrelated combined total). Using
# a combined "county" label that names the real counties involved (not
# a placeholder like FPUC's non-adjacent-territory one) fits the
# existing per-source pipeline honestly. Weather correlation for this
# combined view will always come back empty (a multi-county label can
# never match a single-county NWS alert's areaDesc string) - same
# known, disclosed limitation TCEC's/EREC's/CHELCO's and FPUC's
# original combined-territory trackers had.
COMBINED_TERRITORY_LABEL = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"


def fetch_gcec_outage_summary():
    """
    Fetch GCEC's live combined-territory outage summary. Returns the
    parsed JSON data, or None on failure/missing config.
    """
    if not GCEC_API_URL:
        print("GCEC_API_URL not set - skipping GCEC fetch")
        return None

    try:
        print("Fetching GCEC outage data...")
        response = requests.get(GCEC_API_URL, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching GCEC data: {e}")
        return None


def outages_to_records(data):
    """
    Convert GCEC's raw outage-summary JSON into the same list-of-dicts
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


def get_gcec_records():
    """
    Fetch and parse GCEC's current combined-territory outage record in
    one call.
    """
    return outages_to_records(fetch_gcec_outage_summary())


def main():
    """
    Test function - displays current GCEC outage data
    """
    print("=" * 70)
    print("GULF COAST ELECTRIC COOPERATIVE LIVE OUTAGE DATA")
    print("=" * 70)

    records = get_gcec_records()
    if not records:
        print("\nNo GCEC data fetched.")
    else:
        for r in records:
            pct = (r["customers_out"] / r["customers_served"] * 100) if r["customers_served"] > 0 else 0
            print(f"\n  {r['county']}: {r['customers_out']:,} / {r['customers_served']:,} ({pct:.2f}%)")

    print("=" * 70)


if __name__ == "__main__":
    main()

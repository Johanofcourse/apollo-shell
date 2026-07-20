import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public), loaded from .env instead of hardcoded as
# a literal string, same as every other utility here.
#
# This must be layer index 0 ("Outages", point geometry), not 1
# (a boundary-polygon layer) - confirmed by querying the base service's
# own layer list directly. The URL
# originally captured used /1/query and happened to return the right
# field schema (lat/lon/region/status/cause/customers/off/etr/ticket)
# with zero matching features that day, which looked identical to a
# real empty Outages response; once real requests came back from that
# same URL, they turned out to have real incidents but every one had a
# null "ticket" field - not a wrong-layer symptom after all (see
# get_rollup_summary() below for what that turned out to mean).
TALLAHASSEE_API_URL = os.environ.get("TALLAHASSEE_API_URL")

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data
# ("City of Tallahassee").
UTILITY_NAME = "City of Tallahassee"

# Tallahassee's outage map has a real "region" field (an integer) but
# the layer that names those regions numbers its rows by internal id,
# not by the region number itself - internal id 2 is named "4 West",
# internal id 4 is "3 South". Joining naively on that internal id would
# silently mislabel outages (region 2 would resolve to "West" instead
# of "East"). The real key is the leading digit baked into each
# region's own name ("2 East" -> 2, "5 Outside" -> 5) - confirmed by
# fetching that layer directly and comparing it by hand, same class of
# silent join bug as the county-name mismatches caught earlier in this
# project (Miami-Dade, St Lucie, DeSoto). Region 0 shows up in real
# live data too (confirmed 2026-07-18) - not one of the five named
# zones, so labeled plainly rather than guessed at.
REGION_NAMES = {0: "Unknown", 1: "North", 2: "East", 3: "South", 4: "West", 5: "Outside"}

# Confirmed against real historical PSC storm reports (2026-07-13): City
# of Tallahassee's outages have only ever been reported under Leon
# County, across every storm on file - its service territory is Leon
# County only, so no per-record reverse-geocoding is needed the way
# Duke's lat/lon does.
COUNTY = "Leon"


def fetch_tallahassee_outages():
    """
    Fetch raw individual outage incidents from Tallahassee's public
    outage-map feed. Returns a list of raw "feature" dicts, or an empty
    list if not configured.

    Raises the real request exception on a genuine fetch failure,
    rather than swallowing it into an empty list - real bug found and
    fixed 2026-07-20, same class as TECO's/Duke's. This module's own
    get_rollup_summary() docstring used to note this as an accepted
    tradeoff ("a real network/API failure ... comes back
    indistinguishable from 'genuinely nothing happening'") - no longer
    true now that the failure actually propagates to main.py's
    pipeline-health logging instead of being swallowed here.
    """
    if not TALLAHASSEE_API_URL:
        print("TALLAHASSEE_API_URL not set - skipping City of Tallahassee fetch")
        return []

    try:
        print("Fetching City of Tallahassee outage data...")
        response = requests.get(TALLAHASSEE_API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        features = data.get("features", [])
        print(f"Found {len(features)} active Tallahassee outage incidents")
        return features
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Tallahassee data: {e}")
        raise


def get_rollup_summary():
    """
    Fetch City of Tallahassee's current outage features and collapse
    them into one county-wide total for Leon County.

    Replaced the original incident-level design 2026-07-18 after
    finding every real feature this feed has ever returned came back
    with attrs["ticket"] = None - checked directly against 9 real,
    concurrent live incidents, not an occasional gap. incident_id was
    this project's whole basis for tracking a specific incident across
    polls (same as TECO/Duke), so every real Tallahassee incident was
    silently dropped by the old get_incidents_summary()'s "no ticket, no
    track" rule - this table had logged zero rows in this project's
    entire life despite real, ongoing outages the whole time. No other
    field in this feed is a reliable stable identity for a specific
    incident across polls (OBJECTID reflects query row order, not a
    persistent id).

    Tallahassee's whole territory is Leon County only (see COUNTY), so
    collapsing to one county-wide total isn't a granularity loss the
    way it would be for a multi-county source - it gives up per-
    incident cause/region/crew-status detail this feed can't reliably
    let us track the identity of anyway. Same shape as
    fetch_talquin_outages.get_talquin_records(): a list of one dict per
    county (here, always exactly one).
    """
    features = fetch_tallahassee_outages()
    customers_out = sum((f.get("attributes", {}).get("customers") or 0) for f in features)
    return [{"county": COUNTY, "customers_out": customers_out}]


def main():
    """
    Test function - displays current City of Tallahassee outage data
    """
    print("=" * 70)
    print("CITY OF TALLAHASSEE LIVE OUTAGE DATA")
    print("=" * 70)

    summary = get_rollup_summary()
    print(f"\n{summary[0]['county']} County: {summary[0]['customers_out']:,} customers currently out\n")
    print("=" * 70)


if __name__ == "__main__":
    main()

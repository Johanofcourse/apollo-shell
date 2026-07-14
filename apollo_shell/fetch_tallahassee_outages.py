import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from fetch_teco_outages import categorize_reason, categorize_status

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
# real empty Outages response; once a real request came back from that
# same URL, it turned out to be the boundary polygon instead (fields
# OBJECTID/SHAPE_Length/SHAPE_Area only). Caught before shipping by
# re-querying the layer list and layer 0 directly, not by a crash -
# every "ticket" read from a boundary feature is None, and
# get_incidents_summary() silently drops anything without one, so this
# would have reported zero incidents forever without ever erroring.
TALLAHASSEE_API_URL = os.environ.get("TALLAHASSEE_API_URL")

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data
# ("City of Tallahassee").
UTILITY_NAME = "City of Tallahassee"

# Tallahassee's outage map has a real "region" field (an integer 1-5)
# but the layer that names those regions numbers its rows by internal
# id, not by the region number itself - internal id 2 is named
# "4 West", internal id 4 is "3 South". Joining naively on that
# internal id would silently mislabel outages (region 2 would resolve
# to "West" instead of "East"). The real key is the leading digit baked
# into each region's own name ("2 East" -> 2, "5 Outside" -> 5) -
# confirmed by fetching that layer directly and comparing it by hand,
# same class of silent join bug as the county-name mismatches caught
# earlier in this project (Miami-Dade, St Lucie, DeSoto).
REGION_NAMES = {1: "North", 2: "East", 3: "South", 4: "West", 5: "Outside"}

# Confirmed against real historical PSC storm reports (2026-07-13): City
# of Tallahassee's outages have only ever been reported under Leon
# County, across every storm on file - its service territory is Leon
# County only, so no per-record reverse-geocoding is needed the way
# Duke's lat/lon does.
COUNTY = "Leon"


def fetch_tallahassee_outages():
    """
    Fetch raw individual outage incidents from Tallahassee's public
    outage-map feed. Returns a list of raw "feature" dicts,
    or an empty list on failure/missing config.
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
        return []


def _epoch_to_iso(millis):
    """
    This feed's date fields are documented as epoch milliseconds (UTC) - not yet
    confirmed against a real populated value here, since the feed had
    zero active incidents (features: []) the day this was written.
    Worth double-checking the first time a real outage populates
    'off'/'etr'.
    """
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def parse_incidents(raw_features):
    """
    Convert raw Tallahassee outage features into a flat list of
    dicts, matching the same incident shape TECO/Duke use (utility,
    incident_id, customer_count, lat, lon, county, cause/cause_category)
    plus two fields unique to this source: region_name (the sub-county
    zone) and status_category (reusing TECO's own free-text categorizer,
    since Tallahassee's "status" field is the same kind of free text).
    """
    records = []
    for feature in raw_features:
        attrs = feature.get("attributes", {})
        region = attrs.get("region")
        status = attrs.get("status")
        cause = attrs.get("cause")
        ticket = attrs.get("ticket")

        records.append({
            "utility": UTILITY_NAME,
            "incident_id": str(ticket) if ticket is not None else None,
            "customer_count": attrs.get("customers"),
            "lat": attrs.get("lat"),
            "lon": attrs.get("lon"),
            "county": COUNTY,
            "region_name": REGION_NAMES.get(region, str(region) if region is not None else None),
            "status": status,
            "status_category": categorize_status(status),
            "cause": cause,
            "cause_category": categorize_reason(cause),
            "outage_type": attrs.get("outagetype"),
            "reported_start_time": _epoch_to_iso(attrs.get("off")),
            "estimated_restoration": _epoch_to_iso(attrs.get("etr")),
        })
    return records


def get_incidents_summary():
    """
    Fetch and parse current City of Tallahassee outage incidents in one
    call. Incidents missing a ticket number are dropped - incident_id is
    the whole basis for lifecycle tracking (see
    OutageDatabase.sync_tallahassee_incident_events), same as an
    unidentifiable TECO/Duke incident would be.
    """
    return [r for r in parse_incidents(fetch_tallahassee_outages()) if r["incident_id"]]


def main():
    """
    Test function - displays current City of Tallahassee outage data
    """
    print("=" * 70)
    print("CITY OF TALLAHASSEE LIVE OUTAGE DATA")
    print("=" * 70)

    incidents = get_incidents_summary()
    if not incidents:
        print("\nNo active Tallahassee outage incidents.")
    else:
        total = sum(i["customer_count"] or 0 for i in incidents)
        print(f"\n{len(incidents)} active incidents, {total} customers affected\n")
        for incident in incidents:
            print(f"  Ticket {incident['incident_id']}: {incident['customer_count']} customers")
            print(f"    Region: {incident['region_name']} | Cause: {incident['cause']} ({incident['cause_category']})")
            print(f"    Status: {incident['status']} ({incident['status_category']}) | ETR: {incident['estimated_restoration']}")
            print()

    print("=" * 70)


if __name__ == "__main__":
    main()

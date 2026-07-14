import os
import requests
from datetime import datetime
from dotenv import load_dotenv

from fetch_teco_outages import lookup_county

load_dotenv()

# JEA's live outage map runs on a shared third-party vendor platform -
# a genuinely different backend from the other utilities integrated
# here. The host below is the vendor's own shared domain (used by many
# utilities, not JEA-specific), so it's fine to hardcode - JEA's
# specific instance/view ids are the only real secrets, kept out of the
# committed code the same way TECO's/Duke's endpoints are.
JEA_PLATFORM_HOST = "https://kubra.io"
JEA_INSTANCE_ID = os.environ.get("JEA_STORMCENTER_INSTANCE_ID")
JEA_VIEW_ID = os.environ.get("JEA_STORMCENTER_VIEW_ID")

# Matches the exact string this same real entity is stored as in
# historical_import.py's PSC-report data ("Jacksonville (JEA)") - one
# canonical name across both tables, same principle as TECO/Duke.
UTILITY_NAME = "Jacksonville (JEA)"

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# Resolved once per process and reused - JEA's ~38 ZIP codes don't move
# county lines between polls, so there's no need to re-hit the FCC's
# geocoder every 15-minute cycle for the same fixed set.
_zip_county_cache = {}


def _get_current_state():
    """
    Step 1 of the vendor platform's chain: resolves which data-directory and config
    deployment are currently live. Deliberately not cached/hardcoded -
    these ids rotate whenever JEA's ops team republishes, so the full
    chain has to be re-resolved every call, same as a real browser would.
    """
    url = f"{JEA_PLATFORM_HOST}/stormcenter/api/v1/stormcenters/{JEA_INSTANCE_ID}/views/{JEA_VIEW_ID}/currentState"
    response = requests.get(url, params={"preview": "false"}, headers=_HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def _get_configuration(deployment_id):
    """
    Step 2: resolves the real data file paths (these also rotate per
    deployment, hence re-fetched every call rather than hardcoded).
    """
    url = (f"{JEA_PLATFORM_HOST}/stormcenter/api/v1/stormcenters/{JEA_INSTANCE_ID}"
           f"/views/{JEA_VIEW_ID}/configuration/{deployment_id}")
    response = requests.get(url, headers=_HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_jea_areas():
    """
    Resolve the vendor platform's full chain (currentState -> configuration -> the
    actual per-ZIP report file) and return the raw list of per-ZIP area
    dicts, or an empty list on failure.
    """
    if not JEA_INSTANCE_ID or not JEA_VIEW_ID:
        raise RuntimeError(
            "JEA_STORMCENTER_INSTANCE_ID / JEA_STORMCENTER_VIEW_ID are not set. "
            "Copy .env.example to .env and fill in the real values."
        )

    try:
        print("Fetching JEA outage report...")
        state = _get_current_state()
        data_dir = state["data"]["interval_generation_data"]
        deployment_id = state["stormcenterDeploymentId"]

        config = _get_configuration(deployment_id)
        report_source = config["config"]["reports"]["data"]["interval_generation_data"][0]["source"]

        # data_dir already includes its own "data/" prefix (e.g.
        # "data/1e1de736-...") - do not add a second one here.
        report_url = f"{JEA_PLATFORM_HOST}/{data_dir}/{report_source}"
        response = requests.get(report_url, headers=_HEADERS, timeout=15)
        response.raise_for_status()

        areas = response.json().get("file_data", {}).get("areas", [])
        print(f"Found {len(areas)} JEA ZIP-code areas")
        return areas

    except (requests.exceptions.RequestException, KeyError, IndexError) as e:
        print(f"Error fetching JEA outage data: {e}")
        return []


def _zip_to_county(zip_code, bbox):
    """
    Resolve a ZIP code to a Florida county using the bbox center JEA's
    own report already supplies for each ZIP, reverse-geocoded through
    the same FCC lookup TECO's incidents use - no separate ZIP-to-county
    dataset needed, and grounded in JEA's own real ZIP boundary data
    rather than a guessed/external crosswalk.
    """
    if zip_code in _zip_county_cache:
        return _zip_county_cache[zip_code]

    county = None
    if bbox and len(bbox) == 4:
        min_lon, min_lat, max_lon, max_lat = bbox
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2
        county = lookup_county(center_lat, center_lon)

    _zip_county_cache[zip_code] = county
    return county


def parse_jea_areas(areas):
    """
    Convert the vendor platform's raw per-ZIP area list into per-ZIP records (zip,
    county, customers, ETR) ready for OutageDatabase.log_jea_outages(),
    and a separate per-county rollup ready for
    OutageDatabase.sync_jea_outage_events() - JEA's live feed is
    ZIP-level, but weather-alert correlation and lifecycle tracking are
    both county-based here, same as FPL.
    """
    zip_records = []
    county_totals = {}

    for area in areas:
        zip_code = area.get("name")
        county = _zip_to_county(zip_code, area.get("gotoMap", {}).get("bbox"))

        customers_out = (area.get("cust_a") or {}).get("val") or 0
        customers_served = area.get("cust_s") or 0
        percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

        # JEA's own sentinel for "no restoration estimate yet" - normalized
        # to a real None rather than shown to users as the literal string
        # "ETR-NULL", which would read as a bug, not an intentional state.
        etr = area.get("etr")
        if etr == "ETR-NULL":
            etr = None

        zip_records.append({
            "zip_code": zip_code,
            "county": county,
            "customers_out": customers_out,
            "customers_served": customers_served,
            "percentage_out": percentage_out,
            "etr": etr,
            "etr_confidence": area.get("etr_confidence"),
            "n_out": area.get("n_out") or 0,
        })

        if county:
            totals = county_totals.setdefault(county, {"customers_out": 0, "customers_served": 0})
            totals["customers_out"] += customers_out
            totals["customers_served"] += customers_served

    county_rollup = [
        {"county": county, "customers_out": t["customers_out"], "customers_served": t["customers_served"]}
        for county, t in county_totals.items()
    ]

    return zip_records, county_rollup


def get_jea_summary():
    """
    Fetch and parse the current JEA report in one call. Returns
    (zip_records, county_rollup) - see parse_jea_areas().
    """
    areas = fetch_jea_areas()
    return parse_jea_areas(areas)


def main():
    """
    Test function - displays current JEA outage data
    """
    print("=" * 70)
    print("JEA LIVE OUTAGE REPORT")
    print("=" * 70)

    zip_records, county_rollup = get_jea_summary()

    if not zip_records:
        print("\nNo JEA outage data fetched.")
    else:
        active = [z for z in zip_records if z["customers_out"] > 0]
        total_customers = sum(z["customers_out"] for z in zip_records)
        print(f"\n{len(active)} of {len(zip_records)} ZIPs have an active outage, "
              f"{total_customers} customers affected total\n")
        for z in active:
            print(f"  ZIP {z['zip_code']} ({z['county']} County): {z['customers_out']} customers, "
                  f"{z['n_out']} outage(s), ETR={z['etr']} ({z['etr_confidence']})")

        print("\nBy county:")
        for row in sorted(county_rollup, key=lambda r: r["customers_out"], reverse=True):
            print(f"  {row['county']}: {row['customers_out']} of {row['customers_served']} customers")

    print("=" * 70)
    print(f"Data retrieved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    main()

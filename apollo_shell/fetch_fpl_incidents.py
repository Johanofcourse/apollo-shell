import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from fetch_teco_outages import lookup_county, categorize_reason
from fetch_fpl_outages import FPL_API_ORIGIN, UTILITY_NAME

load_dotenv()

# FPL's real per-incident feed - not exposed through CountyOutages.json
# (the county-wide rollup this project integrated first), found by
# reading the outage map widget's own JS config. Same host as
# FPL_API_ORIGIN, kept as its own env var since it's a distinct,
# undocumented endpoint - not officially documented public API, kept
# out of the committed code (this repo is public).
FPL_INCIDENTS_API_URL = os.environ.get("FPL_INCIDENTS_API_URL")

# FPL's own timestamp formats - dateReported/etr use a two-digit year
# ("08/16/26 06:22 PM"), but lastUpdated uses a four-digit year
# ("08/16/2026 10:52 PM") - confirmed real, not a typo in one sample,
# checked across all 60 outages in a real live poll. Converted to ISO
# once, at the source, same principle as Clay's epoch-ms conversion and
# FPUC's "%m/%d %I:%M %p" conversion.
_FPL_TIMESTAMP_FORMATS = ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p")


def _parse_fpl_timestamp(raw):
    if not raw:
        return None
    for fmt in _FPL_TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return None


def fetch_fpl_incidents():
    """
    Fetches FPL's real per-incident outage feed (StormFeedRestoration.json).
    Returns the raw list of outage dicts, or raises on failure - same
    "don't swallow errors" convention as fetch_duke_outages._get().
    """
    if not FPL_INCIDENTS_API_URL:
        raise RuntimeError(
            "FPL_INCIDENTS_API_URL is not set. Copy .env.example to .env "
            "and fill in the real value."
        )

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{FPL_API_ORIGIN}/",
    }

    try:
        response = requests.get(FPL_INCIDENTS_API_URL, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("outages", [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching FPL incident data: {e}")
        raise


def parse_incidents(raw_outages):
    """
    Convert FPL's raw per-incident outage dicts into the same
    list-of-dicts shape TECO/Duke use, plus the real per-incident ETR
    field FPL's own feed carries (unlike Duke's, which has none at the
    incident level).
    """
    records = []
    for outage in raw_outages:
        try:
            lat = float(outage["lat"]) if outage.get("lat") is not None else None
        except (TypeError, ValueError):
            lat = None
        try:
            lon = float(outage["lng"]) if outage.get("lng") is not None else None
        except (TypeError, ValueError):
            lon = None
        try:
            customer_count = int(outage.get("customersAffected") or 0)
        except (TypeError, ValueError):
            customer_count = 0

        cause = outage.get("Cause")

        records.append({
            "utility": UTILITY_NAME,
            "incident_id": outage.get("ticketNum"),
            "customer_count": customer_count,
            "lat": lat,
            "lon": lon,
            "county": lookup_county(lat, lon) if lat is not None and lon is not None else None,
            "cause": cause,
            "cause_category": categorize_reason(cause),
            "status": outage.get("status"),
            "reported_start_time": _parse_fpl_timestamp(outage.get("dateReported")),
            "estimated_restoration": _parse_fpl_timestamp(outage.get("etr")),
            "last_updated": _parse_fpl_timestamp(outage.get("lastUpdated")),
        })
    return records


def get_incidents_summary():
    """
    Fetch and parse current FPL per-incident outages in one call.
    """
    return parse_incidents(fetch_fpl_incidents())


def main():
    """
    Test function - displays current FPL per-incident outages.
    """
    print("=" * 70)
    print("FPL LIVE PER-INCIDENT OUTAGES")
    print("=" * 70)

    incidents = get_incidents_summary()

    if not incidents:
        print("\nNo active FPL per-incident outages.")
    else:
        total_customers = sum(i["customer_count"] or 0 for i in incidents)
        print(f"\n{len(incidents)} active incidents, {total_customers} customers affected\n")
        for incident in incidents:
            print(f"  {incident['incident_id']}: {incident['customer_count']} customers")
            print(f"    Cause: {incident['cause']} ({incident['cause_category']})")
            print(f"    ETR: {incident['estimated_restoration']}")
            print(f"    Location: {incident['lat']}, {incident['lon']} ({incident['county']} County)")
            print()


if __name__ == "__main__":
    main()

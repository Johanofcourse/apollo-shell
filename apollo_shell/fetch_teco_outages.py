import os
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Not an officially documented public API - kept out of the committed
# code (this repo is public) the same way Duke's auth token is, loaded
# from .env instead of hardcoded as a literal string.
TECO_OUTAGE_TILES_URL = os.environ.get("TECO_API_URL")
TECO_API_ORIGIN = os.environ.get("TECO_API_ORIGIN")

# The canonical utility name, matching the exact string this same real
# entity is stored as in historical_import.py's PSC-report data (where
# it appears as "Tampa Electric Company"). Two different tables,
# two different granularities, but one name - so nobody has to guess
# whether "TECO" and "Tampa Electric Company" are the same thing.
UTILITY_NAME = "Tampa Electric Company"

# Rough, best-effort keyword buckets for TECO's free-text reason/status
# fields. This doesn't replace the original text anywhere - reason and
# status are always kept as TECO wrote them. This is a derived label
# stored alongside them so code can filter/count by cause without
# needing to know every phrase TECO might ever use. Order matters:
# first matching category wins, so more specific categories should
# come before more general ones.
REASON_CATEGORIES = [
    ("animal", ["squirrel", "animal", "wildlife", "bird", "snake", "critter", "raccoon"]),
    ("vegetation", ["tree", "limb", "branch", "vegetation", "foliage"]),
    ("vehicle", ["vehicle", "car", "truck", "accident", "collision", "crash"]),
    # "storm" is included both standalone and as an explicit suffix
    # (thunderstorm/windstorm/etc.) - word-boundary matching only checks
    # the leading edge of a keyword, so "storm" alone would not match
    # inside "thunderstorm" (no boundary between "thunder" and "storm").
    ("weather", [
        "storm", "thunderstorm", "windstorm", "rainstorm", "hailstorm",
        "wind", "lightning", "flood", "hurricane", "ice", "rain", "heat",
    ]),
    ("equipment", ["equipment", "transformer", "pole", "wire", "line", "fuse", "breaker", "damage"]),
    ("planned", ["planned", "maintenance", "scheduled"]),
    ("pending", ["pending", "investigat", "assessing", "unknown"]),
]

STATUS_CATEGORIES = [
    ("restored", ["restored", "resolved", "complete"]),
    ("onsite", ["onsite", "on-site", "working on"]),
    ("investigating", ["on our way", "investigat", "assessing", "en route"]),
    ("pending", ["pending", "reported"]),
]


def _categorize(text, categories):
    """
    Best-effort keyword match against a free-text field. Returns the
    first matching category name, or "other" if nothing matches, or
    "unknown" if there was no text to check at all.

    Matches on word boundaries, not plain substrings - naive substring
    matching classified Duke Energy's "unplanned" cause as "planned"
    (the word "planned" is a substring of "unplanned"), silently
    inverting its real meaning.
    """
    if not text:
        return "unknown"

    lowered = text.lower()
    for category, keywords in categories:
        if any(re.search(rf"\b{re.escape(keyword)}", lowered) for keyword in keywords):
            return category

    return "other"


def categorize_reason(reason):
    return _categorize(reason, REASON_CATEGORIES)


def categorize_status(status):
    return _categorize(status, STATUS_CATEGORIES)

# A box generously covering the whole state of Florida. Verified this is
# safe to over-request with - TECO's backend only ever returns their own
# real incidents regardless of how wide the box is, it doesn't error or
# return unrelated data from other utilities.
FLORIDA_BOUNDING_BOX = {
    "top_left": {"lat": 31.5, "lon": -88.0},
    "bottom_right": {"lat": 24.0, "lon": -79.5},
}


def fetch_teco_outages(bounding_box=None):
    """
    Query TECO's live outage-incidents feed (their public map's own
    backend API, not officially documented).

    Returns the raw list of "hits" (each one a single outage incident),
    or an empty list on failure.
    """
    if not TECO_OUTAGE_TILES_URL or not TECO_API_ORIGIN:
        raise RuntimeError(
            "TECO_API_URL / TECO_API_ORIGIN are not set. Copy .env.example "
            "to .env and fill in the real values."
        )

    bounding_box = bounding_box or FLORIDA_BOUNDING_BOX

    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": TECO_API_ORIGIN,
        "Referer": f"{TECO_API_ORIGIN}/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5.2 Safari/605.1.15",
    }

    body = {
        "size": 10000,
        "query": {
            "bool": {
                "must": {"match_all": {}},
                "filter": {"geo_bounding_box": {"polygonCenter": bounding_box}},
            }
        },
        "sort": [{"updateTime": "asc"}, {"incidentId": "asc"}],
        "_source": [
            "updateTime", "status", "reason", "customerCount",
            "polygonCenter", "incidentId", "estimatedTimeOfRestoration",
        ],
    }

    try:
        print("Fetching TECO outage incidents...")
        response = requests.post(TECO_OUTAGE_TILES_URL, headers=headers, json=body, timeout=15)
        response.raise_for_status()

        data = response.json()
        hits = data.get("hits", {}).get("hits", [])
        print(f"Found {len(hits)} active TECO outage incidents")
        return hits

    except requests.exceptions.RequestException as e:
        print(f"Error fetching TECO outage data: {e}")
        return []


def lookup_county(lat, lon):
    """
    Reverse-geocode a coordinate to a Florida county name using the FCC's
    free public Census API - TECO's incidents only have coordinates, but
    our weather-alert correlation logic is entirely county-based, so this
    is the bridge between the two.

    Returns a county name like "Hillsborough" (no "County" suffix, to
    match the naming already used elsewhere in this project), or None on
    failure.
    """
    if lat is None or lon is None:
        return None

    try:
        url = f"https://geo.fcc.gov/api/census/area?lat={lat}&lon={lon}&format=json"
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        results = response.json().get("results", [])
        if not results:
            return None

        county_name = results[0].get("county_name", "")
        return county_name.replace(" County", "").strip() or None

    except requests.exceptions.RequestException as e:
        print(f"County lookup failed for ({lat}, {lon}): {e}")
        return None


def parse_incidents(hits):
    """
    Convert raw feed hits into a flat list of dicts ready for
    OutageDatabase.log_teco_incidents(), including a reverse-geocoded
    county and derived reason/status categories for each one.
    """
    records = []
    for hit in hits:
        source = hit.get("_source", {})
        lon, lat = source.get("polygonCenter", [None, None])
        reason = source.get("reason")
        status = source.get("status")

        records.append({
            "utility": UTILITY_NAME,
            "incident_id": source.get("incidentId"),
            "status": status,
            "status_category": categorize_status(status),
            "reason": reason,
            "reason_category": categorize_reason(reason),
            "customer_count": source.get("customerCount"),
            "lat": lat,
            "lon": lon,
            "county": lookup_county(lat, lon),
            "update_time": source.get("updateTime"),
            "estimated_restoration": source.get("estimatedTimeOfRestoration"),
        })
    return records


def get_incidents_summary():
    """
    Fetch and parse current TECO incidents in one call.
    """
    hits = fetch_teco_outages()
    return parse_incidents(hits)


def main():
    """
    Test function - displays current TECO outage incidents
    """
    print("=" * 70)
    print("TECO LIVE OUTAGE INCIDENTS")
    print("=" * 70)

    incidents = get_incidents_summary()

    if not incidents:
        print("\nNo active TECO outage incidents.")
    else:
        total_customers = sum(i["customer_count"] or 0 for i in incidents)
        print(f"\n{len(incidents)} active incidents, {total_customers} customers affected\n")
        for incident in incidents:
            print(f"  {incident['incident_id']}: {incident['customer_count']} customers")
            print(f"    Reason: {incident['reason']} ({incident['reason_category']})")
            print(f"    Status: {incident['status']} ({incident['status_category']})")
            print(f"    ETR: {incident['estimated_restoration']}")
            print(f"    Location: {incident['lat']}, {incident['lon']} ({incident['county']} County)")
            print()

    print("=" * 70)
    print(f"Data retrieved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    main()

import requests
from datetime import datetime


TECO_OUTAGE_TILES_URL = "https://outage-data-prod-hrcadje2h9aje9c9.a03.azurefd.net/api/v1/outage-tiles"

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
    backend API, found via browser devtools - not officially documented).

    Returns the raw list of Elasticsearch "hits" (each one a single
    outage incident), or an empty list on failure.
    """
    bounding_box = bounding_box or FLORIDA_BOUNDING_BOX

    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://outage.tecoenergy.com",
        "Referer": "https://outage.tecoenergy.com/",
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
    Convert raw Elasticsearch hits into a flat list of dicts ready for
    OutageDatabase.log_teco_incidents(), including a reverse-geocoded
    county for each one.
    """
    records = []
    for hit in hits:
        source = hit.get("_source", {})
        lon, lat = source.get("polygonCenter", [None, None])

        records.append({
            "incident_id": source.get("incidentId"),
            "status": source.get("status"),
            "reason": source.get("reason"),
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
            print(f"    Reason: {incident['reason']}")
            print(f"    Status: {incident['status']}")
            print(f"    ETR: {incident['estimated_restoration']}")
            print(f"    Location: {incident['lat']}, {incident['lon']} ({incident['county']} County)")
            print()

    print("=" * 70)
    print(f"Data retrieved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    main()

import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv
from database import OutageDatabase

load_dotenv()

# Kept out of the committed code (this repo is public), loaded from .env
# instead of hardcoded as a literal string, same as TECO/Duke.
FPL_API_URL = os.environ.get("FPL_API_URL")
FPL_API_ORIGIN = os.environ.get("FPL_API_ORIGIN")

# FPL also runs a second, separate live feed for the Florida Panhandle -
# Gulf Power's former territory, merged into FPL corporately in 2021 but
# apparently never consolidated into the main map/feed. Same real
# utility, same "outages" JSON shape, just a different endpoint and
# Referer - found 2026-07-12 behind real Incapsula bot protection (a
# human had to check a real browser's Network tab; curl alone couldn't
# get past it). Covers Escambia, Santa Rosa, Okaloosa, Walton, Holmes,
# Washington, Jackson, and Bay - confirmed by checking the real response,
# not assumed. Three real Panhandle counties (Calhoun, Gadsden, Liberty)
# still aren't covered by either FPL feed - likely a smaller rural co-op's
# territory, not yet found.
FPL_NORTHWEST_API_URL = os.environ.get("FPL_NORTHWEST_API_URL")
FPL_NORTHWEST_API_REFERER = os.environ.get("FPL_NORTHWEST_API_REFERER")

# The canonical utility name, matching the exact string historical PSC-
# report data uses for this same real entity ("Florida Power and Light
# Company") - same fix already applied to TECO and Duke, just missed for
# FPL originally since it was the first utility integrated, before that
# pattern existed. Live data previously used the short "FPL" instead,
# which meant live and historical FPL records couldn't be matched by
# utility name at all.
UTILITY_NAME = "Florida Power and Light Company"


def fetch_fpl_outages():
    """
    Fetches live outage data from FPL's outage-map JSON endpoint
    Returns the parsed JSON data
    """
    if not FPL_API_URL or not FPL_API_ORIGIN:
        raise RuntimeError(
            "FPL_API_URL / FPL_API_ORIGIN are not set. Copy .env.example "
            "to .env and fill in the real values."
        )

    try:
        print("Fetching FPL outage data...")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': f'{FPL_API_ORIGIN}/'
        }
        
        response = requests.get(FPL_API_URL, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return None


def fetch_fpl_northwest_outages():
    """
    Fetches live outage data from FPL's separate Panhandle ("northwest")
    feed - same JSON shape as fetch_fpl_outages(), different endpoint and
    Referer. Returns None on failure, same as fetch_fpl_outages() - a
    missing/unset config is treated as "not configured yet" rather than
    a hard error, since this feed is a real bonus on top of the main one,
    not something the whole outage cycle should fail without.
    """
    if not FPL_NORTHWEST_API_URL or not FPL_NORTHWEST_API_REFERER:
        print("FPL_NORTHWEST_API_URL / FPL_NORTHWEST_API_REFERER not set - skipping Panhandle feed")
        return None

    try:
        print("Fetching FPL Northwest (Panhandle) outage data...")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': FPL_NORTHWEST_API_REFERER,
        }

        response = requests.get(FPL_NORTHWEST_API_URL, headers=headers, timeout=10)
        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        print(f"Error fetching FPL Northwest data: {e}")
        return None


def display_south_florida_outages(data):
    """
    Filters and displays outages for South Florida counties
    (Miami-Dade, Broward, Palm Beach)
    """
    if not data or 'outages' not in data:
        print("No outage data available")
        return
    
    # South Florida counties we care about
    south_fl_counties = ['Miami-Dade', 'Broward', 'Palm Beach']
    
    print("\n" + "=" * 70)
    print("SOUTH FLORIDA POWER OUTAGES (FPL)")
    print("=" * 70)
    
    total_out = 0
    total_served = 0
    
    for outage in data['outages']:
        county = outage.get('County Name', '')
        
        if county in south_fl_counties:
            customers_out = int(outage.get('Customers Out', '0').replace(',', ''))
            customers_served = int(outage.get('Customers Served', '0').replace(',', ''))
            
            total_out += customers_out
            total_served += customers_served
            
            percentage = (customers_out / customers_served * 100) if customers_served > 0 else 0
            
            print(f"\n{county} County:")
            print(f"  Customers Out: {customers_out:,}")
            print(f"  Total Customers: {customers_served:,}")
            print(f"  Percentage Affected: {percentage:.2f}%")
    
    print("\n" + "-" * 70)
    print(f"TOTAL SOUTH FLORIDA:")
    print(f"  Customers Out: {total_out:,}")
    print(f"  Total Customers: {total_served:,}")
    
    overall_percentage = (total_out / total_served * 100) if total_served > 0 else 0
    print(f"  Percentage Affected: {overall_percentage:.2f}%")
    
    print("=" * 70)
    print(f"Data retrieved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")


def outages_to_records(data):
    """
    Convert raw FPL outage JSON into the list-of-dicts format expected by
    OutageDatabase.log_multiple_outages()
    """
    outage_list = []
    for outage in data.get('outages', []):
        county = outage.get('County Name', '')
        customers_out = int(outage.get('Customers Out', '0').replace(',', ''))
        customers_served = int(outage.get('Customers Served', '0').replace(',', ''))

        outage_list.append({
            'county': county,
            'customers_out': customers_out,
            'customers_served': customers_served
        })
    return outage_list


def get_combined_fpl_records():
    """
    Fetch both FPL feeds (main + Panhandle) and combine into one list of
    records, ready for log_multiple_outages()/sync_outage_events() - both
    represent the same real utility, just two technical data sources, so
    they're combined here rather than tracked as separate utilities the
    way TECO/Duke/JEA are. The Panhandle feed is a bonus on top of the
    main one: if it's unset or fails, this still returns the main feed's
    records rather than failing the whole cycle.
    """
    records = []

    main_data = fetch_fpl_outages()
    if main_data:
        records.extend(outages_to_records(main_data))

    northwest_data = fetch_fpl_northwest_outages()
    if northwest_data:
        records.extend(outages_to_records(northwest_data))

    return records


def main():
    """
    Main function - fetches FPL data (both feeds), displays it, and saves
    to database
    """
    # Fetch the data
    data = fetch_fpl_outages()

    if not data:
        print("Failed to fetch outage data")
        return

    # Display it
    display_south_florida_outages(data)

    # Save to database
    print("\nSaving data to database...")
    db = OutageDatabase()

    # Convert FPL data format to our database format, combining both feeds
    outage_list = get_combined_fpl_records()

    # Log all counties at once
    db.log_multiple_outages(UTILITY_NAME, outage_list)
    db.close()

    print("✓ Data saved to database!")


if __name__ == "__main__":
    main()

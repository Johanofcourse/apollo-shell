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
        
        # Debug: print what we actually got
        print(f"Status Code: {response.status_code}")
        print(f"Content Type: {response.headers.get('content-type')}")
        print(f"First 200 chars of response: {response.text[:200]}")
        
        return response.json()
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
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


def main():
    """
    Main function - fetches FPL data, displays it, and saves to database
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

    # Convert FPL data format to our database format
    outage_list = outages_to_records(data)

    print(f"DEBUG: Prepared {len(outage_list)} records to save")

    # Log all counties at once
    db.log_multiple_outages(UTILITY_NAME, outage_list)
    db.close()

    print("✓ Data saved to database!")


if __name__ == "__main__":
    main()

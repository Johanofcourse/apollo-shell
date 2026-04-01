import requests
import json
from datetime import datetime


def fetch_fpl_outages():
    """
    Fetches live outage data from FPL's CountyOutages.json endpoint
    Returns the parsed JSON data
    """
    url = "https://www.fplmaps.com/customer/outage/CountyOutages.json"
    
    try:
        print("Fetching FPL outage data...")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.fplmaps.com/'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
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


def main():
    """
    Main function - fetches and displays FPL outage data
    """
    data = fetch_fpl_outages()
    
    if data:
        display_south_florida_outages(data)
    else:
        print("Failed to fetch outage data")


if __name__ == "__main__":
    main()

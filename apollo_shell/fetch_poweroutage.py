import requests
import json
from datetime import datetime


def fetch_poweroutage_data():
    """
    Fetches live outage data from PowerOutage.us
    Returns the raw JSON response
    """
    # Use the full URL with query parameters from the browser
    url = "https://poweroutage.us/__data.json?x-sveltekit-trailing-slash=1"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://poweroutage.us/',
        'x-sveltekit-fetch': 'true'
    }
    
    try:
        session = requests.Session()
        
        # Visit main page first
        session.get('https://poweroutage.us/', headers=headers, timeout=10)
        
        # Add a small delay to look more human
        import time
        time.sleep(1)
        
        # Fetch data with SvelteKit parameters
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return None


def parse_florida_data(json_data):
    """
    Parses the compressed PowerOutage.us JSON format
    Finds and returns Florida's outage data
    """
    try:
        # Navigate to the states data node
        # The structure is: nodes[2]["data"][2] contains state records
        states_node = json_data["nodes"][2]["data"]
        
        # Index 2 has the schema (field names mapped to positions)
        schema = states_node[2]
        
        # Index 1 has the array of state data arrays
        state_arrays = states_node[1]
        
        # Find Florida in the data
        for state_array in state_arrays:
            state_dict = {}
            
            # Map the schema positions to values
            for key, position in schema.items():
                if position < len(state_array):
                    state_dict[key] = state_array[position]
            
            # Check if this is Florida
            if state_dict.get("stateAbbr") == "FL":
                return state_dict
        
        print("Florida not found in data")
        return None
    
    except Exception as e:
        print(f"Error parsing data: {e}")
        return None    


def main():
    """
    Main function - fetches and displays Florida outage data
    """
    print("Fetching Florida outage data from PowerOutage.us...")
    print("-" * 60)
    
    # Fetch the data
    data = fetch_poweroutage_data()
    
    if not data:
        print("Failed to fetch data")
        return
    
    # Parse Florida's data
    fl_data = parse_florida_data(data)
    
    if not fl_data:
        print("Failed to parse Florida data")
        return
    
    # Display the results
    print(f"\nState: {fl_data.get('stateName')}")
    print(f"Status: {fl_data.get('status')}")
    print(f"Customers Out: {fl_data.get('outageCount'):,}")
    print(f"Total Customers: {fl_data.get('customerCount'):,}")
    
    # Parse and display the timestamp
    if fl_data.get('lastUpdated'):
        timestamp_str = fl_data['lastUpdated'][1]  # It's stored as ["Date", "ISO-string"]
        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        print(f"Last Updated: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    print("-" * 60)


if __name__ == "__main__":
    main()

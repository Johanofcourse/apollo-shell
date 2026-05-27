import requests
from datetime import datetime


def fetch_florida_alerts():
    """
    Fetches active weather alerts for Florida from National Weather Service API
    Returns list of active alerts
    """
    url = "https://api.weather.gov/alerts/active?area=FL"
    
    headers = {
        'User-Agent': 'Apollo-Shell/1.0 (Grid Outage Tracker)',
        'Accept': 'application/json'
    }
    
    try:
        print("Fetching Florida weather alerts...")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Extract the features (alerts) from the GeoJSON response
        alerts = data.get('features', [])
        
        print(f"Found {len(alerts)} active weather alerts in Florida")
        
        return alerts
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        return []


def parse_alert(alert):
    """
    Parse a single alert into a clean format
    
    Args:
        alert: GeoJSON feature object from NWS API
    
    Returns:
        Dictionary with cleaned alert data
    """
    props = alert.get('properties', {})
    
    return {
        'event': props.get('event'),  # e.g., "Wind Advisory"
        'headline': props.get('headline'),
        'severity': props.get('severity'),  # e.g., "Moderate", "Severe"
        'urgency': props.get('urgency'),  # e.g., "Expected", "Immediate"
        'areas': props.get('areaDesc'),  # Affected counties
        'effective': props.get('effective'),
        'expires': props.get('expires'),
        'description': props.get('description')
    }


def get_alerts_summary():
    """
    Get a summary of current weather alerts affecting Florida
    Returns dict with alert types and counts
    """
    alerts = fetch_florida_alerts()
    
    if not alerts:
        return {"total": 0, "by_type": {}}
    
    # Count alerts by type
    alert_counts = {}
    for alert in alerts:
        event_type = alert.get('properties', {}).get('event', 'Unknown')
        alert_counts[event_type] = alert_counts.get(event_type, 0) + 1
    
    return {
        "total": len(alerts),
        "by_type": alert_counts,
        "alerts": [parse_alert(a) for a in alerts]
    }


def main():
    """
    Test function - displays current Florida weather alerts
    """
    print("=" * 70)
    print("FLORIDA WEATHER ALERTS (National Weather Service)")
    print("=" * 70)
    
    summary = get_alerts_summary()
    
    if summary['total'] == 0:
        print("\nNo active weather alerts in Florida.")
    else:
        print(f"\nTotal active alerts: {summary['total']}\n")
        
        # Show breakdown by type
        print("Alerts by type:")
        for alert_type, count in summary['by_type'].items():
            print(f"  - {alert_type}: {count}")
        
        # Show details for each alert
        print("\n" + "-" * 70)
        print("ALERT DETAILS:")
        print("-" * 70)
        
        for alert in summary['alerts'][:5]:  # Show first 5 for brevity
            print(f"\n{alert['event']}")
            print(f"Severity: {alert['severity']} | Urgency: {alert['urgency']}")
            print(f"Areas: {alert['areas']}")
            print(f"Valid: {alert['effective']} to {alert['expires']}")
    
    print("\n" + "=" * 70)
    print(f"Data retrieved at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == "__main__":
    main()

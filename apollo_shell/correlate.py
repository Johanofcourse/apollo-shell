from datetime import datetime

from database import OutageDatabase


def _parse_timestamp(value):
    """
    Parse an ISO 8601 timestamp string, tolerating a trailing 'Z'.

    Timezone-aware timestamps (weather alerts, from the NWS API) are
    converted to naive local time so they can be compared against outage
    timestamps, which are always naive local time (captured via
    datetime.now()).
    """
    if not value:
        return None

    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _normalize(text):
    return text.lower().replace('.', '').strip()


def _county_in_alert(county, areas):
    """
    Check whether a county name appears in a weather alert's areaDesc
    string, e.g. "Miami-Dade; Broward; Palm Beach"
    """
    if not areas:
        return False
    return _normalize(county) in _normalize(areas)


def _alert_covers_time(effective, expires, outage_time):
    """
    True if outage_time falls within [effective, expires]. A missing bound
    is treated as open-ended on that side.
    """
    if effective and outage_time < effective:
        return False
    if expires and outage_time > expires:
        return False
    return True


def find_correlations(db_path="outages.db"):
    """
    Match outage records to weather alerts active in the same county at the
    same time (county name match + effective/expires time overlap).

    Returns a list of {"outage": {...}, "alert": {...}} dicts, one per match.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM outages')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    matches = []
    for outage in outages:
        outage_time = _parse_timestamp(outage['timestamp'])
        if outage_time is None:
            continue

        for alert in alerts:
            if not _county_in_alert(outage['county'], alert['areas']):
                continue

            effective = _parse_timestamp(alert.get('effective'))
            expires = _parse_timestamp(alert.get('expires'))

            if _alert_covers_time(effective, expires, outage_time):
                matches.append({"outage": outage, "alert": alert})

    return matches


def find_teco_correlations(db_path="outages.db"):
    """
    Match TECO incidents to weather alerts active in the same county at
    the same time. Same logic as find_correlations(), adapted for TECO's
    incident-level schema (county + update_time instead of a plain
    county-rollup timestamp).

    Returns a list of {"incident": {...}, "alert": {...}} dicts.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM teco_incidents')
    incidents = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    matches = []
    for incident in incidents:
        if not incident.get('county'):
            continue

        incident_time = _parse_timestamp(incident.get('update_time'))
        if incident_time is None:
            continue

        for alert in alerts:
            if not _county_in_alert(incident['county'], alert['areas']):
                continue

            effective = _parse_timestamp(alert.get('effective'))
            expires = _parse_timestamp(alert.get('expires'))

            if _alert_covers_time(effective, expires, incident_time):
                matches.append({"incident": incident, "alert": alert})

    return matches


def teco_correlation_summary(matches):
    """
    Aggregate correlated TECO incident/alert pairs by county.

    Returns a dict keyed by county:
        {
            "<county>": {
                "incident_count": int,
                "max_customer_count": int,
                "alert_types": {"Tornado Warning": 2, ...},
            },
            ...
        }
    """
    summary = {}

    for match in matches:
        county = match["incident"]["county"]
        entry = summary.setdefault(county, {
            "incident_count": 0,
            "max_customer_count": 0,
            "alert_types": {},
        })

        entry["incident_count"] += 1
        entry["max_customer_count"] = max(
            entry["max_customer_count"], match["incident"]["customer_count"] or 0
        )

        event_type = match["alert"]["event_type"]
        entry["alert_types"][event_type] = entry["alert_types"].get(event_type, 0) + 1

    return summary


def find_duke_correlations(db_path="outages.db"):
    """
    Match Duke Energy incidents to weather alerts active in the same
    county at the same time. Same logic as find_teco_correlations(),
    adapted for Duke's schema - Duke's raw incidents have no per-record
    update_time (unlike TECO's), so fetched_at (our own poll timestamp)
    is used as the incident time instead.

    Returns a list of {"incident": {...}, "alert": {...}} dicts.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM duke_incidents')
    incidents = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    matches = []
    for incident in incidents:
        if not incident.get('county'):
            continue

        incident_time = _parse_timestamp(incident.get('fetched_at'))
        if incident_time is None:
            continue

        for alert in alerts:
            if not _county_in_alert(incident['county'], alert['areas']):
                continue

            effective = _parse_timestamp(alert.get('effective'))
            expires = _parse_timestamp(alert.get('expires'))

            if _alert_covers_time(effective, expires, incident_time):
                matches.append({"incident": incident, "alert": alert})

    return matches


def duke_correlation_summary(matches):
    """
    Aggregate correlated Duke incident/alert pairs by county.

    Returns a dict keyed by county:
        {
            "<county>": {
                "incident_count": int,
                "max_customer_count": int,
                "alert_types": {"Tornado Warning": 2, ...},
            },
            ...
        }
    """
    summary = {}

    for match in matches:
        county = match["incident"]["county"]
        entry = summary.setdefault(county, {
            "incident_count": 0,
            "max_customer_count": 0,
            "alert_types": {},
        })

        entry["incident_count"] += 1
        entry["max_customer_count"] = max(
            entry["max_customer_count"], match["incident"]["customer_count"] or 0
        )

        event_type = match["alert"]["event_type"]
        entry["alert_types"][event_type] = entry["alert_types"].get(event_type, 0) + 1

    return summary


def correlation_summary(matches):
    """
    Aggregate correlated outage/alert pairs by county.

    Returns a dict keyed by county:
        {
            "<county>": {
                "outage_count": int,
                "max_percentage_out": float,
                "alert_types": {"Tornado Warning": 2, ...},
            },
            ...
        }
    """
    summary = {}

    for match in matches:
        county = match["outage"]["county"]
        entry = summary.setdefault(county, {
            "outage_count": 0,
            "max_percentage_out": 0.0,
            "alert_types": {},
        })

        entry["outage_count"] += 1
        entry["max_percentage_out"] = max(
            entry["max_percentage_out"], match["outage"]["percentage_out"]
        )

        event_type = match["alert"]["event_type"]
        entry["alert_types"][event_type] = entry["alert_types"].get(event_type, 0) + 1

    return summary


def main():
    """
    Test function - prints correlated outages and weather alerts
    """
    print("=" * 70)
    print("APOLLO SHELL - OUTAGE / WEATHER CORRELATION")
    print("=" * 70)

    matches = find_correlations()

    if not matches:
        print("\nNo correlated outages found.")
        print("=" * 70)
        return

    print(f"\nFound {len(matches)} outage/alert matches\n")

    summary = correlation_summary(matches)
    ranked = sorted(
        summary.items(), key=lambda item: item[1]["max_percentage_out"], reverse=True
    )

    for county, stats in ranked:
        print(f"{county} County:")
        print(f"  Correlated outage records: {stats['outage_count']}")
        print(f"  Peak percentage out: {stats['max_percentage_out']:.2f}%")
        print(f"  Alert types: {stats['alert_types']}")
        print()

    print("=" * 70)


if __name__ == "__main__":
    main()

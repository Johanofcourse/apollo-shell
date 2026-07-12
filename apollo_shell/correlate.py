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


# Weather-match confidence: how likely a matched alert actually explains
# an outage, vs. being coincidental overlap in county + time. Event-type
# plausibility is the primary driver, not NWS's own severity field - a
# "Severe" Rip Current Statement should never outrank a "Moderate"
# Tornado Warning, since only one of them can physically cause a power
# outage at all. Grounded in event types actually observed in our own
# weather_alerts data (Special Weather Statement, Flood Advisory, Severe
# Thunderstorm Warning, Rip Current Statement, Heat Advisory all really
# showed up), extended to cover other realistic Florida hazards seen in
# the historical storm dataset but not yet live (hurricane/tropical,
# winter weather, wind). Unrecognized event types default to "medium" -
# an unfamiliar type shouldn't be assumed confidently relevant OR
# confidently irrelevant.
EVENT_TYPE_PLAUSIBILITY = {
    # High: direct wind/ice/tree-damage risk
    "Tornado Warning": "high",
    "Tornado Watch": "high",
    "Severe Thunderstorm Warning": "high",
    "Severe Thunderstorm Watch": "high",
    "Hurricane Warning": "high",
    "Hurricane Watch": "high",
    "Hurricane Force Wind Warning": "high",
    "Tropical Storm Warning": "high",
    "Tropical Storm Watch": "high",
    "Extreme Wind Warning": "high",
    "High Wind Warning": "high",
    "Ice Storm Warning": "high",
    "Winter Storm Warning": "high",
    "Storm Warning": "high",

    # Medium: can contribute, or a milder/earlier-stage version of a
    # high-tier hazard
    "Special Weather Statement": "medium",
    "Flood Advisory": "medium",
    "Flood Warning": "medium",
    "Flash Flood Warning": "medium",
    "Flash Flood Watch": "medium",
    "Coastal Flood Warning": "medium",
    "Wind Advisory": "medium",
    "High Wind Watch": "medium",
    "Winter Weather Advisory": "medium",
    "Winter Storm Watch": "medium",
    "Freeze Warning": "medium",
    "Hard Freeze Warning": "medium",
    "Storm Watch": "medium",
    # Sustained extreme heat (not routine hot weather) has a real, if
    # indirect, grid-strain mechanism - peak AC demand pushing capacity,
    # plus genuine equipment thermal stress - distinct from wind/ice
    # storm damage but not "no connection" either. "Heat Advisory" below
    # is the routine/lower-threshold version and stays low.
    "Excessive Heat Warning": "medium",

    # Low: no meaningful physical connection to power outages
    "Rip Current Statement": "low",
    "Beach Hazards Statement": "low",
    "Coastal Flood Advisory": "low",
    "Heat Advisory": "low",
    "Air Quality Alert": "low",
    "Small Craft Advisory": "low",
    "Dense Fog Advisory": "low",
    "Frost Advisory": "low",
    "Marine Warning": "low",
    "Gale Warning": "low",
}
DEFAULT_EVENT_TYPE_PLAUSIBILITY = "medium"

# NWS's own severity field - a secondary modifier, only applied within a
# plausibility tier (never lets severity alone override plausibility).
SEVERITY_SCORE = {"Extreme": 2, "Severe": 2, "Moderate": 1, "Minor": 0, "Unknown": 0}


def weather_match_confidence(event_type, severity):
    """
    Label ("high"/"medium"/"low") for how much a matched weather alert
    should be trusted as a real explanation for an outage. Deliberately a
    label, not a numeric percentage - the underlying signal (county +
    time overlap, plus NWS's own event-type/severity fields) doesn't
    support that kind of false precision.
    """
    plausibility = EVENT_TYPE_PLAUSIBILITY.get(event_type, DEFAULT_EVENT_TYPE_PLAUSIBILITY)
    severity_score = SEVERITY_SCORE.get(severity, 0)

    if plausibility == "low":
        return "low"
    if plausibility == "high":
        return "high" if severity_score >= 2 else "medium"
    # plausibility == "medium"
    return "medium" if severity_score >= 1 else "low"


def find_correlations(db_path="outages.db"):
    """
    Match outage records to weather alerts active in the same county at the
    same time (county name match + effective/expires time overlap).

    Only rows with a real outage (customers_out > 0) are considered -
    the raw outages table logs a fresh snapshot every poll cycle for
    every county regardless of whether anything was actually wrong, so
    without this filter a weather alert merely being active while
    nothing was happening counted as a "correlated outage." Found
    2026-07-12: this was inflating FPL's match counts by ~59% (18,151 ->
    7,495 once filtered, checked directly against the real data).

    Returns a list of {"outage": {...}, "alert": {...}} dicts, one per match.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM outages WHERE customers_out > 0')
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
                confidence = weather_match_confidence(alert.get('event_type'), alert.get('severity'))
                matches.append({"outage": outage, "alert": alert, "confidence": confidence})

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
                confidence = weather_match_confidence(alert.get('event_type'), alert.get('severity'))
                matches.append({"incident": incident, "alert": alert, "confidence": confidence})

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
                "confidence_breakdown": {"high": 1, "medium": 2, "low": 3},
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
            "confidence_breakdown": {},
        })

        entry["incident_count"] += 1
        entry["max_customer_count"] = max(
            entry["max_customer_count"], match["incident"]["customer_count"] or 0
        )

        event_type = match["alert"]["event_type"]
        entry["alert_types"][event_type] = entry["alert_types"].get(event_type, 0) + 1

        confidence = match["confidence"]
        entry["confidence_breakdown"][confidence] = entry["confidence_breakdown"].get(confidence, 0) + 1

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
                confidence = weather_match_confidence(alert.get('event_type'), alert.get('severity'))
                matches.append({"incident": incident, "alert": alert, "confidence": confidence})

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
                "confidence_breakdown": {"high": 1, "medium": 2, "low": 3},
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
            "confidence_breakdown": {},
        })

        entry["incident_count"] += 1
        entry["max_customer_count"] = max(
            entry["max_customer_count"], match["incident"]["customer_count"] or 0
        )

        event_type = match["alert"]["event_type"]
        entry["alert_types"][event_type] = entry["alert_types"].get(event_type, 0) + 1

        confidence = match["confidence"]
        entry["confidence_breakdown"][confidence] = entry["confidence_breakdown"].get(confidence, 0) + 1

    return summary


def find_jea_correlations(db_path="outages.db"):
    """
    Match JEA's raw per-ZIP outage snapshots to weather alerts active in
    the same county at the same time. Same logic/shape as
    find_correlations() (JEA's county-rollup shape matches FPL's, unlike
    TECO/Duke's incident shape) - reads jea_outages rather than outages,
    kept as its own dedicated table per the same one-utility-per-table
    convention used everywhere else in this project.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning and same real bug as find_correlations() above (found
    2026-07-12): JEA's raw table logs a fresh row every cycle per ZIP
    regardless of whether anything was actually wrong (82.7% of JEA's
    raw rows have customers_out = 0), which was inflating match counts
    by ~84% (596 -> 97 once filtered, checked directly against the real
    data - proportionally worse than FPL's ~59% since JEA's ZIP-level
    polling logs even more "nothing happening" rows per real outage).

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM jea_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    matches = []
    for outage in outages:
        if not outage.get('county'):
            continue

        outage_time = _parse_timestamp(outage['timestamp'])
        if outage_time is None:
            continue

        for alert in alerts:
            if not _county_in_alert(outage['county'], alert['areas']):
                continue

            effective = _parse_timestamp(alert.get('effective'))
            expires = _parse_timestamp(alert.get('expires'))

            if _alert_covers_time(effective, expires, outage_time):
                confidence = weather_match_confidence(alert.get('event_type'), alert.get('severity'))
                matches.append({"outage": outage, "alert": alert, "confidence": confidence})

    return matches


def correlation_summary(matches):
    """
    Aggregate correlated outage/alert pairs by county.

    Returns a dict keyed by county:
        {
            "<county>": {
                "outage_count": int,
                "max_percentage_out": float,
                "alert_types": {"Tornado Warning": 2, ...},
                "confidence_breakdown": {"high": 1, "medium": 2, "low": 3},
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
            "confidence_breakdown": {},
        })

        entry["outage_count"] += 1
        entry["max_percentage_out"] = max(
            entry["max_percentage_out"], match["outage"]["percentage_out"]
        )

        event_type = match["alert"]["event_type"]
        entry["alert_types"][event_type] = entry["alert_types"].get(event_type, 0) + 1

        confidence = match["confidence"]
        entry["confidence_breakdown"][confidence] = entry["confidence_breakdown"].get(confidence, 0) + 1

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
        print(f"  Confidence breakdown: {stats['confidence_breakdown']}")
        print()

    print("=" * 70)


if __name__ == "__main__":
    main()

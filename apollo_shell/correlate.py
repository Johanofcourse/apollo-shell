from datetime import datetime, timedelta

from database import OutageDatabase


def _alert_identity(alert):
    """
    A stable identity for one alert, for de-duplication - NWS's own
    alert_id when we have one (the normal case), or a synthetic identity
    built from its effective time + areas when we don't (~5 known
    legacy rows from before alert_id tracking existed - see
    fetch_weather.py's synthetic-id fallback for new ones).
    """
    return alert.get("alert_id") or f"noid:{alert.get('effective')}:{alert.get('areas')}"


def _window_cutoff(days):
    """
    ISO cutoff timestamp for "the last N days," or None if days is None
    (meaning: no window, all-time - the historical default, still used
    when a caller doesn't ask for a bounded window).
    """
    if days is None:
        return None
    return (datetime.now() - timedelta(days=days)).isoformat()


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


def _match_items_to_alerts(items, alerts, timestamp_key, item_label):
    """
    Shared matching core for every find_*_correlations() function below -
    all 16 turned out to share the exact same nested comparison shape,
    differing only in which field holds the item's timestamp and what
    key the output dict uses for the matched item ("outage" vs
    "incident"). Pulled out into one place 2026-07-17 after a real,
    measured cold dashboard load costing over a minute of actual CPU
    time (confirmed directly on the VM, not assumed) - the original
    per-item, per-alert nested loop reparsed every alert's
    effective/expires timestamps and re-ran the county substring check
    on every single pass, which stayed cheap while the live tables were
    small and got measurably worse as they grew into the tens of
    thousands of rows.

    Each alert's timestamps are now parsed exactly once here, not once
    per item compared against it. Which alerts match a given county is
    also computed once per distinct county actually seen (there are only
    ever 67 real Florida counties, vastly fewer than the item row
    count), not once per item row. Everything else - which matches are
    found, in what order - is identical to the original nested-loop
    version; this is a pure speed fix, not a behavior change.
    """
    parsed_alerts = [
        (alert, _parse_timestamp(alert.get('effective')), _parse_timestamp(alert.get('expires')))
        for alert in alerts
    ]

    alerts_for_county = {}
    matches = []

    for item in items:
        county = item.get('county')
        if not county:
            continue

        item_time = _parse_timestamp(item.get(timestamp_key))
        if item_time is None:
            continue

        if county not in alerts_for_county:
            alerts_for_county[county] = [
                (alert, effective, expires)
                for alert, effective, expires in parsed_alerts
                if _county_in_alert(county, alert['areas'])
            ]

        for alert, effective, expires in alerts_for_county[county]:
            if _alert_covers_time(effective, expires, item_time):
                confidence = weather_match_confidence(alert.get('event_type'), alert.get('severity'))
                matches.append({item_label: item, "alert": alert, "confidence": confidence})

    return matches


def find_correlations(db_path="outages.db", days=None):
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

    days: if given, only considers outage snapshots from the last N
    days - added 2026-07-12 alongside the above fix, since without a
    window these counts are all-time since the poller first started and
    only ever grow. None (the default) preserves the old all-time
    behavior for any caller that doesn't ask for a window.

    Returns a list of {"outage": {...}, "alert": {...}} dicts, one per match.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_teco_correlations(db_path="outages.db", days=None):
    """
    Match TECO incidents to weather alerts active in the same county at
    the same time. Same logic as find_correlations(), adapted for TECO's
    incident-level schema (county + update_time instead of a plain
    county-rollup timestamp).

    days: same windowing as find_correlations() - bounds by fetched_at
    (our own poll timestamp), not TECO's own update_time, so the window
    means "polled in the last N days" consistently across all sources.

    Returns a list of {"incident": {...}, "alert": {...}} dicts.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM teco_incidents WHERE fetched_at >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM teco_incidents')
    incidents = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(incidents, alerts, timestamp_key='update_time', item_label='incident')


def teco_correlation_summary(matches):
    """
    Aggregate correlated TECO incident/alert pairs by county.

    incident_count, alert_types, AND confidence_breakdown all count
    DISTINCT things (added 2026-07-12, alongside days= windowing in
    find_teco_correlations()) - previously counted every matched
    (incident-snapshot, alert) PAIR, so one long-running alert
    overlapping many 15-minute poll cycles for the same incident
    inflated every one of these numbers far past anything meaningful (a
    real example caught after the first pass at this fix only covered
    alert_types: the combined KPI strip still showed "low x27118" -
    confidence is purely a function of the alert's own event_type +
    severity via weather_match_confidence(), not of which incident it
    happened to match, so it needs the exact same per-alert
    deduplication as alert_types, not its own separate count).
    incident_count counts distinct (incident_id, fetched_at) snapshots;
    alert_types and confidence_breakdown are both derived from the same
    set of distinct matched alerts (by alert_id) per county.

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
    raw = {}

    for match in matches:
        county = match["incident"]["county"]
        entry = raw.setdefault(county, {
            "incident_keys": set(),
            "max_customer_count": 0,
            "matched_alerts": {},
        })

        entry["incident_keys"].add((match["incident"]["incident_id"], match["incident"]["fetched_at"]))
        entry["max_customer_count"] = max(
            entry["max_customer_count"], match["incident"]["customer_count"] or 0
        )
        entry["matched_alerts"][_alert_identity(match["alert"])] = (
            match["alert"]["event_type"], match["confidence"]
        )

    summary = {}
    for county, entry in raw.items():
        alert_types = {}
        confidence_breakdown = {}
        for event_type, confidence in entry["matched_alerts"].values():
            alert_types[event_type] = alert_types.get(event_type, 0) + 1
            confidence_breakdown[confidence] = confidence_breakdown.get(confidence, 0) + 1

        summary[county] = {
            "incident_count": len(entry["incident_keys"]),
            "max_customer_count": entry["max_customer_count"],
            "alert_types": alert_types,
            "confidence_breakdown": confidence_breakdown,
        }
    return summary


def find_duke_correlations(db_path="outages.db", days=None):
    """
    Match Duke Energy incidents to weather alerts active in the same
    county at the same time. Same logic as find_teco_correlations(),
    adapted for Duke's schema - Duke's raw incidents have no per-record
    update_time (unlike TECO's), so fetched_at (our own poll timestamp)
    is used as the incident time instead.

    days: same windowing as find_correlations()/find_teco_correlations().

    Returns a list of {"incident": {...}, "alert": {...}} dicts.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM duke_incidents WHERE fetched_at >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM duke_incidents')
    incidents = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(incidents, alerts, timestamp_key='fetched_at', item_label='incident')


def duke_correlation_summary(matches):
    """
    Aggregate correlated Duke incident/alert pairs by county. Same
    distinct-counting fix as teco_correlation_summary() above (2026-07-12,
    extended same day to also cover confidence_breakdown - see its
    docstring for why confidence needed the same per-alert
    deduplication as alert_types, not its own separate count).

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
    raw = {}

    for match in matches:
        county = match["incident"]["county"]
        entry = raw.setdefault(county, {
            "incident_keys": set(),
            "max_customer_count": 0,
            "matched_alerts": {},
        })

        entry["incident_keys"].add((match["incident"]["incident_id"], match["incident"]["fetched_at"]))
        entry["max_customer_count"] = max(
            entry["max_customer_count"], match["incident"]["customer_count"] or 0
        )
        entry["matched_alerts"][_alert_identity(match["alert"])] = (
            match["alert"]["event_type"], match["confidence"]
        )

    summary = {}
    for county, entry in raw.items():
        alert_types = {}
        confidence_breakdown = {}
        for event_type, confidence in entry["matched_alerts"].values():
            alert_types[event_type] = alert_types.get(event_type, 0) + 1
            confidence_breakdown[confidence] = confidence_breakdown.get(confidence, 0) + 1

        summary[county] = {
            "incident_count": len(entry["incident_keys"]),
            "max_customer_count": entry["max_customer_count"],
            "alert_types": alert_types,
            "confidence_breakdown": confidence_breakdown,
        }
    return summary


def find_tallahassee_correlations(db_path="outages.db", days=None):
    """
    Match City of Tallahassee incidents to weather alerts active in the
    same county at the same time. Same logic/shape as
    find_duke_correlations() - Tallahassee's raw incidents have no
    per-record update time either, so fetched_at (our own poll
    timestamp) is used as the incident time, same reasoning as Duke.

    days: same windowing as find_correlations()/find_teco_correlations().

    Returns a list of {"incident": {...}, "alert": {...}} dicts - reuse
    duke_correlation_summary() directly for aggregation, since the shape
    (and even the summary function's body) is identical to Duke's/TECO's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM tallahassee_incidents WHERE fetched_at >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM tallahassee_incidents')
    incidents = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(incidents, alerts, timestamp_key='fetched_at', item_label='incident')


def find_jea_correlations(db_path="outages.db", days=None):
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

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM jea_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM jea_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_talquin_correlations(db_path="outages.db", days=None):
    """
    Match Talquin Electric Cooperative's raw county-level snapshots to
    weather alerts active in the same county at the same time. Same
    logic/shape as find_correlations()/find_jea_correlations() - a
    county-rollup source like FPL/JEA, not an incident shape like
    TECO/Duke/Tallahassee - reads talquin_outages rather than outages,
    kept as its own dedicated table per the same one-utility-per-table
    convention used everywhere else in this project.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_correlations()/find_jea_correlations(): the
    raw table logs a fresh row every poll cycle per county regardless of
    whether anything was actually wrong.

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM talquin_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM talquin_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_preco_correlations(db_path="outages.db", days=None):
    """
    Match Peace River Electric Cooperative's raw county-level snapshots
    to weather alerts active in the same county at the same time. Same
    logic/shape as find_talquin_correlations() - a county-rollup source,
    reads preco_outages rather than outages, kept as its own dedicated
    table per the same one-utility-per-table convention used everywhere
    else in this project.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_talquin_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM preco_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM preco_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_fkec_correlations(db_path="outages.db", days=None):
    """
    Match Florida Keys Electric Cooperative's raw snapshots to weather
    alerts active in Monroe County at the same time. Same logic/shape
    as find_preco_correlations() - a county-rollup source (always
    exactly one row, Monroe - see fetch_fkec_outages.SERVICE_COUNTY),
    reads fkec_outages rather than outages, kept as its own dedicated
    table per the same one-utility-per-table convention used everywhere
    else in this project.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_preco_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM fkec_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM fkec_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_tcec_correlations(db_path="outages.db", days=None):
    """
    Match Tri-County Electric Cooperative's raw combined-territory
    snapshots to weather alerts. Same shape as find_correlations() -
    reads tcec_outages, kept as its own dedicated table per the same
    one-utility-per-table convention used everywhere else here.

    In practice this will always return an empty list: TCEC's "county"
    is a multi-county combined label (see
    fetch_tcec_outages.COMBINED_TERRITORY_LABEL), which can never match
    a real NWS alert's single-county areaDesc string via
    _county_in_alert()'s substring check. Kept for the same reason
    FPUC's original combined-territory tracker had a correlation
    function despite the same limitation - an honest, self-documenting
    empty result rather than a special-cased skip, and ready to work
    correctly the moment a real per-region breakdown (TCEC's
    outagePolygons.json, currently seen only empty) reveals real
    per-county detail.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM tcec_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM tcec_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_erec_correlations(db_path="outages.db", days=None):
    """
    Match Escambia River Electric Cooperative's raw combined-territory
    snapshots to weather alerts. Same shape/limitation as
    find_tcec_correlations() (identical vendor platform) - reads
    erec_outages, kept as its own dedicated table per the same
    one-utility-per-table convention used everywhere else here.

    In practice this will always return an empty list: EREC's "county"
    is a multi-county combined label (see
    fetch_erec_outages.COMBINED_TERRITORY_LABEL), which can never match
    a real NWS alert's single-county areaDesc string via
    _county_in_alert()'s substring check. Kept for the same reason
    FPUC's original combined-territory tracker and TCEC's correlation
    function had one despite the same limitation - an honest, self-
    documenting empty result rather than a special-cased skip, and
    ready to work correctly the moment a real per-region breakdown
    (EREC's outagePolygons.json, currently seen only empty) reveals
    real per-county detail.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM erec_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM erec_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_chelco_correlations(db_path="outages.db", days=None):
    """
    Match Choctawhatchee Electric Cooperative's raw combined-territory
    snapshots to weather alerts. Same shape/limitation as
    find_erec_correlations()/find_tcec_correlations() (identical vendor
    platform) - reads chelco_outages, kept as its own dedicated table
    per the same one-utility-per-table convention used everywhere else
    here.

    In practice this will always return an empty list: CHELCO's
    "county" is a multi-county combined label (see
    fetch_chelco_outages.COMBINED_TERRITORY_LABEL), which can never
    match a real NWS alert's single-county areaDesc string via
    _county_in_alert()'s substring check. Kept for the same reason
    FPUC's original combined-territory tracker and TCEC's/EREC's
    correlation functions had one despite the same limitation - an
    honest, self-documenting empty result rather than a special-cased
    skip, and ready to work correctly the moment a real per-region
    breakdown (CHELCO's outagePolygons.json, currently seen only empty)
    reveals real per-county detail.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM chelco_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM chelco_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_gcec_correlations(db_path="outages.db", days=None):
    """
    Match Gulf Coast Electric Cooperative's raw combined-territory
    snapshots to weather alerts. Same shape/limitation as
    find_chelco_correlations()/find_erec_correlations()/
    find_tcec_correlations() (identical underlying platform) - reads
    gcec_outages, kept as its own dedicated table per the same
    one-utility-per-table convention used everywhere else here.

    In practice this will always return an empty list: GCEC's "county"
    is a multi-county combined label (see
    fetch_gcec_outages.COMBINED_TERRITORY_LABEL), which can never match
    a real NWS alert's single-county areaDesc string via
    _county_in_alert()'s substring check. Kept for the same reason
    FPUC's original combined-territory tracker and TCEC's/EREC's/
    CHELCO's correlation functions had one despite the same limitation -
    an honest, self-documenting empty result rather than a special-cased
    skip, and ready to work correctly the moment a real per-region
    breakdown (currently seen only empty) reveals real per-county
    detail.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM gcec_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM gcec_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_lwbu_correlations(db_path="outages.db", days=None):
    """
    Match Lake Worth Beach Utilities' raw single-county snapshots to
    weather alerts active in Palm Beach County at the same time. Same
    logic/shape as find_fkec_correlations() - a real single-county
    rollup source (always exactly one row, Palm Beach - see
    fetch_lwbu_outages.SERVICE_COUNTY), reads lwbu_outages rather than
    outages, kept as its own dedicated table per the same
    one-utility-per-table convention used everywhere else in this
    project.

    Deliberately reads the rollup, not lwbu_incidents - the same
    "pick exactly one real per-county source for correlation" principle
    already established for FPUC (where the reverse is true: its
    per-incident view is the one that's correlated, not its combined
    total), since correlating both here would double-count the same
    real customers against the same real weather window.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_fkec_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM lwbu_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM lwbu_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_ouc_correlations(db_path="outages.db", days=None):
    """
    Match Orlando Utilities Commission's raw single-county snapshots to
    weather alerts active in Orange County at the same time. Same
    logic/shape as find_fkec_correlations()/find_lwbu_correlations() - a
    real single-county rollup source (always exactly one row, Orange -
    see fetch_ouc_outages.SERVICE_COUNTY), reads ouc_outages rather than
    outages, kept as its own dedicated table per the same
    one-utility-per-table convention used everywhere else in this
    project.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_fkec_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM ouc_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM ouc_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_lcec_correlations(db_path="outages.db", days=None):
    """
    Match Lee County Electric Cooperative's raw per-county snapshots to
    weather alerts active in that same county at the same time. Same
    logic/shape as find_ouc_correlations()/find_fkec_correlations() - a
    real per-county rollup source, reads lcec_outages rather than
    outages, kept as its own dedicated table per the same
    one-utility-per-table convention used everywhere else in this
    project.

    Only rows with a real outage (customers_out > 0) are considered -
    same reasoning as find_fkec_correlations().

    days: same windowing as find_correlations().

    Returns a list of {"outage": {...}, "alert": {...}} dicts - reuse
    correlation_summary() below directly, since the shape matches FPL's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM lcec_outages WHERE customers_out > 0 AND timestamp >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM lcec_outages WHERE customers_out > 0')
    outages = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(outages, alerts, timestamp_key='timestamp', item_label='outage')


def find_fpuc_incident_correlations(db_path="outages.db", days=None):
    """
    Match FPUC's real per-incident markers to weather alerts active in
    the same county at the same time. Same logic as
    find_duke_correlations() - FPUC's raw incidents have no per-record
    update time either, so fetched_at (our own poll timestamp) is used
    as the incident time, same reasoning as Duke.

    Replaces an earlier version of this function that read the
    combined-territory table (fpuc_outages) and always returned an
    empty list by design, since that table's "county" is a fixed
    placeholder that can't match a real alert area. This one reads the
    real per-incident table (fpuc_incidents) instead - confirmed
    possible 2026-07-13 once a live outage finally populated FPUC's
    marker data (with real lat/lon, reverse-geocoded to a real county)
    for the first time. Per fetch_fpuc_outages.markers_to_incidents()'s
    own caveat, this may not include every real FPUC outage (some are
    withheld from markers for privacy), so this can undercount, but it
    is real correlation now, not a permanently-empty placeholder.

    days: same windowing as find_correlations()/find_duke_correlations().

    Returns a list of {"incident": {...}, "alert": {...}} dicts - reuse
    duke_correlation_summary() below directly, since the shape matches
    Duke's/TECO's.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cutoff = _window_cutoff(days)
    if cutoff is not None:
        cursor.execute('SELECT * FROM fpuc_incidents WHERE fetched_at >= ?', (cutoff,))
    else:
        cursor.execute('SELECT * FROM fpuc_incidents')
    incidents = [dict(row) for row in cursor.fetchall()]

    cursor.execute('SELECT * FROM weather_alerts')
    alerts = [dict(row) for row in cursor.fetchall()]

    db.close()

    return _match_items_to_alerts(incidents, alerts, timestamp_key='fetched_at', item_label='incident')


def correlation_summary(matches):
    """
    Aggregate correlated outage/alert pairs by county (shared by FPL and
    JEA - their match shape is identical, keyed "outage"/percentage_out).

    outage_count, alert_types, AND confidence_breakdown all count
    DISTINCT things (added 2026-07-12, alongside days= windowing in
    find_correlations()/find_jea_correlations()) - previously counted
    every matched (outage-snapshot, alert) PAIR, so one long-running
    alert overlapping many 15-minute poll cycles for the same outage
    inflated every one of these numbers far past anything meaningful (a
    real example: "Air Quality Alert x190" on a live dashboard row,
    which was really a handful of distinct alerts re-counted once per
    poll cycle each happened to overlap; a second real example caught
    after the first pass at this fix only covered alert_types: the
    combined KPI strip still showed "low x27118" - confidence is purely
    a function of the alert's own event_type + severity via
    weather_match_confidence(), not of which outage it happened to
    match, so it needs the exact same per-alert deduplication as
    alert_types, not its own separate count). outage_count counts
    distinct (county, timestamp) raw snapshots; alert_types and
    confidence_breakdown are both derived from the same set of distinct
    matched alerts (by alert_id) per county.

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
    raw = {}

    for match in matches:
        county = match["outage"]["county"]
        entry = raw.setdefault(county, {
            "outage_keys": set(),
            "max_percentage_out": 0.0,
            "matched_alerts": {},
        })

        entry["outage_keys"].add((match["outage"]["county"], match["outage"]["timestamp"]))
        entry["max_percentage_out"] = max(
            entry["max_percentage_out"], match["outage"]["percentage_out"]
        )
        entry["matched_alerts"][_alert_identity(match["alert"])] = (
            match["alert"]["event_type"], match["confidence"]
        )

    summary = {}
    for county, entry in raw.items():
        alert_types = {}
        confidence_breakdown = {}
        for event_type, confidence in entry["matched_alerts"].values():
            alert_types[event_type] = alert_types.get(event_type, 0) + 1
            confidence_breakdown[confidence] = confidence_breakdown.get(confidence, 0) + 1

        summary[county] = {
            "outage_count": len(entry["outage_keys"]),
            "max_percentage_out": entry["max_percentage_out"],
            "alert_types": alert_types,
            "confidence_breakdown": confidence_breakdown,
        }
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

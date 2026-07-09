import csv
import os
import re
from datetime import datetime, timedelta

import requests

from database import OutageDatabase


SEVERE_WEATHER_EVENT_TYPES = {
    "Hurricane (Typhoon)", "Tropical Storm", "Tropical Depression",
    "Winter Storm", "Ice Storm", "Extreme Cold/Wind Chill", "Heavy Snow",
    "Thunderstorm Wind", "Tornado", "Hail", "Funnel Cloud", "High Wind",
}
# NOAA also legitimately writes "peak intensity of NN mph" for real local
# damage-survey wind estimates (e.g. a tornado's EF-rating "determined by
# the roof decking damage here" - a real find while fixing this), so
# excluding that exact phrase generically was tried first and was wrong -
# it would have thrown out real local readings too. A looser "wind figure
# paired with a pressure figure within N characters" heuristic was also
# tried and was *worse* - checked against every already-imported storm and
# it wrongly nulled out dozens of genuine local readings, because two
# unrelated numbers often land within a few dozen characters of each
# other in a long narrative purely by coincidence. Both attempts are kept
# here as a lesson: the fix has to be a specific, anchored phrase, not a
# proximity heuristic, however tempting the "just look nearby" shortcut
# seems.
#
# Elsa's Pinellas County records had three *distinct* storm-history
# figures in the same narrative, each describing the storm-as-a-system at
# some point in its own track, never a local station: "peak intensity of
# 85 mph and 991 mb" (still in the Caribbean), "a second peak of 75 mph
# and 995 mb" (still offshore), and "maximum sustained winds of 65 mph and
# a minimum central pressure of 999 mb" (Taylor County's actual landfall -
# real, but misattributed when this same paragraph is repeated for other
# counties' records too). Real local readings in this same narrative
# style look like "ASOS site KSPG ... reported a 52 mph wind gust" - no
# pressure figure anywhere near them. Each known bad phrasing gets its own
# explicit pattern below - if a differently-worded false positive shows up
# for a future storm, add another explicit pattern the same way rather
# than generalizing into a proximity check again.
WIND_RE = re.compile(r"(\d{2,3})\s*mph", re.IGNORECASE)
STORM_HISTORY_WIND_PATTERNS = [
    re.compile(r"peak intensity of\s+(\d{2,3})\s*mph\s+and\s+\d+\s*mb", re.IGNORECASE),
    re.compile(r"second peak of\s+(\d{2,3})\s*mph\s+and\s+\d+\s*mb", re.IGNORECASE),
    re.compile(
        r"sustained winds? of\s+(\d{2,3})\s*mph\s+and\s+a\s+minimum\s+central\s+pressure\s+of\s+\d+\s*mb",
        re.IGNORECASE,
    ),
]
SNOW_RE = re.compile(r"between\s+(\d+)\s+and\s+(\d+)\s+inches?\s+of\s+snow", re.IGNORECASE)
WIND_CHILL_RE = re.compile(r"wind\s*chills?\s+of\s+(-?\d+)\s*-\s*(-?\d+)\s+degrees", re.IGNORECASE)
ICE_RE = re.compile(r"(?:a |an )?(quarter|half|three[-\s]quarters|\d+(?:\.\d+)?)\s*inch(?:es)?\s+of\s+ice", re.IGNORECASE)
ICE_WORD_VALUES = {"quarter": 0.25, "half": 0.5, "three quarters": 0.75, "three-quarters": 0.75}


def download_storm_events_csv(url, dest_path):
    """
    Download a NOAA Storm Events yearly CSV (gzip) and save it. Caller is
    responsible for knowing the correct URL (the "compile date" suffix in
    NOAA's filenames changes as they reprocess old years, so this isn't
    guessed - check https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/
    for the current filename).
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    with open(dest_path, 'wb') as f:
        f.write(response.content)
    return dest_path


def _normalize(text):
    return text.lower().replace('.', '').strip()


def _county_in_zone(county, zone_name):
    return _normalize(county) in _normalize(zone_name)


def _parse_noaa_datetime(value):
    """
    NOAA Storm Events timestamps look like "04-AUG-24 06:00:00"
    """
    if not value:
        return None
    return datetime.strptime(value, "%d-%b-%y %H:%M:%S")


def extract_wind_mph(narrative):
    """
    Pull the highest "NN mph" figure mentioned in a narrative, if any.
    Narratives are free text, so this is a best-effort extraction, not a
    guaranteed structured field - many records won't have one at all.

    Excludes matches that are actually a storm-history recap of the
    system's own historical peak (see STORM_HISTORY_WIND_PATTERNS) rather
    than a local reading - excluded by exact character span, not by
    value, so a real local reading with the same number elsewhere in the
    same narrative still counts.
    """
    narrative = narrative or ""
    excluded_spans = {
        m.span(1)
        for pattern in STORM_HISTORY_WIND_PATTERNS
        for m in pattern.finditer(narrative)
    }
    matches = [
        m.group(1) for m in WIND_RE.finditer(narrative)
        if m.span(1) not in excluded_spans
    ]
    if not matches:
        return None
    return max(int(m) for m in matches)


def extract_snow_inches(narrative):
    """
    Upper bound of a reported snowfall range, e.g. "between 4 and 6
    inches of snow" -> 6.0. Worse = higher, same convention as wind.
    """
    match = SNOW_RE.search(narrative or "")
    if not match:
        return None
    return float(match.group(2))


def extract_ice_inches(narrative):
    """
    Reported ice accretion thickness, e.g. "a quarter inch of ice" ->
    0.25. NOAA narratives describe this in words ("quarter", "half"),
    not always digits, so both forms are handled.
    """
    match = ICE_RE.search(narrative or "")
    if not match:
        return None
    value = match.group(1).lower()
    if value in ICE_WORD_VALUES:
        return ICE_WORD_VALUES[value]
    try:
        return float(value)
    except ValueError:
        return None


def extract_wind_chill_f(narrative):
    """
    Lower bound of a reported wind chill range, e.g. "wind chills of
    10-15 degrees" -> 10.0. Worse = lower here, opposite convention
    from wind/snow, since colder is more severe.
    """
    match = WIND_CHILL_RE.search(narrative or "")
    if not match:
        return None
    return float(min(int(match.group(1)), int(match.group(2))))


def extract_storm_severity(csv_path, storm_name, start_time, end_time, counties, buffer_days=1):
    """
    Filter a NOAA Storm Events CSV down to Florida hurricane/tropical-storm
    records whose BEGIN_DATE_TIME falls within a storm's known outage
    window (+/- buffer_days), fuzzy-matched to our tracked counties by
    zone name (NOAA tags these records by forecast zone, e.g.
    "COASTAL MANATEE", not plain county names).

    Args:
        csv_path: path to an already-downloaded NOAA Storm Events CSV
        storm_name: label to store alongside each matched record
        start_time / end_time: datetime bounds, typically taken from our
            own outage_events for this storm (MIN(start_time), MAX(end_time))
        counties: list of county names to match against (same casing as
            used in outage_events - i.e. ALL CAPS, from the PSC import)
        buffer_days: how many days before/after the outage window to also
            include (weather reports can be filed slightly before power
            actually goes out, or after it's restored)

    Returns a list of dicts ready for OutageDatabase.log_storm_severity()
    """
    window_start = start_time - timedelta(days=buffer_days)
    window_end = end_time + timedelta(days=buffer_days)

    records = []
    with open(csv_path, newline='', encoding='latin-1') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['STATE'] != 'FLORIDA':
                continue
            if row['EVENT_TYPE'] not in SEVERE_WEATHER_EVENT_TYPES:
                continue

            begin = _parse_noaa_datetime(row['BEGIN_DATE_TIME'])
            if begin is None or not (window_start <= begin <= window_end):
                continue

            zone_name = row['CZ_NAME']
            matched_county = next(
                (c for c in counties if _county_in_zone(c, zone_name)), None
            )
            if matched_county is None:
                continue

            # EVENT_NARRATIVE is specific to this zone/record. EPISODE_NARRATIVE
            # is a storm-wide summary repeated across every record in the
            # episode (e.g. landfall intensity, which may be from a county
            # 100+ miles away) - never use it as a stand-in for this zone's
            # own conditions, or it misattributes the storm's peak landfall
            # wind to every county touched by the episode.
            narrative = row['EVENT_NARRATIVE']
            records.append({
                'storm_name': storm_name,
                'county': matched_county,
                'zone_name': zone_name,
                'event_type': row['EVENT_TYPE'],
                'begin_time': row['BEGIN_DATE_TIME'],
                'end_time': row['END_DATE_TIME'],
                'reported_wind_mph': extract_wind_mph(narrative),
                'snow_inches': extract_snow_inches(narrative),
                'ice_inches': extract_ice_inches(narrative),
                'wind_chill_f': extract_wind_chill_f(narrative),
                'narrative': (narrative or '')[:500],
            })

    return records


def import_storm_severity(csv_path, storm_name, db_path, buffer_days=1):
    """
    Look up the given storm's own outage_events date range in db_path,
    extract matching NOAA severity records, and save them into a new
    storm_severity table in the same database.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('SELECT MIN(start_time), MAX(end_time) FROM outage_events')
    row = cursor.fetchone()
    start_time = datetime.fromisoformat(row[0])
    end_time = datetime.fromisoformat(row[1])

    cursor.execute('SELECT DISTINCT county FROM outage_events')
    counties = [r[0] for r in cursor.fetchall()]

    records = extract_storm_severity(
        csv_path, storm_name, start_time, end_time, counties, buffer_days
    )

    db.log_storm_severity(records)
    db.close()

    return records


def severity_vs_duration(db_path):
    """
    Join outage_events with storm_severity by county to compare reported
    wind speed against actual outage peak/duration for that county.

    Returns a list of dicts, one per county, sorted worst wind first.
    County-level, not per-record: takes each county's single worst
    outage_event and its max reported wind across all matched zones.
    """
    db = OutageDatabase(db_path)
    conn = db.connect()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT county, MAX(peak_percentage_out) AS peak_pct,
               MIN(start_time) AS start_time, MAX(end_time) AS end_time
        FROM outage_events
        GROUP BY county
    ''')
    outage_rows = {r['county']: dict(r) for r in cursor.fetchall()}

    cursor.execute('''
        SELECT county, MAX(reported_wind_mph) AS max_wind_mph
        FROM storm_severity
        GROUP BY county
    ''')
    severity_rows = {r['county']: r['max_wind_mph'] for r in cursor.fetchall()}

    db.close()

    combined = []
    for county, outage in outage_rows.items():
        start = datetime.fromisoformat(outage['start_time'])
        end = datetime.fromisoformat(outage['end_time']) if outage['end_time'] else None
        duration_hours = round((end - start).total_seconds() / 3600, 1) if end else None

        combined.append({
            'county': county,
            'peak_percentage_out': outage['peak_pct'],
            'duration_hours': duration_hours,
            'max_wind_mph': severity_rows.get(county),
        })

    combined.sort(key=lambda r: (r['max_wind_mph'] or 0), reverse=True)
    return combined

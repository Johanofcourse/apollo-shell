import csv
import os
import re
from datetime import datetime, timedelta

import requests

from database import OutageDatabase


HURRICANE_EVENT_TYPES = {"Hurricane (Typhoon)", "Tropical Storm", "Tropical Depression"}
WIND_RE = re.compile(r"(\d{2,3})\s*mph", re.IGNORECASE)


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
    """
    matches = WIND_RE.findall(narrative or "")
    if not matches:
        return None
    return max(int(m) for m in matches)


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
            if row['EVENT_TYPE'] not in HURRICANE_EVENT_TYPES:
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

            narrative = row['EVENT_NARRATIVE'] or row['EPISODE_NARRATIVE']
            records.append({
                'storm_name': storm_name,
                'county': matched_county,
                'zone_name': zone_name,
                'event_type': row['EVENT_TYPE'],
                'begin_time': row['BEGIN_DATE_TIME'],
                'end_time': row['END_DATE_TIME'],
                'reported_wind_mph': extract_wind_mph(narrative),
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

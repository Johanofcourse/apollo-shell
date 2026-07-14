"""
Shared, presentation-agnostic historical storm-data queries - used by
both dashboard.py (the internal ops tool's /history route) and
public_site.py (the public-facing page's storm-history section), so
both read the same consolidated historical database the same way
without either one importing from the other.
"""
import os
import sqlite3

HISTORICAL_DB_PATH = "historical_consolidated.db"


def available_history_counties():
    if not os.path.exists(HISTORICAL_DB_PATH):
        return []
    conn = sqlite3.connect(HISTORICAL_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT county FROM historical_outage_events ORDER BY county")
    counties = [row[0] for row in cursor.fetchall()]
    conn.close()
    return counties


def all_storms():
    """
    Every storm this project has real data for, across all 67 counties -
    used so a single county's history lists every storm explicitly, even
    the ones where that county has nothing, rather than silently
    omitting them (see load_history_for_county).
    """
    conn = sqlite3.connect(HISTORICAL_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT storm_name, storm_year FROM historical_outage_events
        ORDER BY storm_year, storm_name
    ''')
    storms = [{"storm_name": row[0], "storm_year": row[1]} for row in cursor.fetchall()]
    conn.close()
    return storms


def load_history_for_county(county):
    """
    Real historical storm data for one Florida county, from the
    consolidated historical database (see
    apollo_shell/consolidate_historical.py) - built from the 17
    independently-verified per-storm databases, never the raw per-storm
    files directly. County names in this table are stored upper-case (an
    artifact of the PSC report parser), so the lookup is case-insensitive -
    a user typing "Miami-Dade" still matches the stored "MIAMI-DADE".

    Returns every storm this project has data for (see all_storms()),
    not just the ones with a report for this specific county - a storm
    with nothing for this county gets an explicit has_data=False entry
    instead of being silently left out. "No report for this storm" and
    "confirmed unaffected by this storm" are different claims, and only
    listing storms with data blurred that distinction (this is the same
    lesson the Miami-Dade bug hunt turned up - see docs/documentation.md).
    """
    conn = sqlite3.connect(HISTORICAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute('''
        SELECT storm_name, storm_year, utility, start_time, end_time,
               peak_customers_out, peak_percentage_out, customers_served
        FROM historical_outage_events
        WHERE UPPER(county) = UPPER(?)
        ORDER BY storm_year, peak_percentage_out DESC
    ''', (county,))
    outage_rows = [dict(row) for row in cursor.fetchall()]

    cursor.execute('''
        SELECT storm_name, storm_year, event_type, reported_wind_mph,
               snow_inches, ice_inches, wind_chill_f
        FROM historical_storm_severity
        WHERE UPPER(county) = UPPER(?)
        ORDER BY storm_year
    ''', (county,))
    severity_rows = [dict(row) for row in cursor.fetchall()]

    conn.close()

    # Group both tables by storm so the page can show, per storm: which
    # utilities reported an outage here and how bad it got, plus whatever
    # independent NOAA severity readings exist for the same county/storm -
    # two different sources, shown side by side, never merged into one
    # number.
    storms_by_key = {}

    def _storm_bucket(storm_name, storm_year):
        key = (storm_name, storm_year)
        return storms_by_key.setdefault(key, {
            "storm_name": storm_name,
            "storm_year": storm_year,
            "utilities": [],
            "severity": [],
            "has_data": False,
        })

    for row in outage_rows:
        bucket = _storm_bucket(row["storm_name"], row["storm_year"])
        bucket["utilities"].append(row)
        bucket["has_data"] = True
    for row in severity_rows:
        bucket = _storm_bucket(row["storm_name"], row["storm_year"])
        bucket["severity"].append(row)
        bucket["has_data"] = True

    # Every storm gets a row - _storm_bucket() is a no-op for storms
    # already populated above, and creates an honest has_data=False
    # entry for the rest.
    for storm in all_storms():
        _storm_bucket(storm["storm_name"], storm["storm_year"])

    return sorted(storms_by_key.values(), key=lambda s: s["storm_year"])

import glob
import os
import sqlite3

# The 17 original per-storm databases stay untouched, verified source of
# truth. This script only ever reads from them and writes into a new,
# separate, regeneratable file - safe to re-run any time (idempotent via
# unique indexes on the destination tables) and safe to re-run after a
# future 18th storm gets backfilled.
#
# Canonical storm_name -> year mapping, matching the exact strings each
# db's own storm_severity table already uses internally.
STORM_YEARS = {
    "historical_alberto.db": ("Alberto", 2018),
    "historical_michael.db": ("Michael", 2018),
    "historical_dorian.db": ("Dorian", 2019),
    "historical_isaias.db": ("Isaias", 2020),
    "historical_sally.db": ("Sally", 2020),
    "historical_eta.db": ("Eta", 2020),
    "historical_fred.db": ("Fred", 2021),
    "historical_elsa.db": ("Elsa", 2021),
    "historical_nicole.db": ("Nicole", 2022),
    "historical_ian.db": ("Ian", 2022),
    "historical_idalia.db": ("Idalia", 2023),
    "historical_jan2024.db": ("JanuaryWeatherEvent2024", 2024),
    "historical_may2024.db": ("MayWeatherEvent2024", 2024),
    "historical_debby.db": ("Debby", 2024),
    "historical_helene.db": ("Helene", 2024),
    "historical_milton.db": ("Milton", 2024),
    "historical_jan2025.db": ("JanuaryWinterEvent2025", 2025),
}

# Only two tables actually have historical data. teco_incident_events and
# duke_incident_events exist in every per-storm db's schema (inherited
# from the shared table-creation code) but are always empty - historical
# data comes from PSC's county-level PDF reports, a completely different
# shape than the live incident-level APIs, so TECO's and Duke's real
# historical numbers live in outage_events under their utility name,
# alongside 40+ other Florida utilities (co-ops, municipals) per storm -
# not in utility-specific tables. Confirmed directly against the data
# before writing this, not assumed.


def create_consolidated_tables(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historical_outage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            storm_name TEXT NOT NULL,
            storm_year INTEGER NOT NULL,
            utility TEXT NOT NULL,
            county TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            peak_customers_out INTEGER NOT NULL,
            peak_percentage_out REAL NOT NULL,
            customers_served INTEGER NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_historical_outage_events_unique
        ON historical_outage_events(storm_name, utility, county, start_time)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_historical_outage_events_county
        ON historical_outage_events(county)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historical_storm_severity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            storm_name TEXT NOT NULL,
            storm_year INTEGER NOT NULL,
            county TEXT NOT NULL,
            zone_name TEXT NOT NULL,
            event_type TEXT NOT NULL,
            begin_time TEXT,
            end_time TEXT,
            reported_wind_mph INTEGER,
            snow_inches REAL,
            ice_inches REAL,
            wind_chill_f REAL,
            narrative TEXT
        )
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_historical_storm_severity_unique
        ON historical_storm_severity(storm_name, county, zone_name, event_type, begin_time)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_historical_storm_severity_county
        ON historical_storm_severity(county)
    ''')


def consolidate(dest_path="historical_consolidated.db", source_dir="."):
    dest_conn = sqlite3.connect(dest_path)
    dest_cursor = dest_conn.cursor()
    create_consolidated_tables(dest_cursor)
    dest_conn.commit()

    summary = {}
    for filename, (storm_name, storm_year) in STORM_YEARS.items():
        source_path = os.path.join(source_dir, filename)
        if not os.path.exists(source_path):
            print(f"SKIP {filename}: file not found at {source_path}")
            continue

        source_conn = sqlite3.connect(source_path)
        source_cursor = source_conn.cursor()

        row_counts = {}

        source_cursor.execute('''
            SELECT utility, county, start_time, end_time, peak_customers_out,
                   peak_percentage_out, customers_served
            FROM outage_events
        ''')
        rows = source_cursor.fetchall()
        for row in rows:
            dest_cursor.execute('''
                INSERT OR IGNORE INTO historical_outage_events
                (storm_name, storm_year, utility, county, start_time, end_time,
                 peak_customers_out, peak_percentage_out, customers_served)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (storm_name, storm_year, *row))
        row_counts["outage_events"] = len(rows)

        source_cursor.execute('''
            SELECT county, zone_name, event_type, begin_time, end_time,
                   reported_wind_mph, snow_inches, ice_inches, wind_chill_f, narrative
            FROM storm_severity
        ''')
        rows = source_cursor.fetchall()
        for row in rows:
            dest_cursor.execute('''
                INSERT OR IGNORE INTO historical_storm_severity
                (storm_name, storm_year, county, zone_name, event_type, begin_time,
                 end_time, reported_wind_mph, snow_inches, ice_inches, wind_chill_f, narrative)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (storm_name, storm_year, *row))
        row_counts["storm_severity"] = len(rows)

        source_conn.close()
        dest_conn.commit()
        summary[filename] = row_counts

    dest_conn.close()
    return summary


if __name__ == "__main__":
    summary = consolidate()
    total_by_table = {}
    for filename, counts in summary.items():
        storm_name, storm_year = STORM_YEARS[filename]
        print(f"{storm_name} ({storm_year}): {counts}")
        for table, count in counts.items():
            total_by_table[table] = total_by_table.get(table, 0) + count
    print()
    print("Totals across all storms:", total_by_table)

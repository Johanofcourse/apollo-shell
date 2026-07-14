import sqlite3
from datetime import datetime, timedelta, timezone
import os


def _ensure_column(cursor, table, column, coltype='TEXT'):
    """
    Add a column to an existing table if it's not already there. Needed
    because CREATE TABLE IF NOT EXISTS won't retroactively add new
    columns to a database created by an earlier version of this schema.
    """
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


class OutageDatabase:
    """
    Handles all SQLite database operations for storing outage data
    """
    
    def __init__(self, db_path="outages.db"):
        """
        Initialize database connection
        db_path: path to SQLite database file (created if doesn't exist)
        """
        self.db_path = db_path
        self.connection = None
        self.create_tables()
    
    
    def connect(self):
        """
        Create database connection
        """
        if self.connection is None:
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row  # Return rows as dictionaries
        return self.connection
    
    
    def close(self):
        """
        Close database connection
        """
        if self.connection:
            self.connection.close()
            self.connection = None
    
    
    def create_tables(self):
        """
        Create the outages and weather_alerts tables if they don't exist
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        # Outages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')
        
        # Weather alerts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weather_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT,
                urgency TEXT,
                areas TEXT NOT NULL,
                effective TEXT,
                expires TEXT,
                headline TEXT,
                description TEXT
            )
        ''')

        # Safe migration for databases created before alert_id existed
        _ensure_column(cursor, 'weather_alerts', 'alert_id')

        # Outage events table - tracks when an outage starts and ends per
        # county/utility, derived from the outages snapshot table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # Storm severity table - NOAA Storm Events records matched to a
        # storm's outage_events by county/date-window, for comparing
        # reported storm intensity against actual outage duration
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS storm_severity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                storm_name TEXT NOT NULL,
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

        # Safe migration for databases created before these columns
        # existed - winter-event severity metrics, added after realizing
        # wind speed alone means nothing for ice/snow/extreme-cold events
        for column, coltype in (
            ('snow_inches', 'REAL'), ('ice_inches', 'REAL'), ('wind_chill_f', 'REAL')
        ):
            _ensure_column(cursor, 'storm_severity', column, coltype)

        # TECO incident-level outages - a genuinely different shape than
        # the county-rollup outages table: individual incidents with real
        # coordinates, cause, crew status, and an actual restoration-time
        # estimate, from TECO's own live outage-map backend
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS teco_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                utility TEXT,
                status TEXT,
                status_category TEXT,
                reason TEXT,
                reason_category TEXT,
                customer_count INTEGER,
                lat REAL,
                lon REAL,
                county TEXT,
                update_time TEXT,
                estimated_restoration TEXT
            )
        ''')

        # Safe migrations for databases created before these columns
        # existed (CREATE TABLE IF NOT EXISTS won't retroactively add them).
        # utility: the canonical name ("Tampa Electric Company") also used
        # by historical_import.py's PSC-report data for this same real
        # entity, so both representations share one name.
        # reason_category/status_category: a derived, best-effort label
        # alongside the raw free text, not a replacement for it.
        for column in ('utility', 'status_category', 'reason_category', 'county'):
            _ensure_column(cursor, 'teco_incidents', column)

        # TECO incident lifecycle tracking - unlike FPL, TECO gives us a
        # real incident_id directly, so we don't need to infer continuity
        # from county-level number crossing zero. An event opens the
        # first time we see an incident_id, and closes when that id stops
        # appearing in a poll (TECO's feed only lists currently-active
        # incidents, so disappearing is our only signal of resolution).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS teco_incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                utility TEXT,
                county TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customer_count INTEGER,
                reason TEXT,
                reason_category TEXT,
                lat REAL,
                lon REAL
            )
        ''')

        for column in ('utility', 'reason_category'):
            _ensure_column(cursor, 'teco_incident_events', column)

        # Duke Energy raw incident snapshots - Duke's live feed gives no
        # per-incident "last updated" field (unlike TECO's update_time), so
        # unlike teco_incidents this table just logs one fresh row per
        # incident per poll cycle, same as the plain outages table does.
        # The derived lifecycle lives in duke_incident_events below.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS duke_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                utility TEXT,
                customer_count INTEGER,
                lat REAL,
                lon REAL,
                county TEXT,
                cause TEXT,
                cause_category TEXT
            )
        ''')

        # Duke Energy incident lifecycle tracking - same approach as
        # teco_incident_events: an event opens the first time an incident_id
        # is seen and closes when that id stops appearing in a poll (Duke's
        # feed, like TECO's, only lists currently-active incidents).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS duke_incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                utility TEXT,
                county TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customer_count INTEGER,
                cause TEXT,
                cause_category TEXT,
                lat REAL,
                lon REAL
            )
        ''')

        # Duke Energy county rollups - a different shape than individual
        # incidents: per-county totals plus ETR/cause/crew overrides that
        # only populate during a real declared event. Duke's own
        # last_updated field is the real identity of a row here, same
        # principle as weather_alerts using NWS's own alert_id.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS duke_counties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at TEXT NOT NULL,
                utility TEXT,
                county TEXT,
                area_of_interest_id TEXT,
                customers_served INTEGER,
                etr_override TEXT,
                cause_code_override TEXT,
                crew_status_override TEXT,
                customers_affected_override INTEGER,
                max_customers_affected INTEGER,
                active_events_count INTEGER,
                restored_events_count INTEGER,
                last_updated TEXT
            )
        ''')

        # Duke Energy system alerts - notifications about the outage map's
        # own data reliability (e.g. "data may be delayed"), not weather
        # alerts. Duke's own numeric id is the real identity, same
        # principle as weather_alerts using NWS's own alert_id.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS duke_system_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                duke_alert_id TEXT,
                fetched_at TEXT NOT NULL,
                title TEXT,
                description TEXT,
                active_indicator INTEGER,
                alert_type TEXT,
                start_time TEXT,
                end_time TEXT
            )
        ''')

        # City of Tallahassee raw incident snapshots - same incident-level
        # shape as duke_incidents (no per-record update_time of its own,
        # so a fresh row every poll cycle keyed on our own fetched_at is
        # the real timeline), plus two fields unique to this source:
        # region_name (Tallahassee's own 5-zone sub-county breakdown) and
        # status/status_category (reuses TECO's free-text categorizer).
        # Its whole territory is Leon County only (confirmed against real
        # historical PSC storm reports), so there's no per-record
        # reverse-geocoded county the way Duke's lat/lon needs.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tallahassee_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                utility TEXT,
                customer_count INTEGER,
                lat REAL,
                lon REAL,
                county TEXT,
                region_name TEXT,
                status TEXT,
                status_category TEXT,
                cause TEXT,
                cause_category TEXT,
                outage_type TEXT,
                reported_start_time TEXT,
                estimated_restoration TEXT
            )
        ''')

        # City of Tallahassee incident lifecycle tracking - same
        # open-on-first-seen/close-on-disappearance approach as
        # teco_incident_events/duke_incident_events (the feed only lists
        # currently-active incidents).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tallahassee_incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                utility TEXT,
                county TEXT,
                region_name TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customer_count INTEGER,
                cause TEXT,
                cause_category TEXT,
                lat REAL,
                lon REAL
            )
        ''')

        # Talquin Electric Cooperative raw county-level snapshots - a
        # county-rollup source like FPL/JEA (real accounts/affected per
        # county every poll cycle), not an incident list like TECO/Duke/
        # Tallahassee. Same shape as the plain outages table, kept as its
        # own dedicated table for the same one-utility-per-table reason
        # JEA got its own (get_open_events() has no utility filter, so
        # sharing FPL's table would silently mix Talquin's 4 counties
        # into the "FPL" dashboard section and correlation).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS talquin_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')

        # Talquin county-level lifecycle tracking - identical algorithm to
        # outage_events/jea_outage_events (open on customers_out > 0,
        # track peak, close on return to 0).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS talquin_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # FPUC raw snapshots - same table shape as talquin_outages, but
        # "county" is always the same fixed placeholder string (see
        # fetch_fpuc_outages.COMBINED_TERRITORY_LABEL): FPUC's live feed
        # only ever reports ONE combined total across its whole (non-
        # adjacent, multi-county) Florida electric territory - no real
        # per-county breakdown is available from this source, confirmed
        # 2026-07-13 after a real search for one came up empty. The
        # placeholder deliberately can't match any real NWS alert area,
        # so weather correlation for this source naturally always comes
        # back empty - an honest, self-documenting result rather than a
        # special-cased skip.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fpuc_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')

        # Peace River Electric Cooperative (PRECO) raw county-level
        # snapshots - same Siena-platform shape/principle as
        # talquin_outages, kept in its own table for the same
        # one-utility-per-table reason (10 counties: Brevard, DeSoto,
        # Hardee, Highlands, Hillsborough, Indian River, Manatee,
        # Osceola, Polk, Sarasota).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS preco_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')

        # PRECO county-level lifecycle tracking - identical algorithm to
        # talquin_outage_events (open on customers_out > 0, track peak,
        # close on return to 0).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS preco_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            CREATE TABLE IF NOT EXISTS fpuc_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # FPUC raw per-incident markers - confirmed real 2026-07-13 once a
        # live outage finally populated this part of the response (it had
        # only ever been seen empty before, indistinguishable from "no
        # per-county data exists"). Same shape/reasoning as duke_incidents
        # (no per-record update time of its own, real lat/lon reverse-
        # geocoded to county the same way Duke's fetch module does) - kept
        # alongside fpuc_outages/fpuc_outage_events (the combined
        # territory-wide total), not instead of it: the app's own config
        # says some real outages are deliberately withheld from this list
        # for privacy, so this is real and useful but not guaranteed
        # complete.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fpuc_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                utility TEXT,
                customer_count INTEGER,
                lat REAL,
                lon REAL,
                county TEXT,
                substation TEXT,
                feeder TEXT,
                reported_start_time TEXT,
                estimated_restoration TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fpuc_incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL,
                utility TEXT,
                county TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customer_count INTEGER,
                substation TEXT,
                feeder TEXT,
                lat REAL,
                lon REAL
            )
        ''')

        # JEA raw ZIP-level snapshots - JEA's live feed (Kubra's "Storm
        # Center" product) rolls up by ZIP code, not county, and includes
        # a real ETR + a labeled confidence on that estimate (richer than
        # FPL's plain county numbers). One fresh row per ZIP per poll
        # cycle, same principle as the plain outages table.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jea_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                zip_code TEXT NOT NULL,
                county TEXT,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL,
                etr TEXT,
                etr_confidence TEXT,
                n_out INTEGER
            )
        ''')

        # JEA county-level lifecycle tracking - ZIP-level customer counts
        # rolled up to county (weather-alert correlation is county-based
        # here, same as FPL), same open/close-on-zero-customers algorithm
        # as outage_events. Kept as JEA's own dedicated table rather than
        # sharing outage_events with FPL, matching the same one-utility-
        # per-table convention TECO/Duke already established - reusing
        # FPL's table would silently mix JEA rows into the "FPL" dashboard
        # section and correlation, since those reads have no utility filter.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jea_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # Florida Keys Electric Cooperative (FKEC) raw snapshots - a
        # county-rollup source like Talquin/PRECO, always exactly one
        # row (real county: Monroe - FKEC's whole territory is confirmed
        # single-county, see fetch_fkec_outages.SERVICE_COUNTY), kept in
        # its own table per the same one-utility-per-table convention.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fkec_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')

        # FKEC county-level lifecycle tracking - identical algorithm to
        # preco_outage_events/talquin_outage_events (open on
        # customers_out > 0, track peak, close on return to 0).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fkec_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # Tri-County Electric Cooperative (TCEC) raw snapshots - a
        # combined-territory source like FPUC's original tracker,
        # always exactly one row (see
        # fetch_tcec_outages.COMBINED_TERRITORY_LABEL - real counties,
        # just not splittable from this response alone), kept in its
        # own table per the same one-utility-per-table convention.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tcec_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')

        # TCEC lifecycle tracking - identical algorithm to
        # fkec_outage_events/preco_outage_events (open on
        # customers_out > 0, track peak, close on return to 0).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tcec_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # Escambia River Electric Cooperative (EREC) raw snapshots -
        # same vendor platform/shape as TCEC, a combined-territory
        # source, always exactly one row (see
        # fetch_erec_outages.COMBINED_TERRITORY_LABEL - real counties:
        # Escambia, Santa Rosa), kept in its own table per the same
        # one-utility-per-table convention.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS erec_outages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                customers_out INTEGER NOT NULL,
                customers_served INTEGER NOT NULL,
                percentage_out REAL NOT NULL
            )
        ''')

        # EREC lifecycle tracking - identical algorithm to
        # tcec_outage_events/fkec_outage_events (open on
        # customers_out > 0, track peak, close on return to 0).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS erec_outage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                utility TEXT NOT NULL,
                county TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_customers_out INTEGER NOT NULL,
                peak_percentage_out REAL NOT NULL,
                customers_served INTEGER NOT NULL
            )
        ''')

        # Create indexes
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON outages(timestamp)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_outage_events_open
            ON outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_teco_incident_events_open
            ON teco_incident_events(incident_id, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_duke_incident_events_open
            ON duke_incident_events(incident_id, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_jea_outage_events_open
            ON jea_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_tallahassee_incident_events_open
            ON tallahassee_incident_events(incident_id, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_talquin_outage_events_open
            ON talquin_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_preco_outage_events_open
            ON preco_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_fkec_outage_events_open
            ON fkec_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_tcec_outage_events_open
            ON tcec_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_erec_outage_events_open
            ON erec_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_fpuc_outage_events_open
            ON fpuc_outage_events(utility, county, end_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_fpuc_incident_events_open
            ON fpuc_incident_events(incident_id, end_time)
        ''')

        # Uniqueness guards so re-running an import (e.g. replaying the same
        # historical report series twice) can't silently duplicate rows -
        # INSERT OR IGNORE relies on these to make re-imports a safe no-op
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_outages_unique
            ON outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_outage_events_unique
            ON outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_teco_incidents_unique
            ON teco_incidents(incident_id, update_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_teco_incident_events_unique
            ON teco_incident_events(incident_id, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_jea_outages_unique
            ON jea_outages(timestamp, utility, zip_code)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_jea_outage_events_unique
            ON jea_outage_events(utility, county, start_time)
        ''')

        # NWS's own alert id is the real identity of an alert - re-polling
        # the same still-active alert every cycle was silently re-inserting
        # it every time, since the old schema only had our own polling
        # timestamp, which is different every cycle by definition. NULLs
        # (older rows imported before this existed) are exempt from a
        # SQLite UNIQUE constraint, so this only blocks genuine repeats.
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_alerts_unique
            ON weather_alerts(alert_id)
        ''')

        # storm_severity was missed when this idempotency pass was first
        # done - relied on manually clearing the table before every
        # re-import instead of real protection. Natural identity of a
        # matched NOAA record: which storm, which county, which zone
        # (multiple zones can map to one county), which event type, and
        # when it began.
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_storm_severity_unique
            ON storm_severity(storm_name, county, zone_name, event_type, begin_time)
        ''')

        # Applying the idempotency lesson from the start this time, not
        # after the fact. duke_incidents has no natural content-identity
        # (no per-record update_time from Duke), so this guard is mostly
        # defensive against a literal double-run within the same cycle;
        # duke_incident_events and duke_system_alerts have real identity
        # keys, same as their teco/weather_alerts counterparts.
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_duke_incidents_unique
            ON duke_incidents(incident_id, fetched_at)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_duke_incident_events_unique
            ON duke_incident_events(incident_id, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_duke_counties_unique
            ON duke_counties(county, last_updated)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_duke_system_alerts_unique
            ON duke_system_alerts(duke_alert_id)
        ''')

        # Same reasoning as duke_incidents above: no per-record update
        # timestamp of its own, so this is mostly a defensive guard
        # against a literal double-run within one cycle rather than real
        # content-based dedup.
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tallahassee_incidents_unique
            ON tallahassee_incidents(incident_id, fetched_at)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tallahassee_incident_events_unique
            ON tallahassee_incident_events(incident_id, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_talquin_outages_unique
            ON talquin_outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_talquin_outage_events_unique
            ON talquin_outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fpuc_outages_unique
            ON fpuc_outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fpuc_outage_events_unique
            ON fpuc_outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fpuc_incidents_unique
            ON fpuc_incidents(incident_id, fetched_at)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_preco_outages_unique
            ON preco_outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_preco_outage_events_unique
            ON preco_outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fkec_outages_unique
            ON fkec_outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fkec_outage_events_unique
            ON fkec_outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tcec_outages_unique
            ON tcec_outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tcec_outage_events_unique
            ON tcec_outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_erec_outages_unique
            ON erec_outages(timestamp, utility, county)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_erec_outage_events_unique
            ON erec_outage_events(utility, county, start_time)
        ''')

        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fpuc_incident_events_unique
            ON fpuc_incident_events(incident_id, start_time)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_county
            ON outages(county)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_weather_timestamp
            ON weather_alerts(timestamp)
        ''')

        # Every cycle in main.py is already wrapped in its own try/except so
        # one source failing doesn't take down the others - this table is
        # where those caught exceptions actually get recorded, instead of
        # only ever existing as a print() line in a growing text log file
        # nobody's watching. source is the cycle name (fpl/weather/teco/
        # duke/correlation), matching main.py's own naming.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pipeline_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                error_message TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_pipeline_errors_source_timestamp
            ON pipeline_errors(source, timestamp)
        ''')

        conn.commit()
        print(f"Database initialized: {self.db_path}")

    def log_pipeline_error(self, source, error_message, timestamp=None):
        """
        Record a caught pipeline failure (a fetch cycle, a correlation
        pass, etc.) so it's queryable later, not just a line in a text
        log. Deliberately not idempotency-guarded like the data tables -
        every failure is a real, distinct event worth keeping, even if
        the message text happens to repeat cycle to cycle.
        """
        conn = self.connect()
        cursor = conn.cursor()
        timestamp = timestamp or datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO pipeline_errors (timestamp, source, error_message)
            VALUES (?, ?, ?)
        ''', (timestamp, source, error_message))
        conn.commit()

    def get_pipeline_health(self, sources=None, warning_window_hours=24, critical_window_hours=1, critical_count=3):
        """
        Per-source health summary for the dashboard: status ("healthy" /
        "warning" / "critical"), most recent error (if any), and failure
        counts over a short window (default 1h) and a longer one
        (default 24h).

        Thresholds are about *sustained* failure, not a single blip - a
        source polled every 15 min has ~4 chances an hour, so
        `critical_count` failures within `critical_window_hours` means
        it's failing nearly every cycle right now, not just once.
        """
        conn = self.connect()
        cursor = conn.cursor()

        if sources is None:
            cursor.execute('SELECT DISTINCT source FROM pipeline_errors')
            sources = sorted(row[0] for row in cursor.fetchall())

        now = datetime.now()
        warning_cutoff = (now - timedelta(hours=warning_window_hours)).isoformat()
        critical_cutoff = (now - timedelta(hours=critical_window_hours)).isoformat()

        health = {}
        for source in sources:
            cursor.execute('''
                SELECT timestamp, error_message FROM pipeline_errors
                WHERE source = ? ORDER BY timestamp DESC LIMIT 1
            ''', (source,))
            last_row = cursor.fetchone()

            cursor.execute('''
                SELECT COUNT(*) FROM pipeline_errors
                WHERE source = ? AND timestamp >= ?
            ''', (source, warning_cutoff))
            count_warning_window = cursor.fetchone()[0]

            cursor.execute('''
                SELECT COUNT(*) FROM pipeline_errors
                WHERE source = ? AND timestamp >= ?
            ''', (source, critical_cutoff))
            count_critical_window = cursor.fetchone()[0]

            if count_critical_window >= critical_count:
                status = "critical"
            elif count_warning_window > 0:
                status = "warning"
            else:
                status = "healthy"

            health[source] = {
                "status": status,
                "last_error_time": last_row["timestamp"] if last_row else None,
                "last_error_message": last_row["error_message"] if last_row else None,
                "count_recent": count_critical_window,
                "count_today": count_warning_window,
            }

        return health

    def get_pipeline_error_history(self, source=None, limit=200):
        """
        Raw pipeline_errors rows, most recent first - the drill-down
        behind get_pipeline_health()'s "count + last message" summary.
        That summary answers "is anything wrong right now"; this answers
        "what actually happened, and when" - e.g. seeing that a source's
        failures always cluster around a specific time of day, or always
        say the same thing, rather than just a single latest message.

        source=None returns every source's errors combined (still
        useful: shows whether failures are correlated across sources at
        the same moment, e.g. a shared network blip, vs. one source
        alone).
        """
        conn = self.connect()
        cursor = conn.cursor()

        if source:
            cursor.execute('''
                SELECT * FROM pipeline_errors WHERE source = ?
                ORDER BY timestamp DESC LIMIT ?
            ''', (source, limit))
        else:
            cursor.execute('''
                SELECT * FROM pipeline_errors
                ORDER BY timestamp DESC LIMIT ?
            ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_heat_advisory_summary(self, reference_date=None):
        """
        "Heat this month" summary for the dashboard strip. Heat Advisory
        and Excessive Heat Warning are ordinary NWS event types already
        flowing through the generic weather_alerts table (see
        fetch_weather.py) - no separate heat-specific pipeline exists or
        is needed, this just aggregates what's already there.

        Counts distinct calendar days (by the alert's own `effective`
        date, which is already in local Eastern time as published by
        NWS) rather than raw alert rows, since NWS splits one advisory
        into several zone-specific rows covering the same day.
        """
        conn = self.connect()
        cursor = conn.cursor()

        reference = reference_date or datetime.now()
        month_prefix = reference.strftime('%Y-%m')

        cursor.execute('''
            SELECT event_type, effective, expires, areas FROM weather_alerts
            WHERE event_type IN ('Heat Advisory', 'Excessive Heat Warning')
              AND effective LIKE ?
        ''', (f'{month_prefix}%',))
        rows = cursor.fetchall()

        days_covered = set()
        tier_counts = {"Heat Advisory": 0, "Excessive Heat Warning": 0}
        active_areas = set()
        active_alerts = []
        now_utc = datetime.now(timezone.utc)

        for row in rows:
            tier_counts[row["event_type"]] = tier_counts.get(row["event_type"], 0) + 1
            if row["effective"]:
                days_covered.add(row["effective"][:10])

            if row["effective"] and row["expires"]:
                effective_dt = datetime.fromisoformat(row["effective"])
                expires_dt = datetime.fromisoformat(row["expires"])
                if effective_dt <= now_utc <= expires_dt:
                    areas = [a.strip() for a in row["areas"].split(";")]
                    active_areas.update(areas)
                    active_alerts.append({
                        "event_type": row["event_type"],
                        "effective": row["effective"],
                        "expires": row["expires"],
                        "areas": areas,
                    })

        return {
            "month_label": reference.strftime('%B %Y'),
            "days_with_advisory": len(days_covered),
            "days_elapsed_this_month": reference.day,
            "heat_advisory_count": tier_counts["Heat Advisory"],
            "excessive_heat_warning_count": tier_counts["Excessive Heat Warning"],
            "currently_active": len(active_areas) > 0,
            "active_area_count": len(active_areas),
            "active_zones": sorted(active_areas),
            "active_alerts": active_alerts,
        }

    def log_outage(self, utility, county, customers_out, customers_served):
        """
        Insert a single outage record into the database
        
        Args:
            utility: Name of utility (e.g., "FPL")
            county: County name
            customers_out: Number of customers without power
            customers_served: Total customers in that county
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
        
        cursor.execute('''
            INSERT INTO outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (timestamp, utility, county, customers_out, customers_served, percentage_out))
        
        conn.commit()


    def log_multiple_outages(self, utility, outage_list, timestamp=None):
        """
        Insert multiple outage records at once (more efficient)

        Args:
            utility: Name of utility
            outage_list: List of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            records.append((timestamp, utility, county, customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} outage records for {utility}")


    def sync_outage_events(self, utility, outage_list, timestamp=None):
        """
        Update outage_events from a fresh batch of per-county snapshots.

        For each county: if customers_out > 0 and no event is currently
        open (end_time IS NULL), start one. If one is already open, bump
        its peak if this snapshot is worse. If customers_out == 0 and an
        event is open, close it.

        NOT SAFE TO REPLAY: this decides "is an event currently open" by
        querying live database state, which only makes sense if calls
        arrive in real chronological order from wherever history actually
        left off (true for the live poller, one cycle at a time). Calling
        this again with an earlier batch on top of data that already
        covers that period - replaying a historical report series a
        second time, for instance - gets confused by events left open
        from the end of the previous run and fabricates spurious extra
        open/close cycles. Found the hard way 2026-07-08; the fix for
        historical replays was making import_report_series() wipe
        outage_events before every run, not changing this function's own
        behavior. If a similar historical-replay use case is ever built
        for this table directly, wipe first, the same way.

        Args:
            utility: Name of utility (e.g., "FPL")
            outage_list: List of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    # rowcount is 0 if a row with this (utility, county,
                    # start_time) already existed and the insert was
                    # ignored - e.g. replaying an already-imported report
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"Outage events: {opened} opened, {closed} closed this cycle")


    def log_jea_outages(self, zip_records, timestamp=None):
        """
        Insert JEA's raw per-ZIP snapshots (customers, ETR, ETR confidence),
        one fresh row per ZIP per poll cycle - same principle as
        log_multiple_outages, kept in its own table (see jea_outages)
        rather than sharing FPL's outages table.

        Args:
            zip_records: list of dicts as returned by
                fetch_jea_outages.parse_jea_areas()'s first return value
            timestamp: ISO timestamp for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = [
            (
                timestamp, "Jacksonville (JEA)", z['zip_code'], z['county'],
                z['customers_out'], z['customers_served'], z['percentage_out'],
                z['etr'], z['etr_confidence'], z['n_out'],
            )
            for z in zip_records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO jea_outages
                (timestamp, utility, zip_code, county, customers_out,
                 customers_served, percentage_out, etr, etr_confidence, n_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} JEA ZIP-level outage records")


    def sync_jea_outage_events(self, outage_list, timestamp=None):
        """
        Update jea_outage_events from a fresh batch of per-county
        rollups (ZIP-level customer counts summed to county - see
        fetch_jea_outages.parse_jea_areas()'s second return value).

        Identical algorithm to sync_outage_events(), kept as JEA's own
        dedicated table/method rather than parameterizing that one -
        same one-utility-per-table convention TECO/Duke already use.
        Same replay caveat applies: NOT SAFE TO REPLAY historical data,
        only meant for live, chronologically-ordered polling.

        Args:
            outage_list: list of dicts with keys: county, customers_out,
                customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Jacksonville (JEA)"

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM jea_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO jea_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE jea_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE jea_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"JEA outage events: {opened} opened, {closed} closed this cycle")


    def log_talquin_outages(self, outage_list, timestamp=None):
        """
        Insert Talquin Electric Cooperative's raw county-level snapshots
        (from fetch_talquin_outages.py). Same shape/principle as
        log_multiple_outages(), kept in its own table (see
        talquin_outages) rather than sharing FPL's outages table.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            records.append((timestamp, "Talquin Electric Cooperative, Inc.", outage['county'], customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO talquin_outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} Talquin outage records")


    def sync_talquin_outage_events(self, outage_list, timestamp=None):
        """
        Update talquin_outage_events from a fresh batch of per-county
        snapshots. Identical algorithm to sync_outage_events()/
        sync_jea_outage_events() - kept as Talquin's own dedicated
        table/method rather than parameterizing those, same
        one-utility-per-table convention TECO/Duke/JEA already use.

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events()
        (see its docstring): only safe for calls arriving in real
        chronological order, one live poll at a time.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Talquin Electric Cooperative, Inc."

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM talquin_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO talquin_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE talquin_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE talquin_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"Talquin outage events: {opened} opened, {closed} closed this cycle")


    def log_preco_outages(self, outage_list, timestamp=None):
        """
        Insert Peace River Electric Cooperative's raw county-level
        snapshots (from fetch_preco_outages.py). Same shape/principle as
        log_talquin_outages(), kept in its own table (see preco_outages)
        rather than sharing FPL's outages table.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            records.append((timestamp, "Peace River Electric Cooperative, Inc.", outage['county'], customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO preco_outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} PRECO outage records")


    def sync_preco_outage_events(self, outage_list, timestamp=None):
        """
        Update preco_outage_events from a fresh batch of per-county
        snapshots. Identical algorithm to sync_talquin_outage_events() -
        kept as PRECO's own dedicated table/method, same
        one-utility-per-table convention TECO/Duke/JEA/Talquin already use.

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events()
        (see its docstring): only safe for calls arriving in real
        chronological order, one live poll at a time.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Peace River Electric Cooperative, Inc."

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM preco_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO preco_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE preco_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE preco_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"PRECO outage events: {opened} opened, {closed} closed this cycle")


    def log_fkec_outages(self, outage_list, timestamp=None):
        """
        Insert Florida Keys Electric Cooperative's raw snapshot (from
        fetch_fkec_outages.py) - always exactly one record (real county:
        Monroe - see fetch_fkec_outages.SERVICE_COUNTY). Same
        shape/principle as log_preco_outages(), kept in its own table.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            records.append((timestamp, "Florida Keys Electric Cooperative, Inc.", outage['county'], customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO fkec_outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} FKEC outage records")


    def sync_fkec_outage_events(self, outage_list, timestamp=None):
        """
        Update fkec_outage_events from a fresh batch of snapshots.
        Identical algorithm to sync_preco_outage_events() - kept as
        FKEC's own dedicated table/method, same one-utility-per-table
        convention.

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events()
        (see its docstring): only safe for calls arriving in real
        chronological order, one live poll at a time.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Florida Keys Electric Cooperative, Inc."

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM fkec_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO fkec_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE fkec_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE fkec_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"FKEC outage events: {opened} opened, {closed} closed this cycle")


    def log_tcec_outages(self, outage_list, timestamp=None):
        """
        Insert Tri-County Electric Cooperative's raw combined-territory
        snapshot (from fetch_tcec_outages.py) - always exactly one
        record (see fetch_tcec_outages.COMBINED_TERRITORY_LABEL). Same
        shape/principle as log_fpuc_outages(), kept in its own table.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            records.append((timestamp, "Tri-County Electric Cooperative, Inc.", outage['county'], customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO tcec_outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} TCEC outage records")


    def sync_tcec_outage_events(self, outage_list, timestamp=None):
        """
        Update tcec_outage_events from a fresh batch of snapshots.
        Identical algorithm to sync_fpuc_outage_events() - kept as
        TCEC's own dedicated table/method, same one-utility-per-table
        convention.

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events()
        (see its docstring): only safe for calls arriving in real
        chronological order, one live poll at a time.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Tri-County Electric Cooperative, Inc."

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM tcec_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO tcec_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE tcec_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE tcec_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"TCEC outage events: {opened} opened, {closed} closed this cycle")


    def log_erec_outages(self, outage_list, timestamp=None):
        """
        Insert Escambia River Electric Cooperative's raw combined-
        territory snapshot (from fetch_erec_outages.py) - always
        exactly one record (see
        fetch_erec_outages.COMBINED_TERRITORY_LABEL). Same
        shape/principle as log_tcec_outages(), kept in its own table.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            records.append((timestamp, "Escambia River Electric Cooperative, Inc.", outage['county'], customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO erec_outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} EREC outage records")


    def sync_erec_outage_events(self, outage_list, timestamp=None):
        """
        Update erec_outage_events from a fresh batch of snapshots.
        Identical algorithm to sync_tcec_outage_events() - kept as
        EREC's own dedicated table/method, same one-utility-per-table
        convention.

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events()
        (see its docstring): only safe for calls arriving in real
        chronological order, one live poll at a time.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Escambia River Electric Cooperative, Inc."

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM erec_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO erec_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE erec_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE erec_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"EREC outage events: {opened} opened, {closed} closed this cycle")


    def log_fpuc_outages(self, outage_list, timestamp=None):
        """
        Insert FPUC's raw combined-territory snapshot (from
        fetch_fpuc_outages.py) - always exactly one record (see
        fetch_fpuc_outages.COMBINED_TERRITORY_LABEL). Same
        shape/principle as log_talquin_outages(), kept in its own table.

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp to record for this batch; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()

        records = []
        for outage in outage_list:
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            records.append((timestamp, "Florida Public Utilities Corporation", outage['county'], customers_out, customers_served, percentage_out))

        cursor.executemany('''
            INSERT OR IGNORE INTO fpuc_outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} FPUC outage records")


    def sync_fpuc_outage_events(self, outage_list, timestamp=None):
        """
        Update fpuc_outage_events from a fresh combined-territory
        snapshot. Identical algorithm to sync_talquin_outage_events() -
        kept as FPUC's own dedicated table/method for the same
        one-utility-per-table convention.

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events().

        Args:
            outage_list: list of dicts with keys: county, customers_out, customers_served
            timestamp: ISO timestamp for opened/closed events; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        utility = "Florida Public Utilities Corporation"

        opened = 0
        closed = 0

        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0

            cursor.execute('''
                SELECT id, peak_customers_out FROM fpuc_outage_events
                WHERE utility = ? AND county = ? AND end_time IS NULL
            ''', (utility, county))
            open_event = cursor.fetchone()

            if customers_out > 0:
                if open_event is None:
                    cursor.execute('''
                        INSERT OR IGNORE INTO fpuc_outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
                    if cursor.rowcount > 0:
                        opened += 1
                elif customers_out > open_event['peak_customers_out']:
                    cursor.execute('''
                        UPDATE fpuc_outage_events
                        SET peak_customers_out = ?, peak_percentage_out = ?, customers_served = ?
                        WHERE id = ?
                    ''', (customers_out, percentage_out, customers_served, open_event['id']))
            else:
                if open_event is not None:
                    cursor.execute('''
                        UPDATE fpuc_outage_events SET end_time = ? WHERE id = ?
                    ''', (timestamp, open_event['id']))
                    closed += 1

        conn.commit()
        print(f"FPUC outage events: {opened} opened, {closed} closed this cycle")


    def log_fpuc_incidents(self, records):
        """
        Insert FPUC's real per-incident markers (from
        fetch_fpuc_outages.markers_to_incidents()) - see that function's
        docstring for the real caveat: this list may not include every
        outage counted in the combined total (fpuc_outages), some are
        deliberately withheld by the app itself for privacy.

        Args:
            records: list of dicts with keys: incident_id, utility,
                     customer_count, lat, lon, county, substation, feeder,
                     reported_start_time, estimated_restoration
        """
        conn = self.connect()
        cursor = conn.cursor()

        fetched_at = datetime.now().isoformat()

        rows = [
            (
                r['incident_id'], fetched_at, r.get('utility'),
                r.get('customer_count'), r.get('lat'), r.get('lon'),
                r.get('county'), r.get('substation'), r.get('feeder'),
                r.get('reported_start_time'), r.get('estimated_restoration'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO fpuc_incidents
                (incident_id, fetched_at, utility, customer_count, lat, lon,
                 county, substation, feeder, reported_start_time, estimated_restoration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {len(rows)} FPUC incident records")


    def sync_fpuc_incident_events(self, records, timestamp=None):
        """
        Track FPUC incident lifecycle. Same approach as
        sync_duke_incident_events(): an event opens the first time an
        incident_id is seen, stays updated with the latest known values
        while still being reported, and closes once an incident_id that
        was open stops appearing in a poll at all.

        NOT SAFE TO REPLAY - same characteristic as
        sync_duke_incident_events() (see its docstring).

        Args:
            records: list of dicts as returned by
                fetch_fpuc_outages.markers_to_incidents()
            timestamp: ISO timestamp for this poll; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        current_ids = {r['incident_id'] for r in records}

        opened = 0
        closed = 0

        for r in records:
            cursor.execute('''
                SELECT id, peak_customer_count FROM fpuc_incident_events
                WHERE incident_id = ? AND end_time IS NULL
            ''', (r['incident_id'],))
            open_event = cursor.fetchone()

            if open_event is None:
                cursor.execute('''
                    INSERT OR IGNORE INTO fpuc_incident_events
                        (incident_id, utility, county, start_time, end_time,
                         peak_customer_count, substation, feeder, lat, lon)
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                ''', (
                    r['incident_id'], r.get('utility'), r.get('county'), timestamp,
                    r.get('customer_count'), r.get('substation'), r.get('feeder'),
                    r.get('lat'), r.get('lon'),
                ))
                if cursor.rowcount > 0:
                    opened += 1
            else:
                peak = max(open_event['peak_customer_count'] or 0, r.get('customer_count') or 0)
                cursor.execute('''
                    UPDATE fpuc_incident_events
                    SET peak_customer_count = ?, utility = ?, county = ?,
                        substation = ?, feeder = ?, lat = ?, lon = ?
                    WHERE id = ?
                ''', (
                    peak, r.get('utility'), r.get('county'),
                    r.get('substation'), r.get('feeder'),
                    r.get('lat'), r.get('lon'), open_event['id'],
                ))

        cursor.execute('SELECT id, incident_id FROM fpuc_incident_events WHERE end_time IS NULL')
        for row in cursor.fetchall():
            if row['incident_id'] not in current_ids:
                cursor.execute('''
                    UPDATE fpuc_incident_events SET end_time = ? WHERE id = ?
                ''', (timestamp, row['id']))
                closed += 1

        conn.commit()
        print(f"FPUC incident events: {opened} opened, {closed} closed this cycle")


    def log_weather_alerts(self, alert_list):
        """
        Insert multiple weather alert records at once

        Args:
            alert_list: List of dicts as returned by fetch_weather.parse_alert()
                        (keys: id, event, headline, severity, urgency, areas,
                        effective, expires, description)
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = datetime.now().isoformat()

        records = []
        for alert in alert_list:
            records.append((
                alert.get('id'),
                timestamp,
                alert['event'],
                alert.get('severity'),
                alert.get('urgency'),
                alert['areas'],
                alert.get('effective'),
                alert.get('expires'),
                alert.get('headline'),
                alert.get('description'),
            ))

        cursor.executemany('''
            INSERT OR IGNORE INTO weather_alerts
                (alert_id, timestamp, event_type, severity, urgency, areas, effective, expires, headline, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Weather alerts: {cursor.rowcount} new (of {len(records)} currently active)")


    def get_latest_snapshot(self):
        """
        Return the most recent poll cycle's outage rows where customers are
        currently out, sorted worst first.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('SELECT MAX(timestamp) AS ts FROM outages')
        latest = cursor.fetchone()['ts']
        if latest is None:
            return []

        cursor.execute('''
            SELECT * FROM outages
            WHERE timestamp = ? AND customers_out > 0
            ORDER BY percentage_out DESC
        ''', (latest,))
        return [dict(row) for row in cursor.fetchall()]


    def get_open_events(self):
        """
        Return currently open outage_events (end_time IS NULL), worst
        first. Includes current_customers_out/current_percentage_out from
        the latest raw outages snapshot for that county, alongside the
        lifecycle peak - added 2026-07-12 after comparing a peak reading
        against poweroutage.us's live count and realizing they're
        genuinely different numbers (peak-of-episode vs. right-now), not
        a bug, but confusing without both shown side by side.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   o.customers_out AS current_customers_out,
                   o.percentage_out AS current_percentage_out
            FROM outage_events oe
            LEFT JOIN outages o
                ON o.utility = oe.utility AND o.county = oe.county
                AND o.timestamp = (
                    SELECT MAX(timestamp) FROM outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_recent_closed_events(self, limit=10):
        """
        Return the most recently closed outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_jea_open_events(self):
        """
        Return currently open jea_outage_events (end_time IS NULL), worst
        (by peak percentage out) first. Includes current_customers_out/
        current_percentage_out, rolled up from the latest per-ZIP jea_outages
        snapshot for that county - same "peak vs. right now" reasoning as
        get_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM jea_outage_events oe
            LEFT JOIN (
                SELECT county,
                       SUM(customers_out) AS customers_out,
                       CASE WHEN SUM(customers_served) > 0
                            THEN SUM(customers_out) * 100.0 / SUM(customers_served)
                            ELSE 0 END AS percentage_out
                FROM jea_outages
                WHERE timestamp = (SELECT MAX(timestamp) FROM jea_outages)
                GROUP BY county
            ) cur ON cur.county = oe.county
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_jea_recent_closed_events(self, limit=10):
        """
        Return the most recently closed jea_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM jea_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_talquin_open_events(self):
        """
        Return currently open talquin_outage_events (end_time IS NULL),
        worst (by peak percentage out) first. Includes
        current_customers_out/current_percentage_out from the latest
        talquin_outages snapshot for that county, alongside the lifecycle
        peak - same "peak vs. right now" reasoning as get_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM talquin_outage_events oe
            LEFT JOIN talquin_outages cur
                ON cur.utility = oe.utility AND cur.county = oe.county
                AND cur.timestamp = (
                    SELECT MAX(timestamp) FROM talquin_outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_talquin_recent_closed_events(self, limit=10):
        """
        Return the most recently closed talquin_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM talquin_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_preco_open_events(self):
        """
        Return currently open preco_outage_events (end_time IS NULL),
        worst (by peak percentage out) first. Includes
        current_customers_out/current_percentage_out from the latest
        preco_outages snapshot for that county, alongside the lifecycle
        peak - same "peak vs. right now" reasoning as get_talquin_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM preco_outage_events oe
            LEFT JOIN preco_outages cur
                ON cur.utility = oe.utility AND cur.county = oe.county
                AND cur.timestamp = (
                    SELECT MAX(timestamp) FROM preco_outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_preco_recent_closed_events(self, limit=10):
        """
        Return the most recently closed preco_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM preco_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_fkec_open_events(self):
        """
        Return currently open fkec_outage_events (end_time IS NULL),
        worst (by peak percentage out) first. Includes
        current_customers_out/current_percentage_out from the latest
        fkec_outages snapshot for that county, alongside the lifecycle
        peak - same "peak vs. right now" reasoning as get_preco_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM fkec_outage_events oe
            LEFT JOIN fkec_outages cur
                ON cur.utility = oe.utility AND cur.county = oe.county
                AND cur.timestamp = (
                    SELECT MAX(timestamp) FROM fkec_outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_fkec_recent_closed_events(self, limit=10):
        """
        Return the most recently closed fkec_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM fkec_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_tcec_open_events(self):
        """
        Return currently open tcec_outage_events (end_time IS NULL),
        worst (by peak percentage out) first. Includes
        current_customers_out/current_percentage_out from the latest
        tcec_outages snapshot for that county, alongside the lifecycle
        peak - same "peak vs. right now" reasoning as get_fkec_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM tcec_outage_events oe
            LEFT JOIN tcec_outages cur
                ON cur.utility = oe.utility AND cur.county = oe.county
                AND cur.timestamp = (
                    SELECT MAX(timestamp) FROM tcec_outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_tcec_recent_closed_events(self, limit=10):
        """
        Return the most recently closed tcec_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM tcec_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_erec_open_events(self):
        """
        Return currently open erec_outage_events (end_time IS NULL),
        worst (by peak percentage out) first. Includes
        current_customers_out/current_percentage_out from the latest
        erec_outages snapshot for that county, alongside the lifecycle
        peak - same "peak vs. right now" reasoning as get_tcec_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM erec_outage_events oe
            LEFT JOIN erec_outages cur
                ON cur.utility = oe.utility AND cur.county = oe.county
                AND cur.timestamp = (
                    SELECT MAX(timestamp) FROM erec_outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_erec_recent_closed_events(self, limit=10):
        """
        Return the most recently closed erec_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM erec_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_fpuc_open_events(self):
        """
        Return currently open fpuc_outage_events (end_time IS NULL) -
        in practice at most one row, since this source only ever tracks
        one combined territory. Includes current_customers_out/
        current_percentage_out from the latest fpuc_outages snapshot,
        alongside the lifecycle peak - same "peak vs. right now"
        reasoning as get_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customers_out AS current_customers_out,
                   cur.percentage_out AS current_percentage_out
            FROM fpuc_outage_events oe
            LEFT JOIN fpuc_outages cur
                ON cur.utility = oe.utility AND cur.county = oe.county
                AND cur.timestamp = (
                    SELECT MAX(timestamp) FROM fpuc_outages o2
                    WHERE o2.utility = oe.utility AND o2.county = oe.county
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_percentage_out DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_fpuc_recent_closed_events(self, limit=10):
        """
        Return the most recently closed fpuc_outage_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM fpuc_outage_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_fpuc_open_incidents(self):
        """
        Return currently open fpuc_incident_events (end_time IS NULL),
        worst (by peak customer count) first. Includes
        current_customer_count from the latest fpuc_incidents row
        fetched for that incident_id, alongside the lifecycle peak - same
        "peak vs. right now" reasoning as get_open_events(). Distinct
        from get_fpuc_open_events() (the combined-territory tracker) -
        this is the real per-incident, per-county view, confirmed
        possible 2026-07-13 once a live outage populated FPUC's marker
        data for the first time.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customer_count AS current_customer_count
            FROM fpuc_incident_events oe
            LEFT JOIN fpuc_incidents cur
                ON cur.incident_id = oe.incident_id
                AND cur.fetched_at = (
                    SELECT MAX(fetched_at) FROM fpuc_incidents t2
                    WHERE t2.incident_id = oe.incident_id
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_customer_count DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_fpuc_recent_closed_incidents(self, limit=10):
        """
        Return the most recently closed fpuc_incident_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM fpuc_incident_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def _get_incident_events(self, events_table, incident_id):
        """
        Every lifecycle episode (there's usually just one, but an
        incident_id could in principle reopen after closing, giving it
        more than one start/end row) for one TECO/Duke incident_id.
        """
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT * FROM {events_table} WHERE incident_id = ? ORDER BY start_time
        ''', (incident_id,))
        return [dict(row) for row in cursor.fetchall()]

    def _get_incident_raw_history(self, raw_table, incident_id):
        """
        Every raw snapshot on file for one TECO/Duke incident_id, in the
        order we actually observed them (fetched_at, our own poll
        timestamp - not TECO's own update_time, which we don't fully
        trust to always move forward). Both tables log a fresh row every
        poll cycle while the incident is active, so this is a genuine
        timeline (status/cause/customer-count/ETR changes over time),
        not just a start/end pair.
        """
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(f'''
            SELECT * FROM {raw_table} WHERE incident_id = ? ORDER BY fetched_at
        ''', (incident_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_teco_incident_detail(self, incident_id):
        """
        Full detail for one TECO incident: every lifecycle episode plus
        the complete raw snapshot timeline (status/reason/ETR/customer-
        count changes over time), for the incident detail page.
        """
        return {
            "events": self._get_incident_events("teco_incident_events", incident_id),
            "history": self._get_incident_raw_history("teco_incidents", incident_id),
        }

    def get_duke_incident_detail(self, incident_id):
        """
        Same as get_teco_incident_detail(), for a Duke incident_id.
        """
        return {
            "events": self._get_incident_events("duke_incident_events", incident_id),
            "history": self._get_incident_raw_history("duke_incidents", incident_id),
        }

    def get_fpuc_incident_detail(self, incident_id):
        """
        Same as get_teco_incident_detail(), for an FPUC incident_id -
        the real per-incident view, distinct from get_fpuc_outage_detail()
        (the combined-territory tracker's own detail lookup).
        """
        return {
            "events": self._get_incident_events("fpuc_incident_events", incident_id),
            "history": self._get_incident_raw_history("fpuc_incidents", incident_id),
        }

    def get_tallahassee_incident_detail(self, incident_id):
        """
        Same as get_teco_incident_detail(), for a City of Tallahassee
        ticket number.
        """
        return {
            "events": self._get_incident_events("tallahassee_incident_events", incident_id),
            "history": self._get_incident_raw_history("tallahassee_incidents", incident_id),
        }

    def get_fpl_outage_detail(self, utility, county, start_time):
        """
        Full detail for one specific FPL county-level outage occurrence,
        identified by (utility, county, start_time) - the same natural
        key idx_outage_events_unique already enforces, since FPL never
        gives us a discrete incident id the way TECO/Duke do. Returns
        None if no such occurrence exists.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_jea_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_fpl_outage_detail(), for one JEA county-level
        outage occurrence. JEA's raw snapshots (jea_outages) are logged
        per ZIP code, not per county, so the history trend sums across
        every ZIP in the county per poll timestamp - the same
        aggregation sync_jea_outage_events() already does when deriving
        the county-level lifecycle in the first place.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM jea_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT
                timestamp,
                SUM(customers_out) AS customers_out,
                SUM(customers_served) AS customers_served,
                CASE WHEN SUM(customers_served) > 0
                     THEN SUM(customers_out) * 100.0 / SUM(customers_served)
                     ELSE 0 END AS percentage_out
            FROM jea_outages
            WHERE county = ? AND timestamp >= ? AND timestamp <= ?
            GROUP BY timestamp
            ORDER BY timestamp
        ''', (county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_talquin_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_fpl_outage_detail(), for one Talquin county-level
        outage occurrence - talquin_outages is logged per county directly
        (not per ZIP like JEA), so no aggregation is needed here.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM talquin_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM talquin_outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_fpuc_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_talquin_outage_detail(), for one FPUC
        combined-territory outage occurrence.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM fpuc_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM fpuc_outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_preco_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_talquin_outage_detail(), for one PRECO
        county-level outage occurrence - preco_outages is logged per
        county directly, no aggregation needed here.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM preco_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM preco_outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_fkec_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_preco_outage_detail(), for one FKEC outage
        occurrence - fkec_outages is logged directly (always exactly
        one row, Monroe), no aggregation needed here.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM fkec_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM fkec_outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_tcec_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_fkec_outage_detail(), for one TCEC combined-
        territory outage occurrence.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM tcec_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM tcec_outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_erec_outage_detail(self, utility, county, start_time):
        """
        Same idea as get_tcec_outage_detail(), for one EREC combined-
        territory outage occurrence.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM erec_outage_events WHERE utility = ? AND county = ? AND start_time = ?
        ''', (utility, county, start_time))
        event = cursor.fetchone()
        if event is None:
            return None
        event = dict(event)

        end_bound = event["end_time"] or datetime.now().isoformat()
        cursor.execute('''
            SELECT timestamp, customers_out, customers_served, percentage_out FROM erec_outages
            WHERE utility = ? AND county = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        ''', (utility, county, start_time, end_bound))
        history = [dict(row) for row in cursor.fetchall()]

        return {"event": event, "history": history}

    def get_teco_open_events(self):
        """
        Return currently open teco_incident_events (end_time IS NULL),
        worst (by peak customer count) first. Includes
        current_customer_count from the latest teco_incidents row fetched
        for that incident_id, alongside the lifecycle peak - same "peak
        vs. right now" reasoning as get_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customer_count AS current_customer_count
            FROM teco_incident_events oe
            LEFT JOIN teco_incidents cur
                ON cur.incident_id = oe.incident_id
                AND cur.fetched_at = (
                    SELECT MAX(fetched_at) FROM teco_incidents t2
                    WHERE t2.incident_id = oe.incident_id
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_customer_count DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_teco_recent_closed_events(self, limit=10):
        """
        Return the most recently closed teco_incident_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM teco_incident_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def get_recent_weather_alerts(self, limit=10):
        """
        Return the most recently logged weather alerts.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM weather_alerts
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def log_storm_severity(self, records):
        """
        Insert NOAA Storm Events records matched to a storm's outage data.

        Args:
            records: list of dicts with keys: storm_name, county, zone_name,
                     event_type, begin_time, end_time, reported_wind_mph,
                     snow_inches, ice_inches, wind_chill_f, narrative
        """
        conn = self.connect()
        cursor = conn.cursor()

        rows = [
            (
                r['storm_name'], r['county'], r['zone_name'], r['event_type'],
                r.get('begin_time'), r.get('end_time'),
                r.get('reported_wind_mph'), r.get('snow_inches'),
                r.get('ice_inches'), r.get('wind_chill_f'), r.get('narrative'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO storm_severity
                (storm_name, county, zone_name, event_type, begin_time, end_time,
                 reported_wind_mph, snow_inches, ice_inches, wind_chill_f, narrative)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {len(rows)} storm severity records")


    def log_teco_incidents(self, records):
        """
        Insert TECO live outage incidents (from fetch_teco_outages.py).

        Args:
            records: list of dicts with keys: incident_id, utility, status,
                     status_category, reason, reason_category,
                     customer_count, lat, lon, county, update_time,
                     estimated_restoration
        """
        conn = self.connect()
        cursor = conn.cursor()

        fetched_at = datetime.now().isoformat()

        rows = [
            (
                r['incident_id'], fetched_at, r.get('utility'),
                r.get('status'), r.get('status_category'),
                r.get('reason'), r.get('reason_category'),
                r.get('customer_count'), r.get('lat'), r.get('lon'), r.get('county'),
                r.get('update_time'), r.get('estimated_restoration'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO teco_incidents
                (incident_id, fetched_at, utility, status, status_category,
                 reason, reason_category, customer_count, lat, lon, county,
                 update_time, estimated_restoration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {len(rows)} TECO incident records")


    def sync_teco_incident_events(self, records, timestamp=None):
        """
        Track TECO incident lifecycle. Unlike sync_outage_events (which has
        to infer continuity from county-level numbers crossing zero), TECO
        gives us a real incident_id directly:
        - first time an incident_id is seen, open an event
        - while it's still being reported, keep peak_customer_count and
          reason/location updated to the latest known values
        - once an incident_id that was open stops appearing in a poll at
          all, close it (TECO's feed only lists currently-active
          incidents, so disappearing is our only signal of resolution)

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events
        (see its docstring): "is this incident currently open" is decided
        by querying live database state, which only holds up for calls
        arriving in real chronological order, one live poll at a time.
        Nothing replays this today - TECO's historical storm data lives
        in the generic outage_events table via the PSC PDF importer, not
        here, since this table only exists for the live incident-level
        feed's shape. But if a future historical TECO incident backfill
        ever calls this function with a batch of past records, wipe
        teco_incident_events for that period first, the same way
        import_report_series() now always wipes outage_events before
        replaying a storm's reports.

        Args:
            records: list of dicts as returned by fetch_teco_outages.parse_incidents()
            timestamp: ISO timestamp for this poll; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        current_ids = {r['incident_id'] for r in records}

        opened = 0
        closed = 0

        for r in records:
            cursor.execute('''
                SELECT id, peak_customer_count FROM teco_incident_events
                WHERE incident_id = ? AND end_time IS NULL
            ''', (r['incident_id'],))
            open_event = cursor.fetchone()

            if open_event is None:
                cursor.execute('''
                    INSERT OR IGNORE INTO teco_incident_events
                        (incident_id, utility, county, start_time, end_time,
                         peak_customer_count, reason, reason_category, lat, lon)
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                ''', (
                    r['incident_id'], r.get('utility'), r.get('county'), timestamp,
                    r.get('customer_count'), r.get('reason'), r.get('reason_category'),
                    r.get('lat'), r.get('lon'),
                ))
                if cursor.rowcount > 0:
                    opened += 1
            else:
                peak = max(open_event['peak_customer_count'] or 0, r.get('customer_count') or 0)
                cursor.execute('''
                    UPDATE teco_incident_events
                    SET peak_customer_count = ?, utility = ?, county = ?,
                        reason = ?, reason_category = ?, lat = ?, lon = ?
                    WHERE id = ?
                ''', (
                    peak, r.get('utility'), r.get('county'),
                    r.get('reason'), r.get('reason_category'),
                    r.get('lat'), r.get('lon'), open_event['id'],
                ))

        cursor.execute('SELECT id, incident_id FROM teco_incident_events WHERE end_time IS NULL')
        for row in cursor.fetchall():
            if row['incident_id'] not in current_ids:
                cursor.execute('''
                    UPDATE teco_incident_events SET end_time = ? WHERE id = ?
                ''', (timestamp, row['id']))
                closed += 1

        conn.commit()
        print(f"TECO incident events: {opened} opened, {closed} closed this cycle")


    def log_duke_incidents(self, records):
        """
        Insert Duke Energy live outage incidents (from fetch_duke_outages.py).

        Args:
            records: list of dicts with keys: incident_id, utility,
                     customer_count, lat, lon, county, cause, cause_category
        """
        conn = self.connect()
        cursor = conn.cursor()

        fetched_at = datetime.now().isoformat()

        rows = [
            (
                r['incident_id'], fetched_at, r.get('utility'),
                r.get('customer_count'), r.get('lat'), r.get('lon'),
                r.get('county'), r.get('cause'), r.get('cause_category'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO duke_incidents
                (incident_id, fetched_at, utility, customer_count, lat, lon,
                 county, cause, cause_category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {len(rows)} Duke Energy incident records")


    def sync_duke_incident_events(self, records, timestamp=None):
        """
        Track Duke Energy incident lifecycle. Same approach as
        sync_teco_incident_events: an event opens the first time an
        incident_id is seen, stays updated with the latest known values
        while still being reported, and closes once an incident_id that
        was open stops appearing in a poll at all (Duke's feed, like
        TECO's, only lists currently-active incidents).

        NOT SAFE TO REPLAY - same characteristic as sync_outage_events and
        sync_teco_incident_events (see their docstrings): only safe for
        calls arriving in real chronological order, one live poll at a
        time. Nothing replays this today - Duke's historical storm data
        lives in the generic outage_events table via the PSC PDF
        importer, not here. If a future historical Duke incident backfill
        ever calls this with a batch of past records, wipe
        duke_incident_events for that period first.

        Args:
            records: list of dicts as returned by fetch_duke_outages.parse_incidents()
            timestamp: ISO timestamp for this poll; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        current_ids = {r['incident_id'] for r in records}

        opened = 0
        closed = 0

        for r in records:
            cursor.execute('''
                SELECT id, peak_customer_count FROM duke_incident_events
                WHERE incident_id = ? AND end_time IS NULL
            ''', (r['incident_id'],))
            open_event = cursor.fetchone()

            if open_event is None:
                cursor.execute('''
                    INSERT OR IGNORE INTO duke_incident_events
                        (incident_id, utility, county, start_time, end_time,
                         peak_customer_count, cause, cause_category, lat, lon)
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                ''', (
                    r['incident_id'], r.get('utility'), r.get('county'), timestamp,
                    r.get('customer_count'), r.get('cause'), r.get('cause_category'),
                    r.get('lat'), r.get('lon'),
                ))
                if cursor.rowcount > 0:
                    opened += 1
            else:
                peak = max(open_event['peak_customer_count'] or 0, r.get('customer_count') or 0)
                cursor.execute('''
                    UPDATE duke_incident_events
                    SET peak_customer_count = ?, utility = ?, county = ?,
                        cause = ?, cause_category = ?, lat = ?, lon = ?
                    WHERE id = ?
                ''', (
                    peak, r.get('utility'), r.get('county'),
                    r.get('cause'), r.get('cause_category'),
                    r.get('lat'), r.get('lon'), open_event['id'],
                ))

        cursor.execute('SELECT id, incident_id FROM duke_incident_events WHERE end_time IS NULL')
        for row in cursor.fetchall():
            if row['incident_id'] not in current_ids:
                cursor.execute('''
                    UPDATE duke_incident_events SET end_time = ? WHERE id = ?
                ''', (timestamp, row['id']))
                closed += 1

        conn.commit()
        print(f"Duke incident events: {opened} opened, {closed} closed this cycle")


    def log_duke_counties(self, records):
        """
        Insert Duke Energy county rollup snapshots (from
        fetch_duke_outages.py's parse_counties()).
        """
        conn = self.connect()
        cursor = conn.cursor()

        fetched_at = datetime.now().isoformat()

        rows = [
            (
                fetched_at, r.get('utility'), r.get('county'),
                r.get('area_of_interest_id'), r.get('customers_served'),
                r.get('etr_override'), r.get('cause_code_override'),
                r.get('crew_status_override'), r.get('customers_affected_override'),
                r.get('max_customers_affected'), r.get('active_events_count'),
                r.get('restored_events_count'), r.get('last_updated'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO duke_counties
                (fetched_at, utility, county, area_of_interest_id, customers_served,
                 etr_override, cause_code_override, crew_status_override,
                 customers_affected_override, max_customers_affected,
                 active_events_count, restored_events_count, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {cursor.rowcount} new Duke county records (of {len(rows)} counties)")


    def log_duke_system_alerts(self, records):
        """
        Insert Duke Energy system alerts (from fetch_duke_outages.py's
        parse_system_alerts()) - map-data-reliability notices, not weather.
        """
        conn = self.connect()
        cursor = conn.cursor()

        fetched_at = datetime.now().isoformat()

        rows = [
            (
                r.get('duke_alert_id'), fetched_at, r.get('title'),
                r.get('description'), int(bool(r.get('active_indicator'))),
                r.get('alert_type'), r.get('start_time'), r.get('end_time'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO duke_system_alerts
                (duke_alert_id, fetched_at, title, description,
                 active_indicator, alert_type, start_time, end_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {cursor.rowcount} new Duke system alert records (of {len(rows)} fetched)")


    def get_duke_open_events(self):
        """
        Return currently open duke_incident_events (end_time IS NULL),
        worst (by peak customer count) first. Includes
        current_customer_count from the latest duke_incidents row fetched
        for that incident_id, alongside the lifecycle peak - same "peak
        vs. right now" reasoning as get_open_events().
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customer_count AS current_customer_count
            FROM duke_incident_events oe
            LEFT JOIN duke_incidents cur
                ON cur.incident_id = oe.incident_id
                AND cur.fetched_at = (
                    SELECT MAX(fetched_at) FROM duke_incidents t2
                    WHERE t2.incident_id = oe.incident_id
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_customer_count DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_duke_recent_closed_events(self, limit=10):
        """
        Return the most recently closed duke_incident_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM duke_incident_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


    def log_tallahassee_incidents(self, records):
        """
        Insert City of Tallahassee live outage incidents (from
        fetch_tallahassee_outages.py).

        Args:
            records: list of dicts with keys: incident_id, utility,
                     customer_count, lat, lon, county, region_name, status,
                     status_category, cause, cause_category, outage_type,
                     reported_start_time, estimated_restoration
        """
        conn = self.connect()
        cursor = conn.cursor()

        fetched_at = datetime.now().isoformat()

        rows = [
            (
                r['incident_id'], fetched_at, r.get('utility'),
                r.get('customer_count'), r.get('lat'), r.get('lon'),
                r.get('county'), r.get('region_name'),
                r.get('status'), r.get('status_category'),
                r.get('cause'), r.get('cause_category'), r.get('outage_type'),
                r.get('reported_start_time'), r.get('estimated_restoration'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT OR IGNORE INTO tallahassee_incidents
                (incident_id, fetched_at, utility, customer_count, lat, lon,
                 county, region_name, status, status_category, cause,
                 cause_category, outage_type, reported_start_time,
                 estimated_restoration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {len(rows)} City of Tallahassee incident records")


    def sync_tallahassee_incident_events(self, records, timestamp=None):
        """
        Track City of Tallahassee incident lifecycle. Same approach as
        sync_duke_incident_events: an event opens the first time an
        incident_id is seen, stays updated with the latest known values
        while still being reported, and closes once an incident_id that
        was open stops appearing in a poll at all (this feed, like TECO's
        and Duke's, only lists currently-active incidents).

        NOT SAFE TO REPLAY - same characteristic as
        sync_teco_incident_events/sync_duke_incident_events (see their
        docstrings): only safe for calls arriving in real chronological
        order, one live poll at a time.

        Args:
            records: list of dicts as returned by
                fetch_tallahassee_outages.parse_incidents()
            timestamp: ISO timestamp for this poll; defaults to now
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        current_ids = {r['incident_id'] for r in records}

        opened = 0
        closed = 0

        for r in records:
            cursor.execute('''
                SELECT id, peak_customer_count FROM tallahassee_incident_events
                WHERE incident_id = ? AND end_time IS NULL
            ''', (r['incident_id'],))
            open_event = cursor.fetchone()

            if open_event is None:
                cursor.execute('''
                    INSERT OR IGNORE INTO tallahassee_incident_events
                        (incident_id, utility, county, region_name, start_time,
                         end_time, peak_customer_count, cause, cause_category,
                         lat, lon)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                ''', (
                    r['incident_id'], r.get('utility'), r.get('county'), r.get('region_name'),
                    timestamp, r.get('customer_count'), r.get('cause'), r.get('cause_category'),
                    r.get('lat'), r.get('lon'),
                ))
                if cursor.rowcount > 0:
                    opened += 1
            else:
                peak = max(open_event['peak_customer_count'] or 0, r.get('customer_count') or 0)
                cursor.execute('''
                    UPDATE tallahassee_incident_events
                    SET peak_customer_count = ?, utility = ?, county = ?, region_name = ?,
                        cause = ?, cause_category = ?, lat = ?, lon = ?
                    WHERE id = ?
                ''', (
                    peak, r.get('utility'), r.get('county'), r.get('region_name'),
                    r.get('cause'), r.get('cause_category'),
                    r.get('lat'), r.get('lon'), open_event['id'],
                ))

        cursor.execute('SELECT id, incident_id FROM tallahassee_incident_events WHERE end_time IS NULL')
        for row in cursor.fetchall():
            if row['incident_id'] not in current_ids:
                cursor.execute('''
                    UPDATE tallahassee_incident_events SET end_time = ? WHERE id = ?
                ''', (timestamp, row['id']))
                closed += 1

        conn.commit()
        print(f"Tallahassee incident events: {opened} opened, {closed} closed this cycle")


    def get_tallahassee_open_events(self):
        """
        Return currently open tallahassee_incident_events (end_time IS
        NULL), worst (by peak customer count) first. Includes
        current_customer_count, current status, and estimated_restoration
        from the latest tallahassee_incidents row fetched for that
        incident_id, alongside the lifecycle peak - same "peak vs. right
        now" reasoning as get_open_events(). Unlike TECO's equivalent
        (whose lifecycle table never actually stored estimated_restoration
        at all, so its dashboard column has always silently shown "—"),
        this join pulls both fields for real since the raw data is there.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT oe.*,
                   cur.customer_count AS current_customer_count,
                   cur.status AS current_status,
                   cur.estimated_restoration AS current_estimated_restoration
            FROM tallahassee_incident_events oe
            LEFT JOIN tallahassee_incidents cur
                ON cur.incident_id = oe.incident_id
                AND cur.fetched_at = (
                    SELECT MAX(fetched_at) FROM tallahassee_incidents t2
                    WHERE t2.incident_id = oe.incident_id
                )
            WHERE oe.end_time IS NULL
            ORDER BY oe.peak_customer_count DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]


    def get_tallahassee_recent_closed_events(self, limit=10):
        """
        Return the most recently closed tallahassee_incident_events.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM tallahassee_incident_events
            WHERE end_time IS NOT NULL
            ORDER BY end_time DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


				



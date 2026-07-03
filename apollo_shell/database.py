import sqlite3
from datetime import datetime
import os


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
                narrative TEXT
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
            CREATE INDEX IF NOT EXISTS idx_county 
            ON outages(county)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_weather_timestamp 
            ON weather_alerts(timestamp)
        ''')
        
        conn.commit()
        print(f"Database initialized: {self.db_path}")
    
    
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
        print(f"DEBUG: log_multiple_outages called with {len(outage_list)} records")

        conn = self.connect()
        cursor = conn.cursor()

        timestamp = timestamp or datetime.now().isoformat()
        print(f"DEBUG: Timestamp: {timestamp}")
        
        records = []
        for outage in outage_list:
            county = outage['county']
            customers_out = outage['customers_out']
            customers_served = outage['customers_served']
            percentage_out = (customers_out / customers_served * 100) if customers_served > 0 else 0
            
            records.append((timestamp, utility, county, customers_out, customers_served, percentage_out))
        
        print(f"DEBUG: Built {len(records)} records to insert")
        print(f"DEBUG: First record: {records[0] if records else 'NONE'}")
        
        cursor.executemany('''
            INSERT INTO outages (timestamp, utility, county, customers_out, customers_served, percentage_out)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', records)
        
        print(f"DEBUG: executemany completed, rows affected: {cursor.rowcount}")
        
        conn.commit()
        print(f"DEBUG: commit completed")
        print(f"Logged {len(records)} outage records for {utility}")


    def sync_outage_events(self, utility, outage_list, timestamp=None):
        """
        Update outage_events from a fresh batch of per-county snapshots.

        For each county: if customers_out > 0 and no event is currently
        open (end_time IS NULL), start one. If one is already open, bump
        its peak if this snapshot is worse. If customers_out == 0 and an
        event is open, close it.

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
                        INSERT INTO outage_events
                            (utility, county, start_time, end_time, peak_customers_out, peak_percentage_out, customers_served)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    ''', (utility, county, timestamp, customers_out, percentage_out, customers_served))
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


    def log_weather_alerts(self, alert_list):
        """
        Insert multiple weather alert records at once

        Args:
            alert_list: List of dicts as returned by fetch_weather.parse_alert()
                        (keys: event, headline, severity, urgency, areas,
                        effective, expires, description)
        """
        conn = self.connect()
        cursor = conn.cursor()

        timestamp = datetime.now().isoformat()

        records = []
        for alert in alert_list:
            records.append((
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
            INSERT INTO weather_alerts (timestamp, event_type, severity, urgency, areas, effective, expires, headline, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()
        print(f"Logged {len(records)} weather alert records")


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
        Return currently open outage_events (end_time IS NULL), worst first.
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM outage_events
            WHERE end_time IS NULL
            ORDER BY peak_percentage_out DESC
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
                     narrative
        """
        conn = self.connect()
        cursor = conn.cursor()

        rows = [
            (
                r['storm_name'], r['county'], r['zone_name'], r['event_type'],
                r.get('begin_time'), r.get('end_time'),
                r.get('reported_wind_mph'), r.get('narrative'),
            )
            for r in records
        ]

        cursor.executemany('''
            INSERT INTO storm_severity
                (storm_name, county, zone_name, event_type, begin_time, end_time, reported_wind_mph, narrative)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)

        conn.commit()
        print(f"Logged {len(rows)} storm severity records")


				



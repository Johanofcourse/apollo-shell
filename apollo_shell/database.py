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
        Create the outages table if it doesn't exist
        """
        conn = self.connect()
        cursor = conn.cursor()
        
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
        
        # Create index on timestamp for faster queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp 
            ON outages(timestamp)
        ''')
        
        # Create index on county for faster filtering
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_county 
            ON outages(county)
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




    def log_multiple_outages(self, utility, outage_list):
        """
        Insert multiple outage records at once (more efficient)
        
        Args:
            utility: Name of utility
            outage_list: List of dicts with keys: county, customers_out, customers_served
        """
        print(f"DEBUG: log_multiple_outages called with {len(outage_list)} records")
        
        conn = self.connect()
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
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
        


				



import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from fetch_poweroutage import fetch_fpl_outages, outages_to_records
from fetch_weather import get_alerts_summary


POLL_INTERVAL_SECONDS = 15 * 60


def run_outage_cycle(db):
    """
    Fetch current FPL outage data and save it to the database.
    """
    data = fetch_fpl_outages()
    if not data:
        print("Skipping outage save - no data fetched")
        return

    records = outages_to_records(data)
    db.log_multiple_outages('FPL', records)


def run_weather_cycle(db):
    """
    Fetch current Florida weather alerts and save them to the database.
    """
    summary = get_alerts_summary()
    if summary['total'] == 0:
        print("Skipping weather save - no active alerts")
        return

    db.log_weather_alerts(summary['alerts'])


def main():
    """
    Long-running poller: fetches outages and weather alerts every
    POLL_INTERVAL_SECONDS and saves them to the database, so correlate.py
    has concurrent data to match against.
    """
    db = OutageDatabase()

    print(f"Apollo Shell poller starting (every {POLL_INTERVAL_SECONDS // 60} min). Ctrl+C to stop.")

    try:
        while True:
            cycle_start = datetime.now()
            print(f"\n{'=' * 70}\nCycle started at {cycle_start.isoformat()}\n{'=' * 70}")

            try:
                run_outage_cycle(db)
            except Exception as e:
                print(f"Outage fetch cycle failed: {e}")

            try:
                run_weather_cycle(db)
            except Exception as e:
                print(f"Weather fetch cycle failed: {e}")

            print(f"Cycle complete. Sleeping {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down Apollo Shell poller...")
    finally:
        db.close()


if __name__ == "__main__":
    main()

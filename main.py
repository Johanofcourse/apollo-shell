import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from fetch_poweroutage import fetch_fpl_outages, outages_to_records
from fetch_weather import get_alerts_summary
from fetch_teco_outages import get_incidents_summary
from correlate import find_correlations, correlation_summary


POLL_INTERVAL_SECONDS = 15 * 60


def run_outage_cycle(db):
    """
    Fetch current FPL outage data, save the snapshot, and update
    outage_events lifecycle tracking (start/end per county).
    """
    data = fetch_fpl_outages()
    if not data:
        print("Skipping outage save - no data fetched")
        return

    records = outages_to_records(data)
    timestamp = datetime.now().isoformat()
    db.log_multiple_outages('FPL', records, timestamp=timestamp)
    db.sync_outage_events('FPL', records, timestamp=timestamp)


def run_weather_cycle(db):
    """
    Fetch current Florida weather alerts and save them to the database.
    """
    summary = get_alerts_summary()
    if summary['total'] == 0:
        print("Skipping weather save - no active alerts")
        return

    db.log_weather_alerts(summary['alerts'])


def run_teco_cycle(db):
    """
    Fetch TECO's live outage incidents and save them - a separate,
    incident-level feed from a different utility, not part of the
    FPL/outage_events pipeline.
    """
    incidents = get_incidents_summary()
    if not incidents:
        print("Skipping TECO save - no active incidents")
        return

    db.log_teco_incidents(incidents)


def run_correlation_cycle():
    """
    Compute current outage/weather correlations and log a summary.
    """
    matches = find_correlations()
    if not matches:
        print("Correlation: no matches this cycle")
        return

    summary = correlation_summary(matches)
    print(f"Correlation: {len(matches)} matches across {len(summary)} counties")
    for county, stats in summary.items():
        print(
            f"  {county}: {stats['outage_count']} outage(s), "
            f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}"
        )


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

            try:
                run_teco_cycle(db)
            except Exception as e:
                print(f"TECO fetch cycle failed: {e}")

            try:
                run_correlation_cycle()
            except Exception as e:
                print(f"Correlation cycle failed: {e}")

            print(f"Cycle complete. Sleeping {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down Apollo Shell poller...")
    finally:
        db.close()


if __name__ == "__main__":
    main()

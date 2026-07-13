import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from fetch_fpl_outages import get_combined_fpl_records, UTILITY_NAME as FPL_UTILITY_NAME
from fetch_weather import get_alerts_summary
from fetch_teco_outages import get_incidents_summary
from fetch_duke_outages import (
    get_incidents_summary as get_duke_incidents_summary,
    get_counties_summary as get_duke_counties_summary,
    get_system_alerts_summary as get_duke_system_alerts_summary,
)
from fetch_jea_outages import get_jea_summary
from correlate import (
    find_correlations, correlation_summary,
    find_teco_correlations, teco_correlation_summary,
    find_duke_correlations, duke_correlation_summary,
    find_jea_correlations,
)


POLL_INTERVAL_SECONDS = 15 * 60


def run_outage_cycle(db):
    """
    Fetch current FPL outage data (main feed + the separate Panhandle
    feed, combined - see get_combined_fpl_records()), save the snapshot,
    and update outage_events lifecycle tracking (start/end per county).
    """
    records = get_combined_fpl_records()
    if not records:
        print("Skipping outage save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_multiple_outages(FPL_UTILITY_NAME, records, timestamp=timestamp)
    db.sync_outage_events(FPL_UTILITY_NAME, records, timestamp=timestamp)


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
    Fetch TECO's live outage incidents, save them, and update
    teco_incident_events lifecycle tracking (start/end per incident).
    """
    incidents = get_incidents_summary()
    if not incidents:
        print("Skipping TECO save - no active incidents")
        return

    timestamp = datetime.now().isoformat()
    db.log_teco_incidents(incidents)
    db.sync_teco_incident_events(incidents, timestamp=timestamp)


def run_duke_cycle(db):
    """
    Fetch Duke Energy's live outage incidents, county rollups, and system
    alerts; save them, and update duke_incident_events lifecycle tracking
    (start/end per incident).
    """
    incidents = get_duke_incidents_summary()
    timestamp = datetime.now().isoformat()
    if incidents:
        db.log_duke_incidents(incidents)
        db.sync_duke_incident_events(incidents, timestamp=timestamp)
    else:
        print("Skipping Duke incident save - no active incidents")

    counties = get_duke_counties_summary()
    if counties:
        db.log_duke_counties(counties)
    else:
        print("Skipping Duke county save - no county data fetched")

    alerts = get_duke_system_alerts_summary()
    if alerts:
        db.log_duke_system_alerts(alerts)


def run_jea_cycle(db):
    """
    Fetch JEA's live ZIP-level outage report, save the raw per-ZIP
    snapshot, and update jea_outage_events lifecycle tracking (start/end
    per county, rolled up from ZIP-level numbers).
    """
    zip_records, county_rollup = get_jea_summary()
    if not zip_records:
        print("Skipping JEA save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_jea_outages(zip_records, timestamp=timestamp)
    db.sync_jea_outage_events(county_rollup, timestamp=timestamp)


def run_correlation_cycle():
    """
    Compute current outage/weather correlations (FPL, TECO, Duke, and
    JEA) and log a summary.
    """
    matches = find_correlations()
    if not matches:
        print("FPL correlation: no matches this cycle")
    else:
        summary = correlation_summary(matches)
        print(f"FPL correlation: {len(matches)} matches across {len(summary)} counties")
        for county, stats in summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    teco_matches = find_teco_correlations()
    if not teco_matches:
        print("TECO correlation: no matches this cycle")
    else:
        teco_summary = teco_correlation_summary(teco_matches)
        print(f"TECO correlation: {len(teco_matches)} matches across {len(teco_summary)} counties")
        for county, stats in teco_summary.items():
            print(
                f"  {county}: {stats['incident_count']} incident(s), "
                f"max {stats['max_customer_count']} customers, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    duke_matches = find_duke_correlations()
    if not duke_matches:
        print("Duke correlation: no matches this cycle")
    else:
        duke_summary = duke_correlation_summary(duke_matches)
        print(f"Duke correlation: {len(duke_matches)} matches across {len(duke_summary)} counties")
        for county, stats in duke_summary.items():
            print(
                f"  {county}: {stats['incident_count']} incident(s), "
                f"max {stats['max_customer_count']} customers, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    jea_matches = find_jea_correlations()
    if not jea_matches:
        print("JEA correlation: no matches this cycle")
    else:
        jea_summary = correlation_summary(jea_matches)
        print(f"JEA correlation: {len(jea_matches)} matches across {len(jea_summary)} counties")
        for county, stats in jea_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
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
                db.log_pipeline_error("fpl", str(e))

            try:
                run_weather_cycle(db)
            except Exception as e:
                print(f"Weather fetch cycle failed: {e}")
                db.log_pipeline_error("weather", str(e))

            try:
                run_teco_cycle(db)
            except Exception as e:
                print(f"TECO fetch cycle failed: {e}")
                db.log_pipeline_error("teco", str(e))

            try:
                run_duke_cycle(db)
            except Exception as e:
                print(f"Duke fetch cycle failed: {e}")
                db.log_pipeline_error("duke", str(e))

            try:
                run_jea_cycle(db)
            except Exception as e:
                print(f"JEA fetch cycle failed: {e}")
                db.log_pipeline_error("jea", str(e))

            try:
                run_correlation_cycle()
            except Exception as e:
                print(f"Correlation cycle failed: {e}")
                db.log_pipeline_error("correlation", str(e))

            print(f"Cycle complete. Sleeping {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down Apollo Shell poller...")
    finally:
        db.close()


if __name__ == "__main__":
    main()

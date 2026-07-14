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
from fetch_tallahassee_outages import get_incidents_summary as get_tallahassee_incidents_summary
from fetch_talquin_outages import get_talquin_records, TALQUIN_API_URL
from fetch_fpuc_outages import fetch_fpuc_outage_summary, outages_to_records as fpuc_outages_to_records, markers_to_incidents, FPUC_API_URL
from fetch_preco_outages import get_preco_records, PRECO_API_URL
from fetch_fkec_outages import get_fkec_records, FKEC_API_URL
from correlate import (
    find_correlations, correlation_summary,
    find_teco_correlations, teco_correlation_summary,
    find_duke_correlations, duke_correlation_summary,
    find_jea_correlations,
    find_tallahassee_correlations,
    find_talquin_correlations,
    find_fpuc_incident_correlations,
    find_preco_correlations,
    find_fkec_correlations,
)


POLL_INTERVAL_SECONDS = 15 * 60


def run_outage_cycle(db):
    """
    Fetch current FPL outage data (main feed + the separate Panhandle
    feed, combined - see get_combined_fpl_records()), save the snapshot,
    and update outage_events lifecycle tracking (start/end per county).

    Raises if the combined result is empty. FPL's main feed is always
    configured (a missing config already raises inside
    fetch_fpl_outages() itself) and reports on 60+ counties every cycle
    in steady state, so a fully empty result here means the underlying
    request itself failed - not that nothing is happening statewide.
    Previously this was silently swallowed (fetch_fpl_outages() catches
    its own RequestException and returns None), so a real, sustained FPL
    outage-map failure would never have shown up in pipeline_errors /
    the dashboard's health strip - only ever a coincidental failure
    elsewhere, like a database write error. Raising here lets the
    existing try/except in main()'s poll loop catch and log it, same as
    every other cycle.
    """
    records = get_combined_fpl_records()
    if not records:
        raise RuntimeError("FPL fetch returned no records - see the poller's own log for the underlying request error")

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

    Raises if zip_records is empty, same reasoning as run_outage_cycle()
    above - JEA's feed covers every serviced ZIP every cycle in steady
    state (missing config already raises inside fetch_jea_areas()
    itself), so an empty result means the request/parsing chain failed,
    not that nothing is currently affected. Same silent-swallow gap as
    FPL previously had (fetch_jea_areas() catches its own failure and
    returns []).
    """
    zip_records, county_rollup = get_jea_summary()
    if not zip_records:
        raise RuntimeError("JEA fetch returned no records - see the poller's own log for the underlying request error")

    timestamp = datetime.now().isoformat()
    db.log_jea_outages(zip_records, timestamp=timestamp)
    db.sync_jea_outage_events(county_rollup, timestamp=timestamp)


def run_tallahassee_cycle(db):
    """
    Fetch City of Tallahassee's live outage incidents, save them, and
    update tallahassee_incident_events lifecycle tracking (start/end per
    incident).
    """
    incidents = get_tallahassee_incidents_summary()
    if not incidents:
        print("Skipping Tallahassee save - no active incidents")
        return

    timestamp = datetime.now().isoformat()
    db.log_tallahassee_incidents(incidents)
    db.sync_tallahassee_incident_events(incidents, timestamp=timestamp)


def run_talquin_cycle(db):
    """
    Fetch Talquin Electric Cooperative's live county-level outage data,
    save the raw snapshot, and update talquin_outage_events lifecycle
    tracking (start/end per county) - a county-rollup source like
    FPL/JEA, not an incident list.

    Raises only when TALQUIN_API_URL is actually configured but the
    fetch still came back empty - same reasoning as run_outage_cycle()
    above (Talquin's feed reports on all 10 counties every cycle in
    steady state, so that combination means the request failed, not
    that nothing is happening). An unset URL is left exactly as before
    (a deployment where this integration simply isn't turned on yet,
    not a failure).
    """
    records = get_talquin_records()
    if not records:
        if TALQUIN_API_URL:
            raise RuntimeError("Talquin fetch returned no records - see the poller's own log for the underlying request error")
        print("Skipping Talquin save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_talquin_outages(records, timestamp=timestamp)
    db.sync_talquin_outage_events(records, timestamp=timestamp)


def run_preco_cycle(db):
    """
    Fetch Peace River Electric Cooperative's live county-level outage
    data, save the raw snapshot, and update preco_outage_events
    lifecycle tracking (start/end per county) - a county-rollup source
    like Talquin, not an incident list.

    Raises only when PRECO_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_talquin_cycle() above.
    """
    records = get_preco_records()
    if not records:
        if PRECO_API_URL:
            raise RuntimeError("PRECO fetch returned no records - see the poller's own log for the underlying request error")
        print("Skipping PRECO save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_preco_outages(records, timestamp=timestamp)
    db.sync_preco_outage_events(records, timestamp=timestamp)


def run_fkec_cycle(db):
    """
    Fetch Florida Keys Electric Cooperative's live outage data, save the
    raw snapshot, and update fkec_outage_events lifecycle tracking
    (start/end) - a county-rollup source (always exactly one row,
    Monroe), same shape as PRECO/Talquin, not an incident list.

    Raises only when FKEC_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_preco_cycle() above (see the 2026-07-13 pipeline-visibility fix:
    fetch_fkec_outages() catches its own RequestException and returns
    None, so this is what actually gets that failure logged/surfaced on
    the dashboard instead of silently vanishing).
    """
    records = get_fkec_records()
    if not records:
        if FKEC_API_URL:
            raise RuntimeError("FKEC fetch returned no records - see the poller's own log for the underlying request error")
        print("Skipping FKEC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_fkec_outages(records, timestamp=timestamp)
    db.sync_fkec_outage_events(records, timestamp=timestamp)


def run_fpuc_cycle(db):
    """
    Fetch FPUC's live outage data once, then update both trackers from
    that same response: the combined-territory total (always exactly
    one "county" row - see fetch_fpuc_outages.COMBINED_TERRITORY_LABEL)
    AND the real per-incident markers (reverse-geocoded to a real
    county, confirmed possible 2026-07-13). One fetch, two derived
    views, rather than two separate network calls that could observe
    slightly different live state.

    Raises only when FPUC_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_talquin_cycle().
    """
    data = fetch_fpuc_outage_summary()
    if not data:
        if FPUC_API_URL:
            raise RuntimeError("FPUC fetch returned no data - see the poller's own log for the underlying request error")
        print("Skipping FPUC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()

    records = fpuc_outages_to_records(data)
    if records:
        db.log_fpuc_outages(records, timestamp=timestamp)
        db.sync_fpuc_outage_events(records, timestamp=timestamp)

    incidents = markers_to_incidents(data)
    db.log_fpuc_incidents(incidents)
    db.sync_fpuc_incident_events(incidents, timestamp=timestamp)


def run_correlation_cycle():
    """
    Compute current outage/weather correlations (FPL, TECO, Duke, JEA,
    City of Tallahassee, Talquin, FPUC, PRECO, and FKEC) and log a
    summary.
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

    tallahassee_matches = find_tallahassee_correlations()
    if not tallahassee_matches:
        print("Tallahassee correlation: no matches this cycle")
    else:
        tallahassee_summary = duke_correlation_summary(tallahassee_matches)
        print(f"Tallahassee correlation: {len(tallahassee_matches)} matches across {len(tallahassee_summary)} counties")
        for county, stats in tallahassee_summary.items():
            print(
                f"  {county}: {stats['incident_count']} incident(s), "
                f"max {stats['max_customer_count']} customers, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    talquin_matches = find_talquin_correlations()
    if not talquin_matches:
        print("Talquin correlation: no matches this cycle")
    else:
        talquin_summary = correlation_summary(talquin_matches)
        print(f"Talquin correlation: {len(talquin_matches)} matches across {len(talquin_summary)} counties")
        for county, stats in talquin_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    fpuc_matches = find_fpuc_incident_correlations()
    if not fpuc_matches:
        print("FPUC correlation: no matches this cycle")
    else:
        fpuc_summary = duke_correlation_summary(fpuc_matches)
        print(f"FPUC correlation: {len(fpuc_matches)} matches across {len(fpuc_summary)} counties")
        for county, stats in fpuc_summary.items():
            print(
                f"  {county}: {stats['incident_count']} incident(s), "
                f"max {stats['max_customer_count']} customers, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    preco_matches = find_preco_correlations()
    if not preco_matches:
        print("PRECO correlation: no matches this cycle")
    else:
        preco_summary = correlation_summary(preco_matches)
        print(f"PRECO correlation: {len(preco_matches)} matches across {len(preco_summary)} counties")
        for county, stats in preco_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    fkec_matches = find_fkec_correlations()
    if not fkec_matches:
        print("FKEC correlation: no matches this cycle")
    else:
        fkec_summary = correlation_summary(fkec_matches)
        print(f"FKEC correlation: {len(fkec_matches)} matches across {len(fkec_summary)} counties")
        for county, stats in fkec_summary.items():
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
                run_tallahassee_cycle(db)
            except Exception as e:
                print(f"Tallahassee fetch cycle failed: {e}")
                db.log_pipeline_error("tallahassee", str(e))

            try:
                run_talquin_cycle(db)
            except Exception as e:
                print(f"Talquin fetch cycle failed: {e}")
                db.log_pipeline_error("talquin", str(e))

            try:
                run_fpuc_cycle(db)
            except Exception as e:
                print(f"FPUC fetch cycle failed: {e}")
                db.log_pipeline_error("fpuc", str(e))

            try:
                run_preco_cycle(db)
            except Exception as e:
                print(f"PRECO fetch cycle failed: {e}")
                db.log_pipeline_error("preco", str(e))

            try:
                run_fkec_cycle(db)
            except Exception as e:
                print(f"FKEC fetch cycle failed: {e}")
                db.log_pipeline_error("fkec", str(e))

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

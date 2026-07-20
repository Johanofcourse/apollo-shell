import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from alerting import check_and_alert_pipeline_health
from fetch_fpl_outages import get_combined_fpl_records, UTILITY_NAME as FPL_UTILITY_NAME
from fetch_weather import get_alerts_summary
from fetch_teco_outages import get_incidents_summary
from fetch_duke_outages import (
    get_incidents_summary as get_duke_incidents_summary,
    get_counties_summary as get_duke_counties_summary,
    get_system_alerts_summary as get_duke_system_alerts_summary,
)
from fetch_jea_outages import get_jea_summary
from fetch_tallahassee_outages import get_rollup_summary as get_tallahassee_records, TALLAHASSEE_API_URL
from fetch_talquin_outages import get_talquin_records, TALQUIN_API_URL
from fetch_fpuc_outages import fetch_fpuc_outage_summary, outages_to_records as fpuc_outages_to_records, markers_to_incidents, FPUC_API_URL
from fetch_preco_outages import get_preco_records, PRECO_API_URL
from fetch_fkec_outages import get_fkec_records, FKEC_API_URL
from fetch_tcec_outages import (
    fetch_tcec_outage_summary, outages_to_records as tcec_outages_to_records,
    streets_affected as tcec_streets_affected, UTILITY_NAME as TCEC_UTILITY_NAME, TCEC_API_URL,
)
from fetch_erec_outages import (
    fetch_erec_outage_summary, outages_to_records as erec_outages_to_records,
    streets_affected as erec_streets_affected, UTILITY_NAME as EREC_UTILITY_NAME, EREC_API_URL,
)
from fetch_chelco_outages import (
    fetch_chelco_outage_summary, outages_to_records as chelco_outages_to_records,
    streets_affected as chelco_streets_affected, UTILITY_NAME as CHELCO_UTILITY_NAME, CHELCO_API_URL,
)
from fetch_gcec_outages import (
    fetch_gcec_outage_summary, outages_to_records as gcec_outages_to_records,
    streets_affected as gcec_streets_affected, UTILITY_NAME as GCEC_UTILITY_NAME, GCEC_API_URL,
)
import street_county_resolver
from fetch_lwbu_outages import (
    get_lwbu_records, LWBU_API_BASE,
    get_incidents_summary as get_lwbu_incidents_summary,
)
from fetch_ouc_outages import get_ouc_records, OUC_INSTANCE_ID
from fetch_lcec_outages import get_lcec_records, LCEC_API_URL
from fetch_clay_outages import (
    fetch_clay_outages, CLAY_API_URL,
    outages_to_records as clay_outages_to_records,
    incidents_to_records as clay_incidents_to_records,
)
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
    find_tcec_correlations,
    find_erec_correlations,
    find_chelco_correlations,
    find_gcec_correlations,
    find_lwbu_correlations,
    find_ouc_correlations,
    find_lcec_correlations,
    find_clay_correlations,
)
from county_status import historical_confidence_tally


POLL_INTERVAL_SECONDS = 15 * 60

# Talquin/PRECO's credentials need periodic manual refreshing (see
# TALQUIN_API_URL/PRECO_API_URL in .env.example) - checking them less
# often than every other source is a lighter request cadence while
# that's true.
TALQUIN_PRECO_INTERVAL_SECONDS = 30 * 60


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
    Fetch City of Tallahassee's live outage data, save the raw
    snapshot, and update tallahassee_outage_events lifecycle tracking
    (start/end for Leon County) - a county-rollup source like Talquin,
    not an incident list (see fetch_tallahassee_outages.
    get_rollup_summary() for why the original incident-level design,
    which never logged a single real row in this project's life, was
    replaced 2026-07-18).

    Unlike run_talquin_cycle(), there's no "empty means the request
    failed" case to detect here: Tallahassee genuinely has zero active
    outages plenty of cycles (get_rollup_summary() always returns
    exactly one record for Leon County, customers_out=0 included), so
    an empty result isn't a distinguishable failure signal the way it
    is for Talquin's always-full 10-county feed. A real network/API
    failure already prints its own message inside
    fetch_tallahassee_outages() and comes back indistinguishable from
    "genuinely nothing happening" - the same tradeoff every incident-
    level source here already has.
    """
    if not TALLAHASSEE_API_URL:
        print("Skipping Tallahassee save - TALLAHASSEE_API_URL not configured")
        return

    records = get_tallahassee_records()
    timestamp = datetime.now().isoformat()
    db.log_tallahassee_outages(records, timestamp=timestamp)
    db.sync_tallahassee_outage_events(records, timestamp=timestamp)


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


# A real active outage can carry dozens of never-before-seen streets in
# one poll (79 real ones for CHELCO alone, confirmed 2026-07-18) - each
# needing several rate-limited (~1/sec) network round trips to resolve.
# Capping how many NEW streets get resolved per cycle keeps this from
# blocking every other utility's polling behind it; already-cached
# streets (the common case once a backlog is worked through) are
# unaffected by this cap. The rest simply get resolved on a later cycle
# while the outage is still active - see street_county_resolver.
# resolve_streets()'s own docstring.
STREET_RESOLUTION_CAP_PER_CYCLE = 10


def run_tcec_cycle(db):
    """
    Fetch Tri-County Electric Cooperative's live combined-territory
    outage data, save the raw snapshot, update tcec_outage_events
    lifecycle tracking (start/end), and refresh the real per-county
    activity read derived from this cycle's streetsAffected list (see
    street_county_resolver.py) - a combined-territory source (always
    exactly one row, see fetch_tcec_outages.COMBINED_TERRITORY_LABEL),
    same shape as FPUC's original tracker, not a per-county rollup, but
    real street-level detail sometimes rides along in the same response.

    Raises only when TCEC_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_fkec_cycle() above.
    """
    data = fetch_tcec_outage_summary()
    records = tcec_outages_to_records(data)
    if not records:
        if TCEC_API_URL:
            raise RuntimeError("TCEC fetch returned no data - see the poller's own log for the underlying request error")
        print("Skipping TCEC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_tcec_outages(records, timestamp=timestamp)
    db.sync_tcec_outage_events(records, timestamp=timestamp)

    streets = tcec_streets_affected(data)
    active = street_county_resolver.active_counties(
        TCEC_UTILITY_NAME, streets, db, max_new_lookups=STREET_RESOLUTION_CAP_PER_CYCLE
    )
    db.store_active_counties(TCEC_UTILITY_NAME, active, timestamp=timestamp)


def run_erec_cycle(db):
    """
    Fetch Escambia River Electric Cooperative's live combined-territory
    outage data, save the raw snapshot, update erec_outage_events
    lifecycle tracking (start/end), and refresh the real per-county
    activity read derived from this cycle's streetsAffected list - same
    platform/shape as TCEC (always exactly one row, see
    fetch_erec_outages.COMBINED_TERRITORY_LABEL). EREC's own
    streetsAffected has so far always been null even during a real
    active outage (confirmed 2026-07-18), so this will usually resolve
    to [] here - kept for consistency with the other three and in case
    that changes.

    Raises only when EREC_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_tcec_cycle() above.
    """
    data = fetch_erec_outage_summary()
    records = erec_outages_to_records(data)
    if not records:
        if EREC_API_URL:
            raise RuntimeError("EREC fetch returned no data - see the poller's own log for the underlying request error")
        print("Skipping EREC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_erec_outages(records, timestamp=timestamp)
    db.sync_erec_outage_events(records, timestamp=timestamp)

    streets = erec_streets_affected(data)
    active = street_county_resolver.active_counties(
        EREC_UTILITY_NAME, streets, db, max_new_lookups=STREET_RESOLUTION_CAP_PER_CYCLE
    )
    db.store_active_counties(EREC_UTILITY_NAME, active, timestamp=timestamp)


def run_chelco_cycle(db):
    """
    Fetch Choctawhatchee Electric Cooperative's live combined-territory
    outage data, save the raw snapshot, update chelco_outage_events
    lifecycle tracking (start/end), and refresh the real per-county
    activity read derived from this cycle's streetsAffected list (see
    street_county_resolver.py) - same platform/shape as TCEC/EREC
    (always exactly one row, see
    fetch_chelco_outages.COMBINED_TERRITORY_LABEL), but real street-level
    detail sometimes rides along in the same response (confirmed real
    2026-07-18: 79 real street names during an actual active outage).

    Raises only when CHELCO_API_URL is actually configured but the
    fetch still came back empty - same reasoning/config-check pattern
    as run_erec_cycle() above.
    """
    data = fetch_chelco_outage_summary()
    records = chelco_outages_to_records(data)
    if not records:
        if CHELCO_API_URL:
            raise RuntimeError("CHELCO fetch returned no data - see the poller's own log for the underlying request error")
        print("Skipping CHELCO save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_chelco_outages(records, timestamp=timestamp)
    db.sync_chelco_outage_events(records, timestamp=timestamp)

    streets = chelco_streets_affected(data)
    active = street_county_resolver.active_counties(
        CHELCO_UTILITY_NAME, streets, db, max_new_lookups=STREET_RESOLUTION_CAP_PER_CYCLE
    )
    db.store_active_counties(CHELCO_UTILITY_NAME, active, timestamp=timestamp)


def run_gcec_cycle(db):
    """
    Fetch Gulf Coast Electric Cooperative's live combined-territory
    outage data, save the raw snapshot, update gcec_outage_events
    lifecycle tracking (start/end), and refresh the real per-county
    activity read derived from this cycle's streetsAffected list (see
    street_county_resolver.py) - same platform/shape as TCEC/EREC/
    CHELCO (always exactly one row, see
    fetch_gcec_outages.COMBINED_TERRITORY_LABEL), but real street-level
    detail sometimes rides along in the same response (confirmed real
    2026-07-18: 2 real street names during an actual active outage).

    Raises only when GCEC_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_chelco_cycle() above.
    """
    data = fetch_gcec_outage_summary()
    records = gcec_outages_to_records(data)
    if not records:
        if GCEC_API_URL:
            raise RuntimeError("GCEC fetch returned no data - see the poller's own log for the underlying request error")
        print("Skipping GCEC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_gcec_outages(records, timestamp=timestamp)
    db.sync_gcec_outage_events(records, timestamp=timestamp)

    streets = gcec_streets_affected(data)
    active = street_county_resolver.active_counties(
        GCEC_UTILITY_NAME, streets, db, max_new_lookups=STREET_RESOLUTION_CAP_PER_CYCLE
    )
    db.store_active_counties(GCEC_UTILITY_NAME, active, timestamp=timestamp)


def run_lwbu_cycle(db):
    """
    Fetch Lake Worth Beach Utilities' live outage summary and individual
    incidents, save both, and update both lifecycle trackers - a
    real-percentage single-county rollup (Palm Beach) plus a separate
    real per-incident detail feed, same two-shapes-one-utility approach
    as run_duke_cycle() above.

    Raises only when LWBU_API_BASE is actually configured but the
    summary fetch still came back empty - same reasoning/config-check
    pattern as run_gcec_cycle() above.
    """
    records = get_lwbu_records()
    if records:
        timestamp = datetime.now().isoformat()
        db.log_lwbu_outages(records, timestamp=timestamp)
        db.sync_lwbu_outage_events(records, timestamp=timestamp)
    elif LWBU_API_BASE:
        raise RuntimeError("LWBU summary fetch returned no data - see the poller's own log for the underlying request error")
    else:
        print("Skipping LWBU summary save - no data fetched")

    incidents = get_lwbu_incidents_summary()
    if incidents:
        timestamp = datetime.now().isoformat()
        db.log_lwbu_incidents(incidents)
        db.sync_lwbu_incident_events(incidents, timestamp=timestamp)
    else:
        print("Skipping LWBU incident save - no active incidents")


def run_ouc_cycle(db):
    """
    Fetch Orlando Utilities Commission's live county-level outage data,
    save the raw snapshot, and update ouc_outage_events lifecycle
    tracking (start/end) - a real single-county rollup source (Orange),
    same shape as FKEC/PRECO, not an incident list. OUC's own real
    per-incident/cluster layer is confirmed to exist but was only ever
    observed empty (see fetch_ouc_outages.py's module docstring) - not
    integrated here yet.

    Raises only when OUC_INSTANCE_ID is actually configured but the
    fetch still came back empty - same reasoning/config-check pattern
    as run_fkec_cycle() above.
    """
    records = get_ouc_records()
    if not records:
        if OUC_INSTANCE_ID:
            raise RuntimeError("OUC fetch returned no records - see the poller's own log for the underlying request error")
        print("Skipping OUC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_ouc_outages(records, timestamp=timestamp)
    db.sync_ouc_outage_events(records, timestamp=timestamp)


def run_lcec_cycle(db):
    """
    Fetch Lee County Electric Cooperative's live outage data, save the
    raw snapshot, and update lcec_outage_events lifecycle tracking
    (start/end) - a real per-county rollup source (Charlotte, Broward,
    Collier, Hendry, Lee), same shape as FKEC/OUC, not an incident
    list. LCEC's own real per-incident array (outages[], with real
    start/estimated-restoration times) is confirmed to exist and
    genuinely has live data - not integrated here yet, same honest
    disclosure as OUC's cluster layer.

    Raises only when LCEC_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_fkec_cycle() above.
    """
    records = get_lcec_records()
    if not records:
        if LCEC_API_URL:
            raise RuntimeError("LCEC fetch returned no records - see the poller's own log for the underlying request error")
        print("Skipping LCEC save - no data fetched")
        return

    timestamp = datetime.now().isoformat()
    db.log_lcec_outages(records, timestamp=timestamp)
    db.sync_lcec_outage_events(records, timestamp=timestamp)


def run_clay_cycle(db):
    """
    Fetch Clay Electric Cooperative's live outage data once, then update
    both trackers from that same response - same "one fetch, two derived
    views" principle as run_fpuc_cycle(): the real per-county rollup
    (Columbia, Bradford, Gilchrist, Marion, Levy, Duval, Suwannee,
    Alachua, Union, Clay, Volusia, Flagler, Lake, Putnam, Baker) AND
    Clay's real per-incident array. No county on the incident side -
    checked directly 2026-07-19 whether the raw x/y could be resolved to
    one and confirmed it isn't reliably solvable right now, so incidents
    are tracked by their own real identity only (customer count, real
    start time, live restoration estimate, crew/planned status).

    Raises only when CLAY_API_URL is actually configured but the fetch
    still came back empty - same reasoning/config-check pattern as
    run_fpuc_cycle().
    """
    data = fetch_clay_outages()
    if not data:
        if CLAY_API_URL:
            raise RuntimeError("Clay fetch returned no data - see the poller's own log for the underlying request error")
        print("Skipping Clay save - no data fetched")
        return

    timestamp = datetime.now().isoformat()

    records = clay_outages_to_records(data)
    if records:
        db.log_clay_outages(records, timestamp=timestamp)
        db.sync_clay_outage_events(records, timestamp=timestamp)

    incidents = clay_incidents_to_records(data)
    db.log_clay_incidents(incidents)
    db.sync_clay_incident_events(incidents, timestamp=timestamp)


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
    City of Tallahassee, Talquin, FPUC, PRECO, FKEC, TCEC, EREC, CHELCO,
    GCEC, and LWBU) and log a summary.
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
        tallahassee_summary = correlation_summary(tallahassee_matches)
        print(f"Tallahassee correlation: {len(tallahassee_matches)} matches across {len(tallahassee_summary)} counties")
        for county, stats in tallahassee_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"alerts={stats['alert_types']}, "
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

    tcec_matches = find_tcec_correlations()
    if not tcec_matches:
        print("TCEC correlation: no matches this cycle")
    else:
        tcec_summary = correlation_summary(tcec_matches)
        print(f"TCEC correlation: {len(tcec_matches)} matches across {len(tcec_summary)} counties")
        for county, stats in tcec_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    erec_matches = find_erec_correlations()
    if not erec_matches:
        print("EREC correlation: no matches this cycle")
    else:
        erec_summary = correlation_summary(erec_matches)
        print(f"EREC correlation: {len(erec_matches)} matches across {len(erec_summary)} counties")
        for county, stats in erec_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    chelco_matches = find_chelco_correlations()
    if not chelco_matches:
        print("CHELCO correlation: no matches this cycle")
    else:
        chelco_summary = correlation_summary(chelco_matches)
        print(f"CHELCO correlation: {len(chelco_matches)} matches across {len(chelco_summary)} counties")
        for county, stats in chelco_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    gcec_matches = find_gcec_correlations()
    if not gcec_matches:
        print("GCEC correlation: no matches this cycle")
    else:
        gcec_summary = correlation_summary(gcec_matches)
        print(f"GCEC correlation: {len(gcec_matches)} matches across {len(gcec_summary)} counties")
        for county, stats in gcec_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    lwbu_matches = find_lwbu_correlations()
    if not lwbu_matches:
        print("LWBU correlation: no matches this cycle")
    else:
        lwbu_summary = correlation_summary(lwbu_matches)
        print(f"LWBU correlation: {len(lwbu_matches)} matches across {len(lwbu_summary)} counties")
        for county, stats in lwbu_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    ouc_matches = find_ouc_correlations()
    if not ouc_matches:
        print("OUC correlation: no matches this cycle")
    else:
        ouc_summary = correlation_summary(ouc_matches)
        print(f"OUC correlation: {len(ouc_matches)} matches across {len(ouc_summary)} counties")
        for county, stats in ouc_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    lcec_matches = find_lcec_correlations()
    if not lcec_matches:
        print("LCEC correlation: no matches this cycle")
    else:
        lcec_summary = correlation_summary(lcec_matches)
        print(f"LCEC correlation: {len(lcec_matches)} matches across {len(lcec_summary)} counties")
        for county, stats in lcec_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )

    clay_matches = find_clay_correlations()
    if not clay_matches:
        print("Clay correlation: no matches this cycle")
    else:
        clay_summary = correlation_summary(clay_matches)
        print(f"Clay correlation: {len(clay_matches)} matches across {len(clay_summary)} counties")
        for county, stats in clay_summary.items():
            print(
                f"  {county}: {stats['outage_count']} outage(s), "
                f"peak {stats['max_percentage_out']:.2f}%, alerts={stats['alert_types']}, "
                f"confidence={stats['confidence_breakdown']}"
            )


def run_historical_tally_cycle(db):
    """
    Precompute the all-time historical weather-match confidence tally
    (county_status.historical_confidence_tally()) once per poll cycle
    and store it, so the public page can read a precomputed value
    instantly instead of paying for this real, expensive nested-loop
    correlation query (measured at ~44s as of 2026-07-14) on every page
    view. The underlying data only actually changes once per cycle
    anyway, so computing it here - right after the correlation cycle
    above, using the same fresh data - costs nothing extra in
    staleness, only removes real load-time risk from public_site.py.
    """
    tally = historical_confidence_tally()
    db.store_historical_confidence_tally(tally)


def main():
    """
    Long-running poller: fetches outages and weather alerts every
    POLL_INTERVAL_SECONDS and saves them to the database, so correlate.py
    has concurrent data to match against.
    """
    db = OutageDatabase()

    print(f"Apollo Shell poller starting (every {POLL_INTERVAL_SECONDS // 60} min). Ctrl+C to stop.")

    last_talquin_preco_check = 0.0

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

            check_talquin_preco_this_cycle = (
                time.time() - last_talquin_preco_check >= TALQUIN_PRECO_INTERVAL_SECONDS
            )
            if check_talquin_preco_this_cycle:
                last_talquin_preco_check = time.time()
            else:
                print("Skipping Talquin/PRECO this cycle (30-min interval not yet elapsed)")

            if check_talquin_preco_this_cycle:
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

            if check_talquin_preco_this_cycle:
                try:
                    run_preco_cycle(db)
                except Exception as e:
                    print(f"PRECO fetch cycle failed: {e}")
                    db.log_pipeline_error("preco", str(e))

                try:
                    check_and_alert_pipeline_health(
                        db, display_names={"talquin": "Talquin Electric Cooperative", "preco": "Peace River Electric Cooperative"}
                    )
                except Exception as e:
                    print(f"Pipeline health alert check failed: {e}")

            try:
                run_fkec_cycle(db)
            except Exception as e:
                print(f"FKEC fetch cycle failed: {e}")
                db.log_pipeline_error("fkec", str(e))

            try:
                run_tcec_cycle(db)
            except Exception as e:
                print(f"TCEC fetch cycle failed: {e}")
                db.log_pipeline_error("tcec", str(e))

            try:
                run_erec_cycle(db)
            except Exception as e:
                print(f"EREC fetch cycle failed: {e}")
                db.log_pipeline_error("erec", str(e))

            try:
                run_chelco_cycle(db)
            except Exception as e:
                print(f"CHELCO fetch cycle failed: {e}")
                db.log_pipeline_error("chelco", str(e))

            try:
                run_gcec_cycle(db)
            except Exception as e:
                print(f"GCEC fetch cycle failed: {e}")
                db.log_pipeline_error("gcec", str(e))

            try:
                run_lwbu_cycle(db)
            except Exception as e:
                print(f"LWBU fetch cycle failed: {e}")
                db.log_pipeline_error("lwbu", str(e))

            try:
                run_ouc_cycle(db)
            except Exception as e:
                print(f"OUC fetch cycle failed: {e}")
                db.log_pipeline_error("ouc", str(e))

            try:
                run_lcec_cycle(db)
            except Exception as e:
                print(f"LCEC fetch cycle failed: {e}")
                db.log_pipeline_error("lcec", str(e))

            try:
                run_clay_cycle(db)
            except Exception as e:
                print(f"Clay fetch cycle failed: {e}")
                db.log_pipeline_error("clay", str(e))

            try:
                run_correlation_cycle()
            except Exception as e:
                print(f"Correlation cycle failed: {e}")
                db.log_pipeline_error("correlation", str(e))

            try:
                run_historical_tally_cycle(db)
            except Exception as e:
                print(f"Historical tally cycle failed: {e}")
                db.log_pipeline_error("historical_tally", str(e))

            print(f"Cycle complete. Sleeping {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down Apollo Shell poller...")
    finally:
        db.close()


if __name__ == "__main__":
    main()

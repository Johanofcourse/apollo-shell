"""
Reusable data-integrity sweep - formalizes the ad hoc SQL checks this
project has repeatedly done by hand (impossible values, bad durations,
cross-storm anomalies, county coverage, pipeline health) into one script
that can be run anytime, instead of improvising new SQL each time
something feels off.

Usage: python3 apollo_shell/check_data_integrity.py
Exit code 0 if clean, 1 if anything was flagged.

Not run automatically on a schedule - this is a "run it once in a while
and read the output" tool, not something meant to gate the live poller.
"""

import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ISSUES = []


def flag(message):
    ISSUES.append(message)
    print(f"  ISSUE: {message}")


def _run_check(label, fn):
    print(f"{label}...")
    before = len(ISSUES)
    fn()
    if len(ISSUES) == before:
        print("  clean")


def check_live_impossible_values(conn):
    c = conn.cursor()
    for table in ["outage_events", "jea_outage_events", "talquin_outage_events", "fpuc_outage_events", "preco_outage_events", "fkec_outage_events", "tcec_outage_events", "erec_outage_events", "chelco_outage_events", "gcec_outage_events"]:
        c.execute(f"SELECT utility, county, start_time FROM {table} "
                  f"WHERE peak_customers_out > customers_served")
        for r in c.fetchall():
            flag(f"live {table} impossible value (out > served): {r}")


def check_live_bad_durations(conn):
    c = conn.cursor()
    for table in ["outage_events", "teco_incident_events", "duke_incident_events", "jea_outage_events", "tallahassee_outage_events", "talquin_outage_events", "fpuc_outage_events", "fpuc_incident_events", "preco_outage_events", "fkec_outage_events", "tcec_outage_events", "erec_outage_events", "chelco_outage_events", "gcec_outage_events"]:
        c.execute(f"SELECT county, start_time, end_time FROM {table} "
                  f"WHERE end_time IS NOT NULL AND end_time < start_time")
        for r in c.fetchall():
            flag(f"live {table} end before start: {r}")


def check_weather_alerts_nulls(conn):
    # Baseline of 5 is the known legacy count from before alert_id
    # tracking existed (2026-07-02) - see fetch_weather.py's synthetic-id
    # fallback, added 2026-07-08, which should prevent this from growing
    # again. A count above the baseline means that fallback isn't working.
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM weather_alerts WHERE alert_id IS NULL")
    count = c.fetchone()[0]
    if count > 5:
        flag(f"weather_alerts has {count} NULL alert_id rows (baseline: 5 known "
             f"legacy rows) - the synthetic-ID fallback in fetch_weather.py may "
             f"not be working")


def check_pipeline_health():
    from database import OutageDatabase
    db = OutageDatabase("outages.db")
    health = db.get_pipeline_health(sources=["fpl", "weather", "teco", "duke", "jea", "tallahassee", "talquin", "fpuc", "preco", "fkec", "tcec", "erec", "chelco", "gcec", "correlation"])
    db.close()
    for source, info in health.items():
        if info["status"] != "healthy":
            flag(f"pipeline source '{source}' is {info['status']}: "
                 f"{info['count_today']} failure(s) in 24h, last: {info['last_error_message']}")


def _historical_db_paths():
    return sorted(p for p in glob.glob("historical_*.db") if "consolidated" not in p)


def check_historical_databases():
    for db_path in _historical_db_paths():
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT utility, county, start_time FROM outage_events "
                  "WHERE peak_customers_out > customers_served")
        for r in c.fetchall():
            flag(f"{db_path} impossible value (out > served): {r}")

        c.execute("SELECT county, start_time, end_time FROM outage_events "
                  "WHERE end_time IS NOT NULL AND end_time < start_time")
        for r in c.fetchall():
            flag(f"{db_path} end before start: {r}")

        c.execute("SELECT utility, county, start_time, COUNT(*) FROM outage_events "
                  "GROUP BY utility, county, start_time HAVING COUNT(*) > 1")
        for r in c.fetchall():
            flag(f"{db_path} duplicate key (shouldn't be possible - unique index bypassed?): {r}")
        conn.close()


def check_cross_storm_anomalies():
    """
    The technique that caught 2 real bugs during the historical backfill
    (a truncated utility name, a mislabeled county): build a reference of
    every (utility, county) pair seen across all 17 storms combined, then
    surface any pair that appears in exactly one storm. Not proof of an
    error on its own - most small co-ops/municipals are genuinely
    storm-specific by geography - just a shortlist worth a second look
    without needing to already know Florida utility geography by heart.
    """
    pair_storms = {}
    for db_path in _historical_db_paths():
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT DISTINCT utility, county FROM outage_events")
        for utility, county in c.fetchall():
            pair_storms.setdefault((utility, county), set()).add(db_path)
        conn.close()

    rare_pairs = sorted(pair for pair, dbs in pair_storms.items() if len(dbs) == 1)
    print(f"  {len(rare_pairs)} (utility, county) pair(s) appear in only one storm "
          f"(informational only, not flagged as issues - most are genuinely real)")


def check_florida_counties_coverage():
    from historical_import import FLORIDA_COUNTIES
    present = set()
    for db_path in _historical_db_paths():
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT DISTINCT county FROM outage_events")
        present.update(r[0] for r in c.fetchall())
        conn.close()
    missing = FLORIDA_COUNTIES - present
    if missing:
        flag(f"counties with zero historical data across all 17 storms: {sorted(missing)}")


def check_consolidated_db_in_sync():
    consolidated_path = "historical_consolidated.db"
    if not os.path.exists(consolidated_path):
        flag("historical_consolidated.db does not exist - run "
             "apollo_shell/consolidate_historical.py")
        return

    conn = sqlite3.connect(consolidated_path)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM historical_outage_events")
    consolidated_count = c.fetchone()[0]
    conn.close()

    real_count = 0
    for db_path in _historical_db_paths():
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM outage_events")
        real_count += c.fetchone()[0]
        conn.close()

    if consolidated_count != real_count:
        flag(f"historical_consolidated.db has {consolidated_count} outage_events rows, "
             f"but the 17 source databases have {real_count} combined - out of sync, "
             f"re-run apollo_shell/consolidate_historical.py")


def main():
    print("=" * 70)
    print("Apollo Shell data integrity check")
    print("=" * 70)

    conn = sqlite3.connect("outages.db")
    _run_check("Live outages.db: impossible values", lambda: check_live_impossible_values(conn))
    _run_check("Live outages.db: bad durations", lambda: check_live_bad_durations(conn))
    _run_check("Live outages.db: weather_alerts NULL alert_id count", lambda: check_weather_alerts_nulls(conn))
    conn.close()

    _run_check("Pipeline health (last 24h)", check_pipeline_health)
    _run_check("Historical databases: impossible values, bad durations, duplicate keys", check_historical_databases)
    _run_check("Cross-storm (utility, county) anomalies", check_cross_storm_anomalies)
    _run_check("Florida county coverage across all 17 storms", check_florida_counties_coverage)
    _run_check("historical_consolidated.db in sync with source databases", check_consolidated_db_in_sync)

    print("=" * 70)
    if ISSUES:
        print(f"{len(ISSUES)} issue(s) found - see ISSUE lines above")
        sys.exit(1)
    else:
        print("All checks passed clean.")
        sys.exit(0)


if __name__ == "__main__":
    main()

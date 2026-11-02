"""
Tests for apollo_shell/close_stale_duke_events.py - the one-off cleanup
for Duke events left open by the 2026-07-20 API-key outage.
"""

import os
import sqlite3
import tempfile

import pytest

import close_stale_duke_events as cleanup
from database import OutageDatabase

LAST_GOOD = "2026-07-20T09:56:51"


def _incident(incident_id, county="Pinellas"):
    return {"incident_id": incident_id, "utility": "Duke Energy", "county": county, "customer_count": 5,
            "lat": 27.9, "lon": -82.7, "cause": "unplanned", "cause_category": "other"}


def _poll(db, ids, ts):
    """One successful Duke poll: raw rows, county rollup, lifecycle sync."""
    records = [_incident(i) for i in ids]
    conn = db.connect()
    for r in records:
        conn.execute(
            "INSERT INTO duke_incidents (fetched_at, incident_id, utility, customer_count, county) VALUES (?, ?, ?, ?, ?)",
            (ts, r["incident_id"], r["utility"], r["customer_count"], r["county"]),
        )
    conn.execute("INSERT INTO duke_counties (fetched_at, utility, county) VALUES (?, 'Duke Energy', 'Pinellas')", (ts,))
    conn.commit()
    db.sync_duke_incident_events(records, timestamp=ts)


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    for f in os.listdir(os.path.dirname(path)):
        if f.startswith(os.path.basename(path)):
            os.remove(os.path.join(os.path.dirname(path), f))


@pytest.fixture
def outage_history(db_path):
    """
    Mirrors the real timeline: D-OLD opens and closes normally before the
    outage; D1 is seen across two polls and D2 only on the last good one;
    then Duke starts returning 401s.
    """
    db = OutageDatabase(db_path)
    _poll(db, ["D-OLD", "D1"], "2026-07-20T09:26:51")
    _poll(db, ["D1", "D2"], LAST_GOOD)  # D-OLD closes normally here
    db.log_pipeline_error("duke", "401 Client Error: Unauthorized", timestamp="2026-07-20T12:47:00")
    db.log_pipeline_error("duke", "401 Client Error: Unauthorized", timestamp="2026-10-11T04:19:15")
    db.close()
    return db_path


def _events(path):
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT incident_id, start_time, end_time FROM duke_incident_events ORDER BY incident_id, start_time"
    ).fetchall()
    conn.close()
    return {r[0]: (r[1], r[2]) for r in rows}


class TestCleanup:
    def test_auto_detects_the_last_good_poll(self, outage_history):
        conn = sqlite3.connect(outage_history)
        assert cleanup.find_last_good_poll(conn) == LAST_GOOD
        conn.close()

    def test_dry_run_changes_nothing(self, outage_history):
        before = _events(outage_history)
        assert cleanup.main(["--db", outage_history]) == 0
        assert _events(outage_history) == before
        assert not [f for f in os.listdir(os.path.dirname(outage_history)) if ".bak-before-duke-cleanup-" in f
                    and f.startswith(os.path.basename(outage_history))]

    def test_apply_closes_stuck_events_at_last_seen_and_backs_up(self, outage_history):
        before = _events(outage_history)
        cleanup.main(["--db", outage_history, "--apply"])
        after = _events(outage_history)

        assert after["D1"][1] == LAST_GOOD   # seen twice: real nonzero duration
        assert after["D2"][1] == LAST_GOOD   # seen once: start == end, duration unknown
        assert after["D-OLD"] == before["D-OLD"]  # normal close left alone

        backups = [f for f in os.listdir(os.path.dirname(outage_history))
                   if f.startswith(os.path.basename(outage_history) + ".bak-before-duke-cleanup-")]
        assert len(backups) == 1

    def test_rerun_is_a_no_op(self, outage_history):
        cleanup.main(["--db", outage_history, "--apply"])
        once = _events(outage_history)
        cleanup.main(["--db", outage_history, "--apply"])
        assert _events(outage_history) == once

    def test_fixes_events_already_closed_by_recovery_but_not_new_ones(self, outage_history):
        # Duke reconnected before the cleanup ran: the first good poll
        # closed D1/D2 at the recovery time (~100 days) and opened D3,
        # which is genuinely live and must stay open.
        db = OutageDatabase(outage_history)
        _poll(db, ["D3"], "2026-11-03T08:00:00")
        db.close()
        assert _events(outage_history)["D1"][1] == "2026-11-03T08:00:00"

        cleanup.main(["--db", outage_history, "--apply"])
        after = _events(outage_history)

        assert after["D1"][1] == LAST_GOOD
        assert after["D2"][1] == LAST_GOOD
        assert after["D3"][1] is None

    def test_nothing_to_do_without_401_history(self, db_path):
        db = OutageDatabase(db_path)
        _poll(db, ["D1"], LAST_GOOD)
        db.close()
        before = _events(db_path)
        assert cleanup.main(["--db", db_path, "--apply"]) == 0
        assert _events(db_path) == before

"""
Tests for OutageDatabase's outage_events lifecycle tracking
(sync_outage_events) - the piece with the real replay bug found
2026-07-08. Each test gets its own temp file-based SQLite database (not
":memory:" - OutageDatabase opens a fresh connection per instantiation,
and separate connections to ":memory:" don't share state, which would
silently defeat these tests).
"""

import os
import tempfile

import pytest

from database import OutageDatabase


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # OutageDatabase.create_tables() expects to create it fresh
    yield path
    if os.path.exists(path):
        os.remove(path)


def _fpl_row(county, customers_out, customers_served=100_000):
    return {"county": county, "customers_out": customers_out, "customers_served": customers_served}


class TestSyncOutageEventsLifecycle:
    def test_opens_new_event_when_customers_out_positive(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")

        conn = db.connect()
        rows = conn.execute("SELECT county, end_time, peak_customers_out FROM outage_events").fetchall()
        db.close()

        assert len(rows) == 1
        assert rows[0]["county"] == "ALACHUA"
        assert rows[0]["end_time"] is None
        assert rows[0]["peak_customers_out"] == 50

    def test_bumps_peak_when_worse_reading_arrives(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 200)], timestamp="2026-01-01T00:15:00")

        conn = db.connect()
        rows = conn.execute("SELECT peak_customers_out FROM outage_events").fetchall()
        db.close()

        assert len(rows) == 1, "a worsening reading should update the same open event, not open a second one"
        assert rows[0]["peak_customers_out"] == 200

    def test_does_not_lower_peak_on_improving_reading(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 200)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:15:00")

        conn = db.connect()
        rows = conn.execute("SELECT peak_customers_out, end_time FROM outage_events").fetchall()
        db.close()

        assert rows[0]["peak_customers_out"] == 200, "peak should track the worst reading, not the latest one"
        assert rows[0]["end_time"] is None, "still nonzero, so still open"

    def test_closes_event_when_customers_out_returns_to_zero(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 0)], timestamp="2026-01-01T00:15:00")

        conn = db.connect()
        rows = conn.execute("SELECT end_time FROM outage_events").fetchall()
        db.close()

        assert rows[0]["end_time"] == "2026-01-01T00:15:00"

    def test_reopening_after_close_creates_a_second_event(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 0)], timestamp="2026-01-01T00:15:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 75)], timestamp="2026-01-01T01:00:00")

        conn = db.connect()
        rows = conn.execute("SELECT start_time, end_time FROM outage_events ORDER BY start_time").fetchall()
        db.close()

        assert len(rows) == 2
        assert rows[0]["end_time"] == "2026-01-01T00:15:00"
        assert rows[1]["start_time"] == "2026-01-01T01:00:00"
        assert rows[1]["end_time"] is None

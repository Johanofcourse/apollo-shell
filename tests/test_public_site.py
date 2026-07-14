"""
Tests for public_site.py - the public-facing page, built 2026-07-14 as
a genuinely separate Flask app from dashboard.py (own port, own
template folder, shares only the read-only apollo_shell/ data layer).

_statewide_snapshot() is the one piece of real new logic here (map
coloring + hero KPIs from a single pass over live data) - the rest of
the route reuses already-tested county_status.py/storm_history.py
functions directly.
"""
import os
import tempfile

import pytest

import public_site
from database import OutageDatabase


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _fpl_row(county, customers_out, customers_served=100_000):
    return {"county": county, "customers_out": customers_out, "customers_served": customers_served}


class TestStatewideSnapshot:
    def test_clean_database_is_all_clear(self, db_path):
        db = OutageDatabase(db_path)
        snapshot = public_site._statewide_snapshot(db)
        db.close()

        assert snapshot["counties_with_issue"] == 0
        assert snapshot["counties_clear"] == snapshot["total_counties"]
        assert snapshot["total_customers_affected"] == 0
        assert all(v == "clear" for v in snapshot["verdicts"].values())

    def test_one_real_outage_shows_up_in_the_snapshot(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:00:00")

        snapshot = public_site._statewide_snapshot(db)
        db.close()

        assert snapshot["verdicts"]["Alachua"] != "clear"
        assert snapshot["counties_with_issue"] == 1
        assert snapshot["total_customers_affected"] == 500

    def test_total_customers_sums_across_multiple_open_events(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 300)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 300)], timestamp="2026-01-01T00:00:00")
        db.log_multiple_outages("FPL", [_fpl_row("BAKER", 200)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("BAKER", 200)], timestamp="2026-01-01T00:00:00")

        snapshot = public_site._statewide_snapshot(db)
        db.close()

        assert snapshot["total_customers_affected"] == 500
        assert snapshot["counties_with_issue"] == 2

    def test_every_real_county_gets_a_verdict_entry(self, db_path):
        db = OutageDatabase(db_path)
        snapshot = public_site._statewide_snapshot(db)
        db.close()

        assert len(snapshot["verdicts"]) == 67
        assert "Miami-Dade" in snapshot["verdicts"]
        assert "DeSoto" in snapshot["verdicts"]


class TestIndexRoute:
    def test_homepage_loads(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert r.status_code == 200

    def test_county_query_param_renders_detail_section(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Calhoun")
        assert r.status_code == 200
        assert b"Calhoun County" in r.data

    def test_unselected_page_has_no_detail_section(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert b'id="detail"' not in r.data

    def test_county_with_no_history_data_does_not_error(self):
        # Combined-territory-only "counties" (e.g. a made-up name) and
        # real counties genuinely absent from some storms both need to
        # render cleanly, not 500.
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Nonexistent+County")
        assert r.status_code == 200

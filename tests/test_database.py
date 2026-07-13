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


class TestSyncJeaOutageEventsLifecycle:
    """
    Same lifecycle algorithm as sync_outage_events, applied to JEA's own
    dedicated jea_outage_events table (see fetch_jea_outages.py,
    2026-07-09) - mirrored tests for the same reason the FPL ones exist:
    cheap insurance that a near-identical implementation didn't
    introduce a near-identical bug.
    """
    def test_opens_new_event_when_customers_out_positive(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_jea_outage_events([_fpl_row("Duval", 50)], timestamp="2026-01-01T00:00:00")

        conn = db.connect()
        rows = conn.execute("SELECT county, end_time, peak_customers_out FROM jea_outage_events").fetchall()
        db.close()

        assert len(rows) == 1
        assert rows[0]["county"] == "Duval"
        assert rows[0]["end_time"] is None
        assert rows[0]["peak_customers_out"] == 50

    def test_closes_event_when_customers_out_returns_to_zero(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_jea_outage_events([_fpl_row("Duval", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_jea_outage_events([_fpl_row("Duval", 0)], timestamp="2026-01-01T00:15:00")

        conn = db.connect()
        rows = conn.execute("SELECT end_time FROM jea_outage_events").fetchall()
        db.close()

        assert rows[0]["end_time"] == "2026-01-01T00:15:00"

    def test_does_not_mix_with_fpl_outage_events_table(self, db_path):
        # The whole reason JEA got its own dedicated table instead of
        # reusing outage_events: get_open_events() has no utility filter,
        # so sharing FPL's table would silently mix JEA rows into the
        # "FPL" dashboard section.
        db = OutageDatabase(db_path)
        db.sync_outage_events("FPL", [_fpl_row("Duval", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_jea_outage_events([_fpl_row("Duval", 75)], timestamp="2026-01-01T00:00:00")

        fpl_open = db.get_open_events()
        jea_open = db.get_jea_open_events()
        db.close()

        assert len(fpl_open) == 1 and fpl_open[0]["peak_customers_out"] == 50
        assert len(jea_open) == 1 and jea_open[0]["peak_customers_out"] == 75


def _teco_incident(incident_id, county="Hillsborough", customer_count=10, reason="Tree down",
                    update_time="2026-01-01T00:00:00"):
    return {
        "incident_id": incident_id, "utility": "Tampa Electric Company",
        "status": "On our way", "status_category": "investigating",
        "reason": reason, "reason_category": "vegetation",
        "customer_count": customer_count, "lat": 27.9, "lon": -82.4, "county": county,
        "update_time": update_time, "estimated_restoration": "2026-01-01T06:00:00",
    }


def _duke_incident(incident_id, county="Orange", customer_count=20, cause="Equipment"):
    return {
        "incident_id": incident_id, "utility": "Duke Energy",
        "customer_count": customer_count, "lat": 28.5, "lon": -81.4, "county": county,
        "cause": cause, "cause_category": "equipment",
    }


def _tallahassee_incident(incident_id, county="Leon", customer_count=30, cause="Tree down",
                           region_name="East", status="Investigating"):
    return {
        "incident_id": incident_id, "utility": "City of Tallahassee",
        "customer_count": customer_count, "lat": 30.44, "lon": -84.28, "county": county,
        "region_name": region_name, "status": status, "status_category": "investigating",
        "cause": cause, "cause_category": "vegetation", "outage_type": "Unplanned",
        "reported_start_time": "2026-01-01T00:00:00+00:00", "estimated_restoration": None,
    }


class TestIncidentDetailLookup:
    """
    Tests for the incident-detail DB methods added 2026-07-12 for the
    /incident dashboard route - looking up one specific past outage,
    reached by clicking a "Recently Resolved" row rather than typing an
    id from memory.
    """

    def test_teco_incident_detail_has_one_episode_and_raw_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_teco_incidents([_teco_incident("T1")])
        db.sync_teco_incident_events([_teco_incident("T1")], timestamp="2026-01-01T00:00:00")
        db.sync_teco_incident_events([], timestamp="2026-01-01T02:00:00")  # disappears -> closes

        detail = db.get_teco_incident_detail("T1")
        db.close()

        assert len(detail["events"]) == 1
        assert detail["events"][0]["start_time"] == "2026-01-01T00:00:00"
        assert detail["events"][0]["end_time"] == "2026-01-01T02:00:00"
        assert len(detail["history"]) == 1
        assert detail["history"][0]["reason"] == "Tree down"

    def test_teco_incident_detail_empty_for_unknown_id(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_teco_incident_detail("NOT-A-REAL-ID")
        db.close()

        assert detail["events"] == []
        assert detail["history"] == []

    def test_duke_incident_detail_has_one_episode_and_raw_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_duke_incidents([_duke_incident("D1")])
        db.sync_duke_incident_events([_duke_incident("D1")], timestamp="2026-01-01T00:00:00")
        db.sync_duke_incident_events([], timestamp="2026-01-01T03:00:00")

        detail = db.get_duke_incident_detail("D1")
        db.close()

        assert len(detail["events"]) == 1
        assert detail["events"][0]["end_time"] == "2026-01-01T03:00:00"
        assert len(detail["history"]) == 1
        assert detail["history"][0]["cause"] == "Equipment"

    def test_tallahassee_incident_detail_has_one_episode_and_raw_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_tallahassee_incidents([_tallahassee_incident("555")])
        db.sync_tallahassee_incident_events([_tallahassee_incident("555")], timestamp="2026-01-01T00:00:00")
        db.sync_tallahassee_incident_events([], timestamp="2026-01-01T02:00:00")

        detail = db.get_tallahassee_incident_detail("555")
        db.close()

        assert len(detail["events"]) == 1
        assert detail["events"][0]["end_time"] == "2026-01-01T02:00:00"
        assert detail["events"][0]["region_name"] == "East"
        assert len(detail["history"]) == 1
        assert detail["history"][0]["cause"] == "Tree down"

    def test_fpl_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 0)], timestamp="2026-01-01T00:15:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 0)], timestamp="2026-01-01T00:15:00")

        detail = db.get_fpl_outage_detail("FPL", "ALACHUA", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 50
        assert detail["history"][-1]["customers_out"] == 0

    def test_fpl_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_fpl_outage_detail("FPL", "ALACHUA", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_jea_outage_detail_sums_across_zips_in_the_county(self, db_path):
        db = OutageDatabase(db_path)
        zip_records = [
            {"zip_code": "32225", "county": "Duval", "customers_out": 10, "customers_served": 1000,
             "percentage_out": 1.0, "etr": None, "etr_confidence": None, "n_out": 1},
            {"zip_code": "32226", "county": "Duval", "customers_out": 5, "customers_served": 500,
             "percentage_out": 1.0, "etr": None, "etr_confidence": None, "n_out": 1},
        ]
        db.log_jea_outages(zip_records, timestamp="2026-01-01T00:00:00")
        db.sync_jea_outage_events([{"county": "Duval", "customers_out": 15, "customers_served": 1500}],
                                   timestamp="2026-01-01T00:00:00")
        db.log_jea_outages(
            [{**z, "customers_out": 0} for z in zip_records], timestamp="2026-01-01T00:15:00"
        )
        db.sync_jea_outage_events([{"county": "Duval", "customers_out": 0, "customers_served": 1500}],
                                   timestamp="2026-01-01T00:15:00")

        detail = db.get_jea_outage_detail("Jacksonville (JEA)", "Duval", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        # Summed across both ZIPs in the county at the first timestamp
        assert detail["history"][0]["customers_out"] == 15
        assert detail["history"][0]["percentage_out"] == 1.0
        assert detail["history"][-1]["customers_out"] == 0

    def test_talquin_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_talquin_outages([_fpl_row("Gadsden", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_talquin_outage_events([_fpl_row("Gadsden", 50)], timestamp="2026-01-01T00:00:00")
        db.log_talquin_outages([_fpl_row("Gadsden", 0)], timestamp="2026-01-01T00:15:00")
        db.sync_talquin_outage_events([_fpl_row("Gadsden", 0)], timestamp="2026-01-01T00:15:00")

        detail = db.get_talquin_outage_detail("Talquin Electric Cooperative, Inc.", "Gadsden", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 50
        assert detail["history"][-1]["customers_out"] == 0

    def test_talquin_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_talquin_outage_detail("Talquin Electric Cooperative, Inc.", "Gadsden", "2026-01-01T00:00:00")
        db.close()

        assert detail is None


class TestOpenEventsCurrentVsPeak:
    """
    Tests for the current_customers_out/current_customer_count fields
    added 2026-07-12 to all four get_*_open_events() functions, after
    comparing a "peak" reading against poweroutage.us's live count for
    Palm Beach and realizing peak-of-episode and right-now are two
    genuinely different numbers that were only ever tracked as one.
    """

    def test_fpl_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 50)], timestamp="2026-01-01T00:00:00")
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:15:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:15:00")
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 10)], timestamp="2026-01-01T00:30:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 10)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 10

    def test_jea_open_event_current_is_summed_across_zips(self, db_path):
        db = OutageDatabase(db_path)
        db.log_jea_outages(
            [{"zip_code": "32225", "county": "Duval", "customers_out": 200, "customers_served": 1000,
              "percentage_out": 20.0, "etr": None, "etr_confidence": None, "n_out": 1}],
            timestamp="2026-01-01T00:00:00",
        )
        db.sync_jea_outage_events([{"county": "Duval", "customers_out": 200, "customers_served": 1000}],
                                   timestamp="2026-01-01T00:00:00")
        db.log_jea_outages(
            [{"zip_code": "32225", "county": "Duval", "customers_out": 10, "customers_served": 500,
              "percentage_out": 2.0, "etr": None, "etr_confidence": None, "n_out": 1},
             {"zip_code": "32226", "county": "Duval", "customers_out": 5, "customers_served": 500,
              "percentage_out": 1.0, "etr": None, "etr_confidence": None, "n_out": 1}],
            timestamp="2026-01-01T00:15:00",
        )
        db.sync_jea_outage_events([{"county": "Duval", "customers_out": 15, "customers_served": 1000}],
                                   timestamp="2026-01-01T00:15:00")

        open_events = db.get_jea_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 200
        assert open_events[0]["current_customers_out"] == 15

    def test_teco_open_event_reports_current_alongside_peak(self, db_path):
        # update_time varies per call - that's teco_incidents' real
        # dedup key (incident_id, update_time); reusing the same
        # update_time on every call would make log_teco_incidents()'s
        # INSERT OR IGNORE silently treat later calls as duplicates.
        db = OutageDatabase(db_path)
        db.log_teco_incidents([_teco_incident("T1", customer_count=10, update_time="2026-01-01T00:00:00")])
        db.sync_teco_incident_events([_teco_incident("T1", customer_count=10)], timestamp="2026-01-01T00:00:00")
        db.log_teco_incidents([_teco_incident("T1", customer_count=300, update_time="2026-01-01T00:15:00")])
        db.sync_teco_incident_events([_teco_incident("T1", customer_count=300)], timestamp="2026-01-01T00:15:00")
        db.log_teco_incidents([_teco_incident("T1", customer_count=25, update_time="2026-01-01T00:30:00")])
        db.sync_teco_incident_events([_teco_incident("T1", customer_count=25)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_teco_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customer_count"] == 300
        assert open_events[0]["current_customer_count"] == 25

    def test_duke_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_duke_incidents([_duke_incident("D1", customer_count=20)])
        db.sync_duke_incident_events([_duke_incident("D1", customer_count=20)], timestamp="2026-01-01T00:00:00")
        db.log_duke_incidents([_duke_incident("D1", customer_count=400)])
        db.sync_duke_incident_events([_duke_incident("D1", customer_count=400)], timestamp="2026-01-01T00:15:00")
        db.log_duke_incidents([_duke_incident("D1", customer_count=60)])
        db.sync_duke_incident_events([_duke_incident("D1", customer_count=60)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_duke_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customer_count"] == 400
        assert open_events[0]["current_customer_count"] == 60

    def test_tallahassee_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_tallahassee_incidents([_tallahassee_incident("T1", customer_count=15)])
        db.sync_tallahassee_incident_events([_tallahassee_incident("T1", customer_count=15)], timestamp="2026-01-01T00:00:00")
        db.log_tallahassee_incidents([_tallahassee_incident("T1", customer_count=200)])
        db.sync_tallahassee_incident_events([_tallahassee_incident("T1", customer_count=200)], timestamp="2026-01-01T00:15:00")
        db.log_tallahassee_incidents([_tallahassee_incident("T1", customer_count=40)])
        db.sync_tallahassee_incident_events([_tallahassee_incident("T1", customer_count=40)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_tallahassee_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customer_count"] == 200
        assert open_events[0]["current_customer_count"] == 40

    def test_talquin_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_talquin_outages([_fpl_row("Gadsden", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_talquin_outage_events([_fpl_row("Gadsden", 50)], timestamp="2026-01-01T00:00:00")
        db.log_talquin_outages([_fpl_row("Gadsden", 500)], timestamp="2026-01-01T00:15:00")
        db.sync_talquin_outage_events([_fpl_row("Gadsden", 500)], timestamp="2026-01-01T00:15:00")
        db.log_talquin_outages([_fpl_row("Gadsden", 10)], timestamp="2026-01-01T00:30:00")
        db.sync_talquin_outage_events([_fpl_row("Gadsden", 10)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_talquin_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 10

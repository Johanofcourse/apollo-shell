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
from datetime import datetime, timedelta, timezone

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


def _tallahassee_row(county="Leon", customers_out=30):
    return {"county": county, "customers_out": customers_out}


def _fpuc_incident(incident_id, county="Liberty", customer_count=56, substation="5", feeder="9882"):
    return {
        "incident_id": incident_id, "utility": "Florida Public Utilities Corporation",
        "customer_count": customer_count, "lat": 30.43, "lon": -84.95, "county": county,
        "substation": substation, "feeder": feeder,
        "reported_start_time": "2026-01-01T00:00:00", "estimated_restoration": None,
    }


def _lwbu_incident(incident_id, customer_count=2, streets_affected="PENNY LN",
                    cause="Material or equipment fault/failure", crew_assigned=False,
                    work_status="Crew in Route"):
    return {
        "incident_id": incident_id, "utility": "Lake Worth Beach Utilities",
        "customer_count": customer_count, "lat": 26.6, "lon": -80.1, "county": "Palm Beach",
        "cause": cause, "cause_category": "other", "crew_assigned": crew_assigned,
        "work_status": work_status, "streets_affected": streets_affected,
        "is_planned": False, "verified": True,
        "reported_start_time": "2026-01-01T00:00:00", "estimated_restoration": None,
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

    def test_tallahassee_outage_detail_returns_event_and_bounded_history(self, db_path):
        # Redesigned 2026-07-18 - Tallahassee moved from an incident-
        # level lookup (keyed by an unreliable "ticket" field, always
        # None in real data) to a county-rollup lookup like Talquin's,
        # keyed by (utility, county, start_time).
        db = OutageDatabase(db_path)
        db.log_tallahassee_outages([_tallahassee_row("Leon", 30)], timestamp="2026-01-01T00:00:00")
        db.sync_tallahassee_outage_events([_tallahassee_row("Leon", 30)], timestamp="2026-01-01T00:00:00")
        db.log_tallahassee_outages([_tallahassee_row("Leon", 0)], timestamp="2026-01-01T00:15:00")
        db.sync_tallahassee_outage_events([_tallahassee_row("Leon", 0)], timestamp="2026-01-01T00:15:00")

        detail = db.get_tallahassee_outage_detail("City of Tallahassee", "Leon", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 30
        assert detail["history"][-1]["customers_out"] == 0

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

    def test_preco_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_preco_outages([_fpl_row("Manatee", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_preco_outage_events([_fpl_row("Manatee", 50)], timestamp="2026-01-01T00:00:00")
        db.log_preco_outages([_fpl_row("Manatee", 0)], timestamp="2026-01-01T00:15:00")
        db.sync_preco_outage_events([_fpl_row("Manatee", 0)], timestamp="2026-01-01T00:15:00")

        detail = db.get_preco_outage_detail("Peace River Electric Cooperative, Inc.", "Manatee", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 50
        assert detail["history"][-1]["customers_out"] == 0

    def test_preco_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_preco_outage_detail("Peace River Electric Cooperative, Inc.", "Manatee", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_fkec_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fkec_outages([_fpl_row("Monroe", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_fkec_outage_events([_fpl_row("Monroe", 50)], timestamp="2026-01-01T00:00:00")
        db.log_fkec_outages([_fpl_row("Monroe", 0)], timestamp="2026-01-01T00:15:00")
        db.sync_fkec_outage_events([_fpl_row("Monroe", 0)], timestamp="2026-01-01T00:15:00")

        detail = db.get_fkec_outage_detail("Florida Keys Electric Cooperative, Inc.", "Monroe", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 50
        assert detail["history"][-1]["customers_out"] == 0

    def test_fkec_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_fkec_outage_detail("Florida Keys Electric Cooperative, Inc.", "Monroe", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_tcec_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)"
        db.log_tcec_outages([_fpl_row(territory, 50, 20103)], timestamp="2026-01-01T00:00:00")
        db.sync_tcec_outage_events([_fpl_row(territory, 50, 20103)], timestamp="2026-01-01T00:00:00")
        db.log_tcec_outages([_fpl_row(territory, 0, 20103)], timestamp="2026-01-01T00:15:00")
        db.sync_tcec_outage_events([_fpl_row(territory, 0, 20103)], timestamp="2026-01-01T00:15:00")

        detail = db.get_tcec_outage_detail("Tri-County Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 50
        assert detail["history"][-1]["customers_out"] == 0

    def test_tcec_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)"
        detail = db.get_tcec_outage_detail("Tri-County Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_erec_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Escambia/Santa Rosa"
        db.log_erec_outages([_fpl_row(territory, 7, 13663)], timestamp="2026-01-01T00:00:00")
        db.sync_erec_outage_events([_fpl_row(territory, 7, 13663)], timestamp="2026-01-01T00:00:00")
        db.log_erec_outages([_fpl_row(territory, 0, 13663)], timestamp="2026-01-01T00:15:00")
        db.sync_erec_outage_events([_fpl_row(territory, 0, 13663)], timestamp="2026-01-01T00:15:00")

        detail = db.get_erec_outage_detail("Escambia River Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 7
        assert detail["history"][-1]["customers_out"] == 0

    def test_erec_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Escambia/Santa Rosa"
        detail = db.get_erec_outage_detail("Escambia River Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_chelco_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Santa Rosa/Okaloosa/Walton/Holmes"
        db.log_chelco_outages([_fpl_row(territory, 7, 74996)], timestamp="2026-01-01T00:00:00")
        db.sync_chelco_outage_events([_fpl_row(territory, 7, 74996)], timestamp="2026-01-01T00:00:00")
        db.log_chelco_outages([_fpl_row(territory, 0, 74996)], timestamp="2026-01-01T00:15:00")
        db.sync_chelco_outage_events([_fpl_row(territory, 0, 74996)], timestamp="2026-01-01T00:15:00")

        detail = db.get_chelco_outage_detail("Choctawhatchee Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 7
        assert detail["history"][-1]["customers_out"] == 0

    def test_chelco_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Santa Rosa/Okaloosa/Walton/Holmes"
        detail = db.get_chelco_outage_detail("Choctawhatchee Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_gcec_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"
        db.log_gcec_outages([_fpl_row(territory, 7, 23206)], timestamp="2026-01-01T00:00:00")
        db.sync_gcec_outage_events([_fpl_row(territory, 7, 23206)], timestamp="2026-01-01T00:00:00")
        db.log_gcec_outages([_fpl_row(territory, 0, 23206)], timestamp="2026-01-01T00:15:00")
        db.sync_gcec_outage_events([_fpl_row(territory, 0, 23206)], timestamp="2026-01-01T00:15:00")

        detail = db.get_gcec_outage_detail("Gulf Coast Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 7
        assert detail["history"][-1]["customers_out"] == 0

    def test_gcec_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"
        detail = db.get_gcec_outage_detail("Gulf Coast Electric Cooperative, Inc.", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_lwbu_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_lwbu_outages([_fpl_row("Palm Beach", 2, 28232)], timestamp="2026-01-01T00:00:00")
        db.sync_lwbu_outage_events([_fpl_row("Palm Beach", 2, 28232)], timestamp="2026-01-01T00:00:00")
        db.log_lwbu_outages([_fpl_row("Palm Beach", 0, 28232)], timestamp="2026-01-01T00:15:00")
        db.sync_lwbu_outage_events([_fpl_row("Palm Beach", 0, 28232)], timestamp="2026-01-01T00:15:00")

        detail = db.get_lwbu_outage_detail("Lake Worth Beach Utilities", "Palm Beach", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 2
        assert detail["history"][-1]["customers_out"] == 0

    def test_lwbu_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_lwbu_outage_detail("Lake Worth Beach Utilities", "Palm Beach", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_ouc_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_ouc_outages([_fpl_row("Orange", 500, 291868)], timestamp="2026-01-01T00:00:00")
        db.sync_ouc_outage_events([_fpl_row("Orange", 500, 291868)], timestamp="2026-01-01T00:00:00")
        db.log_ouc_outages([_fpl_row("Orange", 0, 291868)], timestamp="2026-01-01T00:15:00")
        db.sync_ouc_outage_events([_fpl_row("Orange", 0, 291868)], timestamp="2026-01-01T00:15:00")

        detail = db.get_ouc_outage_detail("Orlando Utilities Commission", "Orange", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 500
        assert detail["history"][-1]["customers_out"] == 0

    def test_ouc_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_ouc_outage_detail("Orlando Utilities Commission", "Orange", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_lcec_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_lcec_outages([_fpl_row("Lee", 4, 227335)], timestamp="2026-01-01T00:00:00")
        db.sync_lcec_outage_events([_fpl_row("Lee", 4, 227335)], timestamp="2026-01-01T00:00:00")
        db.log_lcec_outages([_fpl_row("Lee", 0, 227335)], timestamp="2026-01-01T00:15:00")
        db.sync_lcec_outage_events([_fpl_row("Lee", 0, 227335)], timestamp="2026-01-01T00:15:00")

        detail = db.get_lcec_outage_detail("Lee County Electric Cooperative", "Lee", "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 4
        assert detail["history"][-1]["customers_out"] == 0

    def test_lcec_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_lcec_outage_detail("Lee County Electric Cooperative", "Lee", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_lwbu_incident_detail_has_one_episode_and_raw_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_lwbu_incidents([_lwbu_incident("2026-07-14-0099")])
        db.sync_lwbu_incident_events([_lwbu_incident("2026-07-14-0099")], timestamp="2026-01-01T00:00:00")
        db.sync_lwbu_incident_events([], timestamp="2026-01-01T03:00:00")

        detail = db.get_lwbu_incident_detail("2026-07-14-0099")
        db.close()

        assert len(detail["events"]) == 1
        assert detail["events"][0]["end_time"] == "2026-01-01T03:00:00"
        assert detail["events"][0]["county"] == "Palm Beach"
        assert detail["events"][0]["streets_affected"] == "PENNY LN"
        assert len(detail["history"]) == 1
        assert detail["history"][0]["work_status"] == "Crew in Route"

    def test_lwbu_incident_detail_empty_for_unknown_id(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_lwbu_incident_detail("NOT-A-REAL-ID")
        db.close()

        assert detail["events"] == []
        assert detail["history"] == []

    def test_fpuc_outage_detail_returns_event_and_bounded_history(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Multiple Counties (NW FL & Nassau)"
        db.log_fpuc_outages([_fpl_row(territory, 50, 30668)], timestamp="2026-01-01T00:00:00")
        db.sync_fpuc_outage_events([_fpl_row(territory, 50, 30668)], timestamp="2026-01-01T00:00:00")
        db.log_fpuc_outages([_fpl_row(territory, 0, 30668)], timestamp="2026-01-01T00:15:00")
        db.sync_fpuc_outage_events([_fpl_row(territory, 0, 30668)], timestamp="2026-01-01T00:15:00")

        detail = db.get_fpuc_outage_detail("Florida Public Utilities Corporation", territory, "2026-01-01T00:00:00")
        db.close()

        assert detail is not None
        assert detail["event"]["end_time"] == "2026-01-01T00:15:00"
        assert len(detail["history"]) == 2
        assert detail["history"][0]["customers_out"] == 50
        assert detail["history"][-1]["customers_out"] == 0

    def test_fpuc_outage_detail_none_for_unknown_occurrence(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_fpuc_outage_detail("Florida Public Utilities Corporation", "Multiple Counties (NW FL & Nassau)", "2026-01-01T00:00:00")
        db.close()

        assert detail is None

    def test_fpuc_incident_detail_has_one_episode_and_raw_history(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpuc_incidents([_fpuc_incident("D1")])
        db.sync_fpuc_incident_events([_fpuc_incident("D1")], timestamp="2026-01-01T00:00:00")
        db.sync_fpuc_incident_events([], timestamp="2026-01-01T03:00:00")

        detail = db.get_fpuc_incident_detail("D1")
        db.close()

        assert len(detail["events"]) == 1
        assert detail["events"][0]["end_time"] == "2026-01-01T03:00:00"
        assert detail["events"][0]["county"] == "Liberty"
        assert detail["events"][0]["substation"] == "5"
        assert len(detail["history"]) == 1
        assert detail["history"][0]["feeder"] == "9882"

    def test_fpuc_incident_detail_empty_for_unknown_id(self, db_path):
        db = OutageDatabase(db_path)
        detail = db.get_fpuc_incident_detail("NOT-A-REAL-ID")
        db.close()

        assert detail["events"] == []
        assert detail["history"] == []


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

    def test_teco_transient_geocode_failure_does_not_erase_known_county(self, db_path):
        # Real bug, 2026-07-17: TECO/Duke's county comes from a live
        # reverse-geocode call (fetch_teco_outages.lookup_county) that can
        # fail transiently on any given poll. sync_teco_incident_events
        # used to unconditionally overwrite county with that poll's fresh
        # (possibly-None) result, so one bad lookup permanently downgraded
        # an already-known-good county back to None for a still-open
        # incident. A later successful lookup should still fill in county
        # if it was never known yet.
        db = OutageDatabase(db_path)
        db.sync_teco_incident_events([_teco_incident("T1", county="Hillsborough")], timestamp="2026-01-01T00:00:00")
        db.sync_teco_incident_events([_teco_incident("T1", county=None)], timestamp="2026-01-01T00:15:00")

        open_events = db.get_teco_open_events()
        db.close()

        assert open_events[0]["county"] == "Hillsborough"

    def test_teco_county_fills_in_once_a_later_lookup_succeeds(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_teco_incident_events([_teco_incident("T1", county=None)], timestamp="2026-01-01T00:00:00")
        db.sync_teco_incident_events([_teco_incident("T1", county="Hillsborough")], timestamp="2026-01-01T00:15:00")

        open_events = db.get_teco_open_events()
        db.close()

        assert open_events[0]["county"] == "Hillsborough"

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

    def test_duke_transient_geocode_failure_does_not_erase_known_county(self, db_path):
        # Same real bug as TECO's equivalent test above - this is the exact
        # scenario that crashed the public site on 2026-07-17 (a Duke
        # incident's county flipped from a real value to None after a later
        # poll's reverse-geocode call failed).
        db = OutageDatabase(db_path)
        db.sync_duke_incident_events([_duke_incident("D1", county="Orange")], timestamp="2026-01-01T00:00:00")
        db.sync_duke_incident_events([_duke_incident("D1", county=None)], timestamp="2026-01-01T00:15:00")

        open_events = db.get_duke_open_events()
        db.close()

        assert open_events[0]["county"] == "Orange"

    def test_duke_county_fills_in_once_a_later_lookup_succeeds(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_duke_incident_events([_duke_incident("D1", county=None)], timestamp="2026-01-01T00:00:00")
        db.sync_duke_incident_events([_duke_incident("D1", county="Orange")], timestamp="2026-01-01T00:15:00")

        open_events = db.get_duke_open_events()
        db.close()

        assert open_events[0]["county"] == "Orange"

    def test_fpuc_transient_geocode_failure_does_not_erase_known_county(self, db_path):
        # Same real bug as Duke's/TECO's equivalent test above, found in
        # this project's own 2026-07-18 audit sweep: FPUC's county is
        # reverse-geocoded per-record too (see
        # fetch_fpuc_outages.markers_to_incidents), but
        # sync_fpuc_incident_events never got the overwrite guard Duke
        # and TECO did, so it was still exposed to the same failure mode.
        db = OutageDatabase(db_path)
        db.sync_fpuc_incident_events([_fpuc_incident("F1", county="Liberty")], timestamp="2026-01-01T00:00:00")
        db.sync_fpuc_incident_events([_fpuc_incident("F1", county=None)], timestamp="2026-01-01T00:15:00")

        open_incidents = db.get_fpuc_open_incidents()
        db.close()

        assert open_incidents[0]["county"] == "Liberty"

    def test_fpuc_county_fills_in_once_a_later_lookup_succeeds(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_fpuc_incident_events([_fpuc_incident("F1", county=None)], timestamp="2026-01-01T00:00:00")
        db.sync_fpuc_incident_events([_fpuc_incident("F1", county="Liberty")], timestamp="2026-01-01T00:15:00")

        open_incidents = db.get_fpuc_open_incidents()
        db.close()

        assert open_incidents[0]["county"] == "Liberty"

    def test_tallahassee_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_tallahassee_outages([_tallahassee_row("Leon", 15)], timestamp="2026-01-01T00:00:00")
        db.sync_tallahassee_outage_events([_tallahassee_row("Leon", 15)], timestamp="2026-01-01T00:00:00")
        db.log_tallahassee_outages([_tallahassee_row("Leon", 200)], timestamp="2026-01-01T00:15:00")
        db.sync_tallahassee_outage_events([_tallahassee_row("Leon", 200)], timestamp="2026-01-01T00:15:00")
        db.log_tallahassee_outages([_tallahassee_row("Leon", 40)], timestamp="2026-01-01T00:30:00")
        db.sync_tallahassee_outage_events([_tallahassee_row("Leon", 40)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_tallahassee_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 200
        assert open_events[0]["current_customers_out"] == 40

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

    def test_preco_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_preco_outages([_fpl_row("Manatee", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_preco_outage_events([_fpl_row("Manatee", 50)], timestamp="2026-01-01T00:00:00")
        db.log_preco_outages([_fpl_row("Manatee", 500)], timestamp="2026-01-01T00:15:00")
        db.sync_preco_outage_events([_fpl_row("Manatee", 500)], timestamp="2026-01-01T00:15:00")
        db.log_preco_outages([_fpl_row("Manatee", 10)], timestamp="2026-01-01T00:30:00")
        db.sync_preco_outage_events([_fpl_row("Manatee", 10)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_preco_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 10

    def test_fkec_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fkec_outages([_fpl_row("Monroe", 50)], timestamp="2026-01-01T00:00:00")
        db.sync_fkec_outage_events([_fpl_row("Monroe", 50)], timestamp="2026-01-01T00:00:00")
        db.log_fkec_outages([_fpl_row("Monroe", 500)], timestamp="2026-01-01T00:15:00")
        db.sync_fkec_outage_events([_fpl_row("Monroe", 500)], timestamp="2026-01-01T00:15:00")
        db.log_fkec_outages([_fpl_row("Monroe", 10)], timestamp="2026-01-01T00:30:00")
        db.sync_fkec_outage_events([_fpl_row("Monroe", 10)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_fkec_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 10

    def test_tcec_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)"
        db.log_tcec_outages([_fpl_row(territory, 50, 20103)], timestamp="2026-01-01T00:00:00")
        db.sync_tcec_outage_events([_fpl_row(territory, 50, 20103)], timestamp="2026-01-01T00:00:00")
        db.log_tcec_outages([_fpl_row(territory, 500, 20103)], timestamp="2026-01-01T00:15:00")
        db.sync_tcec_outage_events([_fpl_row(territory, 500, 20103)], timestamp="2026-01-01T00:15:00")
        db.log_tcec_outages([_fpl_row(territory, 10, 20103)], timestamp="2026-01-01T00:30:00")
        db.sync_tcec_outage_events([_fpl_row(territory, 10, 20103)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_tcec_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 10

    def test_erec_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Escambia/Santa Rosa"
        db.log_erec_outages([_fpl_row(territory, 7, 13663)], timestamp="2026-01-01T00:00:00")
        db.sync_erec_outage_events([_fpl_row(territory, 7, 13663)], timestamp="2026-01-01T00:00:00")
        db.log_erec_outages([_fpl_row(territory, 50, 13663)], timestamp="2026-01-01T00:15:00")
        db.sync_erec_outage_events([_fpl_row(territory, 50, 13663)], timestamp="2026-01-01T00:15:00")
        db.log_erec_outages([_fpl_row(territory, 3, 13663)], timestamp="2026-01-01T00:30:00")
        db.sync_erec_outage_events([_fpl_row(territory, 3, 13663)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_erec_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 50
        assert open_events[0]["current_customers_out"] == 3

    def test_chelco_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Santa Rosa/Okaloosa/Walton/Holmes"
        db.log_chelco_outages([_fpl_row(territory, 7, 74996)], timestamp="2026-01-01T00:00:00")
        db.sync_chelco_outage_events([_fpl_row(territory, 7, 74996)], timestamp="2026-01-01T00:00:00")
        db.log_chelco_outages([_fpl_row(territory, 50, 74996)], timestamp="2026-01-01T00:15:00")
        db.sync_chelco_outage_events([_fpl_row(territory, 50, 74996)], timestamp="2026-01-01T00:15:00")
        db.log_chelco_outages([_fpl_row(territory, 3, 74996)], timestamp="2026-01-01T00:30:00")
        db.sync_chelco_outage_events([_fpl_row(territory, 3, 74996)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_chelco_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 50
        assert open_events[0]["current_customers_out"] == 3

    def test_gcec_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"
        db.log_gcec_outages([_fpl_row(territory, 7, 23206)], timestamp="2026-01-01T00:00:00")
        db.sync_gcec_outage_events([_fpl_row(territory, 7, 23206)], timestamp="2026-01-01T00:00:00")
        db.log_gcec_outages([_fpl_row(territory, 50, 23206)], timestamp="2026-01-01T00:15:00")
        db.sync_gcec_outage_events([_fpl_row(territory, 50, 23206)], timestamp="2026-01-01T00:15:00")
        db.log_gcec_outages([_fpl_row(territory, 3, 23206)], timestamp="2026-01-01T00:30:00")
        db.sync_gcec_outage_events([_fpl_row(territory, 3, 23206)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_gcec_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 50
        assert open_events[0]["current_customers_out"] == 3

    def test_fpuc_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Multiple Counties (NW FL & Nassau)"
        db.log_fpuc_outages([_fpl_row(territory, 50, 30668)], timestamp="2026-01-01T00:00:00")
        db.sync_fpuc_outage_events([_fpl_row(territory, 50, 30668)], timestamp="2026-01-01T00:00:00")
        db.log_fpuc_outages([_fpl_row(territory, 500, 30668)], timestamp="2026-01-01T00:15:00")
        db.sync_fpuc_outage_events([_fpl_row(territory, 500, 30668)], timestamp="2026-01-01T00:15:00")
        db.log_fpuc_outages([_fpl_row(territory, 10, 30668)], timestamp="2026-01-01T00:30:00")
        db.sync_fpuc_outage_events([_fpl_row(territory, 10, 30668)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_fpuc_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 10

    def test_fpuc_incident_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpuc_incidents([_fpuc_incident("D1", customer_count=20)])
        db.sync_fpuc_incident_events([_fpuc_incident("D1", customer_count=20)], timestamp="2026-01-01T00:00:00")
        db.log_fpuc_incidents([_fpuc_incident("D1", customer_count=400)])
        db.sync_fpuc_incident_events([_fpuc_incident("D1", customer_count=400)], timestamp="2026-01-01T00:15:00")
        db.log_fpuc_incidents([_fpuc_incident("D1", customer_count=60)])
        db.sync_fpuc_incident_events([_fpuc_incident("D1", customer_count=60)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_fpuc_open_incidents()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customer_count"] == 400
        assert open_events[0]["current_customer_count"] == 60

    def test_lwbu_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_lwbu_outages([_fpl_row("Palm Beach", 2, 28232)], timestamp="2026-01-01T00:00:00")
        db.sync_lwbu_outage_events([_fpl_row("Palm Beach", 2, 28232)], timestamp="2026-01-01T00:00:00")
        db.log_lwbu_outages([_fpl_row("Palm Beach", 50, 28232)], timestamp="2026-01-01T00:15:00")
        db.sync_lwbu_outage_events([_fpl_row("Palm Beach", 50, 28232)], timestamp="2026-01-01T00:15:00")
        db.log_lwbu_outages([_fpl_row("Palm Beach", 3, 28232)], timestamp="2026-01-01T00:30:00")
        db.sync_lwbu_outage_events([_fpl_row("Palm Beach", 3, 28232)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_lwbu_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 50
        assert open_events[0]["current_customers_out"] == 3

    def test_lwbu_open_incident_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_lwbu_incidents([_lwbu_incident("I1", customer_count=2)])
        db.sync_lwbu_incident_events([_lwbu_incident("I1", customer_count=2)], timestamp="2026-01-01T00:00:00")
        db.log_lwbu_incidents([_lwbu_incident("I1", customer_count=40)])
        db.sync_lwbu_incident_events([_lwbu_incident("I1", customer_count=40)], timestamp="2026-01-01T00:15:00")
        db.log_lwbu_incidents([_lwbu_incident("I1", customer_count=6)])
        db.sync_lwbu_incident_events([_lwbu_incident("I1", customer_count=6)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_lwbu_open_incidents()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customer_count"] == 40
        assert open_events[0]["current_customer_count"] == 6

    def test_ouc_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_ouc_outages([_fpl_row("Orange", 500, 291868)], timestamp="2026-01-01T00:00:00")
        db.sync_ouc_outage_events([_fpl_row("Orange", 500, 291868)], timestamp="2026-01-01T00:00:00")
        db.log_ouc_outages([_fpl_row("Orange", 5000, 291868)], timestamp="2026-01-01T00:15:00")
        db.sync_ouc_outage_events([_fpl_row("Orange", 5000, 291868)], timestamp="2026-01-01T00:15:00")
        db.log_ouc_outages([_fpl_row("Orange", 300, 291868)], timestamp="2026-01-01T00:30:00")
        db.sync_ouc_outage_events([_fpl_row("Orange", 300, 291868)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_ouc_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 5000
        assert open_events[0]["current_customers_out"] == 300

    def test_lcec_open_event_reports_current_alongside_peak(self, db_path):
        db = OutageDatabase(db_path)
        db.log_lcec_outages([_fpl_row("Lee", 4, 227335)], timestamp="2026-01-01T00:00:00")
        db.sync_lcec_outage_events([_fpl_row("Lee", 4, 227335)], timestamp="2026-01-01T00:00:00")
        db.log_lcec_outages([_fpl_row("Lee", 500, 227335)], timestamp="2026-01-01T00:15:00")
        db.sync_lcec_outage_events([_fpl_row("Lee", 500, 227335)], timestamp="2026-01-01T00:15:00")
        db.log_lcec_outages([_fpl_row("Lee", 50, 227335)], timestamp="2026-01-01T00:30:00")
        db.sync_lcec_outage_events([_fpl_row("Lee", 50, 227335)], timestamp="2026-01-01T00:30:00")

        open_events = db.get_lcec_open_events()
        db.close()

        assert len(open_events) == 1
        assert open_events[0]["peak_customers_out"] == 500
        assert open_events[0]["current_customers_out"] == 50


class TestPipelineErrorHistory:
    """
    get_pipeline_error_history() - the drill-down behind
    get_pipeline_health()'s "count + last message" summary, added
    2026-07-13 so the raw failure history is actually browsable, not
    just summarized.
    """

    def test_returns_most_recent_first(self, db_path):
        db = OutageDatabase(db_path)
        db.log_pipeline_error("fpl", "first failure", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("fpl", "second failure", timestamp="2026-01-01T00:15:00")
        history = db.get_pipeline_error_history(source="fpl")
        db.close()

        assert len(history) == 2
        assert history[0]["error_message"] == "second failure"
        assert history[1]["error_message"] == "first failure"

    def test_filters_by_source(self, db_path):
        db = OutageDatabase(db_path)
        db.log_pipeline_error("fpl", "fpl failure", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("preco", "preco failure", timestamp="2026-01-01T00:00:00")
        history = db.get_pipeline_error_history(source="preco")
        db.close()

        assert len(history) == 1
        assert history[0]["source"] == "preco"

    def test_no_source_returns_all_combined(self, db_path):
        db = OutageDatabase(db_path)
        db.log_pipeline_error("fpl", "fpl failure", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("preco", "preco failure", timestamp="2026-01-01T00:00:01")
        history = db.get_pipeline_error_history()
        db.close()

        assert len(history) == 2

    def test_respects_limit(self, db_path):
        db = OutageDatabase(db_path)
        for i in range(5):
            db.log_pipeline_error("fpl", f"failure {i}", timestamp=f"2026-01-01T00:0{i}:00")
        history = db.get_pipeline_error_history(source="fpl", limit=2)
        db.close()

        assert len(history) == 2

    def test_empty_when_no_errors_logged(self, db_path):
        db = OutageDatabase(db_path)
        history = db.get_pipeline_error_history(source="fpl")
        db.close()

        assert history == []


def _weather_alert_row(event_type, areas, effective, expires, alert_id=None):
    return {
        "id": alert_id or f"test-{effective}-{areas}",
        "event": event_type,
        "severity": "Severe",
        "urgency": "Expected",
        "areas": areas,
        "effective": effective,
        "expires": expires,
        "headline": "test",
        "description": "test",
    }


class TestGetActiveWeatherAlerts:
    """
    get_active_weather_alerts() - added 2026-07-14 for the /county page,
    which needs "what's active right now for this county" across any
    alert type, not just heat (get_heat_advisory_summary() already
    covers that narrower heat-only, current-month case).
    """

    def test_currently_active_alert_is_returned(self, db_path):
        now = datetime.now(timezone.utc)
        db = OutageDatabase(db_path)
        db.log_weather_alerts([_weather_alert_row(
            "Flood Advisory", "Duval",
            (now - timedelta(hours=1)).isoformat(),
            (now + timedelta(hours=1)).isoformat(),
        )])
        active = db.get_active_weather_alerts()
        db.close()

        assert len(active) == 1
        assert active[0]["event_type"] == "Flood Advisory"

    def test_expired_alert_is_not_returned(self, db_path):
        now = datetime.now(timezone.utc)
        db = OutageDatabase(db_path)
        db.log_weather_alerts([_weather_alert_row(
            "Flood Advisory", "Duval",
            (now - timedelta(hours=3)).isoformat(),
            (now - timedelta(hours=1)).isoformat(),
        )])
        active = db.get_active_weather_alerts()
        db.close()

        assert active == []

    def test_future_alert_is_not_returned(self, db_path):
        now = datetime.now(timezone.utc)
        db = OutageDatabase(db_path)
        db.log_weather_alerts([_weather_alert_row(
            "Flood Advisory", "Duval",
            (now + timedelta(hours=1)).isoformat(),
            (now + timedelta(hours=3)).isoformat(),
        )])
        active = db.get_active_weather_alerts()
        db.close()

        assert active == []

    def test_missing_effective_or_expires_excluded(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "no-window", "event": "Special Weather Statement",
            "severity": None, "urgency": None, "areas": "Duval",
            "effective": None, "expires": None,
            "headline": "test", "description": "test",
        }])
        active = db.get_active_weather_alerts()
        db.close()

        assert active == []

    def test_no_alerts_logged_returns_empty(self, db_path):
        db = OutageDatabase(db_path)
        active = db.get_active_weather_alerts()
        db.close()

        assert active == []


class TestHistoricalConfidenceTallyStorage:
    """
    store_historical_confidence_tally()/get_historical_confidence_tally() -
    added 2026-07-14 so the public page can read a precomputed value
    instead of re-running the real, expensive nested-loop correlation
    query (county_status.historical_confidence_tally(), ~44s on real
    data) on every single page view.
    """

    def test_round_trip(self, db_path):
        db = OutageDatabase(db_path)
        db.store_historical_confidence_tally({
            "Alachua": {"high": 2, "medium": 1, "low": 0},
            "Duval": {"high": 0, "medium": 3, "low": 5},
        })
        result = db.get_historical_confidence_tally()
        db.close()

        assert result == {
            "Alachua": {"high": 2, "medium": 1, "low": 0},
            "Duval": {"high": 0, "medium": 3, "low": 5},
        }

    def test_empty_table_before_first_compute_returns_empty_dict(self, db_path):
        db = OutageDatabase(db_path)
        result = db.get_historical_confidence_tally()
        db.close()

        assert result == {}

    def test_recompute_fully_replaces_previous_result(self, db_path):
        # A county with no more correlation history at all should
        # disappear, not linger with a stale nonzero count from an
        # earlier cycle.
        db = OutageDatabase(db_path)
        db.store_historical_confidence_tally({"Alachua": {"high": 2, "medium": 0, "low": 0}})
        db.store_historical_confidence_tally({"Duval": {"high": 0, "medium": 1, "low": 0}})
        result = db.get_historical_confidence_tally()
        db.close()

        assert result == {"Duval": {"high": 0, "medium": 1, "low": 0}}

    def test_missing_tier_keys_default_to_zero(self, db_path):
        db = OutageDatabase(db_path)
        db.store_historical_confidence_tally({"Alachua": {"high": 3}})
        result = db.get_historical_confidence_tally()
        db.close()

        assert result == {"Alachua": {"high": 3, "medium": 0, "low": 0}}

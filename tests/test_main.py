"""
Tests for main.py's run_X_cycle() functions - specifically the
2026-07-13 fix closing a real blind spot in the pipeline-health system:
every fetch_X_outages() module (except this fix) catches its own
RequestException internally and returns None/empty, so main.py's
wrapping try/except (the thing that actually calls
OutageDatabase.log_pipeline_error()) never fired for real network
failures - only for failures happening outside the fetch call itself
(e.g. a database write error). Confirmed live: Talquin and PRECO failed
every poll cycle for 20+ minutes while the dashboard's health strip
still showed both as "healthy."

The fix: for county-rollup sources that report on every serviced
county/ZIP every cycle in steady state (FPL, JEA, Talquin, PRECO,
FPUC's combined view), an empty result is itself a reliable signal of a
real fetch failure, EXCEPT when the source's API URL is legitimately
unconfigured (Talquin/PRECO/FPUC only - FPL/JEA are always-required).
These tests exercise that distinction with a temp file-based database
(not ":memory:" - see tests/test_database.py's db_path fixture for why)
so a real db round-trip can be checked, not just the raise/no-raise
behavior in isolation.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apollo_shell"))

from database import OutageDatabase
import main


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def db(db_path):
    database = OutageDatabase(db_path)
    yield database
    database.close()


class TestRunOutageCycleFplFailureVisibility:
    def test_raises_when_combined_fpl_result_is_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_combined_fpl_records", lambda: [])
        with pytest.raises(RuntimeError):
            main.run_outage_cycle(db)

    def test_does_not_raise_and_saves_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_combined_fpl_records", lambda: [
            {"county": "Alachua", "customers_out": 5, "customers_served": 1000}
        ])
        main.run_outage_cycle(db)  # should not raise
        open_events = db.get_open_events()
        assert len(open_events) == 1
        assert open_events[0]["county"] == "Alachua"


class TestRunJeaCycleFailureVisibility:
    def test_raises_when_zip_records_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_jea_summary", lambda: ([], []))
        with pytest.raises(RuntimeError):
            main.run_jea_cycle(db)

    def test_does_not_raise_when_zip_records_present(self, db, monkeypatch):
        zip_records = [{"zip_code": "32202", "county": "Duval", "customers_out": 5,
                         "customers_served": 1000, "percentage_out": 0.5, "etr": None,
                         "etr_confidence": None, "n_out": 1}]
        county_rollup = [{"county": "Duval", "customers_out": 5, "customers_served": 1000}]
        monkeypatch.setattr(main, "get_jea_summary", lambda: (zip_records, county_rollup))
        main.run_jea_cycle(db)  # should not raise


class TestRunTalquinCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_talquin_records", lambda: [])
        monkeypatch.setattr(main, "TALQUIN_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_talquin_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        # An unset URL is a real, accepted "this deployment doesn't have
        # this integration turned on" state, not a failure - must not
        # raise just because TALQUIN_API_URL is falsy.
        monkeypatch.setattr(main, "get_talquin_records", lambda: [])
        monkeypatch.setattr(main, "TALQUIN_API_URL", None)
        main.run_talquin_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_talquin_records", lambda: [
            {"county": "Gadsden", "customers_out": 0, "customers_served": 15493}
        ])
        monkeypatch.setattr(main, "TALQUIN_API_URL", "https://example.com/real-endpoint")
        main.run_talquin_cycle(db)  # should not raise


class TestRunPrecoCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_preco_records", lambda: [])
        monkeypatch.setattr(main, "PRECO_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_preco_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_preco_records", lambda: [])
        monkeypatch.setattr(main, "PRECO_API_URL", None)
        main.run_preco_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_preco_records", lambda: [
            {"county": "Manatee", "customers_out": 3, "customers_served": 54383}
        ])
        monkeypatch.setattr(main, "PRECO_API_URL", "https://example.com/real-endpoint")
        main.run_preco_cycle(db)  # should not raise


class TestRunFkecCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_fkec_records", lambda: [])
        monkeypatch.setattr(main, "FKEC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_fkec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_fkec_records", lambda: [])
        monkeypatch.setattr(main, "FKEC_API_URL", None)
        main.run_fkec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_fkec_records", lambda: [
            {"county": "Monroe", "customers_out": 12, "customers_served": 34475}
        ])
        monkeypatch.setattr(main, "FKEC_API_URL", "https://example.com/real-endpoint")
        main.run_fkec_cycle(db)  # should not raise


class TestRunTcecCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_tcec_records", lambda: [])
        monkeypatch.setattr(main, "TCEC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_tcec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_tcec_records", lambda: [])
        monkeypatch.setattr(main, "TCEC_API_URL", None)
        main.run_tcec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_tcec_records", lambda: [
            {"county": "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)",
             "customers_out": 42, "customers_served": 20103}
        ])
        monkeypatch.setattr(main, "TCEC_API_URL", "https://example.com/real-endpoint")
        main.run_tcec_cycle(db)  # should not raise


class TestRunErecCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_erec_records", lambda: [])
        monkeypatch.setattr(main, "EREC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_erec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_erec_records", lambda: [])
        monkeypatch.setattr(main, "EREC_API_URL", None)
        main.run_erec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_erec_records", lambda: [
            {"county": "Escambia/Santa Rosa", "customers_out": 7, "customers_served": 13663}
        ])
        monkeypatch.setattr(main, "EREC_API_URL", "https://example.com/real-endpoint")
        main.run_erec_cycle(db)  # should not raise


class TestRunChelcoCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_chelco_records", lambda: [])
        monkeypatch.setattr(main, "CHELCO_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_chelco_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_chelco_records", lambda: [])
        monkeypatch.setattr(main, "CHELCO_API_URL", None)
        main.run_chelco_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_chelco_records", lambda: [
            {"county": "Santa Rosa/Okaloosa/Walton/Holmes", "customers_out": 7, "customers_served": 74996}
        ])
        monkeypatch.setattr(main, "CHELCO_API_URL", "https://example.com/real-endpoint")
        main.run_chelco_cycle(db)  # should not raise


class TestRunFpucCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_fpuc_outage_summary", lambda: None)
        monkeypatch.setattr(main, "FPUC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_fpuc_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_fpuc_outage_summary", lambda: None)
        monkeypatch.setattr(main, "FPUC_API_URL", None)
        main.run_fpuc_cycle(db)  # should not raise

    def test_does_not_raise_when_data_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_fpuc_outage_summary", lambda: {"markers": []})
        monkeypatch.setattr(main, "FPUC_API_URL", "https://example.com/real-endpoint")
        monkeypatch.setattr(main, "fpuc_outages_to_records", lambda data: [])
        monkeypatch.setattr(main, "markers_to_incidents", lambda data: [])
        main.run_fpuc_cycle(db)  # should not raise

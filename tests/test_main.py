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
        monkeypatch.setattr(main, "fetch_tcec_outage_summary", lambda: None)
        monkeypatch.setattr(main, "TCEC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_tcec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_tcec_outage_summary", lambda: None)
        monkeypatch.setattr(main, "TCEC_API_URL", None)
        main.run_tcec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_tcec_outage_summary", lambda: {
            "customersOutNow": 42, "customersServed": 20103,
        })
        monkeypatch.setattr(main, "TCEC_API_URL", "https://example.com/real-endpoint")
        main.run_tcec_cycle(db)  # should not raise

    def test_real_streets_affected_resolves_without_touching_the_network_for_cached_ones(self, db, monkeypatch):
        # A populated streetsAffected list must not make this test hit
        # the real Nominatim service - pre-seed the cache (same real
        # seam street_county_resolver.py itself checks first) rather
        # than mock requests directly.
        db.save_street_county(main.TCEC_UTILITY_NAME, "Some Rd", "Jefferson")
        monkeypatch.setattr(main, "fetch_tcec_outage_summary", lambda: {
            "customersOutNow": 5, "customersServed": 20103, "streetsAffected": ["Some Rd"],
        })
        monkeypatch.setattr(main, "TCEC_API_URL", "https://example.com/real-endpoint")

        main.run_tcec_cycle(db)

        assert db.get_active_counties(main.TCEC_UTILITY_NAME) == ["Jefferson"]


class TestRunErecCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_erec_outage_summary", lambda: None)
        monkeypatch.setattr(main, "EREC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_erec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_erec_outage_summary", lambda: None)
        monkeypatch.setattr(main, "EREC_API_URL", None)
        main.run_erec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_erec_outage_summary", lambda: {
            "customersOutNow": 7, "customersServed": 13663,
        })
        monkeypatch.setattr(main, "EREC_API_URL", "https://example.com/real-endpoint")
        main.run_erec_cycle(db)  # should not raise


class TestRunChelcoCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_chelco_outage_summary", lambda: None)
        monkeypatch.setattr(main, "CHELCO_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_chelco_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_chelco_outage_summary", lambda: None)
        monkeypatch.setattr(main, "CHELCO_API_URL", None)
        main.run_chelco_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_chelco_outage_summary", lambda: {
            "customersOutNow": 7, "customersServed": 74996,
        })
        monkeypatch.setattr(main, "CHELCO_API_URL", "https://example.com/real-endpoint")
        main.run_chelco_cycle(db)  # should not raise

    def test_real_streets_affected_resolves_using_the_cache(self, db, monkeypatch):
        db.save_street_county(main.CHELCO_UTILITY_NAME, "Howell Bluff Rd", "Walton")
        db.save_street_county(main.CHELCO_UTILITY_NAME, "Cotton Creek Rd", "Okaloosa")
        monkeypatch.setattr(main, "fetch_chelco_outage_summary", lambda: {
            "customersOutNow": 347, "customersServed": 74996,
            "streetsAffected": ["Howell Bluff Rd", "Cotton Creek Rd"],
        })
        monkeypatch.setattr(main, "CHELCO_API_URL", "https://example.com/real-endpoint")

        main.run_chelco_cycle(db)

        assert db.get_active_counties(main.CHELCO_UTILITY_NAME) == ["Okaloosa", "Walton"]


class TestRunGcecCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_gcec_outage_summary", lambda: None)
        monkeypatch.setattr(main, "GCEC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_gcec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_gcec_outage_summary", lambda: None)
        monkeypatch.setattr(main, "GCEC_API_URL", None)
        main.run_gcec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "fetch_gcec_outage_summary", lambda: {
            "customersOutNow": 7, "customersServed": 23206,
        })
        monkeypatch.setattr(main, "GCEC_API_URL", "https://example.com/real-endpoint")
        main.run_gcec_cycle(db)  # should not raise


class TestRunLwbuCycleFailureVisibility:
    """
    run_lwbu_cycle() fetches two independent shapes (summary rollup +
    incidents) in one call, same two-shapes-one-utility approach as
    run_duke_cycle()/run_fpuc_cycle() - the raise-on-empty check only
    applies to the summary rollup (the always-present real total);
    incidents are allowed to legitimately be empty on a quiet day.
    """

    def test_raises_when_configured_but_summary_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_lwbu_records", lambda: [])
        monkeypatch.setattr(main, "get_lwbu_incidents_summary", lambda: [])
        monkeypatch.setattr(main, "LWBU_API_BASE", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_lwbu_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_lwbu_records", lambda: [])
        monkeypatch.setattr(main, "get_lwbu_incidents_summary", lambda: [])
        monkeypatch.setattr(main, "LWBU_API_BASE", None)
        main.run_lwbu_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_lwbu_records", lambda: [
            {"county": "Palm Beach", "customers_out": 2, "customers_served": 28232}
        ])
        monkeypatch.setattr(main, "get_lwbu_incidents_summary", lambda: [])
        monkeypatch.setattr(main, "LWBU_API_BASE", "https://example.com/real-endpoint")
        main.run_lwbu_cycle(db)  # should not raise

    def test_does_not_raise_when_only_incidents_present(self, db, monkeypatch):
        # A quiet day for the summary total but a real incident still on
        # file would be a real, if odd, live state - must not crash.
        monkeypatch.setattr(main, "get_lwbu_records", lambda: [
            {"county": "Palm Beach", "customers_out": 0, "customers_served": 28232}
        ])
        monkeypatch.setattr(main, "get_lwbu_incidents_summary", lambda: [
            {"incident_id": "2026-07-14-0099", "utility": "Lake Worth Beach Utilities",
             "customer_count": 2, "lat": 26.6, "lon": -80.1, "county": "Palm Beach",
             "cause": "Material or equipment fault/failure", "cause_category": "other",
             "crew_assigned": False, "work_status": "Crew in Route", "streets_affected": "PENNY LN",
             "is_planned": False, "verified": True,
             "reported_start_time": "2026-01-01T00:00:00", "estimated_restoration": None}
        ])
        monkeypatch.setattr(main, "LWBU_API_BASE", "https://example.com/real-endpoint")
        main.run_lwbu_cycle(db)  # should not raise


class TestRunOucCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_ouc_records", lambda: [])
        monkeypatch.setattr(main, "OUC_INSTANCE_ID", "some-real-instance-id")
        with pytest.raises(RuntimeError):
            main.run_ouc_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_ouc_records", lambda: [])
        monkeypatch.setattr(main, "OUC_INSTANCE_ID", None)
        main.run_ouc_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_ouc_records", lambda: [
            {"county": "Orange", "customers_out": 500, "customers_served": 291868}
        ])
        monkeypatch.setattr(main, "OUC_INSTANCE_ID", "some-real-instance-id")
        main.run_ouc_cycle(db)  # should not raise


class TestRunLcecCycleFailureVisibility:
    def test_raises_when_configured_but_empty(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_lcec_records", lambda: [])
        monkeypatch.setattr(main, "LCEC_API_URL", "https://example.com/real-endpoint")
        with pytest.raises(RuntimeError):
            main.run_lcec_cycle(db)

    def test_does_not_raise_when_not_configured(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_lcec_records", lambda: [])
        monkeypatch.setattr(main, "LCEC_API_URL", None)
        main.run_lcec_cycle(db)  # should not raise

    def test_does_not_raise_when_records_present(self, db, monkeypatch):
        monkeypatch.setattr(main, "get_lcec_records", lambda: [
            {"county": "Lee", "customers_out": 4, "customers_served": 227335}
        ])
        monkeypatch.setattr(main, "LCEC_API_URL", "https://example.com/real-endpoint")
        main.run_lcec_cycle(db)  # should not raise


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


class TestRunHistoricalTallyCycle:
    """
    run_historical_tally_cycle() - added 2026-07-14 so the public page
    reads a precomputed value instead of re-running the real, expensive
    nested-loop correlation query on every page view (measured at ~44s
    on real data). Only tests the wiring (compute -> store) here, not
    historical_confidence_tally()'s own logic - that's covered directly
    in test_county_status.py.
    """

    def test_stores_the_computed_tally(self, db, monkeypatch):
        monkeypatch.setattr(
            main, "historical_confidence_tally",
            lambda: {"Alachua": {"high": 2, "medium": 0, "low": 0}},
        )
        main.run_historical_tally_cycle(db)
        assert db.get_historical_confidence_tally() == {"Alachua": {"high": 2, "medium": 0, "low": 0}}

    def test_empty_tally_clears_any_previous_result(self, db, monkeypatch):
        db.store_historical_confidence_tally({"Duval": {"high": 1, "medium": 0, "low": 0}})
        monkeypatch.setattr(main, "historical_confidence_tally", lambda: {})
        main.run_historical_tally_cycle(db)
        assert db.get_historical_confidence_tally() == {}

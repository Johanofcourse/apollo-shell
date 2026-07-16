"""
Tests for apollo_shell/county_status.py - the shared per-county status
logic extracted 2026-07-14 out of dashboard.py so both dashboard.py
(internal ops tool) and public_site.py (public-facing page) read live
data the same way without either one importing from the other.

_row_tier()/county_verdict() are genuinely new here, added for the
public page's map coloring - not just moved from dashboard.py like the
rest of this module.
"""

import os
import tempfile

import pytest

import county_status as cs
from database import OutageDatabase


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


class TestRowTier:
    def test_uses_real_percentage_when_present(self):
        assert cs._row_tier({"peak_percentage_out": 35, "peak_customers": 1}) == "critical"
        assert cs._row_tier({"peak_percentage_out": 15, "peak_customers": 1}) == "high"
        assert cs._row_tier({"peak_percentage_out": 5, "peak_customers": 1}) == "medium"
        assert cs._row_tier({"peak_percentage_out": 1, "peak_customers": 1}) == "low"

    def test_falls_back_to_raw_count_when_no_percentage(self):
        assert cs._row_tier({"peak_percentage_out": None, "peak_customers": 5000}) == "critical"
        assert cs._row_tier({"peak_percentage_out": None, "peak_customers": 800}) == "high"
        assert cs._row_tier({"peak_percentage_out": None, "peak_customers": 100}) == "medium"
        assert cs._row_tier({"peak_percentage_out": None, "peak_customers": 3}) == "low"

    def test_missing_percentage_key_treated_as_no_percentage(self):
        # Some raw rows (incident-level sources) never carry this key
        # at all, not even as None - .get() must not raise.
        assert cs._row_tier({"peak_customers": 10}) == "low"

    def test_zero_customers_with_no_percentage_is_low_not_error(self):
        assert cs._row_tier({"peak_percentage_out": None, "peak_customers": 0}) == "low"


class TestCountyVerdict:
    def test_no_rows_is_clear(self):
        assert cs.county_verdict([], []) == "clear"

    def test_single_real_row_drives_the_verdict(self):
        rows = [{"peak_percentage_out": 35, "peak_customers": 1}]
        assert cs.county_verdict(rows, []) == "critical"

    def test_single_combined_row_drives_the_verdict(self):
        rows = [{"peak_percentage_out": 12, "peak_customers": 1}]
        assert cs.county_verdict([], rows) == "high"

    def test_worst_of_multiple_rows_wins(self):
        real = [{"peak_percentage_out": 1, "peak_customers": 1}]
        combined = [{"peak_percentage_out": 35, "peak_customers": 1}]
        assert cs.county_verdict(real, combined) == "critical"

    def test_low_severity_row_alone_is_low_not_clear(self):
        # A real open event, even a small one, is "something is
        # happening here" - distinct from no data at all.
        rows = [{"peak_percentage_out": 0.5, "peak_customers": 1}]
        assert cs.county_verdict(rows, []) == "low"


class TestAllCountyVerdicts:
    def test_computes_a_verdict_per_requested_county(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [
            {"county": "ALACHUA", "customers_out": 40000, "customers_served": 100000},
        ], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [
            {"county": "ALACHUA", "customers_out": 40000, "customers_served": 100000},
        ], timestamp="2026-01-01T00:00:00")

        verdicts = cs.all_county_verdicts(db, county_names=["Alachua", "Baker"])
        db.close()

        assert verdicts["Alachua"] == "critical"
        assert verdicts["Baker"] == "clear"

    def test_counties_with_no_open_events_are_clear(self, db_path):
        db = OutageDatabase(db_path)
        verdicts = cs.all_county_verdicts(db, county_names=["Alachua", "Baker"])
        db.close()

        assert verdicts == {"Alachua": "clear", "Baker": "clear"}


class TestHistoricalConfidenceTally:
    """
    historical_confidence_tally() - added 2026-07-14 to power the
    public page's "Historical Pattern" map view. Genuinely different
    question from county_verdict()/all_county_verdicts() above (current
    live severity): this asks "how often has this county's outage
    history plausibly overlapped with real weather, all-time."
    """

    def test_no_data_returns_empty_dict(self, db_path):
        db = OutageDatabase(db_path)
        tally = cs.historical_confidence_tally(db_path)
        db.close()

        assert tally == {}

    def test_real_correlated_match_shows_up_in_the_tally(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "test-alert-1", "event": "Tornado Warning", "severity": "Severe",
            "urgency": "Expected", "areas": "ALACHUA",
            "effective": "2026-01-01T00:00:00", "expires": "2026-01-01T23:59:59",
            "headline": "test", "description": "test",
        }])
        db.log_multiple_outages("FPL", [
            {"county": "ALACHUA", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T12:00:00")
        db.close()

        tally = cs.historical_confidence_tally(db_path)

        # correlation_summary() groups by whatever raw county string is
        # stored (no casing normalization) - real live FPL data happens
        # to already be properly cased, but this test seeds the same
        # ALL-CAPS convention the rest of the suite uses for FPL rows.
        assert "ALACHUA" in tally
        assert sum(tally["ALACHUA"].values()) == 1

    def test_real_incident_shaped_source_does_not_crash_the_whole_tally(self, db_path):
        # Real regression (found 2026-07-16 during the Oracle Cloud
        # migration, on a VM run that finally had a real all-time FPUC
        # incident-level match): find_tallahassee_correlations() and
        # find_fpuc_incident_correlations() both return
        # {"incident": ..., "alert": ...}-shaped matches, not
        # {"outage": ...}-shaped ones - pairing either with the generic
        # correlation_summary() (which reads match["outage"]) instead of
        # duke_correlation_summary() (which reads match["incident"])
        # raises a real KeyError the moment either source ever has a
        # non-empty all-time match, aborting the ENTIRE tally
        # computation for every other county too, not just theirs. This
        # had been silently dormant because both real sources rarely had
        # a non-empty all-time match until now.
        db = OutageDatabase(db_path)
        # log_tallahassee_incidents() stamps fetched_at as the real
        # current time (not reported_start_time below), and
        # find_tallahassee_correlations() matches against that real
        # timestamp - so the alert's window has to be wide enough to
        # cover "now", not a fixed date.
        db.log_weather_alerts([{
            "id": "test-alert-tally", "event": "Severe Thunderstorm Warning", "severity": "Severe",
            "urgency": "Expected", "areas": "Leon",
            "effective": "2020-01-01T00:00:00", "expires": "2030-01-01T23:59:59",
            "headline": "test", "description": "test",
        }])
        db.log_tallahassee_incidents([{
            "incident_id": "T1", "utility": "City of Tallahassee", "customer_count": 50,
            "lat": 30.44, "lon": -84.28, "county": "Leon", "region_name": "North",
            "status": "Investigating", "status_category": "investigating",
            "cause": "Tree down", "cause_category": "vegetation", "outage_type": "Unplanned",
            "reported_start_time": "2026-01-01T12:00:00", "estimated_restoration": None,
        }])
        db.close()

        # Must not raise, and every other real source (FPL included)
        # must still get processed despite Tallahassee's real match.
        tally = cs.historical_confidence_tally(db_path)
        assert "Leon" in tally
        assert sum(tally["Leon"].values()) == 1

    def test_counties_with_no_history_are_absent_not_zero(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "test-alert-1", "event": "Tornado Warning", "severity": "Severe",
            "urgency": "Expected", "areas": "ALACHUA",
            "effective": "2026-01-01T00:00:00", "expires": "2026-01-01T23:59:59",
            "headline": "test", "description": "test",
        }])
        db.log_multiple_outages("FPL", [
            {"county": "ALACHUA", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T12:00:00")
        db.close()

        tally = cs.historical_confidence_tally(db_path)

        assert "Baker" not in tally

    def test_every_real_correlation_function_is_registered(self):
        # Real regression (found 2026-07-17 during a full VM test sweep):
        # find_tcec_correlations()/find_erec_correlations()/
        # find_chelco_correlations()/find_gcec_correlations() all existed
        # and worked fine, but were never added to
        # _REAL_CORRELATION_SOURCES - so their data silently never
        # reached historical_confidence_tally() (the public site's
        # Historical Pattern map), even though the same four functions
        # were correctly wired into dashboard.py's own per-utility
        # correlation display. No crash, just a quiet undercount - this
        # guards against the same class of oversight for any future
        # utility, not just these four.
        import inspect
        import correlate

        all_correlation_fns = {
            name for name, obj in inspect.getmembers(correlate, inspect.isfunction)
            if name.startswith("find_") and name.endswith("_correlations")
        }
        registered_fns = {fn.__name__ for fn, _ in cs._REAL_CORRELATION_SOURCES}

        assert all_correlation_fns == registered_fns


class TestCountyPickerChoicesSharedCorrectly:
    def test_has_all_67_real_counties(self):
        assert len(cs.COUNTY_PICKER_CHOICES) == 67

    def test_desoto_casing_special_case_preserved(self):
        assert "DeSoto" in cs.COUNTY_PICKER_CHOICES
        assert "Desoto" not in cs.COUNTY_PICKER_CHOICES


class TestNormalizeClosedEvents:
    def test_shape_and_bounded_duration(self):
        rows = cs._normalize_closed_events([{
            "utility": "FPL", "county": "Alachua", "peak_customers_out": 500,
            "peak_percentage_out": 2.5, "customers_served": 20000,
            "start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T02:00:00",
        }], "peak_customers_out")

        assert rows == [{
            "utility": "FPL", "county": "Alachua", "peak_customers": 500,
            "peak_percentage_out": 2.5, "customers_served": 20000,
            "start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T02:00:00",
            "duration": "2h 0m",
        }]

    def test_incident_level_source_has_no_percentage(self):
        rows = cs._normalize_closed_events([{
            "utility": "TECO", "county": "Hillsborough", "peak_customer_count": 40,
            "start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T00:30:00",
        }], "peak_customer_count")

        assert rows[0]["peak_customers"] == 40
        assert rows[0]["peak_percentage_out"] is None


class TestRealPerCountyClosedEvents:
    def test_includes_a_real_resolved_fpl_outage(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [
            {"county": "ALACHUA", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [
            {"county": "ALACHUA", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T00:00:00")
        db.log_multiple_outages("FPL", [
            {"county": "ALACHUA", "customers_out": 0, "customers_served": 1000},
        ], timestamp="2026-01-01T02:00:00")
        db.sync_outage_events("FPL", [
            {"county": "ALACHUA", "customers_out": 0, "customers_served": 1000},
        ], timestamp="2026-01-01T02:00:00")

        rows = cs._real_per_county_closed_events(db)
        db.close()

        alachua_rows = [r for r in rows if r["county"] == "ALACHUA"]
        assert len(alachua_rows) == 1
        assert alachua_rows[0]["peak_customers"] == 50
        assert alachua_rows[0]["end_time"] == "2026-01-01T02:00:00"

    def test_still_open_events_are_not_included(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [
            {"county": "ALACHUA", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [
            {"county": "ALACHUA", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T00:00:00")

        rows = cs._real_per_county_closed_events(db)
        db.close()

        assert rows == []


class TestCombinedTerritoryClosedEvents:
    def test_includes_a_real_resolved_gcec_outage(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"
        db.log_gcec_outages([{"county": territory, "customers_out": 7, "customers_served": 23206}], timestamp="2026-01-01T00:00:00")
        db.sync_gcec_outage_events([{"county": territory, "customers_out": 7, "customers_served": 23206}], timestamp="2026-01-01T00:00:00")
        db.log_gcec_outages([{"county": territory, "customers_out": 0, "customers_served": 23206}], timestamp="2026-01-01T01:00:00")
        db.sync_gcec_outage_events([{"county": territory, "customers_out": 0, "customers_served": 23206}], timestamp="2026-01-01T01:00:00")

        rows = cs._combined_territory_closed_events(db)
        db.close()

        assert len(rows) == 1
        assert rows[0]["county"] == territory
        assert rows[0]["peak_customers"] == 7

    def test_rows_for_county_finds_it_by_real_county_name(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"
        db.log_gcec_outages([{"county": territory, "customers_out": 7, "customers_served": 23206}], timestamp="2026-01-01T00:00:00")
        db.sync_gcec_outage_events([{"county": territory, "customers_out": 7, "customers_served": 23206}], timestamp="2026-01-01T00:00:00")
        db.log_gcec_outages([{"county": territory, "customers_out": 0, "customers_served": 23206}], timestamp="2026-01-01T01:00:00")
        db.sync_gcec_outage_events([{"county": territory, "customers_out": 0, "customers_served": 23206}], timestamp="2026-01-01T01:00:00")

        rows = cs._combined_territory_closed_events(db)
        calhoun_rows = cs._rows_for_county(rows, "Calhoun")
        db.close()

        assert len(calhoun_rows) == 1

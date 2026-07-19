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


class TestCanonicalizeCountyName:
    """
    _canonicalize_county_name() - added 2026-07-18 after finding that
    FPL's own "De Soto"/"St Johns"/"St Lucie" (space, no period) never
    matched this project's canonical "DeSoto"/"St. Johns"/"St. Lucie"
    anywhere downstream.
    """

    def test_exact_canonical_name_passes_through(self):
        assert cs._canonicalize_county_name("DeSoto") == "DeSoto"

    def test_space_variant_resolves_to_canonical(self):
        assert cs._canonicalize_county_name("De Soto") == "DeSoto"

    def test_missing_period_variant_resolves_to_canonical(self):
        assert cs._canonicalize_county_name("St Lucie") == "St. Lucie"
        assert cs._canonicalize_county_name("St Johns") == "St. Johns"

    def test_all_caps_variant_resolves_to_canonical(self):
        assert cs._canonicalize_county_name("MIAMI-DADE") == "Miami-Dade"

    def test_unrecognized_name_falls_back_unchanged(self):
        # A genuine data problem (e.g. FPL's real "Undefined" county
        # rows) should stay visible, not get silently mapped to
        # something real-looking.
        assert cs._canonicalize_county_name("Undefined") == "Undefined"


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

        # Canonicalized to COUNTY_PICKER_CHOICES's real casing ("Alachua"),
        # not left as whatever raw casing the source happened to store
        # ("ALACHUA") - see _canonicalize_county_name, added 2026-07-18.
        assert "Alachua" in tally
        assert sum(tally["Alachua"].values()) == 1

    def test_real_incident_shaped_source_does_not_crash_the_whole_tally(self, db_path):
        # Real regression (found 2026-07-16 during the Oracle Cloud
        # migration, on a VM run that finally had a real all-time FPUC
        # incident-level match): find_fpuc_incident_correlations()
        # returns {"incident": ..., "alert": ...}-shaped matches, not
        # {"outage": ...}-shaped ones - pairing it with the generic
        # correlation_summary() (which reads match["outage"]) instead of
        # duke_correlation_summary() (which reads match["incident"])
        # raises a real KeyError the moment it ever has a non-empty
        # all-time match, aborting the ENTIRE tally computation for
        # every other county too, not just theirs. This had been
        # silently dormant because this real source rarely had a non-
        # empty all-time match until now. Tallahassee used to be this
        # test's other example of an incident-shaped source, but moved
        # to a county-rollup design 2026-07-18 (see
        # fetch_tallahassee_outages.get_rollup_summary()) - FPUC's
        # incident-level view is the real remaining case now.
        db = OutageDatabase(db_path)
        # log_fpuc_incidents() stamps fetched_at as the real current
        # time (not reported_start_time below), and
        # find_fpuc_incident_correlations() matches against that real
        # timestamp - so the alert's window has to be wide enough to
        # cover "now", not a fixed date.
        db.log_weather_alerts([{
            "id": "test-alert-tally", "event": "Severe Thunderstorm Warning", "severity": "Severe",
            "urgency": "Expected", "areas": "Bay",
            "effective": "2020-01-01T00:00:00", "expires": "2030-01-01T23:59:59",
            "headline": "test", "description": "test",
        }])
        db.log_fpuc_incidents([{
            "incident_id": "F1", "utility": "Florida Public Utilities Company", "customer_count": 50,
            "lat": 30.16, "lon": -85.66, "county": "Bay", "substation": "Test", "feeder": "1",
            "reported_start_time": "2026-01-01T12:00:00", "estimated_restoration": None,
        }])
        db.close()

        # Must not raise, and every other real source (FPL included)
        # must still get processed despite FPUC's real match.
        tally = cs.historical_confidence_tally(db_path)
        assert "Bay" in tally
        assert sum(tally["Bay"].values()) == 1

    def test_spelling_variant_gets_canonicalized_not_a_separate_key(self, db_path):
        # Real bug found 2026-07-18: FPL stores "De Soto" (with a space)
        # against this project's own canonical "DeSoto" - without
        # canonicalizing, a real match here would be stored under "De
        # Soto" and be permanently invisible to the map's lookup by
        # "DESOTO" (see _county_map_data in public_site.py), since that
        # lookup only .upper()s, it doesn't strip spaces.
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "test-alert-1", "event": "Tornado Warning", "severity": "Severe",
            "urgency": "Expected", "areas": "De Soto",
            "effective": "2026-01-01T00:00:00", "expires": "2026-01-01T23:59:59",
            "headline": "test", "description": "test",
        }])
        db.log_multiple_outages("FPL", [
            {"county": "De Soto", "customers_out": 50, "customers_served": 1000},
        ], timestamp="2026-01-01T12:00:00")
        db.close()

        tally = cs.historical_confidence_tally(db_path)

        assert "DeSoto" in tally
        assert "De Soto" not in tally
        assert sum(tally["DeSoto"].values()) == 1

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


class TestExplainMissingHistoricalData:
    """
    explain_missing_historical_data() - added 2026-07-17 for dashboard.py's
    /county page, so an operator sees why a county has no Historical
    Pattern entry instead of silence. Computed fresh from live data every
    call (never hardcoded per-county text) so it stays accurate as a
    chronic source recovers or a new live source gets added.
    """

    def test_county_with_a_tally_entry_needs_no_explanation(self, db_path):
        db = OutageDatabase(db_path)
        db.store_historical_confidence_tally({"Alachua": {"high": 1, "medium": 0, "low": 0}})

        result = cs.explain_missing_historical_data("Alachua", db)
        db.close()

        assert result is None

    def test_tally_match_is_case_insensitive(self, db_path):
        db = OutageDatabase(db_path)
        db.store_historical_confidence_tally({"ALACHUA": {"high": 1, "medium": 0, "low": 0}})

        result = cs.explain_missing_historical_data("Alachua", db)
        db.close()

        assert result is None

    def test_real_events_with_no_tally_entry_reports_not_yet_matched(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [
            {"county": "BRADFORD", "customers_out": 5, "customers_served": 1000},
        ], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [
            {"county": "BRADFORD", "customers_out": 5, "customers_served": 1000},
        ], timestamp="2026-01-01T00:00:00")

        result = cs.explain_missing_historical_data("Bradford", db)
        db.close()

        assert result["reason"] == "not_yet_matched"
        assert result["utilities"] == ["FPL"]
        assert result["real_event_count"] == 1

    def test_combined_territory_only_reports_combined_only(self, db_path):
        db = OutageDatabase(db_path)
        territory = "Bay/Calhoun/Gulf/Jackson/Walton/Washington"
        db.log_gcec_outages([
            {"county": territory, "customers_out": 7, "customers_served": 23206},
        ], timestamp="2026-01-01T00:00:00")
        db.sync_gcec_outage_events([
            {"county": territory, "customers_out": 7, "customers_served": 23206},
        ], timestamp="2026-01-01T00:00:00")

        result = cs.explain_missing_historical_data("Calhoun", db)
        db.close()

        assert result["reason"] == "combined_only"
        assert result["utilities"] == ["Gulf Coast Electric Cooperative, Inc."]

    def test_no_coverage_at_all_reports_no_live_source(self, db_path):
        db = OutageDatabase(db_path)

        result = cs.explain_missing_historical_data("Baker", db)
        db.close()

        assert result == {"reason": "no_live_source", "utilities": []}


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


class TestNormalizeOpenEvents:
    def test_teco_row_carries_estimated_restoration_through(self):
        rows = cs._normalize_open_events([{
            "utility": "Tampa Electric Company", "county": "Hillsborough",
            "current_customer_count": 40, "peak_customer_count": 100,
            "current_estimated_restoration": "2026-01-01T06:00:00",
            "start_time": "2026-01-01T00:00:00",
        }], "current_customer_count", "peak_customer_count")

        assert rows[0]["estimated_restoration"] == "2026-01-01T06:00:00"

    def test_source_without_estimated_restoration_gets_none(self):
        rows = cs._normalize_open_events([{
            "utility": "FPL", "county": "Alachua",
            "current_customers_out": 40, "peak_customers_out": 100,
            "start_time": "2026-01-01T00:00:00",
        }], "current_customers_out", "peak_customers_out")

        assert rows[0]["estimated_restoration"] is None


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


def _open_and_close_fpl_event(db, county, start, end, customers=50):
    db.sync_outage_events(
        cs.FPL_UTILITY_NAME, [{"county": county, "customers_out": customers, "customers_served": 100_000}],
        timestamp=start,
    )
    db.sync_outage_events(
        cs.FPL_UTILITY_NAME, [{"county": county, "customers_out": 0, "customers_served": 100_000}],
        timestamp=end,
    )


class TestFplOrdinaryRestorationStats:
    """
    fpl_ordinary_restoration_stats() - added 2026-07-18 alongside
    fpl_restoration_precedent() (the major-storm archive version), but
    reading this project's OWN live outage_events instead - "how long
    does an ordinary FPL outage actually take here," a genuinely
    different question from the storm one, deliberately never merged
    into it.
    """

    def test_no_data_for_county_returns_none(self, db_path):
        db = OutageDatabase(db_path)
        result = cs.fpl_ordinary_restoration_stats("Alachua", db)
        db.close()

        assert result is None

    def test_single_event_computes_stats_and_is_flagged_limited(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_fpl_event(db, "Alachua", "2026-01-01T00:00:00", "2026-01-01T03:00:00")

        result = cs.fpl_ordinary_restoration_stats("Alachua", db)
        db.close()

        assert result["n"] == 1
        assert result["min_hours"] == 3.0
        assert result["median_hours"] == 3.0
        assert result["max_hours"] == 3.0
        assert result["limited"] is True
        assert result["excluded_count"] == 0

    def test_reaching_the_confident_threshold_clears_the_limited_flag(self, db_path):
        db = OutageDatabase(db_path)
        for i in range(cs.MIN_EVENTS_FOR_CONFIDENT_RANGE):
            _open_and_close_fpl_event(db, "Duval", f"2026-01-0{i + 1}T00:00:00", f"2026-01-0{i + 1}T05:00:00")

        result = cs.fpl_ordinary_restoration_stats("Duval", db)
        db.close()

        assert result["n"] == cs.MIN_EVENTS_FOR_CONFIDENT_RANGE
        assert result["limited"] is False

    def test_county_name_match_is_case_insensitive(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_fpl_event(db, "MIAMI-DADE", "2026-01-01T00:00:00", "2026-01-01T02:00:00")

        result = cs.fpl_ordinary_restoration_stats("Miami-Dade", db)
        db.close()

        assert result is not None

    def test_events_beyond_the_plausible_cutoff_are_excluded_not_averaged_in(self, db_path):
        # The real reason this function exists: FPL's live feed only
        # ever reports a county-wide total, and a busy county's
        # aggregate can run non-zero for many real days straight without
        # representing one actual repair job - this is that exact case,
        # reproduced directly rather than assumed.
        db = OutageDatabase(db_path)
        _open_and_close_fpl_event(db, "Palm Beach", "2026-01-01T00:00:00", "2026-01-01T03:00:00")  # 3h, real
        _open_and_close_fpl_event(db, "Palm Beach", "2026-02-01T00:00:00", "2026-02-11T14:00:00")  # 254h, blurred

        result = cs.fpl_ordinary_restoration_stats("Palm Beach", db)
        db.close()

        assert result["n"] == 1
        assert result["median_hours"] == 3.0
        assert result["excluded_count"] == 1

    def test_every_event_excluded_returns_none_not_a_crash(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_fpl_event(db, "Brevard", "2026-01-01T00:00:00", "2026-01-10T00:00:00")  # 216h

        result = cs.fpl_ordinary_restoration_stats("Brevard", db)
        db.close()

        assert result is None

    def test_other_utilities_in_the_same_county_are_ignored(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_duke_incident_events(
            [{"incident_id": "D1", "utility": "Duke Energy", "county": "Leon", "customer_count": 50,
              "lat": 30.44, "lon": -84.28, "cause": "Tree down", "cause_category": "vegetation"}],
            timestamp="2026-01-01T00:00:00",
        )

        result = cs.fpl_ordinary_restoration_stats("Leon", db)
        db.close()

        assert result is None


def _teco_incident(incident_id, county="Hillsborough", customer_count=10, estimated_restoration="2026-01-01T06:00:00", update_time="2026-01-01T00:00:00"):
    return {
        "incident_id": incident_id, "utility": "Tampa Electric Company",
        "status": "On our way", "status_category": "investigating",
        "reason": "Tree down", "reason_category": "vegetation",
        "customer_count": customer_count, "lat": 27.9, "lon": -82.4, "county": county,
        "update_time": update_time, "estimated_restoration": estimated_restoration,
    }


def _open_and_close_teco_incident(db, incident_id, county, first_etr, actual_end, open_at="2026-01-01T00:00:00"):
    db.log_teco_incidents([_teco_incident(incident_id, county=county, estimated_restoration=first_etr, update_time=open_at)])
    db.sync_teco_incident_events([_teco_incident(incident_id, county=county, estimated_restoration=first_etr)], timestamp=open_at)
    db.sync_teco_incident_events([], timestamp=actual_end)


class TestTecoEtrAccuracy:
    """
    teco_etr_accuracy() - added 2026-07-18, a genuinely different Phase
    3 signal than the FPL restoration-precedent pair: TECO already
    reports a real per-incident ETR, so instead of inventing a
    precedent range, this checks how trustworthy TECO's own existing
    number has actually been - "when TECO tells you when your power
    will be back, how close does that number usually land?"
    """

    def test_no_data_for_county_returns_none(self, db_path):
        db = OutageDatabase(db_path)
        result = cs.teco_etr_accuracy("Hillsborough", db)
        db.close()

        assert result is None

    def test_resolved_earlier_than_etr_is_a_negative_error(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_teco_incident(
            db, "T1", "Hillsborough",
            first_etr="2026-01-01T06:00:00", actual_end="2026-01-01T03:00:00",
        )

        result = cs.teco_etr_accuracy("Hillsborough", db)
        db.close()

        assert result["n"] == 1
        assert result["median_error_hours"] == -3.0
        assert result["on_time_pct"] == 100.0
        assert result["limited"] is True

    def test_resolved_later_than_etr_is_a_positive_error(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_teco_incident(
            db, "T1", "Hillsborough",
            first_etr="2026-01-01T06:00:00", actual_end="2026-01-01T09:00:00",
        )

        result = cs.teco_etr_accuracy("Hillsborough", db)
        db.close()

        assert result["median_error_hours"] == 3.0
        assert result["on_time_pct"] == 0.0

    def test_on_time_pct_reflects_a_real_mix(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_teco_incident(db, "T1", "Polk", first_etr="2026-01-01T06:00:00", actual_end="2026-01-01T03:00:00", open_at="2026-01-01T00:00:00")
        _open_and_close_teco_incident(db, "T2", "Polk", first_etr="2026-01-02T06:00:00", actual_end="2026-01-02T09:00:00", open_at="2026-01-02T00:00:00")
        _open_and_close_teco_incident(db, "T3", "Polk", first_etr="2026-01-03T06:00:00", actual_end="2026-01-03T06:00:00", open_at="2026-01-03T00:00:00")

        result = cs.teco_etr_accuracy("Polk", db)
        db.close()

        assert result["n"] == 3
        assert round(result["on_time_pct"], 2) == round(2 / 3 * 100, 2)

    def test_county_name_match_is_case_insensitive(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_teco_incident(db, "T1", "PINELLAS", first_etr="2026-01-01T06:00:00", actual_end="2026-01-01T03:00:00")

        result = cs.teco_etr_accuracy("Pinellas", db)
        db.close()

        assert result is not None

    def test_reaching_the_confident_threshold_clears_the_limited_flag(self, db_path):
        db = OutageDatabase(db_path)
        for i in range(cs.MIN_EVENTS_FOR_CONFIDENT_RANGE):
            _open_and_close_teco_incident(
                db, f"T{i}", "Hillsborough",
                first_etr=f"2026-01-0{i + 1}T06:00:00", actual_end=f"2026-01-0{i + 1}T05:00:00",
                open_at=f"2026-01-0{i + 1}T00:00:00",
            )

        result = cs.teco_etr_accuracy("Hillsborough", db)
        db.close()

        assert result["n"] == cs.MIN_EVENTS_FOR_CONFIDENT_RANGE
        assert result["limited"] is False

    def test_incidents_with_no_etr_ever_reported_are_excluded(self, db_path):
        db = OutageDatabase(db_path)
        db.log_teco_incidents([_teco_incident("T1", county="Hillsborough", estimated_restoration=None)])
        db.sync_teco_incident_events(
            [_teco_incident("T1", county="Hillsborough", estimated_restoration=None)],
            timestamp="2026-01-01T00:00:00",
        )
        db.sync_teco_incident_events([], timestamp="2026-01-01T03:00:00")

        result = cs.teco_etr_accuracy("Hillsborough", db)
        db.close()

        assert result is None

    def test_other_utilities_in_the_same_county_are_ignored(self, db_path):
        db = OutageDatabase(db_path)
        db.sync_duke_incident_events(
            [{"incident_id": "D1", "utility": "Duke Energy", "county": "Hillsborough", "customer_count": 50,
              "lat": 27.9, "lon": -82.4, "cause": "Tree down", "cause_category": "vegetation"}],
            timestamp="2026-01-01T00:00:00",
        )

        result = cs.teco_etr_accuracy("Hillsborough", db)
        db.close()

        assert result is None


def _lwbu_incident(incident_id, county="Palm Beach", customer_count=2, estimated_restoration="2026-01-01T06:00:00"):
    return {
        "incident_id": incident_id, "utility": "Lake Worth Beach Utilities",
        "customer_count": customer_count, "lat": 26.6, "lon": -80.1, "county": county,
        "cause": "Material or equipment fault/failure", "cause_category": "other",
        "crew_assigned": False, "work_status": "Crew in Route", "streets_affected": "PENNY LN",
        "is_planned": False, "verified": True,
        "reported_start_time": "2026-01-01T00:00:00", "estimated_restoration": estimated_restoration,
    }


def _open_and_close_lwbu_incident(db, incident_id, county, first_etr, actual_end, open_at="2026-01-01T00:00:00"):
    db.log_lwbu_incidents([_lwbu_incident(incident_id, county=county, estimated_restoration=first_etr)])
    db.sync_lwbu_incident_events([_lwbu_incident(incident_id, county=county, estimated_restoration=first_etr)], timestamp=open_at)
    db.sync_lwbu_incident_events([], timestamp=actual_end)


class TestLwbuEtrAccuracy:
    """
    lwbu_etr_accuracy() - added 2026-07-18, the same accuracy-check
    shape as teco_etr_accuracy(). Real bug found and fixed while
    building this: LWBU's raw ETR always carries a real UTC offset
    (e.g. "...-04:00"), unlike TECO's naive format, and this project's
    own end_time is naive-but-actually-UTC (the server's own clock runs
    in UTC, confirmed via timedatectl) - a naive subtraction would have
    raised TypeError on every single real row, so this would have always
    returned None, indistinguishable from "no data." Also real: LWBU's
    API doesn't zero-pad fractional seconds (".74" vs ".967" vs none at
    all in the same field), which datetime.fromisoformat() rejects
    outright on Python <3.11 (this runs on 3.9) - caught by testing
    against the real messy VM data, not just clean local fixtures.
    """

    def test_no_data_for_county_returns_none(self, db_path):
        db = OutageDatabase(db_path)
        result = cs.lwbu_etr_accuracy("Palm Beach", db)
        db.close()

        assert result is None

    def test_resolved_earlier_than_etr_is_a_negative_error(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_lwbu_incident(
            db, "L1", "Palm Beach",
            first_etr="2026-01-01T06:00:00", actual_end="2026-01-01T03:00:00",
        )

        result = cs.lwbu_etr_accuracy("Palm Beach", db)
        db.close()

        assert result["n"] == 1
        assert result["median_error_hours"] == -3.0
        assert result["on_time_pct"] == 100.0
        assert result["limited"] is True

    def test_offset_aware_etr_is_converted_to_match_naive_utc_end_time(self, db_path):
        # The real bug: LWBU's raw ETR carries a real timezone offset
        # ("-04:00", real Eastern time) while end_time is this project's
        # own naive-but-actually-UTC timestamp. "06:00:00-04:00" is
        # 10:00:00 UTC - closing at "2026-01-01T11:00:00" (naive UTC) is
        # 1 real hour late, not the wildly wrong number a naive
        # subtraction (or a crash) would have produced.
        db = OutageDatabase(db_path)
        _open_and_close_lwbu_incident(
            db, "L1", "Palm Beach",
            first_etr="2026-01-01T06:00:00-04:00", actual_end="2026-01-01T11:00:00",
        )

        result = cs.lwbu_etr_accuracy("Palm Beach", db)
        db.close()

        assert result["median_error_hours"] == 1.0

    def test_non_zero_padded_fractional_seconds_do_not_break_parsing(self, db_path):
        # The other real bug: LWBU's API sends inconsistent fractional-
        # second digit counts (".74" seen in real data, not the 3 or 6
        # digits datetime.fromisoformat() requires pre-3.11).
        db = OutageDatabase(db_path)
        _open_and_close_lwbu_incident(
            db, "L1", "Palm Beach",
            first_etr="2026-01-01T06:00:00.74", actual_end="2026-01-01T09:00:00",
        )

        result = cs.lwbu_etr_accuracy("Palm Beach", db)
        db.close()

        assert result["n"] == 1
        assert round(result["median_error_hours"], 2) == 3.0

    def test_county_name_match_is_case_insensitive(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_lwbu_incident(db, "L1", "PALM BEACH", first_etr="2026-01-01T06:00:00", actual_end="2026-01-01T03:00:00")

        result = cs.lwbu_etr_accuracy("Palm Beach", db)
        db.close()

        assert result is not None

    def test_reaching_the_confident_threshold_clears_the_limited_flag(self, db_path):
        db = OutageDatabase(db_path)
        for i in range(cs.MIN_EVENTS_FOR_CONFIDENT_RANGE):
            _open_and_close_lwbu_incident(
                db, f"L{i}", "Palm Beach",
                first_etr=f"2026-01-0{i + 1}T06:00:00", actual_end=f"2026-01-0{i + 1}T05:00:00",
                open_at=f"2026-01-0{i + 1}T00:00:00",
            )

        result = cs.lwbu_etr_accuracy("Palm Beach", db)
        db.close()

        assert result["n"] == cs.MIN_EVENTS_FOR_CONFIDENT_RANGE
        assert result["limited"] is False


def _open_and_close_duke_incident(db, incident_id, county, start, end, customers=50):
    record = {
        "incident_id": incident_id, "utility": "Duke Energy", "county": county,
        "customer_count": customers, "lat": 28.5, "lon": -81.4,
        "cause": "Equipment", "cause_category": "equipment",
    }
    db.sync_duke_incident_events([record], timestamp=start)
    db.sync_duke_incident_events([], timestamp=end)


class TestDukeRestorationPrecedent:
    """
    duke_restoration_precedent() - added 2026-07-18, the same underlying
    idea as fpl_ordinary_restoration_stats() but for Duke, which - unlike
    FPL - already reports real, individually-tracked incidents, so it
    doesn't need FPL's outlier-exclusion filter (confirmed against real
    data before building: 7,195 real closed incidents statewide, only 1
    over 48 hours, none over 96).
    """

    def test_no_data_for_county_returns_none(self, db_path):
        db = OutageDatabase(db_path)
        result = cs.duke_restoration_precedent("Orange", db)
        db.close()

        assert result is None

    def test_single_incident_computes_stats_and_is_flagged_limited(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_duke_incident(db, "D1", "Orange", "2026-01-01T00:00:00", "2026-01-01T03:00:00")

        result = cs.duke_restoration_precedent("Orange", db)
        db.close()

        assert result["n"] == 1
        assert result["min_hours"] == 3.0
        assert result["median_hours"] == 3.0
        assert result["max_hours"] == 3.0
        assert result["limited"] is True

    def test_reaching_the_confident_threshold_clears_the_limited_flag(self, db_path):
        db = OutageDatabase(db_path)
        for i in range(cs.MIN_EVENTS_FOR_CONFIDENT_RANGE):
            _open_and_close_duke_incident(db, f"D{i}", "Pinellas", f"2026-01-0{i + 1}T00:00:00", f"2026-01-0{i + 1}T02:00:00")

        result = cs.duke_restoration_precedent("Pinellas", db)
        db.close()

        assert result["n"] == cs.MIN_EVENTS_FOR_CONFIDENT_RANGE
        assert result["limited"] is False

    def test_multiple_incidents_compute_real_min_median_max(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_duke_incident(db, "D1", "Pinellas", "2026-01-01T00:00:00", "2026-01-01T01:00:00")
        _open_and_close_duke_incident(db, "D2", "Pinellas", "2026-01-02T00:00:00", "2026-01-02T03:00:00")
        _open_and_close_duke_incident(db, "D3", "Pinellas", "2026-01-03T00:00:00", "2026-01-04T00:00:00")

        result = cs.duke_restoration_precedent("Pinellas", db)
        db.close()

        assert result["n"] == 3
        assert result["min_hours"] == 1.0
        assert result["median_hours"] == 3.0
        assert result["max_hours"] == 24.0

    def test_county_name_match_is_case_insensitive(self, db_path):
        db = OutageDatabase(db_path)
        _open_and_close_duke_incident(db, "D1", "PINELLAS", "2026-01-01T00:00:00", "2026-01-01T03:00:00")

        result = cs.duke_restoration_precedent("Pinellas", db)
        db.close()

        assert result is not None

    def test_other_utilities_in_the_same_county_are_ignored(self, db_path):
        db = OutageDatabase(db_path)
        db.log_teco_incidents([{
            "incident_id": "T1", "utility": "Tampa Electric Company", "status": "On our way",
            "status_category": "investigating", "reason": "Tree down", "reason_category": "vegetation",
            "customer_count": 50, "lat": 27.9, "lon": -82.4, "county": "Orange",
            "update_time": "2026-01-01T00:00:00", "estimated_restoration": "2026-01-01T06:00:00",
        }])
        db.sync_teco_incident_events([{
            "incident_id": "T1", "utility": "Tampa Electric Company", "status": "On our way",
            "status_category": "investigating", "reason": "Tree down", "reason_category": "vegetation",
            "customer_count": 50, "lat": 27.9, "lon": -82.4, "county": "Orange",
            "update_time": "2026-01-01T00:00:00", "estimated_restoration": "2026-01-01T06:00:00",
        }], timestamp="2026-01-01T00:00:00")

        result = cs.duke_restoration_precedent("Orange", db)
        db.close()

        assert result is None

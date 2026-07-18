"""
Tests for _incident_label() in dashboard.py - the "rhetorical naming"
fix added 2026-07-12, same spirit as the humanize timestamp filter.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import (
    _incident_label, _explain_pipeline_error, _group_pipeline_errors,
    _is_pipeline_error_ongoing, _normalize_open_events, _rows_for_county,
    _paginate, _split_chronic_errors, _summarize_chronic_errors,
    _build_unified_view, COUNTY_PICKER_CHOICES,
)


class TestBuildUnifiedView:
    """
    _build_unified_view() - added 2026-07-18 after finding it had no
    direct test at all, only indirect coverage via the "/" route. Real
    regression covered here: fpuc_open_incidents (FPUC's real per-
    incident view, distinct from its combined-territory total) was
    never passed into this function at all, so it never contributed to
    the dashboard's own "Customers Out Right Now"/"Open Incidents" KPIs
    - found by comparing this page's total against the public site's
    own total on the same live moment and getting different numbers.
    Silent until then only because FPUC's incident-level view happened
    to have zero open incidents every time this was checked before.
    """

    def _row(self, utility="FPL", county="Alachua", customers=10, peak=20,
              customers_key="current_customers_out", peak_key="peak_customers_out"):
        return {
            "utility": utility, "county": county,
            customers_key: customers, peak_key: peak,
            "start_time": "2026-01-01T00:00:00", "duration": "1h",
        }

    def test_fpuc_open_incidents_are_included_in_the_unified_total(self):
        fpuc_incident_row = self._row(
            utility="Florida Public Utilities Corporation", county="Nassau",
            customers=15, peak=15,
            customers_key="current_customer_count", peak_key="peak_customer_count",
        )
        unified = _build_unified_view(
            [], [], [], [], [], [], [], [fpuc_incident_row], [], [], [], [], [], [], [], [], [],
        )

        assert len(unified) == 1
        assert unified[0]["utility"] == "Florida Public Utilities Corporation"
        assert unified[0]["county"] == "Nassau"
        assert unified[0]["customers"] == 15
        assert unified[0]["peak_customers"] == 15

    def test_fpuc_incidents_and_combined_events_both_count_independently(self):
        combined_row = self._row(utility="Florida Public Utilities Corporation", county="Multiple Counties (NW FL & Nassau)", customers=5, peak=5)
        incident_row = self._row(
            utility="Florida Public Utilities Corporation", county="Nassau",
            customers=15, peak=15,
            customers_key="current_customer_count", peak_key="peak_customer_count",
        )
        unified = _build_unified_view(
            [], [], [], [], [], [], [combined_row], [incident_row], [], [], [], [], [], [], [], [], [],
        )

        assert len(unified) == 2
        assert sum(row["customers"] for row in unified) == 20

    def test_empty_everything_returns_empty_list(self):
        unified = _build_unified_view([], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [])
        assert unified == []


class TestIncidentLabel:
    def test_duke_shaped_id_shows_just_the_daily_sequence_number(self):
        # Confirmed against real data 2026-07-12: Duke's incident_id is
        # literally YYYYMMDD + a 6-digit per-day sequence number - the
        # date half is redundant with the row's own "Started" column.
        assert _incident_label("20260712000423") == "Incident #423"

    def test_duke_shaped_id_strips_leading_zeros_from_sequence(self):
        assert _incident_label("20260703000005") == "Incident #5"

    def test_teco_shaped_id_passed_through_unchanged(self):
        # TECO's id doesn't decode to a date at all - it's a large,
        # steadily-incrementing enterprise ticket counter (confirmed by
        # growth rate against real data), so there's nothing real to
        # translate.
        assert _incident_label("A202619308291") == "A202619308291"

    def test_14_digit_but_not_a_real_date_passed_through_unchanged(self):
        # Structural detection (14 digits) alone isn't enough - the
        # first 8 digits have to actually parse as a real calendar date,
        # or this isn't really Duke's shape and shouldn't be reformatted.
        assert _incident_label("99999999000423") == "99999999000423"

    def test_none_passed_through(self):
        assert _incident_label(None) is None

    def test_empty_string_passed_through(self):
        assert _incident_label("") == ""


class TestExplainPipelineError:
    """
    _explain_pipeline_error() - the plain-English translation layer for
    /pipeline-errors, added 2026-07-13 alongside the drill-down page
    itself. Same non-destructive spirit as fetch_teco_outages.py's
    reason/status categorization: never replaces the raw message, just
    derives a label/explanation/severity to show alongside it.
    """

    def test_generic_fetch_failed_message_recognized(self):
        # The message main.py's run_X_cycle() functions raise when a
        # fetch genuinely fails (2026-07-13 pipeline-visibility fix) -
        # doesn't carry the original specific network error, so this
        # exists to avoid it falling into the "other/uncommon" bucket.
        label, explanation, severity = _explain_pipeline_error(
            "PRECO fetch returned no records - see the poller's own log for the underlying request error"
        )
        assert label == "fetch-failed"
        assert severity == "warn"

    def test_database_locked_is_info_severity(self):
        label, explanation, severity = _explain_pipeline_error("database is locked")
        assert label == "database-lock"
        assert severity == "info"
        assert "database" in explanation.lower()

    def test_read_timeout_is_warn_severity(self):
        # Real shape this project has actually seen: requests' own
        # ConnectionError/Timeout message text wraps a pool repr around
        # the literal phrase "Read timed out."
        label, explanation, severity = _explain_pipeline_error(
            "HTTPSConnectionPool(host='example.com', port=443): Read timed out. (read timeout=15)"
        )
        assert label == "timeout"
        assert severity == "warn"

    def test_connection_refused_is_crit_severity(self):
        label, explanation, severity = _explain_pipeline_error(
            "Failed to establish a new connection: [Errno 61] Connection refused"
        )
        assert label == "connection"
        assert severity == "crit"

    def test_rate_limit_status_code_detected(self):
        label, explanation, severity = _explain_pipeline_error("429 Client Error: Too Many Requests")
        assert label == "rate-limited"
        assert severity == "warn"

    def test_server_error_status_code_detected(self):
        label, explanation, severity = _explain_pipeline_error("503 Server Error: Service Unavailable")
        assert label == "server-error"
        assert severity == "warn"

    def test_json_decode_error_flagged_as_format_change(self):
        label, explanation, severity = _explain_pipeline_error("Expecting value: line 1 column 1 (char 0)")
        assert label == "unexpected-format"
        assert severity == "crit"

    def test_unrecognized_message_falls_back_honestly(self):
        label, explanation, severity = _explain_pipeline_error("some completely novel error nobody has seen")
        assert label == "other"
        assert "raw message" in explanation.lower()

    def test_empty_message_handled(self):
        label, explanation, severity = _explain_pipeline_error("")
        assert label == "unknown"
        assert severity == "info"

    def test_none_message_handled(self):
        label, explanation, severity = _explain_pipeline_error(None)
        assert label == "unknown"


def _error_row(source, timestamp, message="database is locked"):
    return {"source": source, "timestamp": timestamp, "error_message": message}


class TestGroupPipelineErrors:
    """
    _group_pipeline_errors() - collapses consecutive same-source
    failures into one streak (first occurrence -> last occurrence)
    added 2026-07-13 after the /pipeline-errors page was found to show
    a redundant "date + time ago" pair on every individual row instead
    of anything about how long a failure actually persisted.
    """

    def test_single_error_is_its_own_group_of_one(self):
        groups = _group_pipeline_errors([_error_row("fpl", "2026-01-01T00:00:00")])
        assert len(groups) == 1
        assert groups[0]["count"] == 1
        assert groups[0]["first_timestamp"] == groups[0]["last_timestamp"] == "2026-01-01T00:00:00"

    def test_close_together_same_source_errors_merge_into_one_streak(self):
        # 15-min poll cycle - two failures 10 minutes apart are almost
        # certainly the same ongoing episode.
        rows = [
            _error_row("fpl", "2026-01-01T00:00:00"),
            _error_row("fpl", "2026-01-01T00:10:00"),
        ]
        groups = _group_pipeline_errors(rows)
        assert len(groups) == 1
        assert groups[0]["count"] == 2
        assert groups[0]["first_timestamp"] == "2026-01-01T00:00:00"
        assert groups[0]["last_timestamp"] == "2026-01-01T00:10:00"

    def test_far_apart_same_source_errors_stay_separate_streaks(self):
        rows = [
            _error_row("fpl", "2026-01-01T00:00:00"),
            _error_row("fpl", "2026-01-01T05:00:00"),
        ]
        groups = _group_pipeline_errors(rows)
        assert len(groups) == 2
        assert all(g["count"] == 1 for g in groups)

    def test_different_sources_never_merge_even_if_simultaneous(self):
        rows = [
            _error_row("fpl", "2026-01-01T00:00:00"),
            _error_row("preco", "2026-01-01T00:00:00"),
        ]
        groups = _group_pipeline_errors(rows)
        assert len(groups) == 2
        assert {g["source"] for g in groups} == {"fpl", "preco"}

    def test_latest_message_in_streak_is_the_most_recent_one(self):
        rows = [
            _error_row("fpl", "2026-01-01T00:00:00", "connection refused"),
            _error_row("fpl", "2026-01-01T00:05:00", "database is locked"),
        ]
        groups = _group_pipeline_errors(rows)
        assert groups[0]["latest_message"] == "database is locked"

    def test_groups_sorted_most_recent_streak_first(self):
        rows = [
            _error_row("fpl", "2026-01-01T00:00:00"),
            _error_row("preco", "2026-01-01T05:00:00"),
        ]
        groups = _group_pipeline_errors(rows)
        assert groups[0]["source"] == "preco"
        assert groups[1]["source"] == "fpl"

    def test_empty_input_returns_empty(self):
        assert _group_pipeline_errors([]) == []


class TestIsPipelineErrorOngoing:
    """
    _is_pipeline_error_ongoing() - added 2026-07-14 after Talquin/PRECO's
    real, still-active Siena outage exposed that /pipeline-errors showed
    no distinction between "this happened once, long ago" and "this is
    happening right now" - both read in identical past tense
    ("occurred over 6h24m").
    """

    def test_recent_failure_is_ongoing(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        last_timestamp = (now - timedelta(minutes=5)).isoformat()
        assert _is_pipeline_error_ongoing(last_timestamp, now=now) is True

    def test_failure_right_at_the_gap_threshold_is_ongoing(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        last_timestamp = (now - timedelta(minutes=20)).isoformat()
        assert _is_pipeline_error_ongoing(last_timestamp, now=now) is True

    def test_failure_past_the_gap_threshold_is_not_ongoing(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        last_timestamp = (now - timedelta(minutes=21)).isoformat()
        assert _is_pipeline_error_ongoing(last_timestamp, now=now) is False

    def test_long_past_failure_is_not_ongoing(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        last_timestamp = (now - timedelta(hours=6, minutes=24)).isoformat()
        assert _is_pipeline_error_ongoing(last_timestamp, now=now) is False


class TestPaginate:
    """
    _paginate() - added 2026-07-14 so /pipeline-errors's streak list
    doesn't render as one unbounded page as real history accumulates
    (Talquin/PRECO's ongoing failures were the concrete trigger).
    """

    def test_first_page_returns_first_slice(self):
        items = list(range(25))
        result = _paginate(items, page=1, per_page=10)
        assert result["items"] == list(range(10))
        assert result["page"] == 1
        assert result["total_pages"] == 3
        assert result["total"] == 25
        assert result["has_prev"] is False
        assert result["has_next"] is True

    def test_middle_page_returns_middle_slice(self):
        items = list(range(25))
        result = _paginate(items, page=2, per_page=10)
        assert result["items"] == list(range(10, 20))
        assert result["has_prev"] is True
        assert result["has_next"] is True

    def test_last_page_returns_partial_slice(self):
        items = list(range(25))
        result = _paginate(items, page=3, per_page=10)
        assert result["items"] == list(range(20, 25))
        assert result["has_prev"] is True
        assert result["has_next"] is False

    def test_exact_multiple_has_no_trailing_empty_page(self):
        items = list(range(20))
        result = _paginate(items, page=2, per_page=10)
        assert result["total_pages"] == 2
        assert result["has_next"] is False

    def test_page_number_above_range_clamps_to_last_page(self):
        items = list(range(25))
        result = _paginate(items, page=999, per_page=10)
        assert result["page"] == 3
        assert result["items"] == list(range(20, 25))

    def test_page_number_below_one_clamps_to_first_page(self):
        items = list(range(25))
        result = _paginate(items, page=0, per_page=10)
        assert result["page"] == 1
        assert result["items"] == list(range(10))

    def test_empty_list_returns_one_empty_page_not_zero_pages(self):
        result = _paginate([], page=1, per_page=10)
        assert result["items"] == []
        assert result["total_pages"] == 1
        assert result["page"] == 1
        assert result["has_prev"] is False
        assert result["has_next"] is False


class TestSplitChronicErrors:
    """
    _split_chronic_errors() - added 2026-07-17 so Talquin/PRECO's real
    but already-understood recurring credential failures don't dominate
    /pipeline-errors's combined "all sources" view and bury genuinely
    rare failures from every other source.
    """

    def _err(self, source):
        return {"source": source}

    def test_chronic_sources_pulled_into_their_own_list(self):
        errors = [self._err("talquin"), self._err("fpl"), self._err("preco"), self._err("duke")]
        chronic, other = _split_chronic_errors(errors, {"talquin", "preco"}, limit=10)

        assert chronic == [self._err("talquin"), self._err("preco")]
        assert other == [self._err("fpl"), self._err("duke")]

    def test_no_chronic_sources_present_leaves_other_unchanged(self):
        errors = [self._err("fpl"), self._err("duke")]
        chronic, other = _split_chronic_errors(errors, {"talquin", "preco"}, limit=10)

        assert chronic == []
        assert other == errors

    def test_chronic_list_is_capped_at_limit(self):
        errors = [self._err("talquin") for _ in range(15)]
        chronic, other = _split_chronic_errors(errors, {"talquin", "preco"}, limit=10)

        assert len(chronic) == 10
        assert other == []

    def test_order_is_preserved_within_each_list(self):
        errors = [self._err("fpl"), self._err("talquin"), self._err("duke"), self._err("preco")]
        chronic, other = _split_chronic_errors(errors, {"talquin", "preco"}, limit=10)

        assert chronic == [self._err("talquin"), self._err("preco")]
        assert other == [self._err("fpl"), self._err("duke")]


class TestSummarizeChronicErrors:
    """
    _summarize_chronic_errors() - collapses a chronic source's full streak
    list down to one compact line (count + latest streak) for the
    collapsed-by-default /pipeline-errors summary, added 2026-07-17.
    """

    def _err(self, source, display_name, is_ongoing=False, last_timestamp="2026-07-17T12:00:00"):
        return {
            "source": source,
            "display_name": display_name,
            "is_ongoing": is_ongoing,
            "last_timestamp": last_timestamp,
        }

    def test_empty_input_returns_empty_list(self):
        assert _summarize_chronic_errors([]) == []

    def test_single_source_single_streak(self):
        errors = [self._err("talquin", "Talquin Electric Cooperative")]
        result = _summarize_chronic_errors(errors)

        assert len(result) == 1
        assert result[0]["display_name"] == "Talquin Electric Cooperative"
        assert result[0]["streak_count"] == 1
        assert result[0]["latest"] == errors[0]

    def test_counts_all_streaks_for_the_same_source(self):
        errors = [
            self._err("talquin", "Talquin Electric Cooperative"),
            self._err("talquin", "Talquin Electric Cooperative"),
            self._err("talquin", "Talquin Electric Cooperative"),
        ]
        result = _summarize_chronic_errors(errors)

        assert len(result) == 1
        assert result[0]["streak_count"] == 3

    def test_first_occurrence_per_source_is_treated_as_latest(self):
        newest = self._err("talquin", "Talquin Electric Cooperative", is_ongoing=True, last_timestamp="2026-07-17T18:00:00")
        older = self._err("talquin", "Talquin Electric Cooperative", is_ongoing=False, last_timestamp="2026-07-17T06:00:00")
        result = _summarize_chronic_errors([newest, older])

        assert result[0]["latest"] == newest

    def test_multiple_sources_each_get_their_own_summary(self):
        errors = [
            self._err("talquin", "Talquin Electric Cooperative"),
            self._err("preco", "Peace River Electric Cooperative"),
            self._err("talquin", "Talquin Electric Cooperative"),
        ]
        result = _summarize_chronic_errors(errors)

        by_source = {e["latest"]["source"]: e for e in result}
        assert len(result) == 2
        assert by_source["talquin"]["streak_count"] == 2
        assert by_source["preco"]["streak_count"] == 1


class TestCountyPickerChoices:
    """
    COUNTY_PICKER_CHOICES - the /county page's dropdown list, added
    2026-07-14. Built from historical_import.FLORIDA_COUNTIES (all-caps)
    title-cased for display, with the one known exception .title() gets
    wrong on its own.
    """

    def test_has_all_67_real_counties(self):
        assert len(COUNTY_PICKER_CHOICES) == 67

    def test_desoto_keeps_internal_capital_not_titlecased_naively(self):
        # .title() alone would produce "Desoto", not "DeSoto" - same
        # casing bug already caught once in fetch_preco_outages.py.
        assert "DeSoto" in COUNTY_PICKER_CHOICES
        assert "Desoto" not in COUNTY_PICKER_CHOICES

    def test_hyphenated_and_multiword_names_handled_by_title(self):
        assert "Miami-Dade" in COUNTY_PICKER_CHOICES
        assert "St. Johns" in COUNTY_PICKER_CHOICES
        assert "Palm Beach" in COUNTY_PICKER_CHOICES

    def test_sorted_alphabetically(self):
        assert COUNTY_PICKER_CHOICES == sorted(COUNTY_PICKER_CHOICES)


class TestNormalizeOpenEvents:
    """
    _normalize_open_events() - factors out the same per-source field
    mapping _build_unified_view() does inline, so /county's per-source
    aggregation doesn't duplicate each source's field names a second
    time. Added 2026-07-14 alongside the /county page.
    """

    def test_maps_custom_field_names_to_common_shape(self):
        events = [{
            "utility": "Tampa Electric Company", "county": "Hillsborough",
            "current_customer_count": 10, "peak_customer_count": 25,
            "start_time": "2026-01-01T00:00:00",
        }]
        rows = _normalize_open_events(events, "current_customer_count", "peak_customer_count")
        assert len(rows) == 1
        row = rows[0]
        assert row["utility"] == "Tampa Electric Company"
        assert row["county"] == "Hillsborough"
        assert row["customers"] == 10
        assert row["peak_customers"] == 25
        assert row["start_time"] == "2026-01-01T00:00:00"
        assert row["duration"]  # computed fresh from start_time, just check it's present

    def test_empty_input_returns_empty(self):
        assert _normalize_open_events([], "customers_out", "peak_customers_out") == []


class TestRowsForCounty:
    """
    _rows_for_county() - the /county page's matching logic, added
    2026-07-14. Reuses correlate.py's _county_in_alert() (a normalized
    substring check) for both real single-county rows and combined-
    territory labels, verified safe since no two of Florida's 67 real
    county names are substrings of each other.
    """

    def _row(self, county, utility="Test Utility"):
        return {"utility": utility, "county": county, "customers": 1,
                "peak_customers": 1, "start_time": "2026-01-01T00:00:00", "duration": "1h"}

    def test_exact_real_county_matches(self):
        rows = [self._row("Duval")]
        assert _rows_for_county(rows, "Duval") == rows

    def test_real_county_does_not_match_a_different_county(self):
        rows = [self._row("Duval")]
        assert _rows_for_county(rows, "Leon") == []

    def test_combined_territory_label_matches_a_named_county_within_it(self):
        rows = [self._row("Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)", "TCEC")]
        assert _rows_for_county(rows, "Jefferson") == rows
        assert _rows_for_county(rows, "Leon") == rows

    def test_combined_territory_label_does_not_match_an_unrelated_county(self):
        rows = [self._row("Escambia/Santa Rosa", "EREC")]
        assert _rows_for_county(rows, "Duval") == []

    def test_row_with_no_county_never_matches(self):
        rows = [self._row(None)]
        assert _rows_for_county(rows, "Duval") == []

    def test_empty_rows_returns_empty(self):
        assert _rows_for_county([], "Duval") == []

"""
Tests for _incident_label() in dashboard.py - the "rhetorical naming"
fix added 2026-07-12, same spirit as the humanize timestamp filter.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import _incident_label, _explain_pipeline_error, _group_pipeline_errors


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

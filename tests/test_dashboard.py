"""
Tests for _incident_label() in dashboard.py - the "rhetorical naming"
fix added 2026-07-12, same spirit as the humanize timestamp filter.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import _incident_label


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

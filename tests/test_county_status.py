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


class TestCountyPickerChoicesSharedCorrectly:
    def test_has_all_67_real_counties(self):
        assert len(cs.COUNTY_PICKER_CHOICES) == 67

    def test_desoto_casing_special_case_preserved(self):
        assert "DeSoto" in cs.COUNTY_PICKER_CHOICES
        assert "Desoto" not in cs.COUNTY_PICKER_CHOICES

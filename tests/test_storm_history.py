"""
Tests for storm_history.fpl_restoration_precedent() - added 2026-07-18
as the first piece of Phase 3's FPL historical-precedent restoration
model (see docs/ROADMAP.md). FPL's live feed can never support real
incident-level restoration modeling, so this is the only honest
restoration signal this project can give for FPL counties: "storms
like this have historically taken about this long to restore here,"
computed from the 17-storm PSC archive, not a live prediction.
"""

import os
import sqlite3
import tempfile

import pytest

import storm_history


@pytest.fixture
def historical_db_path(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE historical_outage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            storm_name TEXT, storm_year INTEGER, utility TEXT, county TEXT,
            start_time TEXT, end_time TEXT,
            peak_customers_out INTEGER, peak_percentage_out REAL, customers_served INTEGER
        )
    ''')
    conn.commit()
    conn.close()

    monkeypatch.setattr(storm_history, "HISTORICAL_DB_PATH", path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _insert(path, county, start_time, end_time, utility="Florida Power and Light Company",
            storm_name="Test Storm", storm_year=2024):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO historical_outage_events (storm_name, storm_year, utility, county, start_time, end_time) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (storm_name, storm_year, utility, county, start_time, end_time),
    )
    conn.commit()
    conn.close()


class TestFplRestorationPrecedent:
    def test_no_data_for_county_returns_none(self, historical_db_path):
        assert storm_history.fpl_restoration_precedent("Alachua") is None

    def test_single_storm_computes_stats_and_is_flagged_limited(self, historical_db_path):
        _insert(historical_db_path, "HARDEE", "2024-10-01T00:00:00", "2024-10-02T18:00:00")

        result = storm_history.fpl_restoration_precedent("Hardee")

        assert result["n"] == 1
        assert result["min_hours"] == 42.0
        assert result["median_hours"] == 42.0
        assert result["max_hours"] == 42.0
        assert result["limited"] is True

    def test_county_name_match_is_case_insensitive(self, historical_db_path):
        _insert(historical_db_path, "ALACHUA", "2024-10-01T00:00:00", "2024-10-01T12:00:00")

        assert storm_history.fpl_restoration_precedent("Alachua") is not None

    def test_multiple_storms_compute_real_min_median_max(self, historical_db_path):
        _insert(historical_db_path, "BREVARD", "2024-01-01T00:00:00", "2024-01-01T03:00:00", storm_name="A")  # 3h
        _insert(historical_db_path, "BREVARD", "2024-02-01T00:00:00", "2024-02-03T06:00:00", storm_name="B")  # 54h
        _insert(historical_db_path, "BREVARD", "2024-03-01T00:00:00", "2024-03-08T15:00:00", storm_name="C")  # 183h

        result = storm_history.fpl_restoration_precedent("Brevard")

        assert result["n"] == 3
        assert result["min_hours"] == 3.0
        assert result["median_hours"] == 54.0
        assert result["max_hours"] == 183.0
        assert result["limited"] is False

    def test_reaching_the_confident_threshold_clears_the_limited_flag(self, historical_db_path):
        for i in range(storm_history.MIN_STORMS_FOR_CONFIDENT_RANGE):
            _insert(historical_db_path, "DUVAL", f"2024-0{i + 1}-01T00:00:00", f"2024-0{i + 1}-01T10:00:00", storm_name=f"S{i}")

        assert storm_history.fpl_restoration_precedent("Duval")["limited"] is False

    def test_other_utilities_in_the_same_county_are_ignored(self, historical_db_path):
        _insert(historical_db_path, "LEON", "2024-10-01T00:00:00", "2024-10-05T00:00:00", utility="Duke Energy")

        assert storm_history.fpl_restoration_precedent("Leon") is None

    def test_malformed_timestamps_are_skipped_not_a_crash(self, historical_db_path):
        _insert(historical_db_path, "OSCEOLA", None, None)
        _insert(historical_db_path, "OSCEOLA", "2024-10-01T00:00:00", "2024-10-01T12:00:00")

        result = storm_history.fpl_restoration_precedent("Osceola")

        assert result["n"] == 1
        assert result["median_hours"] == 12.0

    def test_zero_or_negative_duration_rows_are_excluded(self, historical_db_path):
        # A real data-entry quirk in the PSC reports - a start/end pair
        # that doesn't represent a real positive-length restoration.
        _insert(historical_db_path, "POLK", "2024-10-01T12:00:00", "2024-10-01T12:00:00")
        _insert(historical_db_path, "POLK", "2024-10-01T00:00:00", "2024-10-01T06:00:00")

        result = storm_history.fpl_restoration_precedent("Polk")

        assert result["n"] == 1
        assert result["median_hours"] == 6.0

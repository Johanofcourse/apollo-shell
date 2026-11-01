"""
Tests for apollo_shell/business_intelligence.py - the statewide,
cross-utility rollups behind the public Sentinel site's "Business
Intelligence" page.
"""

import os
import tempfile

import pytest

import business_intelligence as bi
from database import OutageDatabase


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


class TestPercentile:
    def test_empty_returns_none(self):
        assert bi._percentile([], 50) is None

    def test_single_value_is_itself_at_any_percentile(self):
        assert bi._percentile([7.0], 50) == 7.0
        assert bi._percentile([7.0], 90) == 7.0

    def test_interpolates_between_the_two_nearest_ranks(self):
        assert bi._percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5


class TestOutageFrequencyByUtility:
    def test_sorted_most_frequent_first_with_real_display_names(self, db_path):
        db = OutageDatabase(db_path)
        conn = db.connect()
        conn.execute(
            "INSERT INTO outage_events (utility, county, start_time, end_time, "
            "peak_customers_out, peak_percentage_out, customers_served) VALUES (?, ?, ?, ?, 1, 1.0, 100)",
            ("FPL", "Hillsborough", "2026-07-03T10:00:00", "2026-07-03T12:00:00"),
        )
        for incident_id in ("a", "b"):
            conn.execute(
                "INSERT INTO teco_incident_events (incident_id, utility, county, start_time, end_time) "
                "VALUES (?, ?, ?, ?, ?)",
                (incident_id, "TECO", "Hillsborough", "2026-07-20T10:00:00", "2026-07-20T12:00:00"),
            )
        conn.commit()

        results = bi.outage_frequency_by_utility(db)
        assert results[0] == {"source": "teco", "display_name": "TECO", "count": 2}
        assert results[1] == {"source": "fpl", "display_name": "FPL", "count": 1}


class TestRestorationDurationByUtility:
    def test_excludes_implausibly_long_single_events_and_flags_limited(self, db_path):
        db = OutageDatabase(db_path)
        conn = db.connect()
        conn.execute(
            "INSERT INTO outage_events (utility, county, start_time, end_time, "
            "peak_customers_out, peak_percentage_out, customers_served) VALUES (?, ?, ?, ?, 1, 1.0, 100)",
            ("FPL", "Hillsborough", "2026-07-01T00:00:00", "2026-07-01T02:00:00"),
        )
        conn.execute(
            "INSERT INTO outage_events (utility, county, start_time, end_time, "
            "peak_customers_out, peak_percentage_out, customers_served) VALUES (?, ?, ?, ?, 1, 1.0, 100)",
            ("FPL", "Hillsborough", "2026-07-02T00:00:00", "2026-07-10T00:00:00"),
        )
        conn.commit()

        results = bi.restoration_duration_by_utility(db)
        assert len(results) == 1
        fpl = results[0]
        assert fpl["source"] == "fpl"
        assert fpl["n"] == 1
        assert fpl["median_hours"] == 2.0
        assert fpl["excluded_count"] == 1
        assert fpl["limited"] is True

    def test_longest_median_sorts_first(self, db_path):
        db = OutageDatabase(db_path)
        conn = db.connect()
        conn.execute(
            "INSERT INTO outage_events (utility, county, start_time, end_time, "
            "peak_customers_out, peak_percentage_out, customers_served) VALUES (?, ?, ?, ?, 1, 1.0, 100)",
            ("FPL", "Hillsborough", "2026-07-01T00:00:00", "2026-07-01T01:00:00"),
        )
        conn.execute(
            "INSERT INTO teco_incident_events (incident_id, utility, county, start_time, end_time) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a", "TECO", "Hillsborough", "2026-07-01T00:00:00", "2026-07-01T05:00:00"),
        )
        conn.commit()

        results = bi.restoration_duration_by_utility(db)
        assert [r["source"] for r in results] == ["teco", "fpl"]


class TestEtrAccuracyByUtility:
    def test_skips_sources_with_no_usable_data_and_sorts_most_honest_first(self, db_path, monkeypatch):
        db = OutageDatabase(db_path)
        monkeypatch.setattr(bi, "_ETR_ACCURACY_FUNCTIONS", {
            "teco": lambda db: {"n": 10, "median_error_hours": -1.0, "on_time_pct": 90.0, "limited": False},
            "fpl": lambda db: {"n": 10, "median_error_hours": 2.0, "on_time_pct": 40.0, "limited": False},
            "lwbu": lambda db: None,
            "clay": lambda db: {"n": 2, "median_error_hours": 0.5, "on_time_pct": 50.0, "limited": True},
        })

        results = bi.etr_accuracy_by_utility(db)
        assert [r["source"] for r in results] == ["teco", "clay", "fpl"]
        assert results[0]["display_name"] == "TECO"


class TestUtilityCauseBreakdown:
    def test_no_real_cause_categories_are_tracked_separately_from_the_mix(self, db_path):
        db = OutageDatabase(db_path)
        conn = db.connect()
        rows = [
            ("a", "equipment"), ("b", "equipment"), ("c", "pending"),
            ("d", "vehicle"), ("e", "animal"),
        ]
        for incident_id, reason_category in rows:
            conn.execute(
                "INSERT INTO teco_incident_events "
                "(incident_id, utility, county, start_time, end_time, reason_category) "
                "VALUES (?, 'TECO', 'Hillsborough', '2026-07-01T00:00:00', '2026-07-01T01:00:00', ?)",
                (incident_id, reason_category),
            )
        conn.commit()

        breakdown = bi.utility_cause_breakdown(db)
        teco_transparency = next(r for r in breakdown["transparency"] if r["source"] == "teco")
        assert teco_transparency["n_total"] == 5
        assert teco_transparency["n_real_cause"] == 4
        assert teco_transparency["real_cause_pct"] == 80.0

        assert breakdown["mix"]["teco"] == {"equipment": 2, "other_cause": 2}

    def test_most_transparent_utility_sorts_first(self, db_path):
        db = OutageDatabase(db_path)
        conn = db.connect()
        conn.execute(
            "INSERT INTO teco_incident_events "
            "(incident_id, utility, county, start_time, end_time, reason_category) "
            "VALUES ('a', 'TECO', 'Hillsborough', '2026-07-01T00:00:00', '2026-07-01T01:00:00', 'equipment')"
        )
        conn.execute(
            "INSERT INTO duke_incident_events "
            "(incident_id, utility, county, start_time, end_time, cause_category) "
            "VALUES ('b', 'Duke', 'Hillsborough', '2026-07-01T00:00:00', '2026-07-01T01:00:00', 'other')"
        )
        conn.commit()

        breakdown = bi.utility_cause_breakdown(db)
        assert [r["source"] for r in breakdown["transparency"]] == ["teco", "duke"]

"""
Tests for weather_match_confidence() in correlate.py - the event-type
plausibility logic, and the Excessive Heat Warning / Heat Advisory split
added 2026-07-08 (sustained extreme heat has a real grid-strain
mechanism; a routine hot day doesn't) - plus find_correlations()/
find_jea_correlations()'s customers_out > 0 filter, added 2026-07-12.
"""

import os
import tempfile

import pytest

from correlate import weather_match_confidence, find_correlations, find_jea_correlations
from database import OutageDatabase


class TestWeatherMatchConfidence:
    def test_high_plausibility_severe_stays_high(self):
        assert weather_match_confidence("Tornado Warning", "Severe") == "high"

    def test_high_plausibility_minor_severity_downgrades_to_medium(self):
        # Severity is only ever a secondary modifier - a weak reading of
        # an otherwise-plausible event type doesn't drop all the way to low
        assert weather_match_confidence("Tornado Warning", "Minor") == "medium"

    def test_low_plausibility_never_promoted_by_severity(self):
        # The whole point of this design: a "Severe" rating on an event
        # type with no physical connection to power outages must never
        # outrank a genuinely plausible one
        assert weather_match_confidence("Rip Current Statement", "Severe") == "low"
        assert weather_match_confidence("Rip Current Statement", "Extreme") == "low"

    def test_medium_plausibility_needs_real_severity_to_count(self):
        assert weather_match_confidence("Wind Advisory", "Moderate") == "medium"
        assert weather_match_confidence("Wind Advisory", "Minor") == "low"

    def test_unrecognized_event_type_defaults_to_medium_plausibility(self):
        # An unfamiliar type shouldn't be assumed confidently relevant OR
        # confidently irrelevant - this is a deliberate default, not a gap
        assert weather_match_confidence("Some Brand New Alert Type", "Severe") == "medium"

    def test_excessive_heat_warning_vs_heat_advisory_split(self):
        # 2026-07-08: sustained extreme heat has a real, if indirect,
        # grid-strain mechanism (peak AC demand, equipment thermal
        # stress) - distinct from routine hot weather, which stays low
        # regardless of NWS's severity rating.
        assert weather_match_confidence("Heat Advisory", "Severe") == "low"
        assert weather_match_confidence("Heat Advisory", "Extreme") == "low"
        assert weather_match_confidence("Excessive Heat Warning", "Severe") == "medium"
        assert weather_match_confidence("Excessive Heat Warning", "Minor") == "low"


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _weather_alert(county="ALACHUA"):
    return [{
        "id": "test-alert-1", "event": "Heat Advisory", "severity": "Severe",
        "urgency": "Expected", "areas": county,
        "effective": "2026-01-01T00:00:00", "expires": "2026-01-01T23:59:59",
        "headline": "test", "description": "test",
    }]


class TestFindCorrelationsRealOutageFilter:
    """
    2026-07-12: find_correlations()/find_jea_correlations() used to match
    every raw snapshot regardless of whether an actual outage was
    happening - the raw tables log a fresh row every poll cycle for every
    county/ZIP whether or not anything was wrong, so a weather alert
    merely being active while nothing was happening counted as a
    "correlated outage." Checked directly against real data: this was
    inflating FPL's match count by ~59% and JEA's by ~84%.
    """

    def test_zero_customer_snapshot_is_not_a_match(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert())
        db.log_multiple_outages(
            "FPL", [{"county": "ALACHUA", "customers_out": 0, "customers_served": 1000}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        matches = find_correlations(db_path)
        assert matches == []

    def test_real_outage_snapshot_still_matches(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert())
        db.log_multiple_outages(
            "FPL", [{"county": "ALACHUA", "customers_out": 50, "customers_served": 1000}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        matches = find_correlations(db_path)
        assert len(matches) == 1
        assert matches[0]["outage"]["customers_out"] == 50

    def test_jea_zero_customer_snapshot_is_not_a_match(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("DUVAL"))
        db.log_jea_outages(
            [{"zip_code": "32225", "county": "DUVAL", "customers_out": 0, "customers_served": 1000,
              "percentage_out": 0.0, "etr": None, "etr_confidence": None, "n_out": 0}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        assert find_jea_correlations(db_path) == []

    def test_jea_real_outage_snapshot_still_matches(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("DUVAL"))
        db.log_jea_outages(
            [{"zip_code": "32225", "county": "DUVAL", "customers_out": 10, "customers_served": 1000,
              "percentage_out": 1.0, "etr": None, "etr_confidence": None, "n_out": 1}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        matches = find_jea_correlations(db_path)
        assert len(matches) == 1
        assert matches[0]["outage"]["customers_out"] == 10

"""
Tests for weather_match_confidence() in correlate.py - the event-type
plausibility logic, and the Excessive Heat Warning / Heat Advisory split
added 2026-07-08 (sustained extreme heat has a real grid-strain
mechanism; a routine hot day doesn't).
"""

from correlate import weather_match_confidence


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

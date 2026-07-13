"""
Tests for weather_match_confidence() in correlate.py - the event-type
plausibility logic, and the Excessive Heat Warning / Heat Advisory split
added 2026-07-08 (sustained extreme heat has a real grid-strain
mechanism; a routine hot day doesn't) - plus find_correlations()/
find_jea_correlations()'s customers_out > 0 filter and days= window,
and correlation_summary()'s distinct-counting fix, all added 2026-07-12.
"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from correlate import (
    weather_match_confidence, find_correlations, find_jea_correlations,
    find_tallahassee_correlations, find_talquin_correlations,
    find_fpuc_incident_correlations, duke_correlation_summary, correlation_summary,
    find_preco_correlations,
)
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


class TestCorrelationSummaryDistinctCounting:
    """
    2026-07-12: correlation_summary() used to count every matched
    (outage-snapshot, alert) PAIR, so one long-running alert overlapping
    many 15-minute poll cycles for the same outage inflated the number
    far past anything meaningful (a real dashboard row showed "Air
    Quality Alert x190" for what was actually a handful of distinct
    alerts). Now counts distinct alert_ids per event type, and distinct
    (county, timestamp) snapshots for outage_count.

    A second real instance of the same bug was caught the same day,
    after the first fix shipped: confidence_breakdown was still counting
    raw matches (a live combined KPI strip showed "low x27118"), because
    confidence is a pure function of the alert's own event_type +
    severity, not of which outage snapshot it happened to match, and so
    needs the exact same per-alert deduplication as alert_types.
    """

    def test_one_alert_matching_many_snapshots_counts_once(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert())
        # Same county, same alert window, 5 separate poll-cycle snapshots -
        # simulating one real outage that stayed open across several
        # 15-minute polls while one Heat Advisory remained active.
        for i in range(5):
            db.log_multiple_outages(
                "FPL", [{"county": "ALACHUA", "customers_out": 50, "customers_served": 1000}],
                timestamp=f"2026-01-01T{12 + i:02d}:00:00",
            )
        db.close()

        summary = correlation_summary(find_correlations(db_path))
        assert summary["ALACHUA"]["alert_types"]["Heat Advisory"] == 1
        assert summary["ALACHUA"]["outage_count"] == 5
        # Same alert matched 5 times (once per poll cycle) - still only
        # one distinct confidence signal, not five.
        assert summary["ALACHUA"]["confidence_breakdown"] == {"low": 1}

    def test_one_snapshot_matching_two_alerts_counts_as_one_outage(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts([
            {"id": "alert-a", "event": "Heat Advisory", "severity": "Severe", "urgency": "Expected",
             "areas": "ALACHUA", "effective": "2026-01-01T00:00:00", "expires": "2026-01-01T23:59:59",
             "headline": "a", "description": "a"},
            {"id": "alert-b", "event": "Special Weather Statement", "severity": "Moderate", "urgency": "Expected",
             "areas": "ALACHUA", "effective": "2026-01-01T00:00:00", "expires": "2026-01-01T23:59:59",
             "headline": "b", "description": "b"},
        ])
        db.log_multiple_outages(
            "FPL", [{"county": "ALACHUA", "customers_out": 50, "customers_served": 1000}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        summary = correlation_summary(find_correlations(db_path))
        # Two alerts overlap the one real snapshot - 2 matches, but still
        # only 1 real outage occasion
        assert summary["ALACHUA"]["outage_count"] == 1
        assert summary["ALACHUA"]["alert_types"] == {"Heat Advisory": 1, "Special Weather Statement": 1}


class TestFindCorrelationsWindow:
    """
    2026-07-12: find_correlations()/find_teco_correlations()/
    find_duke_correlations()/find_jea_correlations() gained a days=
    parameter so the dashboard can bound these to "last 7/30 days"
    instead of all-time-since-the-poller-started - added alongside the
    distinct-counting fix above, for the same underlying complaint
    (unbounded numbers that only ever grow less meaningful).
    """

    def test_days_none_includes_old_data(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert())
        db.log_multiple_outages(
            "FPL", [{"county": "ALACHUA", "customers_out": 50, "customers_served": 1000}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        assert len(find_correlations(db_path, days=None)) == 1

    def test_days_window_excludes_old_data(self, db_path):
        now = datetime.now()
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "old-alert", "event": "Heat Advisory", "severity": "Severe", "urgency": "Expected",
            "areas": "ALACHUA",
            "effective": (now - timedelta(days=100)).isoformat(),
            "expires": (now - timedelta(days=99)).isoformat(),
            "headline": "old", "description": "old",
        }])
        db.log_multiple_outages(
            "FPL", [{"county": "ALACHUA", "customers_out": 50, "customers_served": 1000}],
            timestamp=(now - timedelta(days=100)).isoformat(),
        )
        db.close()

        assert find_correlations(db_path, days=None) != []
        assert find_correlations(db_path, days=30) == []

    def test_days_window_includes_recent_data(self, db_path):
        now = datetime.now()
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "recent-alert", "event": "Heat Advisory", "severity": "Severe", "urgency": "Expected",
            "areas": "ALACHUA",
            "effective": (now - timedelta(days=2)).isoformat(),
            "expires": (now + timedelta(days=2)).isoformat(),
            "headline": "recent", "description": "recent",
        }])
        db.log_multiple_outages(
            "FPL", [{"county": "ALACHUA", "customers_out": 50, "customers_served": 1000}],
            timestamp=(now - timedelta(days=1)).isoformat(),
        )
        db.close()

        assert len(find_correlations(db_path, days=30)) == 1


class TestFindTallahasseeCorrelations:
    """
    find_tallahassee_correlations() reuses the exact same matching helpers
    (_window_cutoff/_parse_timestamp/_county_in_alert/_alert_covers_time)
    already proven via find_correlations()/find_duke_correlations() above -
    this is a wiring smoke test (right table/field names), not a re-proof
    of that shared logic.
    """

    def test_matches_a_tallahassee_incident_to_an_overlapping_alert(self, db_path):
        # log_tallahassee_incidents() always stamps fetched_at as
        # datetime.now() (no timestamp override, same as Duke's) - the
        # alert window has to actually bracket "now", not a fixed date.
        now = datetime.now()
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "test-alert-1", "event": "Heat Advisory", "severity": "Severe",
            "urgency": "Expected", "areas": "Leon",
            "effective": (now - timedelta(hours=1)).isoformat(),
            "expires": (now + timedelta(hours=1)).isoformat(),
            "headline": "test", "description": "test",
        }])
        db.log_tallahassee_incidents([{
            "incident_id": "1", "utility": "City of Tallahassee",
            "customer_count": 30, "lat": 30.44, "lon": -84.28, "county": "Leon",
            "region_name": "East", "status": "Investigating", "status_category": "investigating",
            "cause": "Tree down", "cause_category": "vegetation", "outage_type": "Unplanned",
            "reported_start_time": None, "estimated_restoration": None,
        }])
        db.close()

        matches = find_tallahassee_correlations(db_path, days=None)
        assert len(matches) == 1

        summary = duke_correlation_summary(matches)
        assert summary["Leon"]["incident_count"] == 1
        assert summary["Leon"]["max_customer_count"] == 30

    def test_no_matches_when_no_incidents_logged(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("Leon"))
        db.close()

        assert find_tallahassee_correlations(db_path, days=None) == []


class TestFindTalquinCorrelations:
    """
    find_talquin_correlations() reuses the exact same matching helpers
    already proven via find_correlations()/find_jea_correlations() above
    (it shares correlation_summary() too, since the shape is identical to
    FPL's) - this is a wiring smoke test, not a re-proof of shared logic.
    """

    def test_matches_a_talquin_outage_to_an_overlapping_alert(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("Gadsden"))
        db.log_talquin_outages(
            [{"county": "Gadsden", "customers_out": 50, "customers_served": 15493}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        matches = find_talquin_correlations(db_path, days=None)
        assert len(matches) == 1

        summary = correlation_summary(matches)
        assert summary["Gadsden"]["outage_count"] == 1

    def test_zero_customer_snapshots_are_not_matched(self, db_path):
        # Same real bug find_correlations()/find_jea_correlations() had
        # fixed for them - the raw table logs a fresh row every poll
        # cycle regardless of whether anything was actually wrong.
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("Gadsden"))
        db.log_talquin_outages(
            [{"county": "Gadsden", "customers_out": 0, "customers_served": 15493}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        assert find_talquin_correlations(db_path, days=None) == []


class TestFindFpucIncidentCorrelations:
    """
    find_fpuc_incident_correlations() reads fpuc_incidents (real
    per-incident markers with a reverse-geocoded county), not the
    combined-territory table - confirmed real 2026-07-13 once a live
    outage finally populated FPUC's marker data for the first time.
    Replaces an earlier version of this function that always returned
    an empty list by design (it read the combined-territory table's
    fixed placeholder county, which could never match a real alert).
    """

    def test_matches_a_real_fpuc_incident_to_an_overlapping_alert(self, db_path):
        now = datetime.now()
        db = OutageDatabase(db_path)
        db.log_weather_alerts([{
            "id": "test-alert-1", "event": "Heat Advisory", "severity": "Severe",
            "urgency": "Expected", "areas": "Liberty",
            "effective": (now - timedelta(hours=1)).isoformat(),
            "expires": (now + timedelta(hours=1)).isoformat(),
            "headline": "test", "description": "test",
        }])
        db.log_fpuc_incidents([{
            "incident_id": "D614473", "utility": "Florida Public Utilities Corporation",
            "customer_count": 56, "lat": 30.43, "lon": -84.95, "county": "Liberty",
            "substation": "5", "feeder": "9882",
            "reported_start_time": "2026-01-01T00:00:00", "estimated_restoration": None,
        }])
        db.close()

        matches = find_fpuc_incident_correlations(db_path, days=None)
        assert len(matches) == 1

        summary = duke_correlation_summary(matches)
        assert summary["Liberty"]["incident_count"] == 1
        assert summary["Liberty"]["max_customer_count"] == 56

    def test_no_matches_when_no_incidents_logged(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("Liberty"))
        db.close()

        assert find_fpuc_incident_correlations(db_path, days=None) == []


class TestFindPrecoCorrelations:
    """
    find_preco_correlations() reuses the exact same matching helpers
    already proven via find_correlations()/find_talquin_correlations()
    above (it shares correlation_summary() too, since the shape is
    identical to Talquin's) - this is a wiring smoke test, not a
    re-proof of shared logic.
    """

    def test_matches_a_preco_outage_to_an_overlapping_alert(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("Manatee"))
        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 3, "customers_served": 54383}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        matches = find_preco_correlations(db_path, days=None)
        assert len(matches) == 1

        summary = correlation_summary(matches)
        assert summary["Manatee"]["outage_count"] == 1

    def test_zero_customer_snapshots_are_not_matched(self, db_path):
        db = OutageDatabase(db_path)
        db.log_weather_alerts(_weather_alert("Manatee"))
        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 0, "customers_served": 54383}],
            timestamp="2026-01-01T12:00:00",
        )
        db.close()

        assert find_preco_correlations(db_path, days=None) == []

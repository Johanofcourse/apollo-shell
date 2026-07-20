"""
Tests for fetch_tallahassee_outages.py - get_rollup_summary(), added
2026-07-18 to replace the original incident-level design.

The original design tracked each raw feature as its own incident, keyed
by a "ticket" field - but every real feature this feed has ever
returned came back with ticket=None (confirmed against 9 real,
concurrent live incidents), so that design silently discarded 100% of
Tallahassee's data for this project's entire life. get_rollup_summary()
instead collapses every current feature into one county-wide total for
Leon County (Tallahassee's whole territory - see COUNTY), the same
shape fetch_talquin_outages.get_talquin_records() already uses.

REGION_NAMES is tested explicitly because of a real, caught mapping bug:
the region-name layer numbers its rows by an internal row id, not by
the digit baked into each region's own name (internal id 2 is named
"4 West", internal id 4 is "3 South") - joining on that internal id
would have silently mislabeled every outage's region. Region 0 also
shows up in real live data (confirmed 2026-07-18), not one of the five
named zones.
"""

import pytest
import requests

import fetch_tallahassee_outages as tally


def _feature(customers=50, region=2, status="Investigating", cause="Tree down",
             lat=30.44, lon=-84.28, ticket=None, outagetype="Unplanned"):
    return {
        "attributes": {
            "OBJECTID": 1,
            "lat": lat,
            "lon": lon,
            "region": region,
            "status": status,
            "cause": cause,
            "customers": customers,
            "off": None,
            "etr": None,
            "ticket": ticket,
            "outagetype": outagetype,
        }
    }


class TestRegionNames:
    def test_region_names_keyed_by_the_digit_in_the_name_not_objectid(self):
        # Confirmed against the real region-name layer's response:
        # OBJECTID 1="1 North", 2="4 West", 3="5 Outside", 4="3 South",
        # 5="2 East" - REGION_NAMES must be keyed 1->North, 2->East,
        # 3->South, 4->West, 5->Outside (the digit in the name), not by
        # that OBJECTID ordering. 0 is a real, unnamed value seen live.
        assert tally.REGION_NAMES == {
            0: "Unknown", 1: "North", 2: "East", 3: "South", 4: "West", 5: "Outside",
        }


class TestGetRollupSummary:
    def test_sums_customers_across_every_feature(self, monkeypatch):
        monkeypatch.setattr(
            tally, "fetch_tallahassee_outages",
            lambda: [_feature(customers=1), _feature(customers=171), _feature(customers=9)],
        )
        summary = tally.get_rollup_summary()
        assert summary == [{"county": "Leon", "customers_out": 181}]

    def test_always_returns_leon_county_even_with_zero_features(self, monkeypatch):
        # A real, legitimate state (no active outages) - genuinely
        # distinguishable from a fetch failure since 2026-07-20,
        # fetch_tallahassee_outages() raises on a real request failure
        # instead of returning [] for both cases (see
        # TestFetchTallahasseeOutagesFailureVisibility below).
        monkeypatch.setattr(tally, "fetch_tallahassee_outages", lambda: [])
        assert tally.get_rollup_summary() == [{"county": "Leon", "customers_out": 0}]


class TestFetchTallahasseeOutagesFailureVisibility:
    """
    Real pipeline-health blind spot found and fixed 2026-07-20:
    fetch_tallahassee_outages() used to catch its own RequestException
    and return an empty list, indistinguishable from a genuinely quiet
    cycle - a real network failure never reached main.py's
    pipeline-health logging at all, not just unalerted.
    """

    def test_a_real_request_failure_raises_instead_of_returning_empty(self, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.exceptions.RequestException("boom")
        monkeypatch.setattr(tally.requests, "get", boom)
        monkeypatch.setattr(tally, "TALLAHASSEE_API_URL", "https://example.com/real-endpoint")

        with pytest.raises(requests.exceptions.RequestException):
            tally.fetch_tallahassee_outages()

    def test_missing_customers_field_counts_as_zero(self, monkeypatch):
        feature = _feature(customers=None)
        monkeypatch.setattr(tally, "fetch_tallahassee_outages", lambda: [feature])
        assert tally.get_rollup_summary() == [{"county": "Leon", "customers_out": 0}]

    def test_missing_ticket_no_longer_excludes_a_feature(self, monkeypatch):
        # The real bug this redesign fixes: every real feature has
        # ticket=None, and the old get_incidents_summary() dropped
        # anything without one - get_rollup_summary() never looks at
        # ticket at all, so it can't repeat that mistake.
        monkeypatch.setattr(
            tally, "fetch_tallahassee_outages",
            lambda: [_feature(ticket=None, customers=5)],
        )
        assert tally.get_rollup_summary() == [{"county": "Leon", "customers_out": 5}]

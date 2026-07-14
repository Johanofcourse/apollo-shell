"""
Tests for fetch_tallahassee_outages.py - the parsing logic
(parse_incidents, _epoch_to_iso) and the ticket-based filtering in
get_incidents_summary(), added 2026-07-13 alongside the City of
Tallahassee integration.

REGION_NAMES is tested explicitly because of a real, caught mapping bug:
the region-name layer numbers its rows by an internal row id, not by
the digit baked into each region's own name (internal id 2 is named
"4 West", internal id 4 is "3 South") - joining on that internal id
would have silently mislabeled every outage's region.
"""

from datetime import datetime, timezone

import fetch_tallahassee_outages as tally


def _feature(ticket=101, customers=50, region=2, status="Investigating",
             cause="Tree down", lat=30.44, lon=-84.28, off=None, etr=None,
             outagetype="Unplanned"):
    return {
        "attributes": {
            "OBJECTID": 1,
            "lat": lat,
            "lon": lon,
            "region": region,
            "status": status,
            "cause": cause,
            "customers": customers,
            "off": off,
            "etr": etr,
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
        # that OBJECTID ordering.
        assert tally.REGION_NAMES == {
            1: "North", 2: "East", 3: "South", 4: "West", 5: "Outside",
        }


class TestParseIncidents:
    def test_parses_basic_shape(self):
        records = tally.parse_incidents([_feature(ticket=555, customers=42, region=3)])
        assert len(records) == 1
        r = records[0]
        assert r["utility"] == "City of Tallahassee"
        assert r["incident_id"] == "555"
        assert r["customer_count"] == 42
        assert r["county"] == "Leon"
        assert r["region_name"] == "South"
        assert r["cause"] == "Tree down"
        assert r["cause_category"] == "vegetation"

    def test_ticket_is_stringified(self):
        # ticket comes back as a plain integer from the source feed - incident_id
        # needs to be a string to match teco_incidents/duke_incidents'
        # TEXT incident_id column, same convention as every other source.
        records = tally.parse_incidents([_feature(ticket=999)])
        assert records[0]["incident_id"] == "999"
        assert isinstance(records[0]["incident_id"], str)

    def test_missing_ticket_becomes_none(self):
        records = tally.parse_incidents([_feature(ticket=None)])
        assert records[0]["incident_id"] is None

    def test_unrecognized_region_number_falls_back_to_its_own_string(self):
        records = tally.parse_incidents([_feature(region=99)])
        assert records[0]["region_name"] == "99"

    def test_missing_region_is_none(self):
        records = tally.parse_incidents([_feature(region=None)])
        assert records[0]["region_name"] is None

    def test_status_category_reuses_teco_categorizer(self):
        records = tally.parse_incidents([_feature(status="Onsite")])
        assert records[0]["status_category"] == "onsite"

    def test_off_and_etr_epoch_millis_converted_to_iso(self):
        # 2026-01-01T00:00:00 UTC in epoch milliseconds
        millis = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        records = tally.parse_incidents([_feature(off=millis, etr=millis)])
        assert records[0]["reported_start_time"] == "2026-01-01T00:00:00+00:00"
        assert records[0]["estimated_restoration"] == "2026-01-01T00:00:00+00:00"

    def test_missing_off_and_etr_stay_none(self):
        records = tally.parse_incidents([_feature(off=None, etr=None)])
        assert records[0]["reported_start_time"] is None
        assert records[0]["estimated_restoration"] is None


class TestGetIncidentsSummary:
    def test_drops_incidents_with_no_ticket(self, monkeypatch):
        monkeypatch.setattr(
            tally, "fetch_tallahassee_outages",
            lambda: [_feature(ticket=1), _feature(ticket=None), _feature(ticket=2)],
        )
        records = tally.get_incidents_summary()
        assert [r["incident_id"] for r in records] == ["1", "2"]

    def test_empty_feed_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(tally, "fetch_tallahassee_outages", lambda: [])
        assert tally.get_incidents_summary() == []

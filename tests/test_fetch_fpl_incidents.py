"""
Tests for fetch_fpl_incidents.py - FPL's real per-incident outage feed
(StormFeedRestoration.json), discovered 2026-08-16. Unlike
CountyOutages.json (this project's original FPL integration, a
county-wide rollup only), this feed gives a real ticketNum per outage
plus a real per-incident ETR - the field that makes an eventual
restoration-accuracy check possible for FPL for the first time.
"""

import pytest

import fetch_fpl_incidents as fpl


def _outage(ticket_num="20260816-01884-1", lat="30.036633664092804", lng="-81.5016368033269",
            customers_affected="49", cause="Under investigation",
            date_reported="08/16/26 06:22 PM", etr="08/16/26 11:45 PM",
            last_updated="08/16/2026 10:52 PM", status="A power restoration specialist is en route."):
    return {
        "lastUpdated": last_updated,
        "etr": etr,
        "lng": lng,
        "customersAffected": customers_affected,
        "Cause": cause,
        "dateReported": date_reported,
        "ticketNum": ticket_num,
        "lat": lat,
        "status": status,
    }


class TestParseFplTimestamp:
    def test_parses_two_digit_year_format(self):
        # dateReported/etr's real shape.
        assert fpl._parse_fpl_timestamp("08/16/26 06:22 PM") == "2026-08-16T18:22:00"

    def test_parses_four_digit_year_format(self):
        # lastUpdated's real shape - confirmed different from
        # dateReported/etr across all 60 outages in a real live poll,
        # not a one-off typo in a single sample.
        assert fpl._parse_fpl_timestamp("08/16/2026 10:52 PM") == "2026-08-16T22:52:00"

    def test_none_returns_none(self):
        assert fpl._parse_fpl_timestamp(None) is None

    def test_empty_string_returns_none(self):
        assert fpl._parse_fpl_timestamp("") is None

    def test_unrecognized_format_returns_none(self):
        assert fpl._parse_fpl_timestamp("not a real date") is None


class TestParseIncidents:
    def test_parses_basic_shape(self, monkeypatch):
        monkeypatch.setattr(fpl, "lookup_county", lambda lat, lon: "St. Johns")
        records = fpl.parse_incidents([_outage()])

        assert len(records) == 1
        r = records[0]
        assert r["utility"] == "Florida Power and Light Company"
        assert r["incident_id"] == "20260816-01884-1"
        assert r["customer_count"] == 49
        assert r["lat"] == 30.036633664092804
        assert r["lon"] == -81.5016368033269
        assert r["county"] == "St. Johns"
        assert r["cause"] == "Under investigation"
        assert r["cause_category"] == "pending"
        assert r["reported_start_time"] == "2026-08-16T18:22:00"
        assert r["estimated_restoration"] == "2026-08-16T23:45:00"
        assert r["last_updated"] == "2026-08-16T22:52:00"

    def test_reverse_geocodes_using_the_outages_own_lat_lng(self, monkeypatch):
        seen = {}
        def fake_lookup(lat, lon):
            seen["lat"], seen["lon"] = lat, lon
            return "Manatee"
        monkeypatch.setattr(fpl, "lookup_county", fake_lookup)

        records = fpl.parse_incidents([_outage(lat="27.5", lng="-82.5")])

        assert seen == {"lat": 27.5, "lon": -82.5}
        assert records[0]["county"] == "Manatee"

    def test_bad_lat_lng_skips_geocoding_instead_of_crashing(self, monkeypatch):
        called = []
        monkeypatch.setattr(fpl, "lookup_county", lambda lat, lon: called.append(1) or "Manatee")

        records = fpl.parse_incidents([_outage(lat="not-a-number", lng="also-bad")])

        assert records[0]["lat"] is None
        assert records[0]["lon"] is None
        assert records[0]["county"] is None
        assert called == []

    def test_missing_customers_affected_defaults_to_zero(self, monkeypatch):
        monkeypatch.setattr(fpl, "lookup_county", lambda lat, lon: "St. Johns")
        outage = _outage()
        del outage["customersAffected"]
        records = fpl.parse_incidents([outage])
        assert records[0]["customer_count"] == 0

    def test_no_outages_returns_empty(self):
        assert fpl.parse_incidents([]) == []

    def test_multiple_outages_keep_distinct_ticket_nums(self, monkeypatch):
        monkeypatch.setattr(fpl, "lookup_county", lambda lat, lon: "St. Johns")
        records = fpl.parse_incidents([
            _outage(ticket_num="A1"),
            _outage(ticket_num="A2", customers_affected="3"),
        ])
        assert {r["incident_id"] for r in records} == {"A1", "A2"}


class TestFetchFplIncidents:
    def test_missing_config_raises(self, monkeypatch):
        monkeypatch.setattr(fpl, "FPL_INCIDENTS_API_URL", None)
        with pytest.raises(RuntimeError):
            fpl.fetch_fpl_incidents()


class TestGetIncidentsSummary:
    def test_fetch_and_parse(self, monkeypatch):
        monkeypatch.setattr(fpl, "fetch_fpl_incidents", lambda: [_outage()])
        monkeypatch.setattr(fpl, "lookup_county", lambda lat, lon: "St. Johns")

        incidents = fpl.get_incidents_summary()

        assert len(incidents) == 1
        assert incidents[0]["incident_id"] == "20260816-01884-1"

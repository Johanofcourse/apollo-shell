"""
Tests for fetch_fpl_outages.py - the parsing logic (outages_to_records)
and the combined main+Panhandle feed logic (get_combined_fpl_records),
added 2026-07-12 alongside the FPL Northwest ("Panhandle") integration.
"""

import fetch_fpl_outages as fpl


def _raw(county, out, served):
    return {"County Name": county, "Customers Out": out, "Customers Served": served}


class TestOutagesToRecords:
    def test_parses_basic_shape(self):
        data = {"outages": [_raw("Nassau", "29", "32,007")]}
        records = fpl.outages_to_records(data)
        assert records == [{"county": "Nassau", "customers_out": 29, "customers_served": 32007}]

    def test_strips_comma_thousands_separators(self):
        data = {"outages": [_raw("Miami-Dade", "1,234", "1,272,800")]}
        records = fpl.outages_to_records(data)
        assert records[0]["customers_out"] == 1234
        assert records[0]["customers_served"] == 1272800

    def test_empty_outages_list(self):
        assert fpl.outages_to_records({"outages": []}) == []


class TestGetCombinedFplRecords:
    def test_combines_both_feeds(self, monkeypatch):
        monkeypatch.setattr(fpl, "fetch_fpl_outages", lambda: {"outages": [_raw("Nassau", "29", "32,007")]})
        monkeypatch.setattr(fpl, "fetch_fpl_northwest_outages", lambda: {"outages": [_raw("Escambia", "0", "174,512")]})

        records = fpl.get_combined_fpl_records()

        counties = {r["county"] for r in records}
        assert counties == {"Nassau", "Escambia"}

    def test_northwest_feed_unavailable_still_returns_main_feed(self, monkeypatch):
        # The Panhandle feed is a bonus on top of the main one - if it's
        # unset/fails, the whole cycle shouldn't lose the main feed's data.
        monkeypatch.setattr(fpl, "fetch_fpl_outages", lambda: {"outages": [_raw("Nassau", "29", "32,007")]})
        monkeypatch.setattr(fpl, "fetch_fpl_northwest_outages", lambda: None)

        records = fpl.get_combined_fpl_records()

        assert records == [{"county": "Nassau", "customers_out": 29, "customers_served": 32007}]

    def test_main_feed_unavailable_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fpl, "fetch_fpl_outages", lambda: None)
        monkeypatch.setattr(fpl, "fetch_fpl_northwest_outages", lambda: None)

        assert fpl.get_combined_fpl_records() == []

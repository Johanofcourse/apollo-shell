"""
Tests for fetch_chelco_outages.py - the parsing logic
(outages_to_records()) that wraps CHELCO's system-wide-only
outageSummary.json into a single combined-territory record, added
2026-07-14 alongside the Choctawhatchee Electric Cooperative
integration. Same platform/shape as TCEC/EREC (identical vendor
product, different hosting), same combined-territory limitations as
FPUC's original tracker.
"""

import fetch_chelco_outages as chelco


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Real response captured 2026-07-14 (a quiet moment, zero
        # active outages).
        data = {
            "customersAffected": 0,
            "customersRestored": 0,
            "customersOutNow": 0,
            "customersServed": 74996,
            "updateTime": "2026-07-14T00:09:05.6752663-05:00",
            "hourlyCustomersOut": [{"customers": 1, "eventTime": "2026-07-12T07:00:00-05:00"}],
        }
        records = chelco.outages_to_records(data)
        assert records == [{
            "county": "Santa Rosa/Okaloosa/Walton/Holmes",
            "customers_out": 0,
            "customers_served": 74996,
        }]

    def test_always_exactly_one_record(self):
        data = {"customersOutNow": 12, "customersServed": 74996}
        records = chelco.outages_to_records(data)
        assert len(records) == 1

    def test_missing_customers_out_now_defaults_to_zero(self):
        data = {"customersServed": 74996}
        records = chelco.outages_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_customers_served_defaults_to_zero(self):
        data = {"customersOutNow": 5}
        records = chelco.outages_to_records(data)
        assert records[0]["customers_served"] == 0

    def test_none_data_returns_empty(self):
        assert chelco.outages_to_records(None) == []

    def test_empty_dict_returns_empty(self):
        assert chelco.outages_to_records({}) == []


class TestGetChelcoRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(chelco, "fetch_chelco_outage_summary", lambda: None)
        assert chelco.get_chelco_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = {"customersOutNow": 4, "customersServed": 74996}
        monkeypatch.setattr(chelco, "fetch_chelco_outage_summary", lambda: data)
        records = chelco.get_chelco_records()
        assert records == [{
            "county": "Santa Rosa/Okaloosa/Walton/Holmes",
            "customers_out": 4,
            "customers_served": 74996,
        }]

"""
Tests for fetch_gcec_outages.py - the parsing logic
(outages_to_records()) that wraps GCEC's system-wide-only outage
summary into a single combined-territory record, added 2026-07-14
alongside the Gulf Coast Electric Cooperative integration. Same
platform/shape as TCEC/EREC/CHELCO, same combined-territory
limitations as FPUC's original tracker.
"""

import fetch_gcec_outages as gcec


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Real response captured 2026-07-14 (a quiet moment, zero
        # active outages).
        data = {
            "customersAffected": 0,
            "customersRestored": 0,
            "customersOutNow": 0,
            "customersServed": 23206,
            "updateTime": "2026-07-14T14:12:54.3468936-05:00",
            "hourlyCustomersOut": [{"customers": 10, "eventTime": "2026-07-12T14:00:00-05:00"}],
        }
        records = gcec.outages_to_records(data)
        assert records == [{
            "county": "Bay/Calhoun/Gulf/Jackson/Walton/Washington",
            "customers_out": 0,
            "customers_served": 23206,
        }]

    def test_always_exactly_one_record(self):
        data = {"customersOutNow": 12, "customersServed": 23206}
        records = gcec.outages_to_records(data)
        assert len(records) == 1

    def test_missing_customers_out_now_defaults_to_zero(self):
        data = {"customersServed": 23206}
        records = gcec.outages_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_customers_served_defaults_to_zero(self):
        data = {"customersOutNow": 5}
        records = gcec.outages_to_records(data)
        assert records[0]["customers_served"] == 0

    def test_none_data_returns_empty(self):
        assert gcec.outages_to_records(None) == []

    def test_empty_dict_returns_empty(self):
        assert gcec.outages_to_records({}) == []


class TestGetGcecRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(gcec, "fetch_gcec_outage_summary", lambda: None)
        assert gcec.get_gcec_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = {"customersOutNow": 4, "customersServed": 23206}
        monkeypatch.setattr(gcec, "fetch_gcec_outage_summary", lambda: data)
        records = gcec.get_gcec_records()
        assert records == [{
            "county": "Bay/Calhoun/Gulf/Jackson/Walton/Washington",
            "customers_out": 4,
            "customers_served": 23206,
        }]

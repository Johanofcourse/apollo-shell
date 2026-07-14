"""
Tests for fetch_erec_outages.py - the parsing logic
(outages_to_records()) that wraps EREC's system-wide-only
outageSummary.json into a single combined-territory record, added
2026-07-13 alongside the Escambia River Electric Cooperative
integration. Same platform/shape as TCEC (identical vendor product,
different hosting), same combined-territory limitations as FPUC's
original tracker.
"""

import fetch_erec_outages as erec


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Real response captured 2026-07-13 (currently quiet, but real
        # recent activity earlier the same day - 7 customers out around
        # noon, 3 more around 7pm, both already resolved by fetch time).
        data = {
            "customersAffected": 0,
            "customersRestored": 0,
            "customersOutNow": 0,
            "customersServed": 13663,
            "updateTime": "2026-07-13T21:17:45.3898759-05:00",
            "hourlyCustomersOut": [{"customers": 7, "eventTime": "2026-07-13T12:00:00-05:00"}],
        }
        records = erec.outages_to_records(data)
        assert records == [{
            "county": "Escambia/Santa Rosa",
            "customers_out": 0,
            "customers_served": 13663,
        }]

    def test_always_exactly_one_record(self):
        data = {"customersOutNow": 7, "customersServed": 13663}
        records = erec.outages_to_records(data)
        assert len(records) == 1

    def test_missing_customers_out_now_defaults_to_zero(self):
        data = {"customersServed": 13663}
        records = erec.outages_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_customers_served_defaults_to_zero(self):
        data = {"customersOutNow": 5}
        records = erec.outages_to_records(data)
        assert records[0]["customers_served"] == 0

    def test_none_data_returns_empty(self):
        assert erec.outages_to_records(None) == []

    def test_empty_dict_returns_empty(self):
        assert erec.outages_to_records({}) == []


class TestGetErecRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(erec, "fetch_erec_outage_summary", lambda: None)
        assert erec.get_erec_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = {"customersOutNow": 3, "customersServed": 13663}
        monkeypatch.setattr(erec, "fetch_erec_outage_summary", lambda: data)
        records = erec.get_erec_records()
        assert records == [{
            "county": "Escambia/Santa Rosa",
            "customers_out": 3,
            "customers_served": 13663,
        }]

"""
Tests for fetch_tcec_outages.py - the parsing logic
(outages_to_records()) that wraps TCEC's system-wide-only
outageSummary.json into a single combined-territory record, added
2026-07-13 alongside the Tri-County Electric Cooperative integration.
Same combined-territory shape/limitations as FPUC's original tracker.
"""

import fetch_tcec_outages as tcec


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Real response captured 2026-07-13 (a quiet moment, zero
        # active outages).
        data = {
            "customersAffected": 0,
            "customersRestored": 0,
            "customersOutNow": 0,
            "customersServed": 20103,
            "updateTime": "2026-07-13T20:12:24.0394141-04:00",
            "hourlyCustomersOut": [{"customers": 130, "eventTime": "2026-07-11T20:00:00-04:00"}],
            "streetsAffected": None,
        }
        records = tcec.outages_to_records(data)
        assert records == [{
            "county": "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)",
            "customers_out": 0,
            "customers_served": 20103,
        }]

    def test_always_exactly_one_record(self):
        data = {"customersOutNow": 42, "customersServed": 20103}
        records = tcec.outages_to_records(data)
        assert len(records) == 1

    def test_missing_customers_out_now_defaults_to_zero(self):
        data = {"customersServed": 20103}
        records = tcec.outages_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_customers_served_defaults_to_zero(self):
        data = {"customersOutNow": 5}
        records = tcec.outages_to_records(data)
        assert records[0]["customers_served"] == 0

    def test_none_data_returns_empty(self):
        assert tcec.outages_to_records(None) == []

    def test_empty_dict_returns_empty(self):
        assert tcec.outages_to_records({}) == []


class TestGetTcecRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(tcec, "fetch_tcec_outage_summary", lambda: None)
        assert tcec.get_tcec_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = {"customersOutNow": 15, "customersServed": 20103}
        monkeypatch.setattr(tcec, "fetch_tcec_outage_summary", lambda: data)
        records = tcec.get_tcec_records()
        assert records == [{
            "county": "Jefferson/Madison/Taylor (+ partial Dixie/Lafayette/Leon)",
            "customers_out": 15,
            "customers_served": 20103,
        }]

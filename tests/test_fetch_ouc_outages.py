"""
Tests for fetch_ouc_outages.py - the parsing logic (outages_to_records())
that turns Orlando Utilities Commission's real summary response into a
single Orange County rollup, added 2026-07-16 alongside the OUC
integration.
"""

import fetch_ouc_outages as ouc


def _summary(customers_out=0, customers_served=291868):
    return {
        "fileTitle": "data",
        "summaryFileData": {
            "totals": [{
                "summaryTotalId": "total-1",
                "total_cust_a": {"val": customers_out},
                "total_percent_cust_a": {"val": 0.0},
                "total_percent_cust_active": {"val": 100.0},
                "total_cust_s": customers_served,
                "total_outages": 0,
            }],
            "date_generated": "2026-07-16T05:12:19.932006296Z",
            "overwritten_ca": False,
            "overwritten_etr": False,
            "page_mode": {"mode": "BLUESKY", "redirectURL": "", "pausePublish": False},
        },
    }


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Exact real response shape captured 2026-07-16 (a quiet moment,
        # zero active outages).
        data = _summary(customers_out=0, customers_served=291868)
        records = ouc.outages_to_records(data)
        assert records == [{"county": "Orange", "customers_out": 0, "customers_served": 291868}]

    def test_nonzero_customers_out(self):
        data = _summary(customers_out=1500, customers_served=291868)
        records = ouc.outages_to_records(data)
        assert records[0]["customers_out"] == 1500

    def test_missing_totals_returns_empty(self):
        assert ouc.outages_to_records({"summaryFileData": {"totals": []}}) == []

    def test_missing_summary_file_data_returns_empty(self):
        assert ouc.outages_to_records({}) == []

    def test_none_data_returns_empty(self):
        assert ouc.outages_to_records(None) == []

    def test_missing_total_cust_a_defaults_to_zero(self):
        data = {"summaryFileData": {"totals": [{"total_cust_s": 291868}]}}
        records = ouc.outages_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_total_cust_s_defaults_to_zero(self):
        data = {"summaryFileData": {"totals": [{"total_cust_a": {"val": 5}}]}}
        records = ouc.outages_to_records(data)
        assert records[0]["customers_served"] == 0


class TestGetOucRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ouc, "fetch_ouc_summary", lambda: None)
        assert ouc.get_ouc_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        monkeypatch.setattr(ouc, "fetch_ouc_summary", lambda: _summary(customers_out=42, customers_served=291868))
        records = ouc.get_ouc_records()
        assert records == [{"county": "Orange", "customers_out": 42, "customers_served": 291868}]

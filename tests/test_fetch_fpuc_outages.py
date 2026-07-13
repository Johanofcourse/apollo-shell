"""
Tests for fetch_fpuc_outages.py - the parsing logic (outages_to_records())
that pulls the single combined-territory total out of the DataVoice
"Apprise" system's response shape, added 2026-07-13 alongside the
Florida Public Utilities Corporation integration.
"""

import fetch_fpuc_outages as fpuc


def _response(consumers=30668, affected=0, result="true"):
    return {
        "result": result,
        "0": {
            "markers": [],
            "index": 1,
            "service_index_name": "Electric",
            "stats": {"NumConsumers": consumers},
            "outages": 0,
            "customersAffected": affected,
        },
        "isHighTraffic": False,
        "polygons": [],
        "curl_success": "true",
        "timestamp": "Jul 13, 1 29, am",
    }


class TestOutagesToRecords:
    def test_parses_basic_shape(self):
        records = fpuc.outages_to_records(_response(consumers=30668, affected=0))
        assert records == [{
            "county": fpuc.COMBINED_TERRITORY_LABEL,
            "customers_out": 0,
            "customers_served": 30668,
        }]

    def test_nonzero_affected(self):
        records = fpuc.outages_to_records(_response(consumers=30668, affected=577))
        assert records[0]["customers_out"] == 577

    def test_none_data_returns_empty(self):
        assert fpuc.outages_to_records(None) == []

    def test_result_false_returns_empty(self):
        assert fpuc.outages_to_records(_response(result="false")) == []

    def test_missing_zero_key_returns_empty_record_defaults(self):
        # "result": "true" but no "0" key at all - shouldn't crash, just
        # default to zero.
        data = {"result": "true"}
        records = fpuc.outages_to_records(data)
        assert records == [{
            "county": fpuc.COMBINED_TERRITORY_LABEL,
            "customers_out": 0,
            "customers_served": 0,
        }]


class TestGetFpucRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fpuc, "fetch_fpuc_outage_summary", lambda: None)
        assert fpuc.get_fpuc_records() == []

    def test_fetch_success_returns_parsed_record(self, monkeypatch):
        monkeypatch.setattr(fpuc, "fetch_fpuc_outage_summary", lambda: _response(consumers=30668, affected=12))
        records = fpuc.get_fpuc_records()
        assert records == [{
            "county": fpuc.COMBINED_TERRITORY_LABEL,
            "customers_out": 12,
            "customers_served": 30668,
        }]

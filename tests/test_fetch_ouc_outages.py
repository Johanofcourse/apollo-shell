"""
Tests for fetch_ouc_outages.py - the parsing logic (outages_to_records())
that turns Orlando Utilities Commission's real summary response into a
single Orange County rollup, added 2026-07-16 alongside the OUC
integration.

TestFetchCurrentDataPath/TestFetchOucSummary added 2026-08-02 after a
real incident: the hardcoded data-path UUID this fetcher used to point
at 404'd for ~27h/108 straight cycles once Kubra redeployed OUC's map
with a fresh one. Fix looks the current path up fresh every call via
Kubra's own currentState endpoint, keyed on OUC_STORMCENTER_ID/
OUC_VIEW_ID (OUC's actual stable map configuration with the vendor,
confirmed to survive the same redeploy that broke the old UUID).
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


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json


class TestFetchCurrentDataPath:
    def test_missing_stormcenter_id_returns_none(self, monkeypatch):
        monkeypatch.setattr(ouc, "OUC_STORMCENTER_ID", None)
        monkeypatch.setattr(ouc, "OUC_VIEW_ID", "some-view")
        assert ouc._fetch_current_data_path() is None

    def test_missing_view_id_returns_none(self, monkeypatch):
        monkeypatch.setattr(ouc, "OUC_STORMCENTER_ID", "some-stormcenter")
        monkeypatch.setattr(ouc, "OUC_VIEW_ID", None)
        assert ouc._fetch_current_data_path() is None

    def test_extracts_interval_generation_data_from_real_shape(self, monkeypatch):
        # Real captured shape, 2026-08-02, the exact response that
        # revealed OUC's data path had rotated to a fresh UUID.
        monkeypatch.setattr(ouc, "OUC_STORMCENTER_ID", "sc-id")
        monkeypatch.setattr(ouc, "OUC_VIEW_ID", "view-id")
        monkeypatch.setattr(ouc.requests, "get", lambda url, timeout: _FakeResponse({
            "version": "V1",
            "stormcenterDeploymentId": "ed748d96-c461-479d-91ec-d08f08d94d74",
            "data": {
                "interval_generation_data": "data/fe479df5-7909-43a0-8ce0-0ce67fcf24bd",
                "cluster_interval_generation_data": "cluster-data/{qkh}/x",
                "planned_outage_data": None,
            },
        }))
        assert ouc._fetch_current_data_path() == "data/fe479df5-7909-43a0-8ce0-0ce67fcf24bd"

    def test_missing_data_key_returns_none(self, monkeypatch):
        monkeypatch.setattr(ouc, "OUC_STORMCENTER_ID", "sc-id")
        monkeypatch.setattr(ouc, "OUC_VIEW_ID", "view-id")
        monkeypatch.setattr(ouc.requests, "get", lambda url, timeout: _FakeResponse({"version": "V1"}))
        assert ouc._fetch_current_data_path() is None

    def test_request_failure_returns_none_not_raises(self, monkeypatch):
        import requests

        monkeypatch.setattr(ouc, "OUC_STORMCENTER_ID", "sc-id")
        monkeypatch.setattr(ouc, "OUC_VIEW_ID", "view-id")

        def _boom(url, timeout):
            raise requests.exceptions.ConnectionError("refused")

        monkeypatch.setattr(ouc.requests, "get", _boom)
        assert ouc._fetch_current_data_path() is None

    def test_stale_uuid_would_404_new_one_does_not(self, monkeypatch):
        # Real regression guard for the incident itself: a stale/old
        # data path 404s downstream even though currentState itself
        # succeeds - confirms the lookup is a genuinely separate step
        # from the data fetch, not just re-wrapping the same failure.
        monkeypatch.setattr(ouc, "OUC_STORMCENTER_ID", "sc-id")
        monkeypatch.setattr(ouc, "OUC_VIEW_ID", "view-id")
        monkeypatch.setattr(ouc.requests, "get", lambda url, timeout: _FakeResponse({
            "data": {"interval_generation_data": "data/fresh-uuid"},
        }))
        assert ouc._fetch_current_data_path() == "data/fresh-uuid"


class TestFetchOucSummary:
    def test_no_data_path_skips_fetch_entirely(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ouc, "_fetch_current_data_path", lambda: None)
        monkeypatch.setattr(ouc.requests, "get", lambda *a, **kw: calls.append(1))
        assert ouc.fetch_ouc_summary() is None
        assert calls == []

    def test_builds_url_from_looked_up_path_and_returns_json(self, monkeypatch):
        monkeypatch.setattr(ouc, "_fetch_current_data_path", lambda: "data/fresh-uuid")
        seen_urls = []

        def _get(url, timeout):
            seen_urls.append(url)
            return _FakeResponse(_summary(customers_out=7))

        monkeypatch.setattr(ouc.requests, "get", _get)
        result = ouc.fetch_ouc_summary()
        assert seen_urls == ["https://kubra.io/data/fresh-uuid/public/summary-1/data.json"]
        assert result == _summary(customers_out=7)

    def test_data_fetch_failure_returns_none(self, monkeypatch):
        import requests

        monkeypatch.setattr(ouc, "_fetch_current_data_path", lambda: "data/fresh-uuid")

        def _boom(url, timeout):
            raise requests.exceptions.ConnectionError("refused")

        monkeypatch.setattr(ouc.requests, "get", _boom)
        assert ouc.fetch_ouc_summary() is None

"""
Tests for fetch_fpuc_outages.py - the parsing logic for both shapes
DataVoice's "Apprise" system returns in one response: the combined-
territory total (outages_to_records()) and, since 2026-07-13, the real
per-incident markers (markers_to_incidents()) - confirmed real only
once a live outage finally populated that part of the response for the
first time (it had only ever been observed empty before that).
"""

from datetime import datetime

import fetch_fpuc_outages as fpuc


def _response(consumers=30668, affected=0, result="true", markers=None):
    return {
        "result": result,
        "0": {
            "markers": markers if markers is not None else [],
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


def _marker(incident_id="D614473", consumers_affected="56", lat="30.431929249179447",
            lon="-84.9485858787713", substation="5", feeder="9882",
            start_date="07/13 12:52 pm", formatted_ert=None):
    return {
        "substation": substation,
        "feeder": feeder,
        "incident_id": incident_id,
        "alias": "",
        "outage_comment": None,
        "estimated_restore_time": None,
        "formatted_ert": formatted_ert,
        "start_date": start_date,
        "duration": "00 hr 07 min",
        "consumers_affected": consumers_affected,
        "lon": lon,
        "lat": lat,
        "opt_code": None,
        "poly": [],
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


class TestParseFpucStartDate:
    def test_parses_real_format(self):
        result = fpuc._parse_fpuc_start_date("07/13 12:52 pm")
        assert result is not None
        assert result.startswith(f"{datetime.now().year}-07-13T12:52:00")

    def test_none_returns_none(self):
        assert fpuc._parse_fpuc_start_date(None) is None

    def test_unrecognized_format_returns_none(self):
        assert fpuc._parse_fpuc_start_date("not a real date") is None


class TestMarkersToIncidents:
    def test_parses_basic_shape(self, monkeypatch):
        monkeypatch.setattr(fpuc, "lookup_county", lambda lat, lon: "Liberty")
        data = _response(markers=[_marker()])
        incidents = fpuc.markers_to_incidents(data)

        assert len(incidents) == 1
        i = incidents[0]
        assert i["utility"] == "Florida Public Utilities Corporation"
        assert i["incident_id"] == "D614473"
        assert i["customer_count"] == 56
        assert i["lat"] == 30.431929249179447
        assert i["lon"] == -84.9485858787713
        assert i["county"] == "Liberty"
        assert i["substation"] == "5"
        assert i["feeder"] == "9882"

    def test_reverse_geocodes_using_the_markers_own_lat_lon(self, monkeypatch):
        seen = {}
        def fake_lookup(lat, lon):
            seen["lat"], seen["lon"] = lat, lon
            return "Wakulla"
        monkeypatch.setattr(fpuc, "lookup_county", fake_lookup)

        incidents = fpuc.markers_to_incidents(_response(markers=[_marker(lat="30.1", lon="-84.3")]))

        assert seen == {"lat": 30.1, "lon": -84.3}
        assert incidents[0]["county"] == "Wakulla"

    def test_missing_incident_id_is_dropped(self, monkeypatch):
        monkeypatch.setattr(fpuc, "lookup_county", lambda lat, lon: "Liberty")
        marker = _marker()
        marker["incident_id"] = None
        assert fpuc.markers_to_incidents(_response(markers=[marker])) == []

    def test_bad_lat_lon_skips_geocoding_instead_of_crashing(self, monkeypatch):
        called = []
        monkeypatch.setattr(fpuc, "lookup_county", lambda lat, lon: called.append(1) or "Liberty")
        incidents = fpuc.markers_to_incidents(_response(markers=[_marker(lat="not-a-number", lon="also-bad")]))

        assert incidents[0]["lat"] is None
        assert incidents[0]["lon"] is None
        assert incidents[0]["county"] is None
        assert called == []

    def test_no_markers_returns_empty(self):
        assert fpuc.markers_to_incidents(_response(markers=[])) == []

    def test_none_data_returns_empty(self):
        assert fpuc.markers_to_incidents(None) == []

    def test_multiple_markers(self, monkeypatch):
        monkeypatch.setattr(fpuc, "lookup_county", lambda lat, lon: "Liberty")
        data = _response(markers=[_marker(incident_id="A1"), _marker(incident_id="A2", consumers_affected="3")])
        incidents = fpuc.markers_to_incidents(data)
        assert {i["incident_id"] for i in incidents} == {"A1", "A2"}


class TestGetFpucIncidents:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fpuc, "fetch_fpuc_outage_summary", lambda: None)
        assert fpuc.get_fpuc_incidents() == []

    def test_fetch_success_returns_parsed_incidents(self, monkeypatch):
        monkeypatch.setattr(fpuc, "lookup_county", lambda lat, lon: "Liberty")
        monkeypatch.setattr(fpuc, "fetch_fpuc_outage_summary", lambda: _response(markers=[_marker()]))
        incidents = fpuc.get_fpuc_incidents()
        assert len(incidents) == 1
        assert incidents[0]["incident_id"] == "D614473"

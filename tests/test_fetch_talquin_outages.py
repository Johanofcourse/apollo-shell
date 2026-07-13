"""
Tests for fetch_talquin_outages.py - the parsing logic
(outages_to_records()) that pulls the "County" breakdown out of Siena's
nested reportData.reports shape, added 2026-07-13 alongside the Talquin
Electric Cooperative integration.
"""

import fetch_talquin_outages as talquin


def _siena_response(county_polygons, substation_polygons=None, zip_polygons=None):
    reports = []
    if substation_polygons is not None:
        reports.append({"id": "Substation", "name": "Substation", "polygons": substation_polygons})
    reports.append({"id": "County", "name": "County", "polygons": county_polygons})
    if zip_polygons is not None:
        reports.append({"id": "Zip", "name": "Zip Code", "polygons": zip_polygons})
    return {"reportData": {"reports": reports}}


def _polygon(name, accounts, affected):
    return {"name": name, "polygonID": 1, "accounts": accounts, "affected": affected, "lat": 30.0, "lon": -84.0}


class TestOutagesToRecords:
    def test_parses_basic_shape(self):
        data = _siena_response([_polygon("GADSDEN", 15493, 0)])
        records = talquin.outages_to_records(data)
        assert records == [{"county": "Gadsden", "customers_out": 0, "customers_served": 15493}]

    def test_county_names_are_titlecased_from_all_caps(self):
        data = _siena_response([_polygon("LIBERTY", 3529, 12)])
        records = talquin.outages_to_records(data)
        assert records[0]["county"] == "Liberty"

    def test_only_reads_the_county_level_report_not_substation_or_zip(self):
        # The same response also breaks totals down by substation and by
        # ZIP code - only the "County" report should ever be read here.
        data = _siena_response(
            county_polygons=[_polygon("LEON", 26350, 5)],
            substation_polygons=[_polygon("Killearn", 5991, 5)],
            zip_polygons=[_polygon("32303", 6425, 5)],
        )
        records = talquin.outages_to_records(data)
        assert len(records) == 1
        assert records[0]["county"] == "Leon"

    def test_multiple_counties(self):
        data = _siena_response([
            _polygon("GADSDEN", 15493, 0),
            _polygon("LEON", 26350, 0),
            _polygon("LIBERTY", 3529, 0),
            _polygon("WAKULLA", 11373, 0),
        ])
        records = talquin.outages_to_records(data)
        counties = {r["county"] for r in records}
        assert counties == {"Gadsden", "Leon", "Liberty", "Wakulla"}

    def test_missing_county_report_returns_empty(self):
        data = {"reportData": {"reports": [{"id": "Substation", "name": "Substation", "polygons": []}]}}
        assert talquin.outages_to_records(data) == []

    def test_none_data_returns_empty(self):
        assert talquin.outages_to_records(None) == []

    def test_missing_affected_or_accounts_default_to_zero(self):
        data = _siena_response([{"name": "WAKULLA", "polygonID": 7, "lat": 30.0, "lon": -84.0}])
        records = talquin.outages_to_records(data)
        assert records[0]["customers_out"] == 0
        assert records[0]["customers_served"] == 0


class TestGetTalquinRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(talquin, "fetch_talquin_outages", lambda: None)
        assert talquin.get_talquin_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = _siena_response([_polygon("GADSDEN", 15493, 3)])
        monkeypatch.setattr(talquin, "fetch_talquin_outages", lambda: data)
        records = talquin.get_talquin_records()
        assert records == [{"county": "Gadsden", "customers_out": 3, "customers_served": 15493}]

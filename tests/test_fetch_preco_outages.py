"""
Tests for fetch_preco_outages.py - the parsing logic
(outages_to_records()) that pulls the "County" breakdown out of Siena's
nested reportData.reports shape, added 2026-07-13 alongside the Peace
River Electric Cooperative integration.
"""

import fetch_preco_outages as preco


def _siena_response(county_polygons, district_polygons=None):
    reports = []
    reports.append({"id": "County", "name": "County", "polygons": county_polygons})
    if district_polygons is not None:
        reports.append({"id": "District", "name": "District", "polygons": district_polygons})
    return {"reportData": {"reports": reports}}


def _polygon(name, accounts, affected):
    return {"name": name, "polygonID": 1, "accounts": accounts, "affected": affected, "lat": 27.0, "lng": -82.0}


class TestOutagesToRecords:
    def test_parses_basic_shape(self):
        data = _siena_response([_polygon("Manatee", 54383, 0)])
        records = preco.outages_to_records(data)
        assert records == [{"county": "Manatee", "customers_out": 0, "customers_served": 54383}]

    def test_county_names_kept_as_sent_not_titlecased(self):
        # Unlike Talquin's all-caps feed, PRECO's names already arrive
        # correctly cased - "DeSoto" must stay "DeSoto", not get mangled
        # into "Desoto" by a blind .title() call.
        data = _siena_response([_polygon("DeSoto", 1127, 0)])
        records = preco.outages_to_records(data)
        assert records[0]["county"] == "DeSoto"

    def test_only_reads_the_county_level_report_not_district(self):
        # The same response also breaks totals down by PRECO's 3 internal
        # service districts (CENTRAL/EASTERN/WESTERN) - only the "County"
        # report should ever be read here.
        data = _siena_response(
            county_polygons=[_polygon("Polk", 6128, 0)],
            district_polygons=[_polygon("CENTRAL", 12961, 0)],
        )
        records = preco.outages_to_records(data)
        assert len(records) == 1
        assert records[0]["county"] == "Polk"

    def test_multiple_counties(self):
        data = _siena_response([
            _polygon("Brevard", 59, 0),
            _polygon("Hardee", 9885, 0),
            _polygon("Manatee", 54383, 3),
            _polygon("Sarasota", 39, 0),
        ])
        records = preco.outages_to_records(data)
        counties = {r["county"] for r in records}
        assert counties == {"Brevard", "Hardee", "Manatee", "Sarasota"}

    def test_missing_county_report_returns_empty(self):
        data = {"reportData": {"reports": [{"id": "District", "name": "District", "polygons": []}]}}
        assert preco.outages_to_records(data) == []

    def test_none_data_returns_empty(self):
        assert preco.outages_to_records(None) == []

    def test_missing_affected_or_accounts_default_to_zero(self):
        data = _siena_response([{"name": "Osceola", "polygonID": 47, "lat": 28.0, "lng": -81.1}])
        records = preco.outages_to_records(data)
        assert records[0]["customers_out"] == 0
        assert records[0]["customers_served"] == 0


class TestGetPrecoRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(preco, "fetch_preco_outages", lambda: None)
        assert preco.get_preco_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = _siena_response([_polygon("Manatee", 54383, 3)])
        monkeypatch.setattr(preco, "fetch_preco_outages", lambda: data)
        records = preco.get_preco_records()
        assert records == [{"county": "Manatee", "customers_out": 3, "customers_served": 54383}]

"""
Tests for fetch_clay_outages.py - the parsing logic (outages_to_records())
that pulls the "Counties" regionDataSet out of Clay's real summary.json
response, added 2026-07-19 alongside the Clay Electric Cooperative
integration. Same underlying platform as LCEC, but confirmed against
Clay's own real response rather than assumed identical - the county
dataset id is "Counties" (plural) here, not "County" like LCEC's.
"""

import fetch_clay_outages as clay


def _summary(county_regions, district_regions=None):
    region_datasets = []
    region_datasets.append({"id": "Counties", "description": "Counties", "regions": county_regions})
    if district_regions is not None:
        region_datasets.append({"id": "district", "description": "District", "regions": district_regions})
    return {"totalServed": 200006, "outages": [], "regionDataSets": region_datasets}


def _region(name, numberOut, numberServed):
    return {"id": name, "description": "Counties", "numberOut": numberOut, "numberServed": numberServed}


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Exact real response shape captured 2026-07-19 (a quiet moment
        # with one small active outage in Alachua County).
        data = _summary([
            _region("Columbia", 0, 18816),
            _region("Bradford", 0, 6563),
            _region("Gilchrist", 0, 8),
            _region("Marion", 0, 17081),
            _region("Levy", 0, 685),
            _region("Duval", 0, 16),
            _region("Suwannee", 0, 10),
            _region("Alachua", 3, 26955),
            _region("Union", 0, 4280),
            _region("Clay", 0, 93784),
            _region("Volusia", 0, 2308),
            _region("Flagler", 0, 3),
            _region("Lake", 0, 2296),
            _region("Putnam", 0, 23243),
            _region("Baker", 0, 2938),
        ])
        records = clay.outages_to_records(data)
        assert len(records) == 15
        assert records[7] == {"county": "Alachua", "customers_out": 3, "customers_served": 26955}
        assert records[9] == {"county": "Clay", "customers_out": 0, "customers_served": 93784}

    def test_only_reads_the_counties_dataset_not_district(self):
        data = _summary(
            county_regions=[_region("Clay", 0, 93784)],
            district_regions=[{"id": "Orange Park District", "description": "District", "numberOut": 0, "numberServed": 85571}],
        )
        records = clay.outages_to_records(data)
        assert len(records) == 1
        assert records[0]["county"] == "Clay"

    def test_missing_counties_dataset_returns_empty(self):
        data = {"regionDataSets": [{"id": "district", "description": "District", "regions": []}]}
        assert clay.outages_to_records(data) == []

    def test_none_data_returns_empty(self):
        assert clay.outages_to_records(None) == []

    def test_missing_number_out_or_served_default_to_zero(self):
        data = _summary([{"id": "Clay", "description": "Counties"}])
        records = clay.outages_to_records(data)
        assert records[0]["customers_out"] == 0
        assert records[0]["customers_served"] == 0


class TestGetClayRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(clay, "fetch_clay_outages", lambda: None)
        assert clay.get_clay_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = _summary([_region("Clay", 0, 93784)])
        monkeypatch.setattr(clay, "fetch_clay_outages", lambda: data)
        records = clay.get_clay_records()
        assert records == [{"county": "Clay", "customers_out": 0, "customers_served": 93784}]

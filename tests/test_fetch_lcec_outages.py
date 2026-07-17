"""
Tests for fetch_lcec_outages.py - the parsing logic (outages_to_records())
that pulls the "County" regionDataSet out of LCEC's real summary.json
response, added 2026-07-16 alongside the Lee County Electric
Cooperative integration.
"""

import fetch_lcec_outages as lcec


def _summary(county_regions, zip_regions=None, region_regions=None):
    region_datasets = []
    if zip_regions is not None:
        region_datasets.append({"id": "Zip", "description": "Zip Code", "regions": zip_regions})
    if region_regions is not None:
        region_datasets.append({"id": "Region", "description": "Region", "regions": region_regions})
    region_datasets.append({"id": "County", "description": "County", "regions": county_regions})
    return {"totalServed": 271293, "outages": [], "regionDataSets": region_datasets}


def _region(name, numberOut, numberServed):
    return {"id": name, "description": "County", "numberOut": numberOut, "numberServed": numberServed}


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Exact real response shape captured 2026-07-16 (a quiet moment
        # with one small active outage in Lee County).
        data = _summary([
            _region("Charlotte", 0, 902),
            _region("Broward", 0, 54),
            _region("Collier", 0, 40931),
            _region("Hendry", 0, 844),
            _region("Lee", 4, 227335),
        ])
        records = lcec.outages_to_records(data)
        assert records == [
            {"county": "Charlotte", "customers_out": 0, "customers_served": 902},
            {"county": "Broward", "customers_out": 0, "customers_served": 54},
            {"county": "Collier", "customers_out": 0, "customers_served": 40931},
            {"county": "Hendry", "customers_out": 0, "customers_served": 844},
            {"county": "Lee", "customers_out": 4, "customers_served": 227335},
        ]

    def test_only_reads_the_county_dataset_not_zip_or_region(self):
        data = _summary(
            county_regions=[_region("Lee", 4, 227335)],
            zip_regions=[{"id": "33936", "description": "Zip Code", "numberOut": 4, "numberServed": 12717}],
            region_regions=[{"id": "Lehigh Acres", "description": "Region", "numberOut": 4, "numberServed": 42925}],
        )
        records = lcec.outages_to_records(data)
        assert len(records) == 1
        assert records[0]["county"] == "Lee"

    def test_missing_county_dataset_returns_empty(self):
        data = {"regionDataSets": [{"id": "Zip", "description": "Zip Code", "regions": []}]}
        assert lcec.outages_to_records(data) == []

    def test_none_data_returns_empty(self):
        assert lcec.outages_to_records(None) == []

    def test_missing_number_out_or_served_default_to_zero(self):
        data = _summary([{"id": "Lee", "description": "County"}])
        records = lcec.outages_to_records(data)
        assert records[0]["customers_out"] == 0
        assert records[0]["customers_served"] == 0


class TestGetLcecRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(lcec, "fetch_lcec_outages", lambda: None)
        assert lcec.get_lcec_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = _summary([_region("Lee", 4, 227335)])
        monkeypatch.setattr(lcec, "fetch_lcec_outages", lambda: data)
        records = lcec.get_lcec_records()
        assert records == [{"county": "Lee", "customers_out": 4, "customers_served": 227335}]

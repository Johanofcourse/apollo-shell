"""
Tests for fetch_fkec_outages.py - the parsing logic
(outages_to_records()) that aggregates FKEC's per-ZIP summary.json into
a single Monroe County rollup, added 2026-07-13 alongside the Florida
Keys Electric Cooperative integration.
"""

import fetch_fkec_outages as fkec


def _summary(regions, total_served, outages=None):
    return {
        "totalServed": total_served,
        "outages": outages or [],
        "regionDataSets": [{"id": "zipCode", "description": "Zip Code", "regions": regions}],
        "lastUpdate": 1783984214189,
        "configurationId": 1782241490363,
    }


def _region(zip_code, number_out, number_served):
    return {"id": zip_code, "description": "Zip Code", "numberOut": number_out, "numberServed": number_served}


class TestOutagesToRecords:
    def test_parses_real_captured_shape(self):
        # Exact real response captured 2026-07-13 (a quiet day, zero
        # active outages across all six ZIP codes).
        data = _summary(
            regions=[
                _region("33051", 0, 1546),
                _region("33036", 0, 4662),
                _region("33050", 0, 9880),
                _region("33070", 0, 4904),
                _region("33037", 0, 13329),
                _region("33001", 0, 346),
            ],
            total_served=34475,
        )
        records = fkec.outages_to_records(data)
        assert records == [{"county": "Monroe", "customers_out": 0, "customers_served": 34475}]

    def test_customers_out_summed_across_all_zip_codes(self):
        data = _summary(
            regions=[
                _region("33051", 12, 1546),
                _region("33036", 0, 4662),
                _region("33050", 5, 9880),
            ],
            total_served=16088,
        )
        records = fkec.outages_to_records(data)
        assert records[0]["customers_out"] == 17

    def test_customers_served_uses_authoritative_total_not_sum_of_parts(self):
        # Real observed gap 2026-07-13: summing the six ZIPs' own
        # numberServed came to 34,667, but the response's own
        # totalServed said 34,475 - the authoritative field must win,
        # not a locally re-derived sum.
        data = _summary(
            regions=[_region("33051", 0, 1546), _region("33036", 0, 4662)],
            total_served=6000,  # deliberately does not equal 1546+4662=6208
        )
        records = fkec.outages_to_records(data)
        assert records[0]["customers_served"] == 6000

    def test_missing_region_datasets_returns_empty(self):
        assert fkec.outages_to_records({"totalServed": 0, "outages": []}) == []

    def test_empty_regions_list_returns_empty(self):
        data = _summary(regions=[], total_served=0)
        assert fkec.outages_to_records(data) == []

    def test_none_data_returns_empty(self):
        assert fkec.outages_to_records(None) == []

    def test_missing_number_out_defaults_to_zero(self):
        data = _summary(regions=[{"id": "33051", "numberServed": 1546}], total_served=1546)
        records = fkec.outages_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_total_served_defaults_to_zero(self):
        data = {
            "outages": [],
            "regionDataSets": [{"id": "zipCode", "regions": [_region("33051", 0, 1546)]}],
        }
        records = fkec.outages_to_records(data)
        assert records[0]["customers_served"] == 0


class TestGetFkecRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fkec, "fetch_fkec_outages", lambda: None)
        assert fkec.get_fkec_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = _summary(regions=[_region("33051", 3, 1546)], total_served=1546)
        monkeypatch.setattr(fkec, "fetch_fkec_outages", lambda: data)
        records = fkec.get_fkec_records()
        assert records == [{"county": "Monroe", "customers_out": 3, "customers_served": 1546}]

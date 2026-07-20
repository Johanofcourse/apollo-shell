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


class TestEpochMsToIso:
    def test_converts_real_epoch_ms_to_naive_utc_iso(self):
        # Real value captured 2026-07-19: 1784513967890 ms -> a real UTC
        # instant, not local time - the whole project stores naive
        # timestamps that are implicitly UTC (see county_status.py's own
        # comments on this), so no tzinfo should survive the conversion.
        result = clay._epoch_ms_to_iso(1784513967890)
        assert result == "2026-07-20T02:19:27.890000"

    def test_none_returns_none(self):
        assert clay._epoch_ms_to_iso(None) is None

    def test_zero_returns_none(self):
        # 0 is falsy and also not a real timestamp Clay would ever send -
        # estimateTime in particular is legitimately absent (no crew
        # assigned yet), not epoch-zero.
        assert clay._epoch_ms_to_iso(0) is None


class TestIncidentsToRecords:
    def test_parses_real_captured_shape(self):
        # Exact real response shape captured 2026-07-19 (one real
        # incident, Marion County per the county rollup, crew not yet
        # assigned).
        data = {
            "outages": [{
                "id": "472456", "nbrOut": 1,
                "timeOff": 1784513967890, "estimateTime": 1784521167967,
                "crewAssigned": False, "planned": False,
                "x": 154175, "y": 105483,
            }],
            "regionDataSets": [],
        }
        records = clay.incidents_to_records(data)
        assert records == [{
            "incident_id": "472456",
            "utility": "Clay Electric Cooperative",
            "customer_count": 1,
            "start_time": "2026-07-20T02:19:27.890000",
            "estimated_restoration": "2026-07-20T04:19:27.967000",
            "crew_assigned": False,
            "planned": False,
            "raw_x": 154175,
            "raw_y": 105483,
        }]

    def test_deliberately_no_county_field(self):
        # The real, checked reason there's no county here at all - see
        # this function's own docstring. A test that a "county" key
        # never sneaks in matters as much as testing what's present.
        data = {"outages": [{"id": "1", "nbrOut": 1, "timeOff": 1, "estimateTime": None,
                              "crewAssigned": True, "planned": True, "x": 1, "y": 1}]}
        records = clay.incidents_to_records(data)
        assert "county" not in records[0]

    def test_missing_estimate_time_returns_none_not_zero(self):
        # A real, common case - no crew assigned yet means no real
        # estimate to give, not a fake "epoch zero" restoration time.
        data = {"outages": [{"id": "1", "nbrOut": 1, "timeOff": 1784513967890, "estimateTime": None,
                              "crewAssigned": False, "planned": False, "x": 1, "y": 1}]}
        records = clay.incidents_to_records(data)
        assert records[0]["estimated_restoration"] is None

    def test_empty_outages_returns_empty(self):
        assert clay.incidents_to_records({"outages": []}) == []

    def test_none_data_returns_empty(self):
        assert clay.incidents_to_records(None) == []

    def test_missing_nbr_out_defaults_to_zero(self):
        data = {"outages": [{"id": "1", "timeOff": 1, "estimateTime": None,
                              "crewAssigned": False, "planned": False, "x": 1, "y": 1}]}
        records = clay.incidents_to_records(data)
        assert records[0]["customer_count"] == 0


class TestGetClayIncidentRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(clay, "fetch_clay_outages", lambda: None)
        assert clay.get_clay_incident_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        data = {"outages": [{"id": "1", "nbrOut": 1, "timeOff": 1784513967890, "estimateTime": None,
                              "crewAssigned": False, "planned": False, "x": 1, "y": 1}]}
        monkeypatch.setattr(clay, "fetch_clay_outages", lambda: data)
        records = clay.get_clay_incident_records()
        assert len(records) == 1
        assert records[0]["incident_id"] == "1"

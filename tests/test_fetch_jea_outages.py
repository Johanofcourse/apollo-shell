"""
Tests for parse_jea_areas() in fetch_jea_outages.py - the ZIP-level to
county-level aggregation logic, added 2026-07-09 when JEA (Kubra's
"Storm Center" product) was integrated as a fourth live utility source.

Network calls (the FCC reverse-geocode in _zip_to_county) are avoided by
pre-populating the module's _zip_county_cache directly, rather than
mocking requests - the cache is exactly the seam this module already
uses to avoid repeat network calls, so tests use the same seam.
"""

import fetch_jea_outages as jea
from fetch_jea_outages import parse_jea_areas


def _area(zip_code, cust_a, cust_s, etr=None, etr_confidence=None, n_out=0):
    return {
        "name": zip_code,
        "cust_a": {"val": cust_a},
        "cust_s": cust_s,
        "etr": etr,
        "etr_confidence": etr_confidence,
        "n_out": n_out,
        "gotoMap": {"bbox": None},
    }


class TestParseJeaAreas:
    def setup_method(self):
        # Real network lookups are avoided entirely by pre-seeding the
        # cache this module already checks first - not a mock, the same
        # real seam fetch_jea_outages uses to skip repeat FCC calls.
        jea._zip_county_cache.clear()
        jea._zip_county_cache["32225"] = "Duval"
        jea._zip_county_cache["32226"] = "Duval"
        jea._zip_county_cache["32084"] = "St. Johns"

    def test_zip_records_keep_one_row_per_zip(self):
        areas = [_area("32225", 10, 1000), _area("32226", 5, 500)]
        zip_records, _ = parse_jea_areas(areas)

        assert len(zip_records) == 2
        assert zip_records[0]["zip_code"] == "32225"
        assert zip_records[0]["county"] == "Duval"
        assert zip_records[0]["customers_out"] == 10
        assert zip_records[0]["customers_served"] == 1000

    def test_county_rollup_sums_multiple_zips_in_same_county(self):
        areas = [_area("32225", 10, 1000), _area("32226", 5, 500)]
        _, county_rollup = parse_jea_areas(areas)

        assert len(county_rollup) == 1, "both ZIPs are Duval, should collapse to one county row"
        row = county_rollup[0]
        assert row["county"] == "Duval"
        assert row["customers_out"] == 15
        assert row["customers_served"] == 1500

    def test_county_rollup_keeps_different_counties_separate(self):
        areas = [_area("32225", 10, 1000), _area("32084", 3, 300)]
        _, county_rollup = parse_jea_areas(areas)

        counties = {row["county"]: row for row in county_rollup}
        assert counties["Duval"]["customers_out"] == 10
        assert counties["St. Johns"]["customers_out"] == 3

    def test_percentage_out_computed_correctly(self):
        areas = [_area("32225", 25, 1000)]
        zip_records, county_rollup = parse_jea_areas(areas)

        assert zip_records[0]["percentage_out"] == 2.5
        assert county_rollup[0]["customers_out"] == 25

    def test_etr_null_sentinel_normalized_to_none(self):
        # JEA's own placeholder for "no restoration estimate yet" is the
        # literal string "ETR-NULL" - showing that to users would read
        # as a bug, not an intentional state, so it's normalized here.
        areas = [_area("32225", 5, 1000, etr="ETR-NULL")]
        zip_records, _ = parse_jea_areas(areas)

        assert zip_records[0]["etr"] is None

    def test_real_etr_passed_through_unchanged(self):
        areas = [_area("32225", 5, 1000, etr="2026-07-10T03:33:54Z", etr_confidence="HIGH")]
        zip_records, _ = parse_jea_areas(areas)

        assert zip_records[0]["etr"] == "2026-07-10T03:33:54Z"
        assert zip_records[0]["etr_confidence"] == "HIGH"

    def test_zip_with_unresolvable_county_excluded_from_rollup_not_from_zip_records(self):
        # A ZIP that fails to resolve to any county (no cache entry, no
        # usable bbox) still needs to show up in the raw per-ZIP table -
        # it just can't contribute to a county-level lifecycle row.
        areas = [_area("99999", 5, 1000)]
        zip_records, county_rollup = parse_jea_areas(areas)

        assert len(zip_records) == 1
        assert zip_records[0]["county"] is None
        assert county_rollup == []

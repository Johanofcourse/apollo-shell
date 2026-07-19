"""
Tests for street_county_resolver.py - added 2026-07-18 once a real active
CHELCO outage showed the streetsAffected field (real street names, no
per-street customer count or coordinates) populates during a genuine
event, unlike the "seen empty every time" assumption in the older
fetch_*_outages.py comments.

Network calls (the real Nominatim geocoding in resolve_street()) are
mocked directly for the low-level function; everything built on top of
OutageDatabase's cache (resolve_streets(), active_counties()) is tested
by pre-populating that real cache instead, the same seam
test_fetch_jea_outages.py already established for this kind of test -
not a mock, the same real cache the module itself checks first.
"""

import os
import tempfile

import pytest

import street_county_resolver as scr
from database import OutageDatabase


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    # resolve_street()'s real time.sleep(1.1) between candidate counties
    # is real, deliberate rate-limiting for Nominatim's live usage policy
    # (see the module's own comment) - correct in production, but tests
    # only need the logic exercised, not to actually wait several real
    # seconds per case.
    monkeypatch.setattr(scr.time, "sleep", lambda seconds: None)


class _FakeResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return self._results


CHELCO = "Choctawhatchee Electric Cooperative, Inc."


class TestResolveStreet:
    def test_matches_exactly_one_candidate_county(self, monkeypatch):
        # Real behavior confirmed 2026-07-18: "Howell Bluff Rd" (a real
        # CHELCO street) matches only within Walton County among CHELCO's
        # 4 known counties.
        def fake_get(url, params, headers, timeout):
            county = params["q"].split(",")[1].strip()
            return _FakeResponse([{"display_name": "match"}] if county == "Walton County" else [])

        monkeypatch.setattr(scr.requests, "get", fake_get)

        assert scr.resolve_street(CHELCO, "Howell Bluff Rd") == "Walton"

    def test_no_match_in_any_known_county_returns_none(self, monkeypatch):
        monkeypatch.setattr(scr.requests, "get", lambda *a, **kw: _FakeResponse([]))

        assert scr.resolve_street(CHELCO, "Totally Fake Made Up Street") is None

    def test_matching_more_than_one_county_is_treated_as_ambiguous(self, monkeypatch):
        # A street name that genuinely exists in two of CHELCO's own
        # counties isn't confidently attributable to either - real
        # honesty-over-polish call, same as this project's other thin/
        # ambiguous-data handling.
        monkeypatch.setattr(scr.requests, "get", lambda *a, **kw: _FakeResponse([{"display_name": "match"}]))

        assert scr.resolve_street(CHELCO, "Main St") is None

    def test_a_request_exception_counts_as_no_match_for_that_county(self, monkeypatch):
        import requests as real_requests

        def fake_get(url, params, headers, timeout):
            raise real_requests.exceptions.RequestException("boom")

        monkeypatch.setattr(scr.requests, "get", fake_get)

        assert scr.resolve_street(CHELCO, "Howell Bluff Rd") is None

    def test_unknown_utility_has_no_candidates_and_returns_none(self, monkeypatch):
        monkeypatch.setattr(scr.requests, "get", lambda *a, **kw: _FakeResponse([{"display_name": "match"}]))

        assert scr.resolve_street("Some Utility Not In KNOWN_TERRITORIES", "Main St") is None


class TestResolveStreets:
    def test_cached_streets_never_touch_the_network(self, db_path, monkeypatch):
        db = OutageDatabase(db_path)
        db.save_street_county(CHELCO, "Howell Bluff Rd", "Walton")

        def boom(*a, **kw):
            raise AssertionError("should not call the network for an already-cached street")
        monkeypatch.setattr(scr.requests, "get", boom)

        result = scr.resolve_streets(CHELCO, ["Howell Bluff Rd"], db)
        db.close()

        assert result == {"Howell Bluff Rd": "Walton"}

    def test_uncached_streets_get_resolved_and_saved(self, db_path, monkeypatch):
        db = OutageDatabase(db_path)

        def fake_get(url, params, headers, timeout):
            county = params["q"].split(",")[1].strip()
            return _FakeResponse([{"display_name": "match"}] if county == "Walton County" else [])
        monkeypatch.setattr(scr.requests, "get", fake_get)

        result = scr.resolve_streets(CHELCO, ["Howell Bluff Rd"], db)
        cached_after = db.get_cached_street_counties(CHELCO, ["Howell Bluff Rd"])
        db.close()

        assert result == {"Howell Bluff Rd": "Walton"}
        assert cached_after == {"Howell Bluff Rd": "Walton"}

    def test_max_new_lookups_caps_real_network_calls_this_run(self, db_path, monkeypatch):
        # The real reason this cap exists: a real active outage can carry
        # dozens of uncached streets, each needing several rate-limited
        # network round trips - uncapped, that would block the live
        # 15-minute poll cycle for every other utility behind it.
        db = OutageDatabase(db_path)
        call_count = {"n": 0}

        def fake_get(url, params, headers, timeout):
            call_count["n"] += 1
            return _FakeResponse([])
        monkeypatch.setattr(scr.requests, "get", fake_get)

        result = scr.resolve_streets(CHELCO, ["A Rd", "B Rd", "C Rd"], db, max_new_lookups=1)
        db.close()

        assert len(result) == 1
        assert call_count["n"] == len(scr.KNOWN_TERRITORIES[CHELCO])

    def test_streets_beyond_the_cap_are_absent_not_none(self, db_path, monkeypatch):
        # Absent means "not checked yet, try again next cycle" - a stored
        # None would incorrectly claim "checked, no match" for a street
        # that was never actually looked up this call.
        db = OutageDatabase(db_path)
        monkeypatch.setattr(scr.requests, "get", lambda *a, **kw: _FakeResponse([]))

        result = scr.resolve_streets(CHELCO, ["A Rd", "B Rd"], db, max_new_lookups=0)
        cached_after = db.get_cached_street_counties(CHELCO, ["A Rd", "B Rd"])
        db.close()

        assert result == {}
        assert cached_after == {}

    def test_cached_streets_do_not_count_against_the_cap(self, db_path, monkeypatch):
        db = OutageDatabase(db_path)
        db.save_street_county(CHELCO, "Cached Rd", "Walton")
        monkeypatch.setattr(scr.requests, "get", lambda *a, **kw: _FakeResponse([{"display_name": "match"}]))

        result = scr.resolve_streets(CHELCO, ["Cached Rd", "New Rd"], db, max_new_lookups=1)
        db.close()

        assert set(result.keys()) == {"Cached Rd", "New Rd"}

    def test_a_confirmed_unresolvable_street_is_still_cached_as_none(self, db_path, monkeypatch):
        db = OutageDatabase(db_path)
        monkeypatch.setattr(scr.requests, "get", lambda *a, **kw: _FakeResponse([]))

        scr.resolve_streets(CHELCO, ["Fake Rd"], db)
        cached_after = db.get_cached_street_counties(CHELCO, ["Fake Rd"])
        db.close()

        assert cached_after == {"Fake Rd": None}


class TestActiveCounties:
    def test_returns_sorted_distinct_counties_from_resolved_streets(self, db_path):
        db = OutageDatabase(db_path)
        db.save_street_county(CHELCO, "Howell Bluff Rd", "Walton")
        db.save_street_county(CHELCO, "Cotton Creek Rd", "Okaloosa")
        db.save_street_county(CHELCO, "White Creek Rd", "Walton")

        result = scr.active_counties(CHELCO, ["Howell Bluff Rd", "Cotton Creek Rd", "White Creek Rd"], db)
        db.close()

        assert result == ["Okaloosa", "Walton"]

    def test_unresolved_streets_are_silently_excluded_not_guessed(self, db_path):
        db = OutageDatabase(db_path)
        db.save_street_county(CHELCO, "Howell Bluff Rd", "Walton")
        db.save_street_county(CHELCO, "Fake Rd", None)

        result = scr.active_counties(CHELCO, ["Howell Bluff Rd", "Fake Rd"], db)
        db.close()

        assert result == ["Walton"]

    def test_empty_street_list_returns_empty(self, db_path):
        db = OutageDatabase(db_path)
        result = scr.active_counties(CHELCO, [], db)
        db.close()

        assert result == []

    def test_none_street_list_returns_empty(self, db_path):
        db = OutageDatabase(db_path)
        result = scr.active_counties(CHELCO, None, db)
        db.close()

        assert result == []

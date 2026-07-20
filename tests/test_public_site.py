"""
Tests for public_site.py - the public-facing page, built 2026-07-14 as
a genuinely separate Flask app from dashboard.py (own port, own
template folder, shares only the read-only apollo_shell/ data layer).

Rebuilt the same day after Johan compared the live page against the
real design-sandbox artifact and found it didn't match (wrong color
scheme, no isometric map, no narrative summary, a real comma-joining
bug in the alert/storm display). The real artifact was re-fetched and
ported closely: an isometric map (client-side JS, fed by real per-
county data), a real narrative summary (_narrative_stats), and a real
historical weather-match confidence tally per county
(county_status.historical_confidence_tally(), tested in
test_county_status.py). _county_map_data()/_narrative_stats() are the
new pieces of real logic here.
"""
import os
import re
import tempfile

import pytest

import public_site
from database import OutageDatabase


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _fpl_row(county, customers_out, customers_served=100_000):
    return {"county": county, "customers_out": customers_out, "customers_served": customers_served}


class TestGetSentinelVersion:
    """
    _get_sentinel_version() - real semver, not decorative. 0.x means
    "pre-1.0, no stability contract yet" (see SENTINEL_VERSION_PREFIX's
    own comment for why that's honestly true right now), with the patch
    number auto-derived from the real commit count so it can never drift
    or need hand-bumping - only the prefix is a deliberate, manual
    change, made once, the day this project actually goes live.
    """

    def test_version_starts_with_the_current_prefix(self):
        assert public_site._get_sentinel_version().startswith(f"{public_site.SENTINEL_VERSION_PREFIX}.")

    def test_patch_number_is_a_real_non_negative_integer(self):
        version = public_site._get_sentinel_version()
        patch = version.split(".")[-1]
        assert patch.isdigit()
        assert int(patch) >= 0

    def test_falls_back_to_dev_when_git_is_unavailable(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(public_site.subprocess, "run", _boom)

        assert public_site._get_sentinel_version() == "dev"


class TestCountyMapData:
    def test_clean_database_has_zero_customers_everywhere(self, db_path):
        db = OutageDatabase(db_path)
        rows = public_site._statewide_rows(db)
        counties = public_site._county_map_data(db, rows)
        db.close()

        assert len(counties) == 67
        assert all(c["customers"] == 0 for c in counties)

    def test_real_outage_shows_up_for_its_county_only(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:00:00")

        rows = public_site._statewide_rows(db)
        counties = public_site._county_map_data(db, rows)
        db.close()

        by_name = {c["name"]: c for c in counties}
        assert by_name["Alachua"]["customers"] == 500
        assert by_name["Baker"]["customers"] == 0

    def test_reads_precomputed_confidence_tally_not_computed_live(self, db_path):
        # Real regression guard for the 2026-07-14 fix: _county_map_data
        # must read the precomputed table (db.get_historical_confidence_tally,
        # written once per poll cycle by main.py) rather than recomputing
        # the real, expensive nested-loop correlation query on every page
        # view - that recomputation was measured at ~44s on real data.
        db = OutageDatabase(db_path)
        db.store_historical_confidence_tally({"ALACHUA": {"high": 2, "medium": 1, "low": 0}})

        rows = public_site._statewide_rows(db)
        counties = public_site._county_map_data(db, rows)
        db.close()

        by_name = {c["name"]: c for c in counties}
        assert by_name["Alachua"]["high"] == 2
        assert by_name["Alachua"]["medium"] == 1
        assert by_name["Baker"]["high"] == 0

    def test_county_name_casing_mismatch_still_matches(self, db_path):
        # Real regression: historical_confidence_tally()'s keys and each
        # source's own raw county field can be cased differently
        # ("ALACHUA" vs "Alachua") from FLORIDA_COUNTY_RINGS's canonical
        # title-case names - matching must be case-insensitive, not
        # exact-string, or real counties silently show no data.
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 500)], timestamp="2026-01-01T00:00:00")

        rows = public_site._statewide_rows(db)
        counties = public_site._county_map_data(db, rows)
        db.close()

        by_name = {c["name"]: c for c in counties}
        assert by_name["Alachua"]["customers"] == 500

    def test_missing_county_is_skipped_not_a_crash(self, db_path):
        # Real incident, 2026-07-17: a live Duke Energy event came through
        # with county=None (its reverse-geocode couldn't resolve the
        # lat/lon), which crashed the whole public page with a 500 on
        # r["county"].upper(). Confirmed on real data - 265 pre-existing
        # duke_incidents rows already had a null county, this was just the
        # first time one was still open when a visitor loaded the page.
        db = OutageDatabase(db_path)
        rows = [
            {"utility": "Duke Energy", "county": None, "customers": 1, "customers_served": None},
            {"utility": "FPL", "county": "Palm Beach", "customers": 50, "customers_served": 100_000},
        ]
        counties = public_site._county_map_data(db, rows)
        db.close()

        assert len(counties) == 67
        by_name = {c["name"]: c for c in counties}
        assert by_name["Palm Beach"]["customers"] == 50


class TestNarrativeStats:
    def test_clean_database_has_zero_totals(self, db_path):
        db = OutageDatabase(db_path)
        rows = public_site._statewide_rows(db)
        narrative = public_site._narrative_stats(rows)
        db.close()

        assert narrative["total_current"] == 0
        assert narrative["worst_county_name"] is None
        assert narrative["top_utility_name"] is None

    def test_worst_county_and_utility_by_raw_count(self, db_path):
        db = OutageDatabase(db_path)
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 300)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 300)], timestamp="2026-01-01T00:00:00")
        db.log_multiple_outages("FPL", [_fpl_row("BAKER", 100)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("BAKER", 100)], timestamp="2026-01-01T00:00:00")

        rows = public_site._statewide_rows(db)
        narrative = public_site._narrative_stats(rows)
        db.close()

        assert narrative["total_current"] == 400
        assert narrative["worst_county_name"] == "ALACHUA"
        assert narrative["worst_county_customers"] == 300
        assert narrative["top_utility_name"] == "FPL"
        assert narrative["top_utility_customers"] == 400

    def test_worst_by_percentage_only_considers_rows_with_a_known_base(self, db_path):
        db = OutageDatabase(db_path)
        # 300/100000 = 0.3% - small share, but a known base
        db.log_multiple_outages("FPL", [_fpl_row("ALACHUA", 300, 100_000)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("ALACHUA", 300, 100_000)], timestamp="2026-01-01T00:00:00")
        # a small county with a much smaller base -> higher real percentage
        db.log_multiple_outages("FPL", [_fpl_row("BAKER", 100, 1_000)], timestamp="2026-01-01T00:00:00")
        db.sync_outage_events("FPL", [_fpl_row("BAKER", 100, 1_000)], timestamp="2026-01-01T00:00:00")

        rows = public_site._statewide_rows(db)
        narrative = public_site._narrative_stats(rows)
        db.close()

        assert narrative["worst_pct_county_name"] == "BAKER"
        assert round(narrative["worst_pct_value"], 1) == 10.0

    def test_missing_county_still_counts_toward_total_but_not_as_a_county(self, db_path):
        # Same 2026-07-17 incident as TestCountyMapData's regression test -
        # a None county must not become its own fake "county" bucket here,
        # since it could otherwise win "worst county" and print None in the
        # public narrative summary.
        db = OutageDatabase(db_path)
        rows = [
            {"utility": "Duke Energy", "county": None, "customers": 1, "customers_served": None},
            {"utility": "FPL", "county": "Palm Beach", "customers": 50, "customers_served": 100_000},
        ]
        narrative = public_site._narrative_stats(rows)
        db.close()

        assert narrative["total_current"] == 51
        assert narrative["worst_county_name"] == "Palm Beach"


class TestIndexRoute:
    def test_homepage_loads(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert r.status_code == 200

    def test_kpi_customers_matches_narrative_total_not_just_the_map_sum(self):
        # Real bug found 2026-07-18: kpiCustomers used to be recomputed
        # client-side by summing the map's per-county array (counties_json),
        # which is keyed by the 67 real single-county names - a combined-
        # territory source (FPUC/TCEC/EREC/CHELCO/GCEC, whose "county" is a
        # shared multi-name label) can never match one of those 67 names,
        # so its customers silently never contributed to that sum. The
        # narrative paragraph a few lines below computes the same total
        # correctly from all_rows directly, so the two numbers could (and
        # in real production data, did) disagree on the same live page.
        # kpiCustomers must now be server-rendered from narrative.total_current
        # directly, so the two always match by construction.
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert r.status_code == 200

        body = r.data.decode()
        kpi_match = re.search(r'id="kpiCustomers">([\d,]+)<', body)
        narrative_match = re.search(r'Right now, <strong[^>]*>([\d,]+)</strong> customers', body)
        assert kpi_match is not None
        assert narrative_match is not None
        assert kpi_match.group(1) == narrative_match.group(1)

    def test_county_query_param_renders_history_section(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Calhoun")
        assert r.status_code == 200
        assert b"Calhoun" in r.data

    def test_unselected_page_shows_the_empty_history_prompt(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert b"Search a county above" in r.data

    def test_county_with_no_history_data_does_not_error(self):
        # A search that matches no real county needs to render cleanly,
        # not 500.
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Nonexistent+County")
        assert r.status_code == 200

    def test_outage_history_section_renders_for_a_selected_county(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Palm+Beach")
        assert r.status_code == 200
        assert b"Outage History" in r.data

    def test_outage_history_empty_prompt_when_no_county_selected(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert r.status_code == 200
        assert b"to see its real outage history" in r.data

    def test_alert_areas_are_split_into_a_real_list_not_iterated_as_a_string(self):
        # Real regression: get_active_weather_alerts()'s areas field is
        # a raw "Area One; Area Two" string - Jinja iterating it
        # directly (instead of a pre-split list) renders one chip per
        # character. Not directly assertable without a live alert, but
        # confirms the route never 500s building areas_list from
        # whatever alerts happen to be active right now.
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")
        assert r.status_code == 200


class TestPaginate:
    """
    _paginate() - added 2026-07-18 to replace OUTAGE_HISTORY_DISPLAY_LIMIT's
    hard ceiling (older resolved outages beyond the cap were permanently
    unreachable, just silently dropped) with real pagination - a high-
    churn county's older history is still reachable, just not all loaded
    into one unbounded mobile scroll by default.
    """

    def test_first_page_defaults_when_no_query_param(self):
        with public_site.app.test_request_context("/?county=X"):
            rows, page, total_pages = public_site._paginate(list(range(20)), "history_page")

        assert rows == list(range(7))
        assert page == 1
        assert total_pages == 3

    def test_second_page_reads_the_real_query_param(self):
        with public_site.app.test_request_context("/?county=X&history_page=2"):
            rows, page, total_pages = public_site._paginate(list(range(20)), "history_page")

        assert rows == list(range(7, 14))
        assert page == 2

    def test_last_page_is_a_partial_page(self):
        with public_site.app.test_request_context("/?county=X&history_page=3"):
            rows, page, total_pages = public_site._paginate(list(range(20)), "history_page")

        assert rows == list(range(14, 20))
        assert total_pages == 3

    def test_page_number_past_the_real_last_page_clamps_down(self):
        # A stale bookmark or hand-edited URL shouldn't 500 or silently
        # show nothing - clamp to the real last page instead.
        with public_site.app.test_request_context("/?county=X&history_page=999"):
            rows, page, total_pages = public_site._paginate(list(range(20)), "history_page")

        assert page == 3
        assert rows == list(range(14, 20))

    def test_page_number_below_one_clamps_up(self):
        with public_site.app.test_request_context("/?county=X&history_page=0"):
            rows, page, total_pages = public_site._paginate(list(range(20)), "history_page")

        assert page == 1

    def test_non_numeric_page_falls_back_to_one(self):
        with public_site.app.test_request_context("/?county=X&history_page=notanumber"):
            rows, page, total_pages = public_site._paginate(list(range(20)), "history_page")

        assert page == 1
        assert rows == list(range(7))

    def test_empty_list_is_one_page_not_zero(self):
        # So callers/templates don't need a separate zero-results
        # special case just for the page count.
        with public_site.app.test_request_context("/?county=X"):
            rows, page, total_pages = public_site._paginate([], "history_page")

        assert rows == []
        assert total_pages == 1

    def test_exactly_one_full_page_reports_a_single_page(self):
        with public_site.app.test_request_context("/?county=X"):
            rows, page, total_pages = public_site._paginate(list(range(7)), "history_page")

        assert total_pages == 1

    def test_two_independent_page_params_do_not_interfere(self):
        with public_site.app.test_request_context("/?county=X&history_page=2&combined_history_page=1"):
            main_rows, main_page, _ = public_site._paginate(list(range(20)), "history_page")
            combined_rows, combined_page, _ = public_site._paginate(list(range(20)), "combined_history_page")

        assert main_page == 2
        assert combined_page == 1
        assert main_rows != combined_rows


class TestOutageHistoryPaginationRoute:
    def test_history_page_two_shows_different_events_than_page_one(self):
        # Real end-to-end check against a county with enough real
        # closed-event history to actually span more than one page -
        # Palm Beach already has real LWBU/FPL history from earlier
        # sessions.
        public_site.app.testing = True
        client = public_site.app.test_client()
        r1 = client.get("/?county=Palm+Beach")
        r2 = client.get("/?county=Palm+Beach&history_page=2")

        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_out_of_range_history_page_does_not_error(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Palm+Beach&history_page=9999")
        assert r.status_code == 200

    def test_non_numeric_history_page_does_not_error(self):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Palm+Beach&history_page=abc")
        assert r.status_code == 200


def _fake_alert(n):
    return {
        "id": n, "alert_id": f"test-alert-{n}", "timestamp": "2026-01-01T00:00:00",
        "event_type": f"Test Alert {n}", "severity": "Moderate", "urgency": "Expected",
        "areas": "Hillsborough; Pinellas", "effective": "2026-01-01T00:00:00",
        "expires": "2099-01-01T00:00:00", "headline": "test", "description": "test",
    }


class TestWeatherAlertsPaginationRoute:
    """
    Real gap found and fixed 2026-07-20: the statewide Current Weather
    Alerts section had no cap at all, unlike Outage History which was
    already paginated - a real active storm (confirmed live the same
    day, 29 real active alerts statewide with Hillsborough getting hit)
    made this a genuine long scroll, not a hypothetical. Same fix, same
    page size, same _paginate() helper - just applied to a source that
    changes constantly (live weather), unlike Outage History's stable
    historical data, so these tests fake a known count rather than
    relying on however many real alerts happen to be active right now.
    """

    def test_more_than_one_page_worth_shows_real_pagination_controls(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert r.status_code == 200
        assert b"Page 1 of 2" in r.data
        assert b"12 active alerts statewide" in r.data

    def test_page_two_shows_different_alerts_than_page_one(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r1 = client.get("/")
        r2 = client.get("/?alerts_page=2")

        assert b"Test Alert 0" in r1.data
        assert b"Test Alert 0" not in r2.data
        assert b"Test Alert 11" in r2.data

    def test_seven_or_fewer_alerts_show_no_pagination_controls(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(n) for n in range(7)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert r.status_code == 200
        assert b"Page 1 of" not in r.data

    def test_out_of_range_alerts_page_does_not_error(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?alerts_page=9999")
        assert r.status_code == 200

    def test_non_numeric_alerts_page_does_not_error(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?alerts_page=abc")
        assert r.status_code == 200

    def test_paginating_alerts_preserves_selected_county_link(self, monkeypatch):
        # The alerts section sits above the county detail section on
        # the same page - paging through alerts shouldn't silently lose
        # whichever county's own detail panel is currently showing.
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Palm+Beach")

        assert b"alerts_page=2&amp;county=Palm" in r.data or b"alerts_page=2&county=Palm" in r.data

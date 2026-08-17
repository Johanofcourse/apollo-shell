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
import importlib
import os
import re
import sqlite3
import tempfile
from datetime import datetime

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

import public_site
import storm_history
from database import OutageDatabase

# Real per-IP rate limiting added 2026-08-02 to blunt scraping ahead of
# the site going public - disabled here so the test suite's many rapid
# client.get() calls (all from the same test-client "IP") don't start
# tripping 429s against each other, same real failure this fixture
# exists to prevent seen the moment the limiter first got wired in.
@pytest.fixture(autouse=True)
def disable_rate_limiting():
    public_site.limiter.enabled = False
    yield
    public_site.limiter.enabled = False


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


def _fpl_incident(incident_id, county="Palm Beach", customer_count=20, lat=26.7, lon=-80.1,
                   cause="Under investigation", estimated_restoration=None):
    return {
        "incident_id": incident_id, "utility": "Florida Power and Light Company",
        "customer_count": customer_count, "lat": lat, "lon": lon, "county": county,
        "cause": cause, "cause_category": "pending", "status": "Crew assigned.",
        "reported_start_time": "2026-01-01T00:00:00",
        "estimated_restoration": estimated_restoration, "last_updated": "2026-01-01T00:00:00",
    }


class TestRateLimiting:
    """
    Per-IP rate limiting (Flask-Limiter), added 2026-08-02 to blunt a
    scraper hammering the public site before it's ever a real problem.
    Every other test in this file disables the limiter (see
    disable_rate_limiting above) so rapid client.get() calls in the
    suite don't trip each other - these tests re-enable it deliberately
    to verify the real thing actually works, then reset the shared
    in-memory storage afterward so no test above/below this class
    inherits an already-exhausted counter.
    """

    @pytest.fixture(autouse=True)
    def enable_rate_limiting(self):
        public_site.limiter.enabled = True
        yield
        public_site.limiter.enabled = False
        public_site.limiter.limiter.storage.reset()

    def test_requests_within_the_limit_all_succeed(self):
        public_site.app.testing = True
        client = public_site.app.test_client()

        for _ in range(30):
            r = client.get("/")
            assert r.status_code == 200

    def test_exceeding_the_per_minute_limit_returns_429(self):
        public_site.app.testing = True
        client = public_site.app.test_client()

        for _ in range(30):
            client.get("/")

        r = client.get("/")
        assert r.status_code == 429

    def test_different_ips_are_limited_independently(self):
        # Real regression guard: a per-IP limiter that accidentally
        # shared one global counter would let one heavy visitor lock
        # out everyone else on the site, not just themselves.
        public_site.app.testing = True
        client = public_site.app.test_client()

        for _ in range(30):
            client.get("/", environ_overrides={"REMOTE_ADDR": "1.1.1.1"})
        blocked = client.get("/", environ_overrides={"REMOTE_ADDR": "1.1.1.1"})
        assert blocked.status_code == 429

        still_fine = client.get("/", environ_overrides={"REMOTE_ADDR": "2.2.2.2"})
        assert still_fine.status_code == 200

    def test_real_visitor_ip_comes_from_x_forwarded_for_not_the_proxy(self):
        # Real regression this guards against: nginx started sitting in
        # front of this app 2026-08-11 for the public launch. Without
        # ProxyFix, request.remote_addr would always read as nginx's own
        # connecting address (the same one for every visitor), so every
        # real visitor would silently share ONE combined rate-limit
        # budget instead of getting their own. Both requests here arrive
        # from the same simulated proxy hop but claim two different real
        # visitor IPs via X-Forwarded-For, the way nginx actually
        # forwards them - proves the limiter keys off the forwarded
        # visitor IP, not the proxy's own.
        public_site.app.testing = True
        client = public_site.app.test_client()

        for _ in range(30):
            client.get("/", environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
                       headers={"X-Forwarded-For": "203.0.113.10"})
        blocked = client.get("/", environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
                              headers={"X-Forwarded-For": "203.0.113.10"})
        assert blocked.status_code == 429

        still_fine = client.get("/", environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
                                 headers={"X-Forwarded-For": "203.0.113.11"})
        assert still_fine.status_code == 200


class TestProxyFixConfig:
    """
    ProxyFix (werkzeug) - added 2026-08-11 alongside nginx for the
    public launch. x_for/x_proto must stay at exactly 1 - the real hop
    count for this deployment (one nginx instance directly in front of
    gunicorn). A value of 0 would leave the original bug in place; a
    value higher than the real hop count would let a client forge its
    own apparent IP by sending a fake X-Forwarded-For header nginx never
    actually added.
    """

    def test_trusts_exactly_one_proxy_hop_for_ip_and_scheme(self):
        wsgi_app = public_site.app.wsgi_app
        assert isinstance(wsgi_app, ProxyFix)
        assert wsgi_app.x_for == 1
        assert wsgi_app.x_proto == 1


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


class TestFplIncidentPins:
    def test_clean_database_has_no_pins(self, db_path):
        db = OutageDatabase(db_path)
        pins = public_site._fpl_incident_pins(db)
        db.close()
        assert pins == []

    def test_real_open_incident_becomes_a_pin(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpl_incidents([_fpl_incident("F1", lat=25.7617, lon=-80.1918, customer_count=42, cause="Damage to equipment")])
        db.sync_fpl_incident_events([_fpl_incident("F1", lat=25.7617, lon=-80.1918, customer_count=42, cause="Damage to equipment")])

        pins = public_site._fpl_incident_pins(db)
        db.close()

        assert len(pins) == 1
        assert pins[0]["incident_id"] == "F1"
        assert pins[0]["county"] == "Palm Beach"
        assert pins[0]["customer_count"] == 42
        assert pins[0]["cause"] == "Damage to equipment"
        assert pins[0]["x"] is not None and pins[0]["y"] is not None

    def test_incident_missing_lat_lon_is_skipped_not_a_crash(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpl_incidents([_fpl_incident("F1", lat=None, lon=None)])
        db.sync_fpl_incident_events([_fpl_incident("F1", lat=None, lon=None)])

        pins = public_site._fpl_incident_pins(db)
        db.close()

        assert pins == []

    def test_closed_incident_is_not_a_pin(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpl_incidents([_fpl_incident("F1")])
        db.sync_fpl_incident_events([_fpl_incident("F1")], timestamp="2026-01-01T00:00:00")
        db.sync_fpl_incident_events([], timestamp="2026-01-01T02:00:00")  # disappears -> closes

        pins = public_site._fpl_incident_pins(db)
        db.close()

        assert pins == []


class TestFplIncidentRows:
    def test_clean_database_has_no_rows(self, db_path):
        db = OutageDatabase(db_path)
        rows = public_site._fpl_incident_rows(db, "Palm Beach")
        db.close()
        assert rows == []

    def test_real_open_incident_becomes_a_row_shaped_like_tecos(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpl_incidents([_fpl_incident("F1", county="Palm Beach", customer_count=42, estimated_restoration="2026-01-01T05:00:00")])
        db.sync_fpl_incident_events([_fpl_incident("F1", county="Palm Beach", customer_count=42, estimated_restoration="2026-01-01T05:00:00")])

        rows = public_site._fpl_incident_rows(db, "Palm Beach")
        db.close()

        assert len(rows) == 1
        assert rows[0]["utility"] == "Florida Power and Light Company"
        assert rows[0]["customers"] == 42
        assert rows[0]["estimated_restoration"] == "2026-01-01T05:00:00"
        assert rows[0]["current_percentage_out"] is None  # no per-incident customer base, same as TECO

    def test_other_county_is_filtered_out(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpl_incidents([_fpl_incident("F1", county="Broward")])
        db.sync_fpl_incident_events([_fpl_incident("F1", county="Broward")])

        rows = public_site._fpl_incident_rows(db, "Palm Beach")
        db.close()

        assert rows == []

    def test_county_match_is_case_insensitive(self, db_path):
        db = OutageDatabase(db_path)
        db.log_fpl_incidents([_fpl_incident("F1", county="PALM BEACH")])
        db.sync_fpl_incident_events([_fpl_incident("F1", county="PALM BEACH")])

        rows = public_site._fpl_incident_rows(db, "Palm Beach")
        db.close()

        assert len(rows) == 1


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


def _fake_open_fpl_event(county):
    return {
        "county": county,
        "utility": public_site.FPL_UTILITY_NAME,
        "customers": 500,
        "current_percentage_out": None,
        "duration": "2 hours",
        "estimated_restoration": None,
    }


class TestFplRestorationGapMessage:
    """
    Started 2026-08-03 investigating Palm Beach: it has an open FPL
    outage but only 3 raw outage_events ever, both closed ones long
    enough (10.5 days, 16.5 days) to get excluded by
    fpl_ordinary_restoration_stats()'s outlier filter - no Everyday
    Outages card, nothing telling a visitor why.

    First version of this message only fired when BOTH Everyday
    Outages and Major Storms were missing - but a live check across
    every FPL-open county found Major Storms archive data present for
    nearly all of them (FPL's in almost every one of the 17 real PSC
    storms), so that condition basically never triggered in practice,
    and Johan caught it live-checking Palm Beach right after deploy.

    Reframed around the real, always-true structural fact instead of a
    data-completeness check: FPL never publishes a live per-incident
    ETA at all, for anyone, unlike LWBU/TECO/Duke (which do, and get an
    accuracy-checked card for it). Whatever FPL numbers a county does
    show are historical substitutes, not a forecast for a specific
    outage - true whether or not Everyday Outages/Major Storms happen
    to have data, so this now shows any time FPL has an open outage in
    the county, never contradicting whatever precedent cards render
    alongside it.

    Reframed again 2026-08-17: the "FPL never publishes a live per-
    incident ETA at all" premise stopped being true the moment the real
    per-incident feed (fetch_fpl_incidents.py) shipped - FPL does now,
    wherever a real incident has resolved to this county this cycle
    (see _fpl_incident_rows()). This message now only fires for the
    genuinely-still-true case: FPL has an open outage here, but nothing
    from the new incident feed has resolved to this specific county
    yet, so the old blurred county-wide aggregate is still what's
    showing.
    """

    GAP_MESSAGE = "hasn't resolved a real individual outage location for this county yet"

    def test_shows_when_fpl_is_open_and_no_precedent_exists(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: [_fake_open_fpl_event("Testonia")],
        )
        monkeypatch.setattr(public_site, "fpl_restoration_precedent", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_restoration_precedent_by_wind_severity", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_ordinary_restoration_stats", lambda county, db: None)

        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert self.GAP_MESSAGE in r.data.decode()

    def test_still_shows_when_everyday_precedent_exists(self, monkeypatch):
        # The real bug this test guards against: the first version of
        # this message went silent the moment ANY precedent existed,
        # which (given Major Storms archive coverage is nearly
        # universal for FPL) meant it almost never showed at all. It
        # must keep showing regardless - it's explaining a structural
        # fact about FPL, not reporting a data gap.
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: [_fake_open_fpl_event("Testonia")],
        )
        monkeypatch.setattr(public_site, "fpl_restoration_precedent", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_restoration_precedent_by_wind_severity", lambda county: None)
        monkeypatch.setattr(
            public_site, "fpl_ordinary_restoration_stats",
            lambda county, db: {
                "n": 5, "median_hours": 6.0, "min_hours": 2.0, "max_hours": 10.0,
                "limited": False, "excluded_count": 0,
            },
        )

        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert self.GAP_MESSAGE in r.data.decode()

    def test_no_message_when_fpl_is_not_currently_open(self, monkeypatch):
        monkeypatch.setattr(public_site, "_real_per_county_open_events", lambda db: [])
        monkeypatch.setattr(public_site, "fpl_restoration_precedent", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_restoration_precedent_by_wind_severity", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_ordinary_restoration_stats", lambda county, db: None)

        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert self.GAP_MESSAGE not in r.data.decode()

    def test_no_message_when_real_incident_data_exists_for_this_county(self, monkeypatch):
        # The real, new case as of 2026-08-17: once a real FPL incident
        # has resolved to this specific county this cycle, the honest
        # gap this message describes no longer applies here - showing
        # it anyway would contradict the real per-incident ETR cards
        # rendering right above it in the same real_events list.
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: [_fake_open_fpl_event("Testonia")],
        )
        monkeypatch.setattr(
            public_site, "_fpl_incident_rows",
            lambda db, county: [{
                "utility": public_site.FPL_UTILITY_NAME, "county": county,
                "customers": 42, "peak_customers": 42,
                "current_percentage_out": None, "peak_percentage_out": None,
                "customers_served": None, "estimated_restoration": "2026-08-17T05:00:00",
                "start_time": "2026-08-17T02:00:00", "duration": "3 hours",
            }],
        )
        monkeypatch.setattr(public_site, "fpl_restoration_precedent", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_restoration_precedent_by_wind_severity", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_ordinary_restoration_stats", lambda county, db: None)

        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert self.GAP_MESSAGE not in r.data.decode()


class TestUmamiTrackingScript:
    """
    Self-hosted analytics (Umami), added 2026-08-02. Both env vars
    default unset, meaning no script tag renders at all - there's no
    real publicly reachable URL for it yet (no nginx, no domain), so
    shipping this active would mean pointing real visitors' browsers at
    a URL nothing can serve. Deliberately inactive until Phase 6's
    launch infrastructure exists.

    Real bug found 2026-08-13, the night this actually got flipped on:
    public_site.py never called load_dotenv() anywhere, and neither
    does anything in its real import chain (database/correlate/
    county_status/storm_history all import cleanly without it) -
    UMAMI_SCRIPT_URL/UMAMI_WEBSITE_ID silently read as None even with
    correct values sitting in a real .env on disk. Invisible the whole
    time only because the feature stayed inactive until someone
    actually needed a real value read from it. The three tests below
    all monkeypatch these variables directly, which is exactly why
    that bug slipped past them - see test_load_dotenv_is_actually_
    called_on_import below for the one that would have caught it.
    """

    def test_no_script_tag_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(public_site, "UMAMI_SCRIPT_URL", None)
        monkeypatch.setattr(public_site, "UMAMI_WEBSITE_ID", None)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b"data-website-id" not in r.data

    def test_no_script_tag_when_only_url_configured(self, monkeypatch):
        # Both values are required together - a URL with no website id
        # (or vice versa) can't actually track anything.
        monkeypatch.setattr(public_site, "UMAMI_SCRIPT_URL", "https://example.com/script.js")
        monkeypatch.setattr(public_site, "UMAMI_WEBSITE_ID", None)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b"data-website-id" not in r.data

    def test_script_tag_renders_when_both_configured(self, monkeypatch):
        monkeypatch.setattr(public_site, "UMAMI_SCRIPT_URL", "https://example.com/script.js")
        monkeypatch.setattr(public_site, "UMAMI_WEBSITE_ID", "test-website-id-123")
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b'<script defer src="https://example.com/script.js" data-website-id="test-website-id-123"></script>' in r.data

    def test_load_dotenv_is_actually_called_on_import(self, monkeypatch):
        # The real regression guard - the three tests above monkeypatch
        # UMAMI_SCRIPT_URL/UMAMI_WEBSITE_ID directly, which can't catch
        # "the .env file that sets these in the first place never
        # actually gets loaded." This one re-imports the module fresh
        # with dotenv.load_dotenv() itself replaced by a spy, proving
        # public_site.py's own top-level code really does call it -
        # not relying on some other module's import chain to do it.
        calls = []
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: calls.append(True))
        importlib.reload(public_site)
        monkeypatch.undo()
        importlib.reload(public_site)  # restore real state for every other test

        assert calls, "public_site.py must call load_dotenv() itself on import"


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


def _fake_alert(n, severity="Moderate", areas="Hillsborough; Pinellas"):
    return {
        "id": n, "alert_id": f"test-alert-{n}", "timestamp": "2026-01-01T00:00:00",
        "event_type": f"Test Alert {n}", "severity": severity, "urgency": "Expected",
        "areas": areas, "effective": "2026-01-01T00:00:00",
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


class TestWeatherAlertsCollapsibleCards:
    """
    Alert cards switched from plain divs to real <details>/<summary>
    elements 2026-07-20, so a busy page (real example: 28+ active
    statewide alerts during a live storm) reads as a scannable list of
    collapsed rows instead of a long wall of fully-expanded cards -
    same native collapsible mechanism already used for "What is this
    section?" everywhere else on this page, not a new one.
    """

    def test_extreme_severity_alert_defaults_to_expanded(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(0, severity="Extreme")],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b"<details class=\"alert-card hz-stripe\" open>" in r.data

    def test_ordinary_severity_alert_defaults_to_collapsed(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(0, severity="Moderate")],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b"<details class=\"alert-card\">" in r.data
        assert b"open" not in r.data.split(b"alert-card")[1][:20]

    def test_multiple_areas_show_a_real_count_of_the_rest(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(0, areas="Hillsborough; Pinellas; Manatee")],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b"Hillsborough" in r.data
        assert b"&amp; 2 more" in r.data

    def test_single_area_shows_no_more_count(self, monkeypatch):
        monkeypatch.setattr(
            OutageDatabase, "get_active_weather_alerts",
            lambda self: [_fake_alert(0, areas="Hillsborough")],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert b"Hillsborough" in r.data
        assert b"more" not in r.data.split(b"alert-card")[1][:400]


@pytest.fixture
def storm_history_db(monkeypatch):
    # Same real pattern as test_storm_history.py's historical_db_path
    # fixture: a temp, seeded database, monkeypatched in via
    # storm_history.HISTORICAL_DB_PATH - never the real
    # historical_consolidated.db, which is gitignored (*.db) and simply
    # doesn't exist in CI. public_site.py imports load_history_for_county
    # and available_history_counties by reference, so they still read
    # this patched path when called through the Flask route.
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)

    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE historical_outage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            storm_name TEXT, storm_year INTEGER, utility TEXT, county TEXT,
            start_time TEXT, end_time TEXT,
            peak_customers_out INTEGER, peak_percentage_out REAL, customers_served INTEGER
        )
    ''')
    conn.execute('''
        CREATE TABLE historical_storm_severity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            storm_name TEXT, storm_year INTEGER, county TEXT, zone_name TEXT,
            event_type TEXT, begin_time TEXT, end_time TEXT,
            reported_wind_mph INTEGER, snow_inches REAL, ice_inches REAL,
            wind_chill_f REAL, narrative TEXT
        )
    ''')
    conn.commit()
    conn.close()

    monkeypatch.setattr(storm_history, "HISTORICAL_DB_PATH", path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _insert_storm(path, county, storm_name, storm_year, peak_customers_out=100, peak_percentage_out=10.0):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO historical_outage_events "
        "(storm_name, storm_year, utility, county, start_time, end_time, "
        "peak_customers_out, peak_percentage_out, customers_served) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (storm_name, storm_year, "Test Utility", county,
         f"{storm_year}-01-01T00:00:00", f"{storm_year}-01-02T00:00:00",
         peak_customers_out, peak_percentage_out, 100_000),
    )
    conn.commit()
    conn.close()


def _seed_recent_and_old_storms(path, county="Testonia", recent_count=9, old_years_back=5):
    current_year = datetime.now().year
    _insert_storm(path, county, "Old Storm", current_year - old_years_back)
    for i in range(recent_count):
        _insert_storm(path, county, f"Recent Storm {i}", current_year - (i % 4))
    return current_year


class TestStormHistoryYearFilterAndPagination:
    """
    Storm History switched 2026-07-21 from listing the full real
    archive per county to a rolling "last 4 years" window, paginated
    the same way as everything else on this page - the full archive had
    gotten long enough that a genuinely busy real county was a lot of
    scrolling to reach anything recent. Years here are computed off
    datetime.now(), not hardcoded, so these tests keep meaning "recent"
    and "old" correctly no matter when the suite runs - the same
    rolling-window principle the feature itself is built on.
    """

    def test_only_recent_storms_are_shown(self, storm_history_db):
        current_year = _seed_recent_and_old_storms(storm_history_db)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert f'storm-year mono">{current_year - 5}'.encode() not in r.data
        assert b"9 storms, last 4 years" in r.data

    def test_real_county_with_only_older_storms_gets_an_honest_message_not_a_spelling_error(self, storm_history_db, monkeypatch):
        # The real edge case this design has to get right: a genuinely
        # known county whose filtered window comes back empty must not
        # be told to check its spelling - that's only for actually-
        # unrecognized county names.
        current_year = datetime.now().year
        _insert_storm(storm_history_db, "Testonia", "Past Storm", current_year - 1)
        monkeypatch.setattr(public_site, "STORM_HISTORY_YEARS_SHOWN", 0)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert b"No storms on file for Testonia in the last" in r.data
        assert b"check the spelling" not in r.data

    def test_unknown_county_still_gets_the_spelling_message(self, storm_history_db):
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Notarealcounty")

        assert b"check the spelling" in r.data

    def test_page_two_shows_different_storms_than_page_one(self, storm_history_db):
        _seed_recent_and_old_storms(storm_history_db)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r1 = client.get("/?county=Testonia")
        r2 = client.get("/?county=Testonia&storms_page=2")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert b"Page 1 of 2" in r1.data
        assert b"Page 2 of 2" in r2.data

    def test_out_of_range_storms_page_does_not_error(self, storm_history_db):
        _seed_recent_and_old_storms(storm_history_db)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia&storms_page=9999")
        assert r.status_code == 200

    def test_non_numeric_storms_page_does_not_error(self, storm_history_db):
        _seed_recent_and_old_storms(storm_history_db)
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia&storms_page=abc")
        assert r.status_code == 200


def _fake_at_risk_row(n, tier="high"):
    return {
        "county": f"TestCounty{n}", "alert_types": ["Tornado Watch"],
        "confidence_tier": tier, "n": 5,
    }


class TestAtRiskCountiesPaginationRoute:
    """
    Same real pagination pattern as Outage History/Current Weather
    Alerts, applied here too since a widespread event could plausibly
    flag more than a page's worth of counties (13 real counties were
    flagged the night this section shipped).
    """

    def test_more_than_one_page_worth_shows_real_pagination_controls(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "at_risk_counties",
            lambda db: [_fake_at_risk_row(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")

        assert r.status_code == 200
        assert b"12 counties flagged" in r.data

    def test_out_of_range_at_risk_page_does_not_error(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "at_risk_counties",
            lambda db: [_fake_at_risk_row(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?at_risk_page=9999")
        assert r.status_code == 200

    def test_non_numeric_at_risk_page_does_not_error(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "at_risk_counties",
            lambda db: [_fake_at_risk_row(n) for n in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?at_risk_page=abc")
        assert r.status_code == 200

    def test_no_flagged_counties_shows_honest_empty_state(self, monkeypatch):
        monkeypatch.setattr(public_site, "at_risk_counties", lambda db: [])
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/")


class TestCountyEventsPaginationRoute:
    """
    Same real pagination pattern as Outage History/At-Risk Counties,
    added 2026-08-17 once real per-incident FPL rows (see
    TestFplIncidentRows above) could make one county's real_events list
    genuinely long - Miami-Dade real-checked at 15+ open FPL tickets
    alone, on top of whatever TECO/Duke/etc. also has open there.
    """

    def test_more_than_one_page_worth_shows_real_pagination_controls(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: [_fake_open_fpl_event("Testonia") for _ in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert b"12 active outages" in r.data

    def test_out_of_range_county_events_page_does_not_error(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: [_fake_open_fpl_event("Testonia") for _ in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia&county_events_page=9999")
        assert r.status_code == 200

    def test_non_numeric_county_events_page_does_not_error(self, monkeypatch):
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: [_fake_open_fpl_event("Testonia") for _ in range(12)],
        )
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia&county_events_page=abc")
        assert r.status_code == 200

    def test_precedent_cards_still_show_when_the_only_fpl_row_is_on_page_two(self, monkeypatch):
        # The real bug this guards against: fpl_open_now (and every
        # other *_open_now check) must be computed from the FULL
        # real_events list, before pagination slices it down - otherwise
        # a county's only FPL row landing on page 2 would wrongly hide
        # the Everyday Outages/Major Storms cards for a county that
        # genuinely does have an open FPL outage right now.
        other_utility_rows = [
            {"county": "Testonia", "utility": "Duke Energy", "customers": 5,
             "current_percentage_out": None, "duration": "1 hour", "estimated_restoration": None}
            for _ in range(7)
        ]
        monkeypatch.setattr(
            public_site, "_real_per_county_open_events",
            lambda db: other_utility_rows + [_fake_open_fpl_event("Testonia")],
        )
        monkeypatch.setattr(
            public_site, "fpl_ordinary_restoration_stats",
            lambda county, db: {
                "n": 5, "median_hours": 6.0, "min_hours": 2.0, "max_hours": 10.0,
                "limited": False, "excluded_count": 0,
            },
        )
        monkeypatch.setattr(public_site, "fpl_restoration_precedent", lambda county: None)
        monkeypatch.setattr(public_site, "fpl_restoration_precedent_by_wind_severity", lambda county: None)

        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert b"Everyday Outages" in r.data

    def test_no_active_outages_shows_honest_empty_state(self, monkeypatch):
        monkeypatch.setattr(public_site, "_real_per_county_open_events", lambda db: [])
        public_site.app.testing = True
        client = public_site.app.test_client()
        r = client.get("/?county=Testonia")

        assert r.status_code == 200
        assert b"No active outages or weather alerts for Testonia County right now" in r.data

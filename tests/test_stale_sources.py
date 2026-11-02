"""
Tests for the stale-source guard: a utility whose feed keeps failing must
stop having its last-seen "open" events presented as live outages.

Real incident behind this: Duke's API key died 2026-07-20 for about
22.5 hours (not permanently - Duke recovered the next day), and the
incidents open during that window stayed "open" - and on the public
homepage - until the first good poll after recovery, because an event
only closes when a successful poll stops listing it. See
apollo_shell/close_stale_duke_events.py for the separate cleanup that
corrects the ~38-hour duration distortion that caused (an earlier
version of this docstring wrongly called it "100+ days").
"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

import county_status as cs
from database import OutageDatabase

NOW = datetime(2026, 11, 2, 12, 0, 0)


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def db(db_path):
    database = OutageDatabase(db_path)
    yield database
    database.close()


def _ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _open_duke_incident(db, incident_id="D1", county="Pinellas", seen_hours_ago=2000):
    db.sync_duke_incident_events(
        [{"incident_id": incident_id, "utility": "Duke Energy", "county": county, "customer_count": 5,
          "lat": 27.9, "lon": -82.7, "cause": "unplanned", "cause_category": "other"}],
        timestamp=_ts(seen_hours_ago),
    )


def _open_teco_incident(db, county="Hillsborough"):
    db.sync_teco_incident_events(
        [{"incident_id": "T1", "utility": "Tampa Electric Company",
          "status": "On our way", "status_category": "investigating",
          "reason": "Tree down", "reason_category": "vegetation",
          "customer_count": 7, "lat": 27.9, "lon": -82.4, "county": county,
          "update_time": _ts(0.1), "estimated_restoration": None}],
        timestamp=_ts(0.1),
    )


class TestIsSourceStale:
    def test_source_with_no_errors_is_never_stale(self, db):
        # A quiet source (no rows anywhere, nothing recorded) is not a
        # dead source - only an actual failure streak counts.
        assert cs.is_source_stale(db, "duke", now=NOW) is False

    def test_short_blip_is_not_stale(self, db):
        db.log_pipeline_success("duke", timestamp=_ts(0.5))
        db.log_pipeline_error("duke", "Read timed out", timestamp=_ts(0.25))
        assert cs.is_source_stale(db, "duke", now=NOW) is False

    def test_failing_past_the_threshold_is_stale(self, db):
        db.log_pipeline_success("duke", timestamp=_ts(3))
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=_ts(0.25))
        assert cs.is_source_stale(db, "duke", now=NOW) is True

    def test_success_after_the_last_error_clears_it(self, db):
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=_ts(5))
        db.log_pipeline_success("duke", timestamp=_ts(0.25))
        assert cs.is_source_stale(db, "duke", now=NOW) is False

    def test_errors_with_no_success_ever_is_stale(self, db):
        db.log_pipeline_error("ouc", "boom", timestamp=_ts(0.25))
        assert cs.is_source_stale(db, "ouc", now=NOW) is True

    def test_falls_back_to_duke_counties_when_no_success_row_yet(self, db):
        # Before the new poller code runs there's no pipeline_success
        # row - the Duke county rollup (written every successful poll)
        # stands in for it.
        db.connect().execute(
            "INSERT INTO duke_counties (fetched_at, utility, county) VALUES (?, 'Duke Energy', 'Pinellas')",
            (_ts(1),),
        )
        db.connect().commit()
        db.log_pipeline_error("duke", "Read timed out", timestamp=_ts(0.25))
        assert cs.is_source_stale(db, "duke", now=NOW) is False

        # Two hours later, still failing: the fallback success is now 3h old.
        two_hours_later = NOW + timedelta(hours=2)
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=two_hours_later.isoformat())
        assert cs.is_source_stale(db, "duke", now=two_hours_later) is True

    def test_every_fallback_table_and_column_exists(self, db):
        # Guards the fallback map against typos/schema drift - a wrong
        # name would only surface as a crash on the live homepage.
        for table, column in cs._FALLBACK_SUCCESS_TABLES.values():
            db.get_max_timestamp(table, column)


class TestOpenEventsSkipStaleSources:
    def test_stale_duke_events_are_hidden_but_healthy_sources_still_show(self, db):
        _open_duke_incident(db)
        _open_teco_incident(db)
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=_ts(0.25))

        rows = cs._real_per_county_open_events(db, now=NOW)

        utilities = {r["utility"] for r in rows}
        assert "Duke Energy" not in utilities
        assert "Tampa Electric Company" in utilities

    def test_events_return_once_the_source_recovers(self, db):
        _open_duke_incident(db, seen_hours_ago=0.2)
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=_ts(5))
        db.log_pipeline_success("duke", timestamp=_ts(0.2))

        rows = cs._real_per_county_open_events(db, now=NOW)

        assert [r["utility"] for r in rows] == ["Duke Energy"]

    def test_history_is_untouched(self, db):
        # The guard only filters the "right now" view - the events
        # themselves are still open in the table until cleaned up.
        _open_duke_incident(db)
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=_ts(0.25))
        cs._real_per_county_open_events(db, now=NOW)
        assert len(db.get_duke_open_events()) == 1

    def test_combined_territory_sources_are_guarded_too(self, db):
        db.log_pipeline_error("tcec", "boom", timestamp=_ts(0.25))
        assert "tcec" in {src for _, src, _, _ in cs._COMBINED_OPEN_EVENT_SOURCES}
        assert cs._combined_territory_open_events(db, now=NOW) == []


class TestUnavailableSourceNames:
    def test_lists_display_names_of_stale_sources_only(self, db):
        db.log_pipeline_error("duke", "401 Unauthorized", timestamp=_ts(0.25))
        db.log_pipeline_success("teco", timestamp=_ts(0.1))
        assert cs.unavailable_source_names(db, now=NOW) == ["Duke Energy"]

    def test_empty_when_everything_is_healthy(self, db):
        assert cs.unavailable_source_names(db, now=NOW) == []

    def test_non_utility_sources_are_never_listed(self, db):
        db.log_pipeline_error("weather", "boom", timestamp=_ts(5))
        db.log_pipeline_error("correlation", "boom", timestamp=_ts(5))
        assert cs.unavailable_source_names(db, now=NOW) == []


class TestUnavailableSourcesDetail:
    def test_includes_when_the_source_last_succeeded(self, db):
        db.log_pipeline_error("duke", "500 Server Error", timestamp=_ts(0.25))
        db.log_pipeline_success("duke", timestamp=_ts(5))

        detail = cs.unavailable_sources_detail(db, now=NOW)

        assert len(detail) == 1
        assert detail[0]["display_name"] == "Duke Energy"
        assert detail[0]["last_success"] == _ts(5)
        assert detail[0]["down_for"] == "5h 0m"

    def test_falls_back_to_the_sustained_alert_success_table(self, db):
        # No pipeline_success row for duke yet - same fallback
        # is_source_stale() itself uses (duke_counties.fetched_at).
        db.log_pipeline_error("duke", "500 Server Error", timestamp=_ts(0.25))
        db.connect().execute(
            "INSERT INTO duke_counties (fetched_at, county) VALUES (?, ?)", (_ts(4), "Pinellas"),
        )
        db.connect().commit()

        detail = cs.unavailable_sources_detail(db, now=NOW)

        assert detail[0]["last_success"] == _ts(4)
        assert detail[0]["down_for"] == "4h 0m"

    def test_never_succeeded_reports_no_last_success(self, db):
        db.log_pipeline_error("talquin", "boom", timestamp=_ts(5))

        detail = cs.unavailable_sources_detail(db, now=NOW)

        assert detail[0]["display_name"] == "Talquin Electric Cooperative"
        assert detail[0]["last_success"] is None
        assert detail[0]["down_for"] is None

    def test_empty_when_everything_is_healthy(self, db):
        assert cs.unavailable_sources_detail(db, now=NOW) == []


class TestLogPipelineSuccess:
    def test_keeps_only_the_latest_success(self, db):
        db.log_pipeline_success("duke", timestamp="2026-11-01T00:00:00")
        db.log_pipeline_success("duke", timestamp="2026-11-02T00:00:00")
        assert db.get_last_pipeline_success("duke") == "2026-11-02T00:00:00"
        count = db.connect().execute("SELECT COUNT(*) FROM pipeline_success").fetchone()[0]
        assert count == 1

    def test_never_raises(self, db):
        db.connect().execute("DROP TABLE pipeline_success")
        db.log_pipeline_success("duke")  # must just log, not raise


class TestPublicHomepageNotice:
    def test_notice_names_the_unavailable_utility(self, db_path, monkeypatch):
        import public_site

        setup = OutageDatabase(db_path)
        _open_duke_incident(setup)
        setup.log_pipeline_error("duke", "401 Unauthorized")
        setup.close()

        monkeypatch.setattr(public_site, "OutageDatabase", lambda *a, **k: OutageDatabase(db_path))
        public_site.app.testing = True
        public_site.limiter.enabled = False
        html = public_site.app.test_client().get("/").get_data(as_text=True)

        assert "Live data temporarily unavailable" in html
        assert "Duke Energy" in html

    def test_no_notice_when_everything_is_healthy(self, db_path, monkeypatch):
        import public_site

        OutageDatabase(db_path).close()
        monkeypatch.setattr(public_site, "OutageDatabase", lambda *a, **k: OutageDatabase(db_path))
        public_site.app.testing = True
        public_site.limiter.enabled = False
        html = public_site.app.test_client().get("/").get_data(as_text=True)

        assert "Live data temporarily unavailable" not in html

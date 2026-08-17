"""
Tests for alerting.py's check_and_alert_pipeline_health() - the
state-transition logic that decides when a failing source is worth a
real email (once, on becoming currently-failing) versus staying quiet
(every cycle it's still down) versus a follow-up email (once, on
recovery).

Real regression covered here (found 2026-07-17, live on the VM): the
first version of this check used get_pipeline_health()'s "any failure
in the last hour" window, which fired a false "down" email for a
source that had already recovered - its last real fetch succeeded, but
an older failure was still inside the 1-hour lookback. The fix compares
the source's last failure timestamp against its last success timestamp
directly ("is the MOST RECENT attempt a failure"), not a window count.

send_alert_email() itself is monkeypatched throughout - these tests
verify the decision logic, not real SMTP delivery.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "apollo_shell"))

from database import OutageDatabase
import alerting


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


@pytest.fixture(autouse=True)
def reset_alert_state():
    # _alerted_sources/_last_down_alert_time are module-level, shared
    # across tests unless reset - real behavior in production
    # (persists for the life of the poller process), but each test
    # needs to start clean.
    alerting._alerted_sources.clear()
    alerting._last_down_alert_time.clear()
    alerting._sustained_alerted_sources.clear()
    yield
    alerting._alerted_sources.clear()
    alerting._last_down_alert_time.clear()
    alerting._sustained_alerted_sources.clear()


class TestCheckAndAlertPipelineHealth:
    """
    PRECO is used as this class's generic example, with
    DOWN_ALERT_SUPPRESSED_SOURCES cleared - this class tests the general
    down/recovery state-transition logic, not the suppression feature
    itself (see TestDownAlertSuppression), so it needs a source that
    would otherwise send a real "down" email.
    """

    def test_sends_one_alert_when_a_source_first_fails(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        db.log_pipeline_error("preco", "PRECO fetch returned no records")

        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert len(sent) == 1
        assert "Peace River Electric Cooperative" in sent[0]
        assert "down" in sent[0].lower()

    def test_does_not_resend_while_still_failing(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        db.log_pipeline_error("preco", "first failure")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        db.log_pipeline_error("preco", "still failing")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert len(sent) == 1

    def test_sends_recovery_email_once_a_later_success_is_logged(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        db.log_pipeline_error("preco", "a failure", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})
        assert len(sent) == 1

        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:15:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert len(sent) == 2
        assert "recovered" in sent[1].lower()
        assert "preco" not in alerting._alerted_sources

    def test_no_false_alarm_when_old_failure_precedes_a_newer_success(self, db, monkeypatch):
        # The real regression: an old failure sitting in pipeline_errors
        # should NOT trigger a "down" alert if a more recent successful
        # fetch has already been logged - this is the exact situation a
        # fresh trackingCode recapture produces (old failures from
        # before the fix, a real success from right after it).
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        db.log_pipeline_error("preco", "an old failure", timestamp="2026-01-01T00:00:00")
        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:30:00",
        )

        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert sent == []
        assert "preco" not in alerting._alerted_sources

    def test_ignores_sources_outside_alert_worthy_set(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("fpl", "a normal failure")
        alerting.check_and_alert_pipeline_health(db, display_names={"fpl": "Florida Power and Light"})

        assert sent == []


class TestDownAlertSuppression:
    """
    Johan asked 2026-07-18 to stop the recurring "Talquin is down"
    emails, then asked again the same day for PRECO once its own repeat
    email arrived too - the chronic Sienatech issue is already fully
    disclosed and understood, so a repeat "still down" email isn't new
    information the way it is for an ordinary failure. Down emails are
    fully silenced for both sources in DOWN_ALERT_SUPPRESSED_SOURCES,
    but state tracking and the recovery email both still work normally -
    only the "down" send itself is skipped.
    """

    def test_down_email_fully_suppressed_for_talquin(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "credential expired")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert sent == []

    def test_down_email_fully_suppressed_for_preco(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("preco", "credential expired")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert sent == []

    def test_state_still_tracks_failing_despite_suppressed_email(self, db, monkeypatch):
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: None)

        db.log_pipeline_error("talquin", "credential expired")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert "talquin" in alerting._alerted_sources

    def test_recovery_email_still_fires_for_talquin(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "credential expired", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})
        assert sent == []  # the down email, suppressed

        db.log_talquin_outages(
            [{"county": "Gadsden", "customers_out": 0, "customers_served": 15493}],
            timestamp="2026-01-01T00:15:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 1
        assert "recovered" in sent[0].lower()
        assert "talquin" not in alerting._alerted_sources

    def test_recovery_email_still_fires_for_preco(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("preco", "credential expired", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})
        assert sent == []  # the down email, suppressed

        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:15:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert len(sent) == 1
        assert "recovered" in sent[0].lower()
        assert "preco" not in alerting._alerted_sources

    def test_suppression_is_scoped_to_specific_sources_only(self, db, monkeypatch):
        # DOWN_ALERT_SUPPRESSED_SOURCES silences specific sources, not
        # every alert-worthy one - demonstrated with a temporary third
        # alert-worthy source that isn't in the suppressed set. "outages"
        # (FPL's own table) always exists, so _is_currently_failing()
        # has a real table to query.
        monkeypatch.setitem(alerting.ALERT_WORTHY_SOURCES, "faketest", "outages")
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("faketest", "credential expired")
        alerting.check_and_alert_pipeline_health(db, display_names={"faketest": "Fake Test Utility"})

        assert len(sent) == 1
        assert "down" in sent[0].lower()


class TestDownAlertCooldown:
    """
    Talquin/PRECO's credential dying is a known, ongoing vendor issue -
    without a cooldown, a single chronic day of it flapping down/
    recovered/down again would send a fresh "down" email every single
    time, which is technically correct but not useful once you already
    know it's the same ongoing thing. Recovery emails stay unthrottled -
    each one is a real, wanted confirmation tied to an actual fix.

    PRECO is used as this class's example, with
    DOWN_ALERT_SUPPRESSED_SOURCES cleared - this class tests the
    cooldown mechanism itself, which is a separate concern from the
    full-suppression feature (see TestDownAlertSuppression), so it
    needs a source whose "down" email isn't unconditionally silenced.
    """

    def test_repeat_down_within_cooldown_is_suppressed(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_COOLDOWN_SECONDS", 3600)
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        # First failure -> recovery -> failure again, all within the
        # same (mocked) hour.
        db.log_pipeline_error("preco", "failure 1", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})
        assert len(sent) == 1  # the real first "down" email

        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:10:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})
        assert len(sent) == 2  # recovery email, never throttled

        db.log_pipeline_error("preco", "failure 2", timestamp="2026-01-01T00:20:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        # Still within the 1-hour cooldown since the first "down" email -
        # no new "down" email, but the state still correctly shows failing.
        assert len(sent) == 2
        assert "preco" in alerting._alerted_sources

    def test_recovery_email_still_fires_even_during_cooldown(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_COOLDOWN_SECONDS", 3600)
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        db.log_pipeline_error("preco", "failure 1", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        db.log_pipeline_error("preco", "failure 2", timestamp="2026-01-01T00:10:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        # Recovers again, still well inside the cooldown window - the
        # recovery email must still fire, since it's never throttled.
        db.log_preco_outages(
            [{"county": "Manatee", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:20:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert len(sent) == 2  # one "down" (cooldown suppressed the rest), one "recovered"
        assert "recovered" in sent[-1].lower()

    def test_new_down_email_after_cooldown_expires(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_COOLDOWN_SECONDS", 3600)
        monkeypatch.setattr(alerting, "DOWN_ALERT_SUPPRESSED_SOURCES", set())

        db.log_pipeline_error("preco", "failure 1", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})
        assert len(sent) == 1

        # Simulate real time having actually passed well beyond the
        # cooldown (rather than sleeping in the test).
        alerting._last_down_alert_time["preco"] -= 7200
        alerting._alerted_sources.discard("preco")

        db.log_pipeline_error("preco", "failure 2", timestamp="2026-01-01T02:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"preco": "Peace River Electric Cooperative"})

        assert len(sent) == 2
        assert "down" in sent[-1].lower()


class TestSendAlertEmail:
    def test_skips_silently_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(alerting, "ALERT_EMAIL_ADDRESS", None)
        monkeypatch.setattr(alerting, "ALERT_EMAIL_APP_PASSWORD", None)

        result = alerting.send_alert_email("subject", "body")

        assert result is False


class TestConsecutiveFailureCount:
    def test_zero_when_no_failures_logged(self, db):
        assert alerting._consecutive_failure_count(db, "ouc", "ouc_outages") == 0

    def test_counts_failures_since_last_success(self, db):
        db.log_ouc_outages(
            [{"county": "Orange", "customers_out": 0, "customers_served": 292028}],
            timestamp="2026-01-01T00:00:00",
        )
        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:15:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:30:00")

        assert alerting._consecutive_failure_count(db, "ouc", "ouc_outages") == 2

    def test_older_failures_before_a_success_are_not_counted(self, db):
        # Real regression shape this whole mechanism depends on: a
        # failure predating the most recent success must not count
        # toward the CURRENT streak, or a source that's actually healthy
        # right now could still cross the sustained-failure threshold
        # off old, already-resolved history.
        db.log_pipeline_error("ouc", "an old failure", timestamp="2026-01-01T00:00:00")
        db.log_ouc_outages(
            [{"county": "Orange", "customers_out": 0, "customers_served": 292028}],
            timestamp="2026-01-01T00:15:00",
        )
        db.log_pipeline_error("ouc", "one fresh failure", timestamp="2026-01-01T00:30:00")

        assert alerting._consecutive_failure_count(db, "ouc", "ouc_outages") == 1

    def test_counts_every_failure_when_no_success_ever_logged(self, db):
        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:15:00")
        db.log_pipeline_error("ouc", "failure 3", timestamp="2026-01-01T00:30:00")

        assert alerting._consecutive_failure_count(db, "ouc", "ouc_outages") == 3

    def test_respects_a_non_timestamp_success_column(self, db):
        # Real bug this feature's own first test run caught:
        # teco_incidents/duke_incidents stamp their rows "fetched_at",
        # not "timestamp" like every other table here - inserted
        # directly since log_teco_incidents() doesn't take an
        # explicit timestamp param to control deterministically.
        conn = db.connect()
        conn.execute(
            "INSERT INTO teco_incidents (incident_id, fetched_at) VALUES (?, ?)",
            ("INC-1", "2026-01-01T00:15:00"),
        )
        conn.commit()
        db.log_pipeline_error("teco", "an old failure", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("teco", "a fresh failure", timestamp="2026-01-01T00:30:00")

        # The success at 00:15 should cut off the 00:00 failure but not
        # the 00:30 one - proves success_column="fetched_at" is
        # actually being read, not silently falling back to a
        # "timestamp" column that doesn't exist on this table.
        assert alerting._consecutive_failure_count(db, "teco", "teco_incidents", "fetched_at") == 1


class TestFplIncidentsSustainedAlertRegistration:
    """
    Real regression guard for the exact gap found live 2026-08-17:
    FPL_INCIDENTS_API_URL was never added to the VM's .env, so the new
    fpl_incidents poll cycle failed silently every single cycle since
    deploy - nothing caught it automatically, only a manual live check.
    Registering "fpl_incidents" here means a future failure of this
    kind (wrong table name typo, endpoint going away, etc.) gets a real
    email the same way OUC's/Duke's sustained failures already do,
    instead of relying on someone noticing zero pins on the live page.
    """

    def test_fpl_incidents_is_a_sustained_alert_worthy_source(self):
        assert "fpl_incidents" in alerting.SUSTAINED_ALERT_WORTHY_SOURCES
        table, column = alerting.SUSTAINED_ALERT_WORTHY_SOURCES["fpl_incidents"]
        assert table == "fpl_incidents"
        assert column == "fetched_at"

    def test_fpl_incidents_has_a_real_display_name(self):
        assert "fpl_incidents" in alerting.PIPELINE_SOURCE_DISPLAY_NAMES


class TestCheckAndAlertSustainedFailures:
    """
    Built 2026-08-02, prompted by two real incidents (Duke's expired
    token, OUC's silently-rotated data-path UUID) that each ran for
    hours/days before being caught manually. Uses OUC as the general
    example throughout - a real member of SUSTAINED_ALERT_WORTHY_SOURCES,
    same real incident that motivated building this at all.
    """

    def test_single_failure_sends_no_email(self, db, monkeypatch):
        # The whole point of the sustained threshold: one ordinary,
        # self-healing blip (a real NWS read-timeout, 2026-07-28, that
        # never repeated) must not fire an email on its own.
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("ouc", "single blip")
        alerting.check_and_alert_sustained_failures(db)

        assert sent == []

    def test_two_consecutive_failures_sends_exactly_one_down_email(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)

        assert len(sent) == 1
        assert "Orlando Utilities Commission" in sent[0]
        assert "down" in sent[0].lower()

    def test_does_not_resend_while_still_failing(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)
        assert len(sent) == 1

        db.log_pipeline_error("ouc", "failure 3", timestamp="2026-01-01T00:30:00")
        alerting.check_and_alert_sustained_failures(db)

        assert len(sent) == 1

    def test_sends_recovery_email_once_a_later_success_is_logged(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)
        assert len(sent) == 1

        db.log_ouc_outages(
            [{"county": "Orange", "customers_out": 0, "customers_served": 292028}],
            timestamp="2026-01-01T00:30:00",
        )
        alerting.check_and_alert_sustained_failures(db)

        assert len(sent) == 2
        assert "recovered" in sent[1].lower()
        assert "ouc" not in alerting._sustained_alerted_sources

    def test_no_cooldown_a_second_real_episode_gets_its_own_down_email(self, db, monkeypatch):
        # Deliberately different from Talquin/PRECO's cooldown - these
        # sources are normally reliable, so each real sustained episode
        # is worth its own fresh email, not throttled as "the same
        # known issue" the way a chronic vendor problem is.
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)

        db.log_ouc_outages(
            [{"county": "Orange", "customers_out": 0, "customers_served": 292028}],
            timestamp="2026-01-01T00:30:00",
        )
        alerting.check_and_alert_sustained_failures(db)

        db.log_pipeline_error("ouc", "failure 3", timestamp="2026-01-01T01:00:00")
        db.log_pipeline_error("ouc", "failure 4", timestamp="2026-01-01T01:15:00")
        alerting.check_and_alert_sustained_failures(db)

        assert len(sent) == 3
        assert "down" in sent[0].lower()
        assert "recovered" in sent[1].lower()
        assert "down" in sent[2].lower()

    def test_ignores_sources_outside_sustained_alert_worthy_set(self, db, monkeypatch):
        # talquin/preco keep their own single-failure trigger - not part
        # of this mechanism at all, even though they're real sources.
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("talquin", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)

        assert sent == []

    def test_internal_derived_sources_are_excluded(self, db, monkeypatch):
        # correlation/historical_tally are our own computation, not a
        # real external data source being down - deliberately excluded.
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("correlation", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("correlation", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)

        assert sent == []

    def test_email_body_reports_the_real_failure_count(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(body))

        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "failure 2", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)

        assert "2 times in a row" in sent[0]

    def test_email_body_includes_the_most_recent_error_message(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(body))

        db.log_pipeline_error("ouc", "failure 1", timestamp="2026-01-01T00:00:00")
        db.log_pipeline_error("ouc", "404 Client Error: Not Found", timestamp="2026-01-01T00:15:00")
        alerting.check_and_alert_sustained_failures(db)

        assert "404 Client Error: Not Found" in sent[0]

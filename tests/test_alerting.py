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
    yield
    alerting._alerted_sources.clear()
    alerting._last_down_alert_time.clear()


class TestCheckAndAlertPipelineHealth:
    def test_sends_one_alert_when_a_source_first_fails(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "Talquin fetch returned no records")

        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 1
        assert "Talquin Electric Cooperative" in sent[0]
        assert "down" in sent[0].lower()

    def test_does_not_resend_while_still_failing(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "first failure")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        db.log_pipeline_error("talquin", "still failing")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 1

    def test_sends_recovery_email_once_a_later_success_is_logged(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "a failure", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})
        assert len(sent) == 1

        db.log_talquin_outages(
            [{"county": "Leon", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:15:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 2
        assert "recovered" in sent[1].lower()
        assert "talquin" not in alerting._alerted_sources

    def test_no_false_alarm_when_old_failure_precedes_a_newer_success(self, db, monkeypatch):
        # The real regression: an old failure sitting in pipeline_errors
        # should NOT trigger a "down" alert if a more recent successful
        # fetch has already been logged - this is the exact situation a
        # fresh trackingCode recapture produces (old failures from
        # before the fix, a real success from right after it).
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "an old failure", timestamp="2026-01-01T00:00:00")
        db.log_talquin_outages(
            [{"county": "Leon", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:30:00",
        )

        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert sent == []
        assert "talquin" not in alerting._alerted_sources

    def test_ignores_sources_outside_alert_worthy_set(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("fpl", "a normal failure")
        alerting.check_and_alert_pipeline_health(db, display_names={"fpl": "Florida Power and Light"})

        assert sent == []


class TestDownAlertCooldown:
    """
    Talquin/PRECO's credential dying is a known, ongoing vendor issue -
    without a cooldown, a single chronic day of it flapping down/
    recovered/down again would send a fresh "down" email every single
    time, which is technically correct but not useful once you already
    know it's the same ongoing thing. Recovery emails stay unthrottled -
    each one is a real, wanted confirmation tied to an actual fix.
    """

    def test_repeat_down_within_cooldown_is_suppressed(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_COOLDOWN_SECONDS", 3600)

        # First failure -> recovery -> failure again, all within the
        # same (mocked) hour.
        db.log_pipeline_error("talquin", "failure 1", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})
        assert len(sent) == 1  # the real first "down" email

        db.log_talquin_outages(
            [{"county": "Leon", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:10:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})
        assert len(sent) == 2  # recovery email, never throttled

        db.log_pipeline_error("talquin", "failure 2", timestamp="2026-01-01T00:20:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        # Still within the 1-hour cooldown since the first "down" email -
        # no new "down" email, but the state still correctly shows failing.
        assert len(sent) == 2
        assert "talquin" in alerting._alerted_sources

    def test_recovery_email_still_fires_even_during_cooldown(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_COOLDOWN_SECONDS", 3600)

        db.log_pipeline_error("talquin", "failure 1", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        db.log_pipeline_error("talquin", "failure 2", timestamp="2026-01-01T00:10:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        # Recovers again, still well inside the cooldown window - the
        # recovery email must still fire, since it's never throttled.
        db.log_talquin_outages(
            [{"county": "Leon", "customers_out": 0, "customers_served": 26350}],
            timestamp="2026-01-01T00:20:00",
        )
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 2  # one "down" (cooldown suppressed the rest), one "recovered"
        assert "recovered" in sent[-1].lower()

    def test_new_down_email_after_cooldown_expires(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))
        monkeypatch.setattr(alerting, "DOWN_ALERT_COOLDOWN_SECONDS", 3600)

        db.log_pipeline_error("talquin", "failure 1", timestamp="2026-01-01T00:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})
        assert len(sent) == 1

        # Simulate real time having actually passed well beyond the
        # cooldown (rather than sleeping in the test).
        alerting._last_down_alert_time["talquin"] -= 7200
        alerting._alerted_sources.discard("talquin")

        db.log_pipeline_error("talquin", "failure 2", timestamp="2026-01-01T02:00:00")
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 2
        assert "down" in sent[-1].lower()


class TestSendAlertEmail:
    def test_skips_silently_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(alerting, "ALERT_EMAIL_ADDRESS", None)
        monkeypatch.setattr(alerting, "ALERT_EMAIL_APP_PASSWORD", None)

        result = alerting.send_alert_email("subject", "body")

        assert result is False

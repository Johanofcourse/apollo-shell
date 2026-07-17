"""
Tests for alerting.py's check_and_alert_pipeline_health() - the
state-transition logic that decides when a failing source is worth a
real email (once, on becoming critical) versus staying quiet (every
cycle it's still down) versus a follow-up email (once, on recovery).

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
    # _alerted_sources is module-level, shared across tests unless
    # reset - real behavior in production (persists for the life of
    # the poller process), but each test needs to start clean.
    alerting._alerted_sources.clear()
    yield
    alerting._alerted_sources.clear()


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

    def test_sends_recovery_email_once_healthy_again(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("talquin", "a failure over an hour ago", )
        # Manually push the logged error outside the 1-hour critical
        # window so get_pipeline_health() reports "healthy" - simulates
        # time passing without needing to actually wait.
        conn = db.connect()
        conn.execute("UPDATE pipeline_errors SET timestamp = datetime('now', '-2 hours') WHERE source = 'talquin'")
        conn.commit()

        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})
        assert sent == []  # already outside the window - never alerted in the first place

        alerting._alerted_sources.add("talquin")  # simulate having alerted while it was still failing
        alerting.check_and_alert_pipeline_health(db, display_names={"talquin": "Talquin Electric Cooperative"})

        assert len(sent) == 1
        assert "recovered" in sent[0].lower()
        assert "talquin" not in alerting._alerted_sources

    def test_ignores_sources_outside_alert_worthy_set(self, db, monkeypatch):
        sent = []
        monkeypatch.setattr(alerting, "send_alert_email", lambda subject, body: sent.append(subject))

        db.log_pipeline_error("fpl", "a normal failure")
        alerting.check_and_alert_pipeline_health(db, display_names={"fpl": "Florida Power and Light"})

        assert sent == []


class TestSendAlertEmail:
    def test_skips_silently_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(alerting, "ALERT_EMAIL_ADDRESS", None)
        monkeypatch.setattr(alerting, "ALERT_EMAIL_APP_PASSWORD", None)

        result = alerting.send_alert_email("subject", "body")

        assert result is False

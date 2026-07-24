"""
Tests for check_site_health.py - a different kind of health check than
alerting.py's, which watches whether a utility's data feed is failing.
This watches whether dashboard.py/public_site.py themselves are even
answering, added 2026-07-22 after a real incident where public_site.py
silently froze for 15+ minutes and nothing noticed until a live
comparison caught it.

send_alert_email() is monkeypatched throughout - these tests verify the
state-transition logic (one "down" email per outage, one "recovered"
email on the way back, no repeats in between), not real SMTP delivery.
State is a small JSON file rather than in-memory, since this script is
a fresh process every time cron runs it - each test points STATE_FILE
at its own tmp_path file so tests can't see each other's state or the
real one this would use on the VM.
"""
import os
import tempfile

import pytest

import check_site_health


@pytest.fixture
def state_path(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    monkeypatch.setattr(check_site_health, "STATE_FILE", path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []
    monkeypatch.setattr(
        check_site_health, "send_alert_email",
        lambda subject, body: sent.append({"subject": subject, "body": body}) or True,
    )
    return sent


def _mock_reachability(monkeypatch, results):
    """results: dict of url -> bool, whatever _is_reachable should return for it."""
    monkeypatch.setattr(check_site_health, "_is_reachable", lambda url: results.get(url, True))


class TestCheckSiteHealth:
    def test_all_reachable_sends_no_email(self, state_path, sent_emails, monkeypatch):
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": True,
        })
        check_site_health.check_site_health()
        assert sent_emails == []

    def test_newly_unreachable_service_sends_exactly_one_down_email(self, state_path, sent_emails, monkeypatch):
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": False,
        })
        check_site_health.check_site_health()

        assert len(sent_emails) == 1
        assert "public_site" in sent_emails[0]["subject"]
        assert "not responding" in sent_emails[0]["subject"]

    def test_still_down_on_next_run_sends_no_repeat_email(self, state_path, sent_emails, monkeypatch):
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": False,
        })
        check_site_health.check_site_health()
        check_site_health.check_site_health()

        assert len(sent_emails) == 1

    def test_recovery_sends_exactly_one_recovered_email(self, state_path, sent_emails, monkeypatch):
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": False,
        })
        check_site_health.check_site_health()

        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": True,
        })
        check_site_health.check_site_health()

        assert len(sent_emails) == 2
        assert "responding again" in sent_emails[1]["subject"]

    def test_state_persists_across_separate_invocations(self, state_path, sent_emails, monkeypatch):
        # Real regression guard: this script is a fresh process every
        # cron run, so state MUST survive via the file, not just an
        # in-memory dict that would silently reset every single call.
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": False,
        })
        check_site_health.check_site_health()
        assert os.path.exists(state_path)

        state = check_site_health._load_state()
        assert state["public_site"] is True
        # dashboard was always reachable, so it never earns a state
        # entry at all - defaults to "not down" via state.get(), same
        # as a service this check has never once seen fail.
        assert state.get("dashboard", False) is False

    def test_both_services_down_sends_two_down_emails(self, state_path, sent_emails, monkeypatch):
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": False,
            "http://127.0.0.1:5051/": False,
        })
        check_site_health.check_site_health()

        assert len(sent_emails) == 2
        subjects = {e["subject"] for e in sent_emails}
        assert any("dashboard" in s for s in subjects)
        assert any("public_site" in s for s in subjects)

    def test_first_run_with_no_state_file_does_not_crash(self, state_path, sent_emails, monkeypatch):
        assert not os.path.exists(state_path)
        _mock_reachability(monkeypatch, {
            "http://127.0.0.1:5050/": True,
            "http://127.0.0.1:5051/": True,
        })
        check_site_health.check_site_health()
        assert sent_emails == []


class TestIsReachable:
    def test_200_response_is_reachable(self, monkeypatch):
        class _FakeResponse:
            status_code = 200

        monkeypatch.setattr(check_site_health.requests, "get", lambda url, timeout: _FakeResponse())
        assert check_site_health._is_reachable("http://127.0.0.1:5050/") is True

    def test_non_200_response_is_not_reachable(self, monkeypatch):
        class _FakeResponse:
            status_code = 500

        monkeypatch.setattr(check_site_health.requests, "get", lambda url, timeout: _FakeResponse())
        assert check_site_health._is_reachable("http://127.0.0.1:5050/") is False

    def test_connection_error_is_not_reachable(self, monkeypatch):
        import requests as requests_module

        def _boom(url, timeout):
            raise requests_module.exceptions.ConnectionError("refused")

        monkeypatch.setattr(check_site_health.requests, "get", _boom)
        assert check_site_health._is_reachable("http://127.0.0.1:5050/") is False

    def test_timeout_is_not_reachable(self, monkeypatch):
        import requests as requests_module

        def _boom(url, timeout):
            raise requests_module.exceptions.Timeout("too slow")

        monkeypatch.setattr(check_site_health.requests, "get", _boom)
        assert check_site_health._is_reachable("http://127.0.0.1:5050/") is False

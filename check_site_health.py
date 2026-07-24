"""
Self-check for the two Flask apps this project runs (dashboard.py,
public_site.py) - a different kind of health check than
alerting.py's, which watches whether a *utility's data feed* is
failing. This watches whether the app itself is even answering,
independent of the poller's own 15-minute cycle - run on its own
schedule via cron, not from main.py.

Added 2026-07-22 after a real incident: public_site.py's old Flask
dev-server process silently froze mid-storm for 15+ minutes, serving a
stale response, and nothing noticed until a live comparison against
the dashboard caught it. Gunicorn's worker recycling makes that exact
bug unlikely to recur, but this check exists for the general case -
any reason either app stops answering gets caught within minutes,
not whenever someone happens to look.

Reuses alerting.py's send_alert_email() - same iCloud SMTP channel
already configured for the Talquin/PRECO alerts. State (whether a
"down" email has already been sent for the current outage) lives in a
small local JSON file rather than in memory, since this script is a
fresh process every time cron runs it - nothing would survive between
runs otherwise, unlike main.py's poller, which is one long-lived
process for its whole in-memory alerting state.
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from alerting import send_alert_email

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_health_state.json")

# name -> URL. Both loopback-only - this always runs on the VM itself,
# so it never depends on the SSH tunnel or a real public domain, unlike
# a third-party uptime monitor, which can't reach either service at all
# right now (neither is exposed to the public internet).
CHECKED_SERVICES = {
    "dashboard": "http://127.0.0.1:5050/",
    "public_site": "http://127.0.0.1:5051/",
}

REQUEST_TIMEOUT_SECONDS = 10


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _is_reachable(url):
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


def check_site_health():
    """
    Checks every service in CHECKED_SERVICES once. Sends exactly one
    "down" email the moment a service first fails to respond, and one
    "recovered" email once it answers again - never a repeat email on
    every cron run for the same ongoing outage, same one-alert-per-
    episode principle alerting.py's pipeline-health checks already use.
    """
    state = _load_state()

    for name, url in CHECKED_SERVICES.items():
        reachable = _is_reachable(url)
        was_down = state.get(name, False)

        if not reachable and not was_down:
            state[name] = True
            send_alert_email(
                subject=f"Apollo Shell: {name} is not responding",
                body=(
                    f"{name} did not answer a plain health check at {url}.\n\n"
                    "This is the app itself, not a utility data source - check "
                    "whether its systemd service is still running on the VM."
                ),
            )
        elif reachable and was_down:
            state[name] = False
            send_alert_email(
                subject=f"Apollo Shell: {name} is responding again",
                body=f"{name} answered a plain health check again at {url}.",
            )

    _save_state(state)


if __name__ == "__main__":
    check_site_health()

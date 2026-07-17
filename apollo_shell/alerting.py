import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

ALERT_EMAIL_ADDRESS = os.environ.get("ALERT_EMAIL_ADDRESS")
ALERT_EMAIL_APP_PASSWORD = os.environ.get("ALERT_EMAIL_APP_PASSWORD")

ICLOUD_SMTP_HOST = "smtp.mail.me.com"
ICLOUD_SMTP_PORT = 587

# Sources whose failures are worth a real email, not just the
# dashboard's own pipeline-health strip - ones known to need manual
# intervention to recover, not just a transient network blip.
ALERT_WORTHY_SOURCES = {"talquin", "preco"}

# In-memory only, per source - tracks whether an alert has already
# been sent for the CURRENT ongoing failure, so a persistent outage
# sends exactly one "down" email and one later "recovered" email,
# never a repeat every cycle for the whole duration. Resets on a
# poller restart (worst case: one possible duplicate alert) rather
# than needing a dedicated persistent-state table for this.
_alerted_sources = set()


def send_alert_email(subject, body):
    """
    Send a plain-text alert email via iCloud Mail's SMTP server, using
    an app-specific password (not the real account password) - same
    address as both sender and recipient. Never raises - a missing or
    misconfigured alert channel should never take down the poller
    itself, just skip silently (with a log line).
    """
    if not ALERT_EMAIL_ADDRESS or not ALERT_EMAIL_APP_PASSWORD:
        print("Alert email not configured - skipping")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = ALERT_EMAIL_ADDRESS
    msg["To"] = ALERT_EMAIL_ADDRESS

    try:
        with smtplib.SMTP(ICLOUD_SMTP_HOST, ICLOUD_SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(ALERT_EMAIL_ADDRESS, ALERT_EMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"Alert email sent: {subject}")
        return True
    except Exception as e:
        print(f"Failed to send alert email: {e}")
        return False


def check_and_alert_pipeline_health(db, display_names):
    """
    Fires one email the moment a source in ALERT_WORTHY_SOURCES has any
    failure logged in the last hour, and one follow-up "recovered"
    email once it clears - not a repeated alert every cycle for the
    whole duration it's down.

    critical_count=1 (any single failure counts) rather than
    get_pipeline_health()'s usual sustained-failure threshold -
    deliberately more sensitive here, since these two sources are
    known to need manual intervention to recover rather than
    self-healing on the next cycle like a normal transient blip would.
    """
    health = db.get_pipeline_health(
        sources=list(ALERT_WORTHY_SOURCES), critical_window_hours=1, critical_count=1
    )

    for source in ALERT_WORTHY_SOURCES:
        info = health.get(source, {})
        is_failing = info.get("status") == "critical"
        display_name = display_names.get(source, source)

        if is_failing and source not in _alerted_sources:
            _alerted_sources.add(source)
            send_alert_email(
                subject=f"Apollo Shell: {display_name} is down",
                body=(
                    f"{display_name} just failed its live data fetch.\n\n"
                    f"Most recent error: {info.get('last_error_message') or 'no error message'}\n\n"
                    "This source usually needs a fresh browser capture to recover - "
                    "see the private VM notes for the steps."
                ),
            )
        elif not is_failing and source in _alerted_sources:
            _alerted_sources.discard(source)
            send_alert_email(
                subject=f"Apollo Shell: {display_name} recovered",
                body=f"{display_name} is reporting healthy again.",
            )

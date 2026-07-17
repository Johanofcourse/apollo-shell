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
# intervention to recover, not just a transient network blip. Maps
# each source's pipeline_errors key to the table a successful fetch
# actually logs a row into, so "is this currently failing" can compare
# last failure vs. last success directly, rather than just counting
# failures in a fixed time window (get_pipeline_health()'s own
# threshold is right for the dashboard's "recent issues" strip, but
# wrong for an alert decision - a source that failed 20 minutes ago and
# has since succeeded is NOT currently down, even though that old
# failure is still well within a 1-hour window).
ALERT_WORTHY_SOURCES = {"talquin": "talquin_outages", "preco": "preco_outages"}

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


def _is_currently_failing(db, source, success_table):
    """
    True only if this source's most recent attempt was a failure - a
    failure timestamp with no later success timestamp. A source that
    failed a while ago but has since logged a fresh successful fetch is
    not currently down, regardless of how recent that old failure is.
    """
    conn = db.connect()
    cursor = conn.cursor()

    last_failure = cursor.execute(
        "SELECT MAX(timestamp) FROM pipeline_errors WHERE source = ?", (source,)
    ).fetchone()[0]
    if last_failure is None:
        return False

    last_success = cursor.execute(f"SELECT MAX(timestamp) FROM {success_table}").fetchone()[0]
    return last_success is None or last_failure > last_success


def check_and_alert_pipeline_health(db, display_names):
    """
    Fires one email the moment a source in ALERT_WORTHY_SOURCES is
    currently failing (its last attempt, not just any attempt within a
    time window - see _is_currently_failing()), and one follow-up
    "recovered" email once it succeeds again - not a repeated alert
    every cycle for the whole duration it's down.
    """
    for source, success_table in ALERT_WORTHY_SOURCES.items():
        is_failing = _is_currently_failing(db, source, success_table)
        display_name = display_names.get(source, source)

        if is_failing and source not in _alerted_sources:
            _alerted_sources.add(source)
            conn = db.connect()
            last_error = conn.execute(
                "SELECT error_message FROM pipeline_errors WHERE source = ? ORDER BY timestamp DESC LIMIT 1",
                (source,),
            ).fetchone()
            send_alert_email(
                subject=f"Apollo Shell: {display_name} is down",
                body=(
                    f"{display_name} just failed its live data fetch.\n\n"
                    f"Most recent error: {last_error[0] if last_error else 'no error message'}\n\n"
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

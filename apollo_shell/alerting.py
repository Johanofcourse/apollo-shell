import os
import smtplib
import time
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

# Talquin/PRECO's credential dying is a known, ongoing vendor-side
# issue (confirmed 2026-07-17 - a major third-party outage aggregator
# independently reports the same "unable to get data from Sienatech
# OMS utilities" problem, unresolved for months on their end too), not
# a fresh incident each time it happens. Without a cooldown, a single
# chronic day could flap down/recovered many times, each pair sending
# a real email - technically correct, but not actually useful signal
# once you already know it's the same ongoing thing. Recovery emails
# are NOT throttled (each one is a direct, wanted confirmation tied to
# a real manual fix), only repeat "down" emails for the same source.
DOWN_ALERT_COOLDOWN_SECONDS = 4 * 60 * 60

_last_down_alert_time = {}

# "Down" emails fully silenced for these sources - Johan asked
# 2026-07-18 to stop the recurring "Talquin is down" emails, then asked
# again the same day for PRECO once its own repeat email arrived too:
# the chronic Sienatech issue is already fully understood and disclosed
# (see the public site's footer), so a repeat "still down" email isn't
# new, actionable information the way it is for an ordinary failure.
# Recovery emails are NOT suppressed here - a "back up" email still
# confirms a real manual fix worked, which is genuinely useful. Doesn't
# touch ALERT_WORTHY_SOURCES itself, since that's also reused by
# dashboard.py's pipeline-errors page to group Talquin/PRECO into their
# own "known chronic issue" section - removing either source there
# would have silently broken that grouping too.
DOWN_ALERT_SUPPRESSED_SOURCES = {"talquin", "preco"}

# Display name for every real source this project polls, plus the two
# internal derived-computation steps (correlation, historical_tally) -
# moved here (was dashboard.py-only) so main.py's sustained-failure
# check below can use the same names without a poller-importing-from-
# a-Flask-app dependency; dashboard.py now imports this from here
# instead, same direction it already imports ALERT_WORTHY_SOURCES from.
PIPELINE_SOURCE_DISPLAY_NAMES = {
    "fpl": "FPL",
    "weather": "NWS Weather",
    "teco": "TECO",
    "duke": "Duke Energy",
    "jea": "JEA",
    "tallahassee": "City of Tallahassee",
    "talquin": "Talquin Electric Cooperative",
    "fpuc": "Florida Public Utilities Corporation",
    "preco": "Peace River Electric Cooperative",
    "fkec": "Florida Keys Electric Cooperative",
    "tcec": "Tri-County Electric Cooperative",
    "erec": "Escambia River Electric Cooperative",
    "chelco": "Choctawhatchee Electric Cooperative",
    "gcec": "Gulf Coast Electric Cooperative",
    "lwbu": "Lake Worth Beach Utilities",
    "ouc": "Orlando Utilities Commission",
    "lcec": "Lee County Electric Cooperative",
    "clay": "Clay Electric Cooperative",
    "correlation": "Correlation",
    "historical_tally": "Historical Confidence Tally",
}

# Every other real data source this project polls - Talquin/PRECO keep
# their own single-failure trigger above (ALERT_WORTHY_SOURCES), since
# their credential dying is a known chronic issue where any failure
# really does mean something's wrong. These sources are normally
# reliable, so a lone failure is usually an ordinary transient blip
# that resolves on its own next cycle (a real NWS read-timeout,
# 2026-07-28, self-healed with zero repeat failures) - alerting on the
# first one would be noise, not signal. Deliberately excludes
# "correlation"/"historical_tally" - internal derived-computation
# errors, not a real external source being down (same self-inflicted-
# vs-real distinction /pipeline-errors already draws for readers).
#
# Built 2026-08-02, prompted by two real incidents that each ran for
# hours/days before being caught manually rather than by an alert:
# Duke's expired scraped token, and OUC's silently-rotated data-path
# UUID (108 straight failed cycles, ~27h, only noticed because the
# dashboard's "0 outages" didn't match the utility's own real map).
#
# Maps source -> (success table, that table's own timestamp column) -
# NOT a uniform "timestamp" everywhere: teco_incidents/duke_incidents
# both stamp their rows "fetched_at" instead (a real schema
# inconsistency caught by this feature's own first test run, not
# assumed away).
SUSTAINED_ALERT_WORTHY_SOURCES = {
    "fpl": ("outages", "timestamp"),
    "weather": ("weather_alerts", "timestamp"),
    "teco": ("teco_incidents", "fetched_at"),
    "duke": ("duke_incidents", "fetched_at"),
    "jea": ("jea_outages", "timestamp"),
    "tallahassee": ("tallahassee_outages", "timestamp"),
    "fpuc": ("fpuc_outages", "timestamp"),
    "fkec": ("fkec_outages", "timestamp"),
    "tcec": ("tcec_outages", "timestamp"),
    "erec": ("erec_outages", "timestamp"),
    "chelco": ("chelco_outages", "timestamp"),
    "gcec": ("gcec_outages", "timestamp"),
    "lwbu": ("lwbu_outages", "timestamp"),
    "ouc": ("ouc_outages", "timestamp"),
    "lcec": ("lcec_outages", "timestamp"),
    "clay": ("clay_outages", "timestamp"),
}

# Still failing next cycle too, not just once - see
# SUSTAINED_ALERT_WORTHY_SOURCES's own comment for why a single failure
# isn't enough signal for these normally-reliable sources. Johan's own
# number, 2026-08-02, prompted directly by OUC running 95 failed cycles
# before anyone noticed: "Two consecutive failures and I get an email."
SUSTAINED_FAILURE_THRESHOLD = 2

_sustained_alerted_sources = set()


def _consecutive_failure_count(db, source, success_table, success_column="timestamp"):
    """
    How many times in a row `source` has failed with no success in
    between - every pipeline_errors row for this source logged after
    its most recent successful fetch in `success_table`. Distinct from
    _is_currently_failing() above, which only asks "did the most recent
    attempt fail" (true after just one failure) - this counts, so a
    real sustained-failure threshold can be applied on top of it.

    success_column defaults to "timestamp" (true for most tables here),
    but teco_incidents/duke_incidents stamp their rows "fetched_at"
    instead - a real schema inconsistency, not assumed away.
    """
    conn = db.connect()
    cursor = conn.cursor()

    last_success = cursor.execute(f"SELECT MAX({success_column}) FROM {success_table}").fetchone()[0]
    if last_success is None:
        return cursor.execute(
            "SELECT COUNT(*) FROM pipeline_errors WHERE source = ?", (source,)
        ).fetchone()[0]

    return cursor.execute(
        "SELECT COUNT(*) FROM pipeline_errors WHERE source = ? AND timestamp > ?",
        (source, last_success),
    ).fetchone()[0]


def check_and_alert_sustained_failures(db):
    """
    Fires one email once a source in SUSTAINED_ALERT_WORTHY_SOURCES has
    failed SUSTAINED_FAILURE_THRESHOLD times in a row, and one
    "recovered" email once it succeeds again - same one-alert-per-
    episode principle as check_and_alert_pipeline_health() above, just
    gated on a real consecutive-failure count instead of firing on the
    very first failure.

    No cooldown/suppression here, unlike Talquin/PRECO - every one of
    these sources is normally reliable, so a real sustained failure is
    always worth a fresh email, not a known chronic issue to throttle.
    """
    for source, (success_table, success_column) in SUSTAINED_ALERT_WORTHY_SOURCES.items():
        failure_count = _consecutive_failure_count(db, source, success_table, success_column)
        is_sustained_failing = failure_count >= SUSTAINED_FAILURE_THRESHOLD
        display_name = PIPELINE_SOURCE_DISPLAY_NAMES.get(source, source)

        if is_sustained_failing and source not in _sustained_alerted_sources:
            _sustained_alerted_sources.add(source)

            conn = db.connect()
            last_error = conn.execute(
                "SELECT error_message FROM pipeline_errors WHERE source = ? ORDER BY timestamp DESC LIMIT 1",
                (source,),
            ).fetchone()
            send_alert_email(
                subject=f"Apollo Shell: {display_name} is down",
                body=(
                    f"{display_name} has failed its live data fetch {failure_count} times in a row.\n\n"
                    f"Most recent error: {last_error[0] if last_error else 'no error message'}\n\n"
                    "This has gone past an ordinary transient blip - worth checking directly."
                ),
            )
        elif not is_sustained_failing and source in _sustained_alerted_sources:
            _sustained_alerted_sources.discard(source)
            send_alert_email(
                subject=f"Apollo Shell: {display_name} recovered",
                body=f"{display_name} is reporting healthy again.",
            )


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

    The "down" email itself is additionally cooled down
    (DOWN_ALERT_COOLDOWN_SECONDS) per source - a source that flaps
    down/recovered/down again within the cooldown window (a known,
    ongoing vendor issue, not a series of distinct incidents) only
    re-sends "down" once that cooldown has elapsed, even though the
    underlying failure/recovery state is still tracked and reported
    accurately every cycle. Recovery emails are never throttled.

    A source in DOWN_ALERT_SUPPRESSED_SOURCES never gets a "down" email
    at all, regardless of the cooldown - state tracking and the
    recovery email both still work normally for it.
    """
    for source, success_table in ALERT_WORTHY_SOURCES.items():
        is_failing = _is_currently_failing(db, source, success_table)
        display_name = display_names.get(source, source)

        if is_failing and source not in _alerted_sources:
            _alerted_sources.add(source)

            if source in DOWN_ALERT_SUPPRESSED_SOURCES:
                print(f"{display_name} is down, but down-alerts are suppressed for this source - skipping email")
                continue

            now = time.time()
            last_sent = _last_down_alert_time.get(source, 0)
            if now - last_sent < DOWN_ALERT_COOLDOWN_SECONDS:
                print(f"{display_name} is down again, but within the cooldown window - skipping repeat email")
                continue
            _last_down_alert_time[source] = now

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

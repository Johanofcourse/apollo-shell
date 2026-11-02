"""
One-off cleanup for Duke incident events left open by the Duke API-key
outage that began 2026-07-20 - a real but short (~22.5 hour) credential
outage, not a permanent one. Duke recovered the next day, 2026-07-21.

What happened: duke_incident_events only closes an event when a
successful poll stops listing it. The incidents open at the last good
poll before the outage got no real follow-up poll until Duke's key
came back, so the first good poll after recovery closed all of them at
that *recovery* timestamp instead of their true last-seen time -
overstating their real duration by up to ~38 hours (confirmed directly
against the real data: 36 affected events, true last-seen between
2026-07-20 08:05 and 16:33, all closed at the single recovery
timestamp 2026-07-21T22:11). A real distortion worth fixing, but not
the "100+ days" an earlier version of this comment claimed - that
confused "how long ago this happened" with "how long the events were
actually open," which was wrong.

What this does: for every Duke event that was open at the last good poll
before the outage and was never closed by a real follow-up poll (still
open, or closed long after), set end_time to the last time Duke actually
reported it. That's the only honest end we observed. For events seen in
just one poll that gives a 0-minute duration, which the duration stats
already exclude (business_intelligence skips hours <= 0) - i.e. "real
outage, duration unknown" - while outage counts still include them.

Safe by default:
  - Dry run unless --apply is passed; prints exactly what would change.
  - --apply first writes a full backup copy of the database next to it.
  - Only touches events open at the last good poll; nothing else.
  - Idempotent: rerunning after --apply finds nothing left to fix.

Usage (from the repo root, poller can stay running):
  venv/bin/python apollo_shell/close_stale_duke_events.py              # dry run
  venv/bin/python apollo_shell/close_stale_duke_events.py --apply      # do it
  options: --db PATH (default: outages.db), --last-good ISO_TIMESTAMP
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

# A real follow-up poll closes an event within one or two 15-min cycles of
# last good poll; anything closed later than this was closed by recovery.
NORMAL_CLOSE_WINDOW = timedelta(hours=1)


def find_last_good_poll(conn):
    """
    The last successful Duke poll before the 401 streak began: newest
    duke_counties.fetched_at (written on every successful poll) that's
    older than the first logged 401. None if there's no 401 history.
    """
    first_401 = conn.execute(
        "SELECT MIN(timestamp) FROM pipeline_errors WHERE source = 'duke' AND error_message LIKE '401%'"
    ).fetchone()[0]
    if first_401 is None:
        return None
    return conn.execute(
        "SELECT MAX(fetched_at) FROM duke_counties WHERE fetched_at < ?", (first_401,)
    ).fetchone()[0]


def find_stuck_events(conn, last_good):
    """
    Events open at `last_good` (started at or before it) that no real
    follow-up poll closed: still open, or closed more than
    NORMAL_CLOSE_WINDOW after it. Each comes with last_seen - the newest
    raw duke_incidents row for that incident within [start_time, last_good].
    """
    cutoff = (datetime.fromisoformat(last_good) + NORMAL_CLOSE_WINDOW).isoformat()
    rows = conn.execute('''
        SELECT e.id, e.incident_id, e.county, e.start_time, e.end_time, e.peak_customer_count,
               (SELECT MAX(d.fetched_at) FROM duke_incidents d
                WHERE d.incident_id = e.incident_id
                  AND d.fetched_at >= e.start_time AND d.fetched_at <= ?) AS last_seen
        FROM duke_incident_events e
        WHERE e.start_time <= ?
          AND (e.end_time IS NULL OR e.end_time > ?)
        ORDER BY e.start_time, e.incident_id
    ''', (last_good, last_good, cutoff)).fetchall()
    return [dict(zip(
        ("id", "incident_id", "county", "start_time", "end_time", "peak_customer_count", "last_seen"), r
    )) for r in rows]


def backup(db_path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{db_path}.bak-before-duke-cleanup-{stamp}"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="outages.db")
    parser.add_argument("--last-good", help="override the auto-detected last good Duke poll (ISO timestamp)")
    parser.add_argument("--apply", action="store_true", help="actually write the changes (default: dry run)")
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"No database at {db_path}")
        return 1
    print(f"Database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 30000")  # the live poller may be mid-write

    last_good = args.last_good or find_last_good_poll(conn)
    if last_good is None:
        print("No Duke 401 history found and no --last-good given - nothing to do.")
        return 0
    print(f"Last good Duke poll: {last_good}{'' if args.last_good else ' (auto-detected)'}")

    events = find_stuck_events(conn, last_good)
    fixable = [e for e in events if e["last_seen"]]
    skipped = [e for e in events if not e["last_seen"]]

    if not events:
        print("No stuck Duke events - already clean.")
        return 0

    print(f"\n{len(fixable)} event(s) to close at their last-seen time:")
    print(f"  {'incident_id':<16} {'county':<14} {'start_time':<27} {'end_time now':<27} -> new end_time")
    for e in fixable:
        print(f"  {e['incident_id']:<16} {str(e['county']):<14} {e['start_time']:<27} "
              f"{str(e['end_time'] or 'OPEN'):<27} -> {e['last_seen']}")
    if skipped:
        print(f"\n{len(skipped)} event(s) skipped - no raw duke_incidents row to take a last-seen time from:")
        for e in skipped:
            print(f"  {e['incident_id']} (start {e['start_time']})")

    if not args.apply:
        print("\nDry run - nothing changed. Rerun with --apply to write these changes.")
        return 0

    backup_path = backup(db_path)
    print(f"\nBackup written: {backup_path}")
    with conn:
        conn.executemany(
            "UPDATE duke_incident_events SET end_time = ? WHERE id = ?",
            [(e["last_seen"], e["id"]) for e in fixable],
        )
    print(f"Closed {len(fixable)} event(s).")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

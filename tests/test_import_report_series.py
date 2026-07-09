"""
Regression test for the actual replay bug found 2026-07-08: calling
import_report_series() a second time against an already-populated
database used to fabricate spurious extra outage_events rows, because
sync_outage_events decides "is this currently open" by querying live
database state, which only makes sense for forward-only replay.

Uses a fake parser (import_report_series accepts one via its `parser`
argument) instead of real PDF fixtures - the bug and the fix both live
entirely in the replay/wipe logic, not in PDF text extraction, so a fake
parser that returns fixed (timestamp, records) tuples exercises the exact
same code path without needing to construct or commit real PDF files.
"""

import os
import tempfile
from datetime import datetime

import pytest

from historical_import import import_report_series


def _make_fake_parser(reports_by_path):
    """
    reports_by_path: {path: (datetime, records)} - the fake parser looks
    up its "PDF" contents by the path string alone, so tests can use
    plain labels like "report_1" instead of real files.
    """
    def fake_parser(path):
        return reports_by_path[path]
    return fake_parser


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _three_report_series():
    return {
        "report_1": (
            datetime(2020, 8, 1, 18, 0),
            [{"utility": "Florida Power and Light Company", "county": "MIAMI-DADE",
              "customers_out": 100, "customers_served": 1_000_000}],
        ),
        "report_2": (
            datetime(2020, 8, 2, 6, 0),
            [{"utility": "Florida Power and Light Company", "county": "MIAMI-DADE",
              "customers_out": 300, "customers_served": 1_000_000}],
        ),
        "report_3": (
            datetime(2020, 8, 2, 12, 0),
            [{"utility": "Florida Power and Light Company", "county": "MIAMI-DADE",
              "customers_out": 0, "customers_served": 1_000_000}],
        ),
    }


class TestImportReportSeriesReplaySafety:
    def test_single_run_produces_expected_events(self, db_path):
        reports = _three_report_series()
        parser = _make_fake_parser(reports)

        summary = import_report_series(list(reports.keys()), db_path=db_path, parser=parser)

        assert summary["reports_parsed"] == 3
        assert summary["reports_skipped"] == 0

    def test_running_twice_produces_identical_result(self, db_path):
        # This is the actual regression test: before the fix, running
        # this twice in a row inflated the row count each time (91 extra
        # rows on the first re-run, another 59 on the second, against
        # real data during the Miami-Dade backfill). After the fix, the
        # second run must produce exactly the same result as the first.
        reports = _three_report_series()
        parser = _make_fake_parser(reports)
        paths = list(reports.keys())

        import_report_series(paths, db_path=db_path, parser=parser)
        import sqlite3
        conn = sqlite3.connect(db_path)
        first_run_rows = conn.execute(
            "SELECT county, start_time, end_time, peak_customers_out FROM outage_events ORDER BY start_time"
        ).fetchall()
        conn.close()

        import_report_series(paths, db_path=db_path, parser=parser)
        conn = sqlite3.connect(db_path)
        second_run_rows = conn.execute(
            "SELECT county, start_time, end_time, peak_customers_out FROM outage_events ORDER BY start_time"
        ).fetchall()
        conn.close()

        assert first_run_rows == second_run_rows, (
            "replaying the same report series must be a no-op, not a source of drift"
        )

    def test_running_three_times_still_stable(self, db_path):
        reports = _three_report_series()
        parser = _make_fake_parser(reports)
        paths = list(reports.keys())

        import sqlite3
        counts = []
        for _ in range(3):
            import_report_series(paths, db_path=db_path, parser=parser)
            conn = sqlite3.connect(db_path)
            counts.append(conn.execute("SELECT COUNT(*) FROM outage_events").fetchone()[0])
            conn.close()

        assert counts[0] == counts[1] == counts[2], (
            f"row count must stay stable across repeated replays, got {counts}"
        )

    def test_wipes_unrelated_preexisting_rows_too(self, db_path):
        # import_report_series is documented to always wipe outage_events
        # before replaying, unconditionally - confirm it actually does,
        # not just "happens to produce the same count" by coincidence.
        import sqlite3
        from database import OutageDatabase

        db = OutageDatabase(db_path)
        db.sync_outage_events(
            "Some Other Utility",
            [{"county": "MONROE", "customers_out": 999, "customers_served": 999_999}],
            timestamp="1999-01-01T00:00:00",
        )
        db.close()

        reports = _three_report_series()
        parser = _make_fake_parser(reports)
        import_report_series(list(reports.keys()), db_path=db_path, parser=parser)

        conn = sqlite3.connect(db_path)
        counties = {r[0] for r in conn.execute("SELECT county FROM outage_events").fetchall()}
        conn.close()

        assert "MONROE" not in counties, "pre-existing unrelated rows must be wiped, not merged with"

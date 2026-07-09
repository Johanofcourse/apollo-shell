"""
Tests for the PSC ESF12 report parsing logic in historical_import.py.

Priority is on regression-testing the two real bugs already found in this
project: the Miami-Dade hyphen omission (2026-07-08) and the general shape
of "a line that should parse doesn't, silently" - these tests exercise the
regexes and validation directly against string literals, not real PDF
files, since that's where the actual bug lived and it's what would have
caught it immediately if it existed before the bug did.
"""

from historical_import import (
    ROW_FULL_RE,
    ROW_NO_OUT_RE,
    ROW_NO_PCT_RE,
    COUNTY_SUMMARY_ROW_RE,
    FLORIDA_COUNTIES,
    _is_real_county,
    _parse_timestamp_from_filename,
)


class TestRowFullRe:
    def test_standard_county(self):
        line = "Florida Power and Light Company ALACHUA 1,280 0 0.00% Not Significantly Impacted"
        m = ROW_FULL_RE.match(line)
        assert m is not None
        assert m.group("county") == "ALACHUA"
        assert m.group("customers") == "1,280"
        assert m.group("out") == "0"

    def test_hyphenated_county_miami_dade(self):
        # The actual regression test for the real bug: MIAMI-DADE is the
        # only Florida county with a hyphen in its name, and every one of
        # its rows silently failed to match this regex for as long as the
        # character class omitted "-" (found 2026-07-08, hid undetected
        # since this project's very first historical storm backfill).
        line = "Florida Power and Light Company MIAMI-DADE 1,122,250 247 0.02% TBD"
        m = ROW_FULL_RE.match(line)
        assert m is not None, "MIAMI-DADE must match - this is the exact bug that hid for weeks"
        assert m.group("county") == "MIAMI-DADE"
        assert m.group("out") == "247"

    def test_multi_word_county_with_period(self):
        line = "Fort Pierce Utilities Authority ST. LUCIE 27,630 0 0.00% TBD"
        m = ROW_FULL_RE.match(line)
        assert m is not None
        assert m.group("county") == "ST. LUCIE"

    def test_two_word_county(self):
        line = "Sumter Electric Cooperative, Inc. PALM BEACH 100 5 5.00% TBD"
        m = ROW_FULL_RE.match(line)
        assert m is not None
        assert m.group("county") == "PALM BEACH"


class TestRowNoOutRe:
    def test_already_restored_row(self):
        line = "Florida Power and Light Company BREVARD 345,490 0.00% Restored"
        m = ROW_NO_OUT_RE.match(line)
        assert m is not None
        assert m.group("county") == "BREVARD"

    def test_hyphenated_county(self):
        line = "Florida Power and Light Company MIAMI-DADE 1,122,250 0.00% Restored"
        m = ROW_NO_OUT_RE.match(line)
        assert m is not None
        assert m.group("county") == "MIAMI-DADE"


class TestRowNoPctRe:
    def test_zero_customer_row(self):
        line = "Duke Energy HARDEE 0 0 Restored"
        m = ROW_NO_PCT_RE.match(line)
        assert m is not None
        assert m.group("county") == "HARDEE"

    def test_hyphenated_county(self):
        line = "Homestead MIAMI-DADE 23,086 0 Restored"
        m = ROW_NO_PCT_RE.match(line)
        assert m is not None
        assert m.group("county") == "MIAMI-DADE"


class TestCountySummaryRowRe:
    """Sally's unique county-only format (no per-utility breakdown)."""

    def test_standard_county(self):
        line = "ESCAMBIA 152,702 4,675 3.06%"
        m = COUNTY_SUMMARY_ROW_RE.match(line)
        assert m is not None
        assert m.group("county") == "ESCAMBIA"

    def test_hyphenated_county(self):
        line = "MIAMI-DADE 1,122,250 247 0.02%"
        m = COUNTY_SUMMARY_ROW_RE.match(line)
        assert m is not None, "Sally-format parsing must also handle MIAMI-DADE"
        assert m.group("county") == "MIAMI-DADE"


class TestIsRealCounty:
    def test_all_67_counties_recognized(self):
        assert len(FLORIDA_COUNTIES) == 67

    def test_miami_dade_is_real(self):
        assert _is_real_county("MIAMI-DADE")

    def test_case_and_period_insensitive(self):
        assert _is_real_county("st. johns")
        assert _is_real_county("St Johns")

    def test_garbled_extraction_fragment_rejected(self):
        # Real garbage this project has actually seen from broken PDF
        # table extraction, e.g. a leading stray letter from an adjacent
        # column ("C ALACHUA") or a truncated multi-word name ("BEACH"
        # alone, from a broken "PALM BEACH").
        assert not _is_real_county("C ALACHUA")
        assert not _is_real_county("BEACH")
        assert not _is_real_county("NDIAN RIVER")


class TestParseTimestampFromFilename:
    def test_ampm_convention(self):
        # Sally/Fred/Elsa/etc.'s convention
        result = _parse_timestamp_from_filename("Sally_09-16-20_0600_AM.pdf")
        assert result is not None
        assert result.year == 2020 and result.month == 9 and result.day == 16
        assert result.hour == 6

    def test_24_hour_convention_no_ampm(self):
        # Michael's convention - straight 24-hour time, no AM/PM at all
        result = _parse_timestamp_from_filename("Michael_10-09-18_2105.pdf")
        assert result is not None
        assert result.hour == 21 and result.minute == 5

    def test_24_hour_convention_no_leading_zero_on_day(self):
        # Michael_10-9-18_2100.pdf - single-digit day, no leading zero,
        # a real quirk in the actual source filenames for this storm
        result = _parse_timestamp_from_filename("Michael_10-9-18_2100.pdf")
        assert result is not None
        assert result.day == 9

    def test_unrecognized_filename_returns_none(self):
        assert _parse_timestamp_from_filename("not_a_real_report.pdf") is None

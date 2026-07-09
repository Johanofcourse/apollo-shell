"""
Tests for the NOAA narrative-text extractors in storm_severity.py.
Covers the ICE_RE unescaped-dot fix (2026-07-08) and the storm-history
wind-misattribution guard (the Elsa bug, found earlier).
"""

from storm_severity import ICE_RE, extract_wind_mph


class TestIceRe:
    def test_quarter_inch(self):
        m = ICE_RE.search("Areas reported a quarter inch of ice accumulation.")
        assert m is not None
        assert m.group(1) == "quarter"

    def test_three_quarters_with_hyphen(self):
        m = ICE_RE.search("Some areas saw three-quarters inch of ice.")
        assert m is not None
        assert m.group(1) == "three-quarters"

    def test_three_quarters_with_space(self):
        m = ICE_RE.search("Some areas saw three quarters inch of ice.")
        assert m is not None
        assert m.group(1) == "three quarters"

    def test_numeric_amount(self):
        m = ICE_RE.search("Ice accretion of 1.5 inches of ice was reported.")
        assert m is not None
        assert m.group(1) == "1.5"

    def test_does_not_match_unrelated_separator(self):
        # The actual bug: an unescaped "." in the old pattern matched ANY
        # character, so "threeXquarters" (X being anything at all) would
        # have wrongly matched too. It shouldn't.
        m = ICE_RE.search("A reading of threeXquarters inch of ice was logged.")
        assert m is None


class TestWindMphStormHistoryExclusion:
    def test_extracts_a_real_local_reading(self):
        narrative = "A local station reported a wind gust of 65 mph during the event."
        assert extract_wind_mph(narrative) == 65

    def test_excludes_storm_history_recap_peak_intensity(self):
        # The real Elsa bug: NOAA narratives sometimes restate the
        # storm's own historical peak (paired with its pressure reading)
        # in the same paragraph as the real local reading - this must not
        # be picked up as if it were local.
        narrative = (
            "Elsa reached a peak intensity of 85 mph and 991 mb in the Caribbean. "
            "Local stations reported gusts of 45 mph as the storm passed."
        )
        assert extract_wind_mph(narrative) == 45

    def test_excludes_second_peak_phrasing(self):
        narrative = (
            "The storm reached a second peak of 90 mph and 985 mb before landfall. "
            "A local gust of 50 mph was recorded."
        )
        assert extract_wind_mph(narrative) == 50

    def test_real_tornado_survey_reading_not_wrongly_excluded(self):
        # The first fix attempt for the Elsa bug (excluding any narrative
        # containing "peak intensity of") was too broad and wrongly
        # excluded genuine readings like this one - confirm the narrower,
        # anchored fix doesn't repeat that mistake.
        narrative = "A damage survey found a peak intensity of 95 mph in the tornado's path."
        assert extract_wind_mph(narrative) == 95

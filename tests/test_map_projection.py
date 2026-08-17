"""
Tests for map_projection.py - the real lat/lon -> flat map coordinate
transform, re-derived 2026-08-16 from the exact same source data and
math build_county_map_paths.py used to build florida_county_paths.py
(confirmed a byte-for-byte match against the committed file). Used to
place FPL's real per-incident pins accurately on the public map.
"""

import florida_county_paths as county_map
from map_projection import project_lat_lon


def _ring_bounds(rings):
    xs, ys = [], []
    for ring in rings:
        for pair in ring.split():
            x, y = pair.split(",")
            xs.append(float(x))
            ys.append(float(y))
    return min(xs), max(xs), min(ys), max(ys)


class TestProjectLatLon:
    def test_none_lat_returns_none(self):
        assert project_lat_lon(None, -80.0) is None

    def test_none_lon_returns_none(self):
        assert project_lat_lon(25.0, None) is None

    def test_miami_lands_inside_miami_dade_bounds(self):
        # Real Miami, FL coordinates.
        x, y = project_lat_lon(25.7617, -80.1918)
        min_x, max_x, min_y, max_y = _ring_bounds(county_map.FLORIDA_COUNTY_RINGS["Miami-Dade"])
        assert min_x <= x <= max_x
        assert min_y <= y <= max_y

    def test_orlando_lands_inside_orange_bounds(self):
        # Real Orlando, FL coordinates.
        x, y = project_lat_lon(28.5383, -81.3792)
        min_x, max_x, min_y, max_y = _ring_bounds(county_map.FLORIDA_COUNTY_RINGS["Orange"])
        assert min_x <= x <= max_x
        assert min_y <= y <= max_y

    def test_pensacola_lands_inside_escambia_bounds(self):
        # Real Pensacola, FL coordinates - far west end of the state, a
        # good check that the longitude scaling isn't just coincidentally
        # right near the middle of Florida.
        x, y = project_lat_lon(30.4213, -87.2169)
        min_x, max_x, min_y, max_y = _ring_bounds(county_map.FLORIDA_COUNTY_RINGS["Escambia"])
        assert min_x <= x <= max_x
        assert min_y <= y <= max_y

    def test_further_south_is_greater_y(self):
        # y = -lat before rescaling, and the rescale doesn't flip sign -
        # a real point further south (smaller lat) must land at a
        # greater y than one further north, matching the existing
        # county rings' own top-down (not inverted) orientation.
        _, y_north = project_lat_lon(30.0, -84.0)
        _, y_south = project_lat_lon(26.0, -84.0)
        assert y_south > y_north

    def test_further_east_is_greater_x(self):
        x_west, _ = project_lat_lon(28.0, -87.0)
        x_east, _ = project_lat_lon(28.0, -80.0)
        assert x_east > x_west

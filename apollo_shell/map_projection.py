"""
Projects a real lat/lon point into the same flat 2D coordinate space
FLORIDA_COUNTY_RINGS (florida_county_paths.py) already uses, so a real
point (e.g. an FPL incident's own lat/lng) can be placed accurately on
the public map's county polygons.

These are the exact same equirectangular-with-cos(mean-lat)-scaled-
longitude projection and rescale constants build_county_map_paths.py
computed when it built florida_county_paths.py from real US Census
county boundary data - not a fresh/independent calibration. Re-derived
2026-08-16 by re-fetching that script's own documented source
(https://raw.githubusercontent.com/danielcs88/fl_geo_json/master/
geojson-fl-counties-fips.json) and re-running its exact math; the
result was a byte-for-byte match against the committed
florida_county_paths.py, confirming these constants are the real ones,
not an approximation. If the county map geometry is ever regenerated
from different source data, these constants must be regenerated the
same way (see build_county_map_paths.py) or points will drift from the
county shapes they're supposed to land inside.

Output is flat top-down (x, y), in the same space FLORIDA_COUNTY_RINGS
ships - the client-side isometric projection + per-county extrusion
(see templates_public/index.html's isoProject()/renderMap()) is what
turns this into final screen position, same as it does for county
boundary points. This module does not do that second step.
"""
import math

_MEAN_LAT = 28.03742794421709
_COS_MEAN_LAT = math.cos(math.radians(_MEAN_LAT))
_MIN_X = -77.35016534192577
_MIN_Y = -31.000888
_SCALE = 104.30285259583744


def project_lat_lon(lat, lon):
    """
    Real lat/lon -> flat (x, y) in FLORIDA_COUNTY_RINGS's coordinate
    space. Returns None if either input is None (a real, possible
    state - not every incident resolves a lat/lon).
    """
    if lat is None or lon is None:
        return None
    x = lon * _COS_MEAN_LAT
    y = -lat
    return ((x - _MIN_X) * _SCALE, (y - _MIN_Y) * _SCALE)

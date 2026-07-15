"""
One-time build script: projects and simplifies real Florida county
boundary polygons into flat 2D point rings, writing the result to
florida_county_paths.py. Not run by the live app - the output is a
static data file, regenerated only if the source geography or
simplification tolerance ever needs to change.

Source: real US Census county boundary data (67 features, one per
Florida county, confirmed by name). Projection is simple
equirectangular with longitude scaled by cos(mean latitude) - the same
approach used for the earlier design-sandbox map, real surveyed
coordinates rather than anything hand-traced. Simplification is a
standard Douglas-Peucker pass per ring, to keep the resulting point
data a reasonable size without visibly distorting county shapes at map
scale.

Output is flat (top-down) point rings, not pre-rendered SVG paths - the
public page's own client-side JS does the isometric projection and
per-county extrusion at render time (height depends on live/historical
severity, which isn't known until request time), the same approach the
original design-sandbox map used.
"""
import json
import math

# Real US Census county boundary data (public domain), via a public
# GitHub mirror in GeoJSON form - re-download to this path before
# re-running this script if the output ever needs regenerating:
# https://raw.githubusercontent.com/danielcs88/fl_geo_json/master/geojson-fl-counties-fips.json
SOURCE_PATH = "/tmp/fl_counties.geojson"
OUTPUT_PATH = "florida_county_paths.py"

# Rescale the raw projected coordinates (a few degrees wide) up to a
# pixel-like range comparable to the original design sandbox's map
# (roughly 700 units wide) - purely cosmetic, so extrusion heights (a
# handful of units) read as a sensible fraction of the map's size
# rather than either invisible or wildly oversized.
TARGET_WIDTH = 700


def _douglas_peucker(points, tolerance):
    if len(points) < 3:
        return points

    def perpendicular_distance(pt, start, end):
        (x, y), (x1, y1), (x2, y2) = pt, start, end
        if (x1, y1) == (x2, y2):
            return math.hypot(x - x1, y - y1)
        num = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
        den = math.hypot(y2 - y1, x2 - x1)
        return num / den

    dmax = 0.0
    index = 0
    for i in range(1, len(points) - 1):
        d = perpendicular_distance(points[i], points[0], points[-1])
        if d > dmax:
            index = i
            dmax = d

    if dmax > tolerance:
        left = _douglas_peucker(points[:index + 1], tolerance)
        right = _douglas_peucker(points[index:], tolerance)
        return left[:-1] + right
    return [points[0], points[-1]]


def _project(lon, lat, cos_mean_lat):
    return (lon * cos_mean_lat, -lat)


def build():
    with open(SOURCE_PATH, encoding="utf-8-sig") as f:
        data = json.load(f)

    all_lats = []
    for feature in data["features"]:
        geom = feature["geometry"]
        rings = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for polygon in rings:
            for ring in polygon:
                for lon, lat in ring:
                    all_lats.append(lat)
    mean_lat = sum(all_lats) / len(all_lats)
    cos_mean_lat = math.cos(math.radians(mean_lat))

    county_rings_raw = {}
    all_x, all_y = [], []

    for feature in data["features"]:
        name = feature["properties"]["NAME"]
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]

        rings = []
        for polygon in polygons:
            exterior_ring = polygon[0]
            projected = [_project(lon, lat, cos_mean_lat) for lon, lat in exterior_ring]
            simplified = _douglas_peucker(projected, tolerance=0.0015)
            for x, y in simplified:
                all_x.append(x)
                all_y.append(y)
            rings.append(simplified)

        county_rings_raw[name] = rings

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    scale = TARGET_WIDTH / (max_x - min_x)

    def to_screen(x, y):
        return ((x - min_x) * scale, (y - min_y) * scale)

    county_rings = {}
    for name, rings in county_rings_raw.items():
        county_rings[name] = [
            " ".join(f"{sx:.1f},{sy:.1f}" for sx, sy in (to_screen(x, y) for x, y in ring))
            for ring in rings
        ]

    with open(OUTPUT_PATH, "w") as f:
        f.write('"""\n')
        f.write("Real Florida county boundary rings, pre-projected and simplified -\n")
        f.write("generated once by build_county_map_paths.py, not computed at request\n")
        f.write("time. See that script for the real source and method. Each county maps\n")
        f.write('to a list of ring strings ("x1,y1 x2,y2 ...", one per real polygon -\n')
        f.write("more than one for the 4 real multi-part counties), ready for a\n")
        f.write("client-side isometric projection + extrusion pass, not pre-rendered.\n")
        f.write('"""\n\n')
        f.write("FLORIDA_COUNTY_RINGS = {\n")
        for name in sorted(county_rings):
            f.write(f"    {name!r}: {county_rings[name]!r},\n")
        f.write("}\n")

    print(f"Wrote {len(county_rings)} county ring sets to {OUTPUT_PATH}")
    print(f"Scale: {scale:.2f}, target width: {TARGET_WIDTH}")


if __name__ == "__main__":
    build()

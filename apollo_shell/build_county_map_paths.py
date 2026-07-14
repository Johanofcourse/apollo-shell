"""
One-time build script: projects and simplifies real Florida county
boundary polygons into SVG path data, writing the result to
florida_county_paths.py. Not run by the live app - the output is a
static data file, regenerated only if the source geography or
simplification tolerance ever needs to change.

Source: real US Census county boundary data (67 features, one per
Florida county, confirmed by name). Projection is simple
equirectangular with longitude scaled by cos(mean latitude) - the same
approach used for the earlier design-sandbox map, real surveyed
coordinates rather than anything hand-traced. Simplification is a
standard Douglas-Peucker pass per ring, to keep the resulting path data
a reasonable size without visibly distorting county shapes at map
scale.
"""
import json
import math

# Real US Census county boundary data (public domain), via a public
# GitHub mirror in GeoJSON form - re-download to this path before
# re-running this script if the output ever needs regenerating:
# https://raw.githubusercontent.com/danielcs88/fl_geo_json/master/geojson-fl-counties-fips.json
SOURCE_PATH = "/tmp/fl_counties.geojson"
OUTPUT_PATH = "florida_county_paths.py"

# Real US Census GEO_ID prefix per Florida county name (not needed for
# rendering, just documents where NAME comes from).


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

    county_paths = {}
    all_x, all_y = [], []

    for feature in data["features"]:
        name = feature["properties"]["NAME"]
        geom = feature["geometry"]
        polygons = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]

        subpaths = []
        for polygon in polygons:
            exterior_ring = polygon[0]
            projected = [_project(lon, lat, cos_mean_lat) for lon, lat in exterior_ring]
            simplified = _douglas_peucker(projected, tolerance=0.0015)
            for x, y in simplified:
                all_x.append(x)
                all_y.append(y)
            d = "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in simplified) + " Z"
            subpaths.append(d)

        county_paths[name] = " ".join(subpaths)

    bounds = {
        "min_x": min(all_x), "max_x": max(all_x),
        "min_y": min(all_y), "max_y": max(all_y),
    }

    with open(OUTPUT_PATH, "w") as f:
        f.write('"""\n')
        f.write("Real Florida county boundary paths, pre-projected and simplified -\n")
        f.write("generated once by build_county_map_paths.py, not computed at request\n")
        f.write("time. See that script for the real source and method.\n")
        f.write('"""\n\n')
        f.write(f"FLORIDA_MAP_BOUNDS = {bounds!r}\n\n")
        f.write("FLORIDA_COUNTY_PATHS = {\n")
        for name in sorted(county_paths):
            f.write(f"    {name!r}: {county_paths[name]!r},\n")
        f.write("}\n")

    print(f"Wrote {len(county_paths)} county paths to {OUTPUT_PATH}")
    print(f"Bounds: {bounds}")


if __name__ == "__main__":
    build()

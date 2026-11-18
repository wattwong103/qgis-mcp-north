"""Buffer CRS choice, dissolve groups, clip checks, voronoi cell claims.

No PyQGIS import. The plugin applies these decisions to real geometries.
A geographic buffer is measured in a projected CRS and written back in the
source CRS. Dissolve is one feature, or one feature per field value.
"""

import math
import os

BUFFER_SEGMENTS = 8
VORONOI_MIN_POINTS = 3

# Map-unit length in metres. distance_m is divided by this to get map units.
METRES_PER_UNIT = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "ft": 0.3048,
    "us_ft": 1200.0 / 3937.0,
    "yd": 0.9144,
    "mi": 1609.344,
    "nmi": 1852.0,
}

_OUTPUT_SUFFIXES = (".gpkg", ".geojson", ".json")


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def utm_epsg(lon, lat):
    """EPSG code for the UTM zone of a WGS84 lon/lat, or None outside UTM."""
    if not _finite(lon) or not _finite(lat):
        return None
    if lon < -180.0 or lon > 180.0 or lat < -80.0 or lat > 84.0:
        return None
    zone = 60 if lon == 180.0 else int((lon + 180.0) / 6.0) + 1
    if zone < 1 or zone > 60:
        return None
    base = 32600 if lat >= 0.0 else 32700
    return base + zone


def _projected_epsg(lon, lat):
    """A metre CRS for a WGS84 lon/lat: UTM, or a polar stereographic."""
    if not _finite(lon) or not _finite(lat):
        return None
    if lon < -180.0 or lon > 180.0 or lat < -90.0 or lat > 90.0:
        return None
    if lat > 84.0:
        return 3995
    if lat < -80.0:
        return 3031
    return utm_epsg(lon, lat)


def plan_buffer(source_crs, geographic, unit, lon, lat):
    """Return ``(measure_crs, metres_per_unit, reproject)`` or None.

    ``reproject`` means buffer in ``measure_crs`` and transform the polygons
    back to the source CRS. A known linear unit on a named projected CRS is
    buffered in place after converting metres into that unit.
    """
    if geographic:
        epsg = _projected_epsg(lon, lat)
        if epsg is None:
            return None
        return (f"EPSG:{epsg}", 1.0, True)
    factor = METRES_PER_UNIT.get(unit)
    if factor is not None and source_crs and source_crs != "unknown":
        return (source_crs, factor, False)
    if not source_crs or source_crs == "unknown":
        return None
    epsg = _projected_epsg(lon, lat)
    if epsg is None:
        return None
    return (f"EPSG:{epsg}", 1.0, True)


def check_distance_m(distance_m):
    """A positive finite float, or None when ``distance_m`` is not metres."""
    if isinstance(distance_m, bool) or not isinstance(distance_m, (int, float)):
        return None
    value = float(distance_m)
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def output_suffix_ok(path):
    return os.path.splitext(str(path))[1].lower() in _OUTPUT_SUFFIXES


def buffer_crs_detail(layer_crs):
    return f"BUFFER_CRS: no projected CRS can be chosen for layer CRS {layer_crs}."


def buffer_empty_detail(n, geom, crs):
    return f"BUFFER_EMPTY: 0 features to buffer ({geom}, {crs}, n={n})."


def buffer_geometry_detail(geom, crs):
    return f"BUFFER_GEOMETRY: layer must be point, line, or polygon. layer is {geom} ({crs})."


def group_key(value):
    """Equality key for a dissolve field. 1 and 1.0 match. The string '1' does not."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return ("nan",)
        return ("num", float(value))
    return ("str", str(value))


def dissolve_groups(n, keys):
    """Index groups. ``keys is None`` is one group. Otherwise first-seen order."""
    if n <= 0:
        return []
    if keys is None:
        return [list(range(n))]
    order = []
    groups = {}
    for index, key in enumerate(keys):
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(index)
    return [groups[key] for key in order]


def dissolve_empty_detail(n, geom, crs):
    return f"DISSOLVE_EMPTY: 0 features to dissolve ({geom}, {crs}, n={n})."


def clip_roles_ok(input_geom, overlay_geom):
    return input_geom in ("point", "line", "polygon") and overlay_geom == "polygon"


def clip_empty_detail(n_input, input_geom, input_crs, n_overlay, overlay_geom, overlay_crs):
    return (
        f"CLIP_EMPTY: 0 of {n_input} input features ({input_geom}, {input_crs}) "
        f"fell inside {n_overlay} overlay features ({overlay_geom}, {overlay_crs})."
    )


def clip_geometry_detail(input_geom, input_crs, overlay_geom, overlay_crs):
    return (
        "CLIP_GEOMETRY: overlay must be polygon and input must be point, line, or polygon. "
        f"input is {input_geom} ({input_crs}); overlay is {overlay_geom} ({overlay_crs})."
    )


def voronoi_ready(n_points):
    return n_points >= VORONOI_MIN_POINTS


def claim_cells(n_points, pairs):
    """``(point_index, cell_index)`` pairs. The first pair wins a point or a cell."""
    used_cells = set()
    claimed = set()
    chosen = {}
    for point_i, cell_i in pairs:
        if point_i in claimed or cell_i in used_cells:
            continue
        if point_i < 0 or point_i >= n_points:
            continue
        claimed.add(point_i)
        used_cells.add(cell_i)
        chosen[point_i] = cell_i
    return [(index, chosen[index]) for index in range(n_points) if index in chosen]


def voronoi_points_detail(n, geom, crs):
    return (
        f"VORONOI_POINTS: need at least {VORONOI_MIN_POINTS} points with geometry, "
        f"got {n} ({geom}, {crs})."
    )


def voronoi_empty_detail(crs):
    return f"VORONOI_EMPTY: Voronoi diagram came out empty ({crs})."


def voronoi_geometry_detail(geom, crs):
    return f"VORONOI_GEOMETRY: layer must be points. layer is {geom} ({crs})."

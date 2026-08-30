"""Real WKT geometry from get_layer_features.

Polygons and lines used to come back only as a summary — type, a point count
and a bbox — while points returned WKT. That asymmetry is deliberate (one
prefecture boundary is megabytes of WKT over a length-prefixed socket), but it
left no way to get the geometry at all, which the v0.3 roadmap flagged as the
blocker for DRM link-density work.

`geometry_format="wkt"` is the opt-in. The live tests need QGIS and skip
without it; the threading test does not.
"""

from __future__ import annotations

import json
import math

import pytest

from qgis_mcp_workflows.client import QgisMCPClient
from tests.conftest import requires_headless


def test_client_threads_geometry_options_into_params():
    """The client must forward the new options, not silently drop them."""
    sent = {}

    class _Recorder(QgisMCPClient):
        def __init__(self):
            pass

        def send_command(self, command, params=None):
            sent["command"] = command
            sent["params"] = params
            return {}

    _Recorder().get_layer_features(
        "L1", limit=3, include_geometry=True,
        geometry_format="wkt", geometry_precision=4, simplify_tolerance=0.01,
    )
    assert sent["command"] == "get_layer_features"
    assert sent["params"]["geometry_format"] == "wkt"
    assert sent["params"]["geometry_precision"] == 4
    assert sent["params"]["simplify_tolerance"] == 0.01


def test_client_defaults_preserve_the_summary_contract():
    sent = {}

    class _Recorder(QgisMCPClient):
        def __init__(self):
            pass

        def send_command(self, command, params=None):
            sent["params"] = params
            return {}

    _Recorder().get_layer_features("L1")
    assert sent["params"]["geometry_format"] == "summary"


# ── live, against real geometry ────────────────────────────────────────────


@pytest.fixture
def dense_polygon(tmp_path):
    """A 400-vertex wobbly circle — something simplification can actually bite on.

    A 5-point square is useless here: simplify() correctly leaves it alone, so
    it cannot distinguish a working tolerance from an ignored one.
    """
    pts = []
    for i in range(400):
        t = 2 * math.pi * i / 400
        r = 0.02 + 0.002 * math.sin(12 * t)
        pts.append([139.7 + r * math.cos(t), 35.7 + r * math.sin(t)])
    pts.append(pts[0])
    path = tmp_path / "dense.geojson"
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"id": 1},
                      "geometry": {"type": "Polygon", "coordinates": [pts]}}],
    }), encoding="utf-8")
    return str(path)


@pytest.fixture
def executor():
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    return HeadlessExecutor()


def _load(executor, path):
    return executor.dispatch("add_vector_layer", {"path": path})["id"]


@requires_headless
def test_default_still_summarises_polygons(executor, dense_polygon):
    """Back-compat: the default response shape is unchanged."""
    lid = _load(executor, dense_polygon)
    r = executor.dispatch(
        "get_layer_features", {"layer_id": lid, "limit": 1, "include_geometry": True}
    )
    geom = r["features"][0]["_geometry"]
    assert "wkt_summary" in geom and "wkt" not in geom
    assert len(geom["bbox"]) == 4


@requires_headless
def test_wkt_format_returns_real_geometry(executor, dense_polygon):
    lid = _load(executor, dense_polygon)
    r = executor.dispatch("get_layer_features", {
        "layer_id": lid, "limit": 1, "include_geometry": True, "geometry_format": "wkt",
    })
    geom = r["features"][0]["_geometry"]
    assert geom["wkt"].startswith("Polygon ((")
    assert geom["wkt"].count(",") > 300  # the real vertices, not a summary
    assert "wkt_summary" not in geom


@requires_headless
def test_simplify_tolerance_reduces_vertices(executor, dense_polygon):
    """The size control the docstring promises — verified, not assumed."""
    lid = _load(executor, dense_polygon)

    def vertices(tol):
        r = executor.dispatch("get_layer_features", {
            "layer_id": lid, "limit": 1, "include_geometry": True,
            "geometry_format": "wkt", "simplify_tolerance": tol,
        })
        return r["features"][0]["_geometry"]["wkt"].count(",") + 1

    full, coarse = vertices(0.0), vertices(0.01)
    assert full > 300
    assert coarse < 20
    assert coarse < full


@requires_headless
def test_precision_shortens_coordinates(executor, dense_polygon):
    lid = _load(executor, dense_polygon)

    def length(precision):
        r = executor.dispatch("get_layer_features", {
            "layer_id": lid, "limit": 1, "include_geometry": True,
            "geometry_format": "wkt", "geometry_precision": precision,
        })
        return len(r["features"][0]["_geometry"]["wkt"])

    assert length(2) < length(8)


@requires_headless
def test_unknown_geometry_format_is_rejected(executor, dense_polygon):
    lid = _load(executor, dense_polygon)
    with pytest.raises(Exception, match="geometry_format"):
        executor.dispatch("get_layer_features", {
            "layer_id": lid, "limit": 1, "include_geometry": True,
            "geometry_format": "geojson",
        })


@requires_headless
def test_attribute_conversion_survives_python39(executor, dense_polygon):
    """Regression: `isinstance(v, int | float | ...)` is 3.10+ syntax.

    Two of those sat on the attribute-conversion path, so get_layer_features
    raised "unsupported operand type(s) for |" under the Python 3.9 that
    QGIS-LTR bundles on macOS — every call, not an edge case.
    """
    lid = _load(executor, dense_polygon)
    r = executor.dispatch("get_layer_features", {"layer_id": lid, "limit": 1})
    assert r["features"][0]["id"] == 1

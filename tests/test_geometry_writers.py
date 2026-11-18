"""Geometry writers — buffer, dissolve, clip, voronoi polygons. No QGIS required.

Each tool writes a file another tool can join. qgis_render_catchment stays a PNG.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from qgis_mcp_workflows_plugin.geometry import (
    BUFFER_SEGMENTS,
    VORONOI_MIN_POINTS,
    buffer_crs_detail,
    buffer_empty_detail,
    buffer_geometry_detail,
    check_distance_m,
    claim_cells,
    clip_empty_detail,
    clip_geometry_detail,
    clip_roles_ok,
    dissolve_empty_detail,
    dissolve_groups,
    group_key,
    output_suffix_ok,
    plan_buffer,
    utm_epsg,
    voronoi_empty_detail,
    voronoi_geometry_detail,
    voronoi_points_detail,
    voronoi_ready,
)

REPO = Path(__file__).resolve().parent.parent


def test_tokyo_geographic_buffer_uses_utm_54n_and_writes_source_crs():
    # Tokyo station is in UTM zone 54N. The file goes back to EPSG:4326.
    plan = plan_buffer("EPSG:4326", True, "deg", 139.6917, 35.6895)
    assert plan == ("EPSG:32654", 1.0, True)
    assert utm_epsg(139.6917, 35.6895) == 32654
    assert utm_epsg(139.6917, -35.6895) == 32754


def test_projected_metres_stay_in_the_layer_crs_and_feet_convert():
    assert plan_buffer("EPSG:3857", False, "m", 139.7, 35.7) == ("EPSG:3857", 1.0, False)
    crs, factor, reproject = plan_buffer("EPSG:2263", False, "ft", -73.9, 40.7)
    assert crs == "EPSG:2263"
    assert reproject is False
    assert factor == pytest.approx(0.3048)
    # 500 metres is this many feet. The plugin divides distance_m by the factor.
    assert 500.0 / factor == pytest.approx(500.0 / 0.3048)


def test_polar_and_missing_centroids_name_the_layer_crs():
    assert plan_buffer("EPSG:4326", True, "deg", 0.0, 89.0) == ("EPSG:3995", 1.0, True)
    assert plan_buffer("EPSG:4326", True, "deg", 0.0, -85.0) == ("EPSG:3031", 1.0, True)
    assert utm_epsg(180.0, 0.0) == 32660
    assert utm_epsg(-180.0, 0.0) == 32601
    assert plan_buffer("EPSG:4326", True, "deg", None, None) is None
    assert plan_buffer("unknown", False, "unknown", None, None) is None
    detail = buffer_crs_detail("EPSG:4326")
    assert detail.startswith("BUFFER_CRS:")
    assert "EPSG:4326" in detail
    empty = buffer_empty_detail(0, "polygon", "EPSG:4326")
    assert empty.startswith("BUFFER_EMPTY:")
    assert "EPSG:4326" in empty
    geom = buffer_geometry_detail("no_geom", "unknown")
    assert geom.startswith("BUFFER_GEOMETRY:")
    assert "no_geom" in geom and "unknown" in geom


def test_distance_must_be_a_positive_finite_number_of_metres():
    assert check_distance_m(500) == 500.0
    assert check_distance_m(0.25) == 0.25
    assert check_distance_m(0) is None
    assert check_distance_m(-10) is None
    assert check_distance_m(float("nan")) is None
    assert check_distance_m(float("inf")) is None
    assert check_distance_m(True) is None
    assert check_distance_m("500") is None
    assert BUFFER_SEGMENTS == 8
    assert output_suffix_ok("wards.gpkg") is True
    assert output_suffix_ok("wards.GEOJSON") is True
    assert output_suffix_ok("wards.json") is True
    assert output_suffix_ok("wards.csv") is False
    assert output_suffix_ok("wards.shp") is False


def test_dissolve_is_one_part_or_one_part_per_value():
    assert dissolve_groups(4, None) == [[0, 1, 2, 3]]
    keys = ["13", "13", "14", None, None]
    assert dissolve_groups(len(keys), [group_key(k) for k in keys]) == [[0, 1], [2], [3, 4]]
    # 1 and 1.0 are one value. The string "1" is another.
    mixed = [group_key(1), group_key(1.0), group_key("1")]
    assert dissolve_groups(3, mixed) == [[0, 1], [2]]
    assert dissolve_groups(0, None) == []
    detail = dissolve_empty_detail(0, "polygon", "EPSG:4326")
    assert detail.startswith("DISSOLVE_EMPTY:")
    assert "EPSG:4326" in detail


def test_clip_keeps_a_polygon_mask_and_names_both_layers_when_empty():
    assert clip_roles_ok("polygon", "polygon") is True
    assert clip_roles_ok("point", "polygon") is True
    assert clip_roles_ok("line", "polygon") is True
    assert clip_roles_ok("polygon", "line") is False
    assert clip_roles_ok("no_geom", "polygon") is False
    detail = clip_empty_detail(23, "polygon", "EPSG:4326", 1, "polygon", "EPSG:3857")
    assert detail.startswith("CLIP_EMPTY:")
    assert "EPSG:4326" in detail and "EPSG:3857" in detail
    assert "polygon" in detail
    geom = clip_geometry_detail("line", "EPSG:4326", "point", "EPSG:6668")
    assert geom.startswith("CLIP_GEOMETRY:")
    assert "line" in geom and "point" in geom
    assert "EPSG:4326" in geom and "EPSG:6668" in geom


def test_voronoi_cells_are_claimed_by_the_first_point_and_need_three():
    assert VORONOI_MIN_POINTS == 3
    assert voronoi_ready(2) is False
    assert voronoi_ready(3) is True
    # Point 0 takes cell 2. Point 1 takes cell 0. Point 2 lands on cell 0 too.
    claimed = claim_cells(3, [(0, 2), (1, 0), (2, 0)])
    assert claimed == [(0, 2), (1, 0)]
    assert claim_cells(3, []) == []
    points = voronoi_points_detail(2, "point", "EPSG:4326")
    assert points.startswith("VORONOI_POINTS:")
    assert "2" in points and "EPSG:4326" in points
    empty = voronoi_empty_detail("EPSG:4326")
    assert empty.startswith("VORONOI_EMPTY:")
    geom = voronoi_geometry_detail("line", "EPSG:3857")
    assert geom.startswith("VORONOI_GEOMETRY:")
    assert "line" in geom and "EPSG:3857" in geom


def _ok_buffer(out):
    return {
        "output_path": str(out),
        "n_input": 23,
        "n_output": 23,
        "distance_m": 500.0,
        "measure_crs": "EPSG:32654",
        "source_crs": "EPSG:4326",
    }


def test_buffer_dispatches_metres_and_returns_the_measure_crs(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_buffer

    out = tmp_path / "buffered.gpkg"
    fake_executor.responses["buffer"] = _ok_buffer(out)
    result = qgis_buffer(path="wards.gpkg", output_path=str(out), distance_m=500)
    cmd, params = fake_executor.calls[0]
    assert cmd == "buffer"
    assert os.path.isabs(params["path"])
    assert os.path.isabs(params["output_path"])
    assert params["distance_m"] == 500.0
    assert result.n_input == 23
    assert result.n_output == 23
    assert result.distance_m == 500.0
    assert result.measure_crs == "EPSG:32654"
    assert result.source_crs == "EPSG:4326"
    assert os.path.isabs(result.output_path)


def test_buffer_refuses_a_bad_distance_or_suffix_before_dispatch(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import InvalidArgumentError
    from qgis_mcp_workflows.server import qgis_buffer

    out = str(tmp_path / "out.gpkg")
    for distance in (0, -5, float("nan")):
        with pytest.raises(InvalidArgumentError, match="distance_m") as exc:
            qgis_buffer(path="/tmp/wards.gpkg", output_path=out, distance_m=distance)
        assert "Next:" in str(exc.value)
    with pytest.raises(InvalidArgumentError, match="output_path"):
        qgis_buffer(path="/tmp/wards.gpkg", output_path=str(tmp_path / "out.csv"), distance_m=100)
    assert fake_executor.calls == []


def test_buffer_errors_name_the_layer_crs(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import (
        BufferCrsError,
        BufferEmptyError,
        ExecutorError,
        InvalidArgumentError,
        LayerNotFoundError,
    )
    from qgis_mcp_workflows.server import qgis_buffer

    out = str(tmp_path / "out.gpkg")

    def fail(message):
        def _fail(_params):
            raise ExecutorError("buffer", message)

        return _fail

    fake_executor.responses["buffer"] = fail(buffer_crs_detail("EPSG:4326"))
    with pytest.raises(BufferCrsError, match="EPSG:4326") as exc:
        qgis_buffer(path="/tmp/wards.gpkg", output_path=out, distance_m=500)
    assert "Next:" in str(exc.value)
    assert "qgis_layer_inspect" in str(exc.value)

    fake_executor.responses["buffer"] = fail(buffer_empty_detail(0, "polygon", "EPSG:4326"))
    with pytest.raises(BufferEmptyError, match="EPSG:4326") as exc:
        qgis_buffer(path="/tmp/wards.gpkg", output_path=out, distance_m=500)
    assert "Next:" in str(exc.value)

    fake_executor.responses["buffer"] = fail(buffer_geometry_detail("no_geom", "unknown"))
    with pytest.raises(InvalidArgumentError, match="no_geom") as exc:
        qgis_buffer(path="/tmp/table.csv", output_path=out, distance_m=500)
    assert "Next:" in str(exc.value)

    fake_executor.responses["buffer"] = fail("LAYER_NOT_FOUND: /tmp/wards.gpkg")
    with pytest.raises(LayerNotFoundError):
        qgis_buffer(path="/tmp/wards.gpkg", output_path=out, distance_m=500)


def test_dissolve_omits_a_blank_field_and_sends_a_real_one(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_dissolve

    out = tmp_path / "dissolved.geojson"
    fake_executor.responses["dissolve"] = {
        "output_path": str(out),
        "n_input": 23,
        "n_output": 1,
        "field": None,
    }
    result = qgis_dissolve(path="wards.gpkg", output_path=str(out))
    params = fake_executor.calls[0][1]
    assert fake_executor.calls[0][0] == "dissolve"
    assert "field" not in params
    assert result.n_output == 1
    assert result.field is None
    assert os.path.isabs(result.output_path)

    fake_executor.calls.clear()
    fake_executor.responses["dissolve"] = {
        "output_path": str(out),
        "n_input": 23,
        "n_output": 2,
        "field": "pref",
    }
    result = qgis_dissolve(path="/tmp/wards.gpkg", output_path=str(out), field=" pref ")
    assert fake_executor.calls[0][1]["field"] == "pref"
    assert result.field == "pref"
    assert result.n_output == 2


def test_dissolve_empty_and_missing_field(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import DissolveEmptyError, ExecutorError, FieldNotFoundError
    from qgis_mcp_workflows.server import qgis_dissolve

    out = str(tmp_path / "out.gpkg")

    def empty(_params):
        raise ExecutorError("dissolve", dissolve_empty_detail(0, "polygon", "EPSG:6677"))

    fake_executor.responses["dissolve"] = empty
    with pytest.raises(DissolveEmptyError, match="EPSG:6677") as exc:
        qgis_dissolve(path="/tmp/wards.gpkg", output_path=out)
    assert "Next:" in str(exc.value)

    def missing(_params):
        raise ExecutorError("dissolve", "FIELD_NOT_FOUND: pref. Available: ['zone_id']")

    fake_executor.responses["dissolve"] = missing
    with pytest.raises(FieldNotFoundError, match="pref"):
        qgis_dissolve(path="/tmp/wards.gpkg", output_path=out, field="pref")


def test_clip_dispatches_the_overlay_and_empty_names_both_crs(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ClipEmptyError, ExecutorError, InvalidArgumentError
    from qgis_mcp_workflows.server import qgis_clip

    out = tmp_path / "clipped.gpkg"
    fake_executor.responses["clip"] = {
        "output_path": str(out),
        "n_input": 23,
        "n_overlay": 1,
        "n_output": 23,
    }
    result = qgis_clip(path="mesh.gpkg", overlay_path="tokyo.gpkg", output_path=str(out))
    cmd, params = fake_executor.calls[0]
    assert cmd == "clip"
    assert os.path.isabs(params["path"])
    assert os.path.isabs(params["overlay_path"])
    assert result.n_input == 23
    assert result.n_overlay == 1
    assert result.n_output == 23

    def empty(_params):
        raise ExecutorError("clip", clip_empty_detail(23, "polygon", "EPSG:4326", 1, "polygon", "EPSG:3857"))

    fake_executor.responses["clip"] = empty
    with pytest.raises(ClipEmptyError, match="EPSG:4326") as exc:
        qgis_clip(path="/tmp/mesh.gpkg", overlay_path="/tmp/tokyo.gpkg", output_path=str(out))
    text = str(exc.value)
    assert "EPSG:3857" in text
    assert "Next:" in text
    assert "qgis_layer_inspect" in text

    def bad(_params):
        raise ExecutorError("clip", clip_geometry_detail("line", "EPSG:4326", "point", "EPSG:4326"))

    fake_executor.responses["clip"] = bad
    with pytest.raises(InvalidArgumentError, match="polygon") as exc:
        qgis_clip(path="/tmp/lines.gpkg", overlay_path="/tmp/pts.gpkg", output_path=str(out))
    assert "Next:" in str(exc.value)


def test_voronoi_dispatches_a_polygon_file_and_the_png_tool_stays(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError, InvalidArgumentError, VoronoiError
    from qgis_mcp_workflows.server import qgis_render_catchment, qgis_voronoi

    out = tmp_path / "cells.gpkg"
    fake_executor.responses["voronoi"] = {
        "output_path": str(out),
        "n_input": 4,
        "n_output": 4,
    }
    result = qgis_voronoi(path="stops.gpkg", output_path=str(out))
    assert fake_executor.calls[0][0] == "voronoi"
    assert os.path.isabs(fake_executor.calls[0][1]["path"])
    assert result.n_input == 4
    assert result.n_output == 4
    assert os.path.isabs(result.output_path)
    # The PNG tool is still the catchment renderer. This call must not be it.
    assert qgis_voronoi is not qgis_render_catchment

    def few(_params):
        raise ExecutorError("voronoi", voronoi_points_detail(2, "point", "EPSG:4326"))

    fake_executor.responses["voronoi"] = few
    with pytest.raises(VoronoiError, match="EPSG:4326") as exc:
        qgis_voronoi(path="/tmp/stops.gpkg", output_path=str(out))
    assert "Next:" in str(exc.value)
    assert "3" in str(exc.value)

    def lines(_params):
        raise ExecutorError("voronoi", voronoi_geometry_detail("line", "EPSG:3857"))

    fake_executor.responses["voronoi"] = lines
    with pytest.raises(InvalidArgumentError, match="point") as exc:
        qgis_voronoi(path="/tmp/lines.gpkg", output_path=str(out))
    assert "Next:" in str(exc.value)


def test_results_are_recorded_files():
    from qgis_mcp_workflows.provenance import WRITTEN_BY_MODEL
    from qgis_mcp_workflows.server import BufferResult, ClipResult, DissolveResult, VoronoiResult

    buffer = BufferResult(
        output_path="/tmp/b.gpkg", n_input=1, n_output=1, distance_m=10.0,
        measure_crs="EPSG:32654", source_crs="EPSG:4326",
    )
    dissolve = DissolveResult(output_path="/tmp/d.gpkg", n_input=2, n_output=1, field=None)
    clip = ClipResult(output_path="/tmp/c.gpkg", n_input=2, n_overlay=1, n_output=1)
    voronoi = VoronoiResult(output_path="/tmp/v.gpkg", n_input=3, n_output=3)
    assert WRITTEN_BY_MODEL["BufferResult"](buffer) == ["/tmp/b.gpkg"]
    assert WRITTEN_BY_MODEL["DissolveResult"](dissolve) == ["/tmp/d.gpkg"]
    assert WRITTEN_BY_MODEL["ClipResult"](clip) == ["/tmp/c.gpkg"]
    assert WRITTEN_BY_MODEL["VoronoiResult"](voronoi) == ["/tmp/v.gpkg"]


def test_compound_mode_does_not_grow_a_geometry_kind():
    text = (REPO / "src" / "qgis_mcp_workflows" / "compound.py").read_text(encoding="utf-8")
    for name in ("qgis_buffer", "qgis_dissolve", "qgis_clip", "qgis_voronoi"):
        assert name not in text


def test_plugin_handlers_write_files_and_do_not_touch_iface():
    source = (REPO / "qgis_mcp_workflows_plugin" / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    table = source[source.find("handlers = {") : source.find("handler = handlers.get")]
    expected = {
        "buffer": ("plan_buffer", "BUFFER_SEGMENTS"),
        "dissolve": ("dissolve_groups",),
        "clip": ("clip_roles_ok", "clip_empty_detail"),
        "voronoi": ("voronoiDiagram", "claim_cells"),
    }
    for name, needles in expected.items():
        assert name in methods
        body = ast.get_source_segment(source, methods[name])
        assert "self.iface" not in body
        assert "processing.run" not in body
        assert "native:" not in body
        for needle in needles:
            assert needle in body
        assert f'"{name}"' in table
    catchment = ast.get_source_segment(source, methods["render_catchment"])
    assert "voronoiDiagram" in catchment
    assert "output_png" in catchment

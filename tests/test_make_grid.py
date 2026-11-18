"""Fishnet polygons. No QGIS required.

A metre grid for cells that are not an existing zone layer. The JIS L3
asset stays scripts/build_gufm_zones.py.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from qgis_mcp_workflows.grid import (
    MAX_CELLS,
    cell_box,
    cell_id,
    check_cell_m,
    check_extent,
    grid_crs_detail,
    grid_too_many_detail,
    index_ceil,
    index_floor,
    normalize_crs,
    plan_grid,
)

REPO = Path(__file__).resolve().parent.parent


def test_server_and_plugin_share_one_plan():
    server = (REPO / "src" / "qgis_mcp_workflows" / "grid.py").read_bytes()
    plugin = (REPO / "qgis_mcp_workflows_plugin" / "grid.py").read_bytes()
    assert server == plugin
    assert MAX_CELLS == 250_000


def test_cells_align_to_the_crs_origin_and_cover_the_extent():
    exact = plan_grid([0, 0, 1000, 500], 250)
    assert exact["n_cols"] == 4
    assert exact["n_rows"] == 2
    assert exact["n_cells"] == 8
    assert exact["grid_extent"] == [0.0, 0.0, 1000.0, 500.0]
    # 10 is not on a 50 m line, so the written box starts at 0 and ends at 100.
    overhang = plan_grid([10, 20, 90, 80], 50)
    assert overhang["col0"] == 0 and overhang["col1"] == 2
    assert overhang["row0"] == 0 and overhang["row1"] == 2
    assert overhang["grid_extent"] == [0.0, 0.0, 100.0, 100.0]
    # An edge already on a cell line does not grow an extra cell.
    closed = plan_grid([0, 0, 100, 100], 50)
    assert closed["n_cols"] == 2 and closed["n_rows"] == 2
    assert closed["grid_extent"] == [0.0, 0.0, 100.0, 100.0]


def test_negative_indices_and_a_shared_cell_id():
    west = plan_grid([-150, -20, 10, 80], 100)
    assert west["col0"] == -2 and west["col1"] == 1
    assert west["row0"] == -1 and west["row1"] == 1
    assert west["grid_extent"] == [-200.0, -100.0, 100.0, 100.0]
    assert cell_box(-2, -1, 100) == (-200, -100, -100, 0)
    assert cell_id(-2, -1) == "-2_-1"
    # Two extents, one cell size. The square that both cover has one id.
    left = plan_grid([10, 10, 90, 90], 50)
    right = plan_grid([60, 60, 140, 140], 50)
    left_ids = {
        cell_id(col, row)
        for row in range(left["row0"], left["row1"])
        for col in range(left["col0"], left["col1"])
    }
    right_ids = {
        cell_id(col, row)
        for row in range(right["row0"], right["row1"])
        for col in range(right["col0"], right["col1"])
    }
    assert left_ids & right_ids == {"1_1"}
    # Adjacent boxes share an edge and no cell.
    a = plan_grid([0, 0, 100, 100], 100)
    b = plan_grid([100, 0, 200, 100], 100)
    assert cell_id(a["col0"], a["row0"]) == "0_0"
    assert cell_id(b["col0"], b["row0"]) == "1_0"


def test_float_edges_on_a_cell_line_do_not_add_a_cell():
    # 0.3 / 0.1 is not 3 in binary. The edge is still the third cell line.
    plan = plan_grid([0.0, 0.0, 0.3, 0.1], 0.1)
    assert plan["n_cols"] == 3
    assert plan["n_rows"] == 1
    assert plan["grid_extent"][2] == pytest.approx(0.3)
    wider = plan_grid([0.0, 0.0, 0.31, 0.1], 0.1)
    assert wider["n_cols"] == 4
    assert index_floor(0.0, 0.1) == 0
    assert index_ceil(0.3, 0.1) == 3
    assert index_ceil(0.31, 0.1) == 4


def test_bad_extent_and_cell_size_are_rejected_before_a_plan():
    assert check_cell_m(500) == 500.0
    assert check_cell_m(0) is None
    assert check_cell_m(-1) is None
    assert check_cell_m(float("nan")) is None
    assert check_cell_m(True) is None
    assert check_cell_m("500") is None
    assert check_extent([0, 0, 1, 1]) == (0.0, 0.0, 1.0, 1.0)
    assert check_extent((0, 0, 1, 2)) == (0.0, 0.0, 1.0, 2.0)
    assert check_extent([0, 0, 0, 1]) is None
    assert check_extent([1, 0, 0, 1]) is None
    assert check_extent([0, 0, 1, float("inf")]) is None
    assert check_extent("assets/zones.gpkg") is None
    assert check_extent([0, 0, 1]) is None
    assert check_extent([True, 0, 1, 1]) is None
    assert plan_grid([0, 0, 1, 1], 0) is None
    assert normalize_crs("32654") == "EPSG:32654"
    assert normalize_crs(" epsg:6677 ") == "EPSG:6677"
    assert normalize_crs("EPSG:32654") == "EPSG:32654"
    assert normalize_crs("") is None
    assert normalize_crs(32654) is None
    assert grid_crs_detail("EPSG:4326", "deg").startswith("GRID_CRS:")
    assert "deg" in grid_crs_detail("EPSG:4326", "deg")
    assert "not a CRS" in grid_crs_detail("nope", "invalid")


def _ids_from(plan):
    return [
        cell_id(col, row)
        for row in range(plan["row0"], plan["row1"])
        for col in range(plan["col0"], plan["col1"])
    ]


def test_grid_dispatches_metres_and_returns_the_join_field(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_make_grid

    out = tmp_path / "fishnet.gpkg"
    plan = plan_grid([139000, 35000, 140000, 36000], 500)
    fake_executor.responses["make_grid"] = {
        "output_path": str(out),
        "n_output": plan["n_cells"],
        "n_rows": plan["n_rows"],
        "n_cols": plan["n_cols"],
        "cell_m": 500.0,
        "crs": "EPSG:6677",
        "extent": plan["extent"],
        "grid_extent": plan["grid_extent"],
        "id_field": "cell_id",
    }
    result = qgis_make_grid(
        extent=[139000, 35000, 140000, 36000],
        output_path=str(out),
        cell_m=500,
        crs="epsg:6677",
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "make_grid"
    assert params["crs"] == "EPSG:6677"
    assert params["cell_m"] == 500.0
    assert params["extent"] == plan["extent"]
    assert os.path.isabs(params["output_path"])
    assert result.n_output == plan["n_cells"]
    assert result.n_rows == plan["n_rows"]
    assert result.n_cols == plan["n_cols"]
    assert result.id_field == "cell_id"
    assert result.crs == "EPSG:6677"
    assert result.grid_extent == plan["grid_extent"]
    assert os.path.isabs(result.output_path)
    assert "code" not in params
    assert len(_ids_from(plan)) == plan["n_cells"]


def test_grid_refuses_a_bad_call_before_dispatch(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import GridTooManyError, InvalidArgumentError
    from qgis_mcp_workflows.helpers import bundled_asset
    from qgis_mcp_workflows.server import qgis_make_grid

    out = str(tmp_path / "out.gpkg")
    extent = [0, 0, 1000, 1000]
    with pytest.raises(InvalidArgumentError, match="cell_m") as exc:
        qgis_make_grid(extent=extent, output_path=out, cell_m=0, crs="EPSG:32654")
    assert "Next:" in str(exc.value)
    with pytest.raises(InvalidArgumentError, match="layer path"):
        qgis_make_grid(extent="wards.gpkg", output_path=out, cell_m=100, crs="EPSG:32654")
    with pytest.raises(InvalidArgumentError, match="extent"):
        qgis_make_grid(extent=[0, 0, 0, 10], output_path=out, cell_m=100, crs="EPSG:32654")
    with pytest.raises(InvalidArgumentError, match="output_path"):
        qgis_make_grid(extent=extent, output_path=str(tmp_path / "out.csv"), cell_m=100, crs="EPSG:32654")
    with pytest.raises(InvalidArgumentError, match="crs"):
        qgis_make_grid(extent=extent, output_path=out, cell_m=100, crs="  ")
    mesh = bundled_asset("assets", "zones_mesh_l3.gpkg")
    assert mesh
    with pytest.raises(InvalidArgumentError, match="zones_mesh_l3") as exc:
        qgis_make_grid(extent=extent, output_path=mesh, cell_m=1000, crs="32654")
    assert "build_gufm_zones.py" in str(exc.value)
    assert "Next:" in str(exc.value)
    # The same file name somewhere else is a normal output.
    copy = tmp_path / "zones_mesh_l3.gpkg"
    fake_executor.responses["make_grid"] = {
        "output_path": str(copy),
        "n_output": 1,
        "n_rows": 1,
        "n_cols": 1,
        "cell_m": 1000.0,
        "crs": "EPSG:32654",
        "extent": [0.0, 0.0, 1000.0, 1000.0],
        "grid_extent": [0.0, 0.0, 1000.0, 1000.0],
        "id_field": "cell_id",
    }
    qgis_make_grid(extent=extent, output_path=str(copy), cell_m=1000, crs="32654")
    assert fake_executor.calls[-1][1]["crs"] == "EPSG:32654"
    # 501 by 500 is one row of cells over the cap. Nothing is dispatched for it.
    calls_before = len(fake_executor.calls)
    with pytest.raises(GridTooManyError, match="250500") as exc:
        qgis_make_grid(extent=[0, 0, 501, 500], output_path=out, cell_m=1, crs="EPSG:32654")
    assert "Next:" in str(exc.value)
    assert "cell_m" in str(exc.value)
    assert len(fake_executor.calls) == calls_before
    detail = grid_too_many_detail(plan_grid([0, 0, 501, 500], 1))
    assert detail.startswith("GRID_TOO_MANY:")
    assert "250000" in detail


def test_plugin_crs_and_extent_errors_keep_their_hint(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import (
        ExecutorError,
        GridCrsError,
        GridTooManyError,
        InvalidArgumentError,
    )
    from qgis_mcp_workflows.server import qgis_make_grid

    out = str(tmp_path / "out.gpkg")

    def fail(message):
        def _fail(_params):
            raise ExecutorError("make_grid", message)

        return _fail

    fake_executor.responses["make_grid"] = fail(grid_crs_detail("EPSG:4326", "deg"))
    with pytest.raises(GridCrsError, match="EPSG:4326") as exc:
        qgis_make_grid(extent=[0, 0, 1000, 1000], output_path=out, cell_m=100, crs="EPSG:4326")
    assert "metres" in str(exc.value)
    assert "Next:" in str(exc.value)
    assert "qgis_make_grid" in str(exc.value)

    fake_executor.responses["make_grid"] = fail(grid_crs_detail("nope", "invalid"))
    with pytest.raises(GridCrsError, match="nope"):
        qgis_make_grid(extent=[0, 0, 1000, 1000], output_path=out, cell_m=100, crs="nope")

    fake_executor.responses["make_grid"] = fail("GRID_EXTENT: extent collapsed")
    with pytest.raises(InvalidArgumentError, match="GRID_EXTENT") as exc:
        qgis_make_grid(extent=[0, 0, 1000, 1000], output_path=out, cell_m=100, crs="EPSG:32654")
    assert "Next:" in str(exc.value)

    # A small grid passes the server cap. A plugin GRID_TOO_MANY still maps.
    fake_executor.responses["make_grid"] = fail(
        "GRID_TOO_MANY: 2 by 2 cells is 4 at cell_m=50.0, and the cap is 250000."
    )
    with pytest.raises(GridTooManyError, match="GRID_TOO_MANY") as exc:
        qgis_make_grid(extent=[0, 0, 100, 100], output_path=out, cell_m=50, crs="EPSG:32654")
    assert "Next:" in str(exc.value)


def test_grid_result_is_a_recorded_file():
    from qgis_mcp_workflows.provenance import WRITTEN_BY_MODEL
    from qgis_mcp_workflows.server import GridResult

    result = GridResult(
        output_path="/tmp/grid.gpkg",
        n_output=4,
        n_rows=2,
        n_cols=2,
        cell_m=100.0,
        crs="EPSG:32654",
        extent=[0, 0, 200, 200],
        grid_extent=[0, 0, 200, 200],
        id_field="cell_id",
    )
    assert WRITTEN_BY_MODEL["GridResult"](result) == ["/tmp/grid.gpkg"]


def test_compound_mode_does_not_grow_a_grid_kind():
    text = (REPO / "src" / "qgis_mcp_workflows" / "compound.py").read_text(encoding="utf-8")
    assert "qgis_make_grid" not in text
    assert "make_grid" not in text


def test_plugin_handler_writes_polygons_and_does_not_touch_iface():
    source = (REPO / "qgis_mcp_workflows_plugin" / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    table = source[source.find("handlers = {") : source.find("handler = handlers.get")]
    assert '"make_grid"' in table
    body = ast.get_source_segment(source, methods["make_grid"])
    assert "self.iface" not in body
    assert "processing.run" not in body
    assert "native:" not in body
    assert "addMapLayer" not in body
    assert "zones_mesh_l3" not in body
    assert "plan_grid" in body
    assert "cell_box" in body
    assert "cell_id" in body
    assert "fromPolygonXY" in body
    assert "MAX_CELLS" in body
    for name in ("buffer", "dissolve", "clip", "voronoi"):
        assert name in methods

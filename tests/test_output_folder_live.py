"""Live tests: render and layout tools create the folder they write into.

Found 2026-10-20: qgis_render_map into a folder that did not exist failed with
"Failed to save render to ... Next: inspect inputs", which hid the cause, while
qgis_spatial_join, qgis_zonal_stats, atlas and batch exports create theirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless
from tests.test_plugin_live_fixes import _atlas_qgz, _points_geojson

pytestmark = requires_headless
ZONES = str(Path(__file__).resolve().parent / "fixtures" / "tiny_zones.geojson")


@pytest.fixture(scope="module")
def headless():
    ex = HeadlessExecutor()
    try:
        yield ex
    finally:
        ex.shutdown()


def test_render_map_creates_its_output_folder(headless, tmp_path):
    path = _points_geojson(tmp_path / "pts.geojson", "k", ["a", "b"])
    layer_id = headless.dispatch("add_vector_layer", {"path": path})["id"]
    out = tmp_path / "new" / "deeper" / "map.png"
    try:
        headless.dispatch("render_layers_to_path", {"layer_ids": [layer_id], "output_png": str(out),
                                                    "width": 300, "height": 200})
    finally:
        headless.dispatch("remove_layer", {"layer_id": layer_id})
    assert out.stat().st_size > 0


def test_a_folder_that_cannot_be_made_gets_an_actionable_error(headless, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    path = _points_geojson(tmp_path / "pts.geojson", "k", ["a"])
    (tmp_path / "blocker").write_text("a file where the folder should go", encoding="utf-8")
    layer_id = headless.dispatch("add_vector_layer", {"path": path})["id"]
    try:
        with pytest.raises(ExecutorError, match=r"Cannot create the folder .*blocker.*Next:") as err:
            headless.dispatch("render_layers_to_path", {"layer_ids": [layer_id],
                                                        "output_png": str(tmp_path / "blocker" / "x.png")})
    finally:
        headless.dispatch("remove_layer", {"layer_id": layer_id})
    assert str(err.value).count("Next:") == 1


def test_compose_layout_creates_its_output_folder(headless, tmp_path):
    out = tmp_path / "new" / "layout.png"
    headless.dispatch("compose_layout", {"layer_paths": [ZONES], "output_path": str(out), "dpi": 72,
                                         "legend": False, "scale_bar": False, "north_arrow": False})
    assert out.stat().st_size > 0


def test_export_layout_creates_its_output_folder(headless, tmp_path):
    qgz = _atlas_qgz(headless, tmp_path)
    out = tmp_path / "new" / "page.png"
    headless.dispatch("export_layout", {"layout_name": "ward_atlas", "format": "png", "dpi": 72,
                                        "qgz_path": qgz, "output_path": str(out)})
    assert out.stat().st_size > 0

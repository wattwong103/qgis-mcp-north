"""Live regression tests for plugin handler bugs found by the 2026-10-06 upstream review.

FakeExecutor tests cannot reach these: the bugs live in PyQGIS calls inside
``qgis_mcp_workflows_plugin/plugin.py``. Skipped unless a QGIS Python launcher is
reachable (see ``tests/conftest.py::requires_headless``).
"""

from __future__ import annotations

import json
import os
import re

import pytest

from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless

pytestmark = requires_headless

OTHER = "all other values"


@pytest.fixture(scope="module")
def headless():
    ex = HeadlessExecutor()
    try:
        yield ex
    finally:
        ex.shutdown()


def _run(headless, code, **values):
    """execute_code with ``values`` bound as Python literals; returns ``result``."""
    prelude = "".join(f"{name} = {value!r}\n" for name, value in values.items())
    out = headless.dispatch("execute_code", {"code": prelude + code, "return_vars": ["result"]})
    assert out.get("executed") and not out.get("exception"), out
    return out["return_values"]["result"]


def _points_geojson(path, field, values):
    features = [
        {
            "type": "Feature",
            "properties": {field: v},
            "geometry": {"type": "Point", "coordinates": [139.7 + i * 0.01, 35.7]},
        }
        for i, v in enumerate(values)
    ]
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return str(path)


# What each feature actually draws with, from the renderer itself (not the
# response's class list, which the handler computes separately).
DRAWN_COLORS = r'''
from qgis.core import QgsProject, QgsRenderContext
layer = QgsProject.instance().mapLayer(LAYER_ID)
renderer = layer.renderer()
ctx = QgsRenderContext()
renderer.startRender(ctx, layer.fields())
result = {}
for feature in layer.getFeatures():
    value = feature[FIELD]
    key = "NULL" if value is None or (hasattr(value, "isNull") and value.isNull()) else str(value)
    symbol = renderer.symbolForFeature(feature, ctx)
    result[key] = symbol.color().name() if symbol else None
renderer.stopRender(ctx)
'''


def _style_subset(headless, path, field, subset):
    """Apply a categorized subset; return (response, {value: drawn color})."""
    layer_id = headless.dispatch("add_vector_layer", {"path": path})["id"]
    try:
        response = headless.dispatch(
            "set_layer_style",
            {"layer_id": layer_id, "style_type": "categorized", "field": field,
             "color_ramp": "Spectral", "classes_subset": subset},
        )
        drawn = _run(headless, DRAWN_COLORS, LAYER_ID=layer_id, FIELD=field)
    finally:
        headless.dispatch("remove_layer", {"layer_id": layer_id})
    return response, drawn


def _class_colors(response):
    return {c["value"]: c["color"] for c in response["classes"]}


def test_categorized_subset_renders_listed_values_in_order_and_lumps_the_rest(headless, tmp_path):
    path = _points_geojson(tmp_path / "modes.geojson", "mode", ["walk", "bus", "rail", "bus", None])
    response, drawn = _style_subset(headless, path, "mode", ["rail", "walk"])

    counts = [(c["value"], c["n_features"]) for c in response["classes"]]
    # bus x2 and the NULL share the one catch-all class, reported last.
    assert counts == [("rail", 1), ("walk", 1), (OTHER, 3)]
    colors = _class_colors(response)
    assert drawn == {
        "rail": colors["rail"], "walk": colors["walk"], "bus": colors[OTHER], "NULL": colors[OTHER],
    }
    assert colors["rail"] != colors["walk"] != colors[OTHER]


def test_categorized_subset_keeps_an_absent_value_as_an_empty_class(headless, tmp_path):
    # Weekly batches keep one legend: "taxi" missing this week is still a class.
    path = _points_geojson(tmp_path / "week.geojson", "mode", ["walk", "rail", "bus"])
    response, drawn = _style_subset(headless, path, "mode", ["rail", "taxi", "rail", "walk"])

    counts = [(c["value"], c["n_features"]) for c in response["classes"]]
    assert counts == [("rail", 1), ("taxi", 0), ("walk", 1), (OTHER, 1)]  # repeat dropped
    assert drawn["bus"] == _class_colors(response)[OTHER]


def test_categorized_subset_matches_integer_values_by_their_string_form(headless, tmp_path):
    path = _points_geojson(tmp_path / "codes.geojson", "code", [1, 3, 3, 2])
    response, drawn = _style_subset(headless, path, "code", ["3", "1"])

    counts = [(c["value"], c["n_features"]) for c in response["classes"]]
    assert counts == [("3", 2), ("1", 1), (OTHER, 1)]
    colors = _class_colors(response)
    assert drawn == {"3": colors["3"], "1": colors["1"], "2": colors[OTHER]}


def test_categorized_subset_cannot_name_null(headless, tmp_path):
    path = _points_geojson(tmp_path / "nulls.geojson", "mode", ["walk", None])
    response, drawn = _style_subset(headless, path, "mode", ["NULL", "walk"])

    counts = [(c["value"], c["n_features"]) for c in response["classes"]]
    assert counts == [("NULL", 0), ("walk", 1), (OTHER, 1)]
    assert drawn["NULL"] == _class_colors(response)[OTHER]


def _ward_squares(path, names=("chiyoda", "chuo", "minato")):
    features = [
        {
            "type": "Feature",
            "properties": {"name": name},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[x, 35.6], [x + 0.05, 35.6], [x + 0.05, 35.65], [x, 35.65], [x, 35.6]]],
            },
        }
        for name, x in zip(names, (139.70, 139.76, 139.82), strict=True)
    ]
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return path.as_posix()


ATLAS_PROJECT = r'''
from qgis.core import QgsProject, QgsVectorLayer, QgsPrintLayout, QgsLayoutItemMap, QgsLayoutPoint, QgsLayoutSize
project = QgsProject.instance()
project.clear()
wards = QgsVectorLayer(GEOJSON, "wards", "ogr")
project.addMapLayer(wards)
layout = QgsPrintLayout(project)
layout.initializeDefaults()
layout.setName("ward_atlas")
panel = QgsLayoutItemMap(layout)
panel.attemptMove(QgsLayoutPoint(10, 10))
panel.attemptResize(QgsLayoutSize(100, 100))
panel.setLayers([wards])
panel.setExtent(wards.extent())
panel.setAtlasDriven(True)
layout.addLayoutItem(panel)
atlas = layout.atlas()
atlas.setCoverageLayer(wards)
atlas.setFilenameExpression("'ward_' || \"name\"")
atlas.setEnabled(True)
project.layoutManager().addLayout(layout)
result = project.write(QGZ)
project.clear()
'''


def _atlas_qgz(headless, tmp_path, names=("chiyoda", "chuo", "minato")):
    geojson = _ward_squares(tmp_path / "wards.geojson", names)
    qgz = (tmp_path / "atlas.qgz").as_posix()
    assert _run(headless, ATLAS_PROJECT, GEOJSON=geojson, QGZ=qgz) is True
    return qgz


def _export(headless, qgz, out_dir, fmt):
    return headless.dispatch(
        "export_atlas",
        {"layout_name": "ward_atlas", "output_dir": str(out_dir), "format": fmt,
         "dpi": 30, "qgz_path": qgz},
    )


def test_atlas_pdf_export_writes_every_page_into_one_file(headless, tmp_path):
    out_dir = tmp_path / "pdf"
    result = _export(headless, _atlas_qgz(headless, tmp_path), out_dir, "pdf")

    assert sorted(p.name for p in out_dir.iterdir()) == ["atlas.pdf"]
    assert result["n_pages"] == 3
    # One /Type /Page object per ward, so a single-page fallback cannot pass.
    pages = re.findall(rb"/Type\s*/Page(?!s)", (out_dir / "atlas.pdf").read_bytes())
    assert len(pages) == 3


def test_atlas_png_export_writes_each_page_once(headless, tmp_path):
    out_dir = tmp_path / "png"
    result = _export(headless, _atlas_qgz(headless, tmp_path), out_dir, "png")

    expected = ["ward_chiyoda.png", "ward_chuo.png", "ward_minato.png"]
    assert sorted(p.name for p in out_dir.iterdir()) == expected  # no second fallback set
    assert sorted(os.path.basename(f) for f in result["files"]) == expected
    assert result["n_pages"] == 3


def test_atlas_png_names_that_differ_only_in_case_do_not_overwrite(headless, tmp_path):
    out_dir = tmp_path / "png"
    qgz = _atlas_qgz(headless, tmp_path, names=("Chiyoda", "chiyoda", "minato"))
    result = _export(headless, qgz, out_dir, "png")

    assert len(set(result["files"])) == 3
    assert len(list(out_dir.iterdir())) == 3  # same file on NTFS / default APFS otherwise


def test_atlas_pdf_failure_raises_with_the_export_code(headless, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    out_dir = tmp_path / "blocked"
    (out_dir / "atlas.pdf").mkdir(parents=True)  # a directory where the PDF must go
    with pytest.raises(ExecutorError, match="Atlas PDF export failed with code"):
        _export(headless, _atlas_qgz(headless, tmp_path), out_dir, "pdf")

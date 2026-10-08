"""Live regression tests for batch_render and project reads (TASK-14).

The bugs live in PyQGIS calls in ``qgis_mcp_workflows_plugin/plugin.py``, which the
FakeExecutor suite cannot reach. Skipped unless a QGIS Python launcher is reachable.
"""

from __future__ import annotations

import json

import pytest

from qgis_mcp_workflows.errors import ExecutorError
from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless

pytestmark = requires_headless

BLUE, GREEN = "#0000ff", "#00ff00"


@pytest.fixture(scope="module")
def headless():
    ex = HeadlessExecutor()
    try:
        yield ex
    finally:
        ex.shutdown()


def _run(headless, code, **values):
    prelude = "".join(f"{name} = {value!r}\n" for name, value in values.items())
    out = headless.dispatch("execute_code", {"code": prelude + code, "return_vars": ["result"]})
    assert out.get("executed"), out
    return out["return_values"]["result"]


def _square(x, y, size):
    return [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]


def _geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": ring}}
        for props, ring in features
    ]}), encoding="utf-8")
    return path.as_posix()


# wards (blue, visible), an optional small green "marker" (visible, above wards)
# at chiyoda's centre, and an opaque red "cover" on top of the layer tree but
# hidden. Registry order puts cover first (layer ids sort by name).
TEMPLATE = r'''
from qgis.core import QgsProject, QgsVectorLayer, QgsFillSymbol, QgsSingleSymbolRenderer
project = QgsProject.instance()
project.clear()
def styled(path, name, rgb):
    layer = QgsVectorLayer(path, name, "ogr")
    symbol = QgsFillSymbol.createSimple({"color": rgb + ",255", "outline_style": "no"})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer
wards = styled(WARDS, "wards", "0,0,255")
if WARDS_SUBSET:
    wards.setSubsetString(WARDS_SUBSET)
project.addMapLayer(wards)
if MARKER:
    # "zz_": its layer id sorts after wards, so registry order would draw it
    # *under* wards; only layer-tree order puts it on top.
    project.addMapLayer(styled(MARKER, "zz_marker", "0,255,0"))
for extra_path, extra_name in EXTRA:
    project.addMapLayer(QgsVectorLayer(extra_path, extra_name, "ogr"))
if COVER:
    cover = styled(COVER, "cover", "255,0,0")
    project.addMapLayer(cover)
    root = project.layerTreeRoot()
    node = root.findLayer(cover.id())
    top = node.clone()
    root.insertChildNode(0, top)
    root.removeChildNode(node)
    top.setItemVisibilityChecked(False)
result = project.write(QGZ)
project.clear()
'''


def _template(headless, tmp_path, wards_subset="", extra=(), with_cover=True, with_marker=False):
    wards = _geojson(tmp_path / "wards.geojson", [
        ({"name": "chiyoda", 'ward"id': "c1"}, _square(139.70, 35.60, 0.05)),
        ({"name": "chuo", 'ward"id': "c2"}, _square(139.76, 35.60, 0.05)),
        ({"name": "minato", 'ward"id': "c3"}, _square(139.82, 35.60, 0.05)),
        ({"name": "ko\\to", 'ward"id': "c4"}, _square(139.88, 35.60, 0.05)),
    ])
    cover = None
    if with_cover:
        cover = _geojson(tmp_path / "cover.geojson", [({"name": "cover"}, _square(139.0, 35.0, 2.0))])
    marker = None
    if with_marker:
        marker = _geojson(tmp_path / "marker.geojson", [({"name": "m"}, _square(139.72, 35.62, 0.01))])
    qgz = (tmp_path / "template.qgz").as_posix()
    assert _run(headless, TEMPLATE, WARDS=wards, COVER=cover, MARKER=marker, QGZ=qgz,
                WARDS_SUBSET=wards_subset, EXTRA=list(extra)) is True
    return qgz


def _batch(headless, qgz, out_dir, values, attribute="name", **extra):
    return headless.dispatch("batch_render", {
        "template_qgz": qgz, "attribute": attribute, "values": values,
        "output_dir": str(out_dir), "width": 200, "height": 200, "dpi": 30, **extra,
    })


PIXELS = r'''
from qgis.PyQt.QtGui import QImage
image = QImage(PNG)
result = [image.pixelColor(x, y).name() for x, y in POINTS]
'''


def test_default_target_is_the_top_visible_vector_layer(headless, tmp_path):
    # cover is above wards in the tree but hidden, so wards is the default.
    result = _batch(headless, _template(headless, tmp_path), tmp_path / "out", ["chiyoda"])
    assert result["target_layer"] == "wards"
    assert result["errors"] == []

    # A visible layer above wards is the top-most one (its id sorts last, so
    # registry order would not pick it).
    (tmp_path / "marked").mkdir()
    marked = _template(headless, tmp_path / "marked", with_marker=True)
    assert _batch(headless, marked, tmp_path / "out2", ["m"])["target_layer"] == "zz_marker"


def test_renders_the_visible_layers_and_never_the_hidden_ones(headless, tmp_path):
    qgz = _template(headless, tmp_path, with_marker=True)
    result = _batch(headless, qgz, tmp_path / "out", ["chiyoda"], layer="wards")

    assert result["target_layer"] == "wards"  # layer= overrides the top-most zz_marker
    assert result["errors"] == []
    entry = result["manifest"][0]
    # Framed on chiyoda alone (0.05° square + 5% margin each side).
    assert entry["extent"] == pytest.approx([139.6975, 35.5975, 139.7525, 35.6525], abs=1e-6)
    # Centre: the visible green marker over wards. Left of it: wards. The hidden
    # red cover on top of the tree is drawn nowhere.
    centre, left = _run(headless, PIXELS, PNG=entry["output_path"], POINTS=[(100, 100), (30, 100)])
    assert (centre, left) == (GREEN, BLUE)


def test_layer_argument_names_the_target(headless, tmp_path):
    qgz = _template(headless, tmp_path)
    assert _batch(headless, qgz, tmp_path / "a", ["chiyoda"], layer="wards")["target_layer"] == "wards"
    with pytest.raises(ExecutorError, match=r"LAYER_NOT_FOUND.*'nope'.*wards"):
        _batch(headless, qgz, tmp_path / "b", ["chiyoda"], layer="nope")


def test_a_filter_is_never_silently_dropped(headless, tmp_path):
    # GDAL 3.x's OGR SQL rejects a quote inside an identifier even when doubled,
    # so setSubsetString returns False. That False used to be ignored and the
    # unfiltered layer was saved as c1.png. Whichever way a future GDAL goes, the
    # invariant holds: refused -> an error and no file; accepted -> one ward only.
    # (wards alone, so the old fallback targets it too.)
    out_dir = tmp_path / "out"
    result = _batch(headless, _template(headless, tmp_path, with_cover=False), out_dir, ["c1"],
                    attribute='ward"id')

    if result["n_rendered"] == 0:
        assert "QGIS refused the filter" in result["errors"][0]["error"]
        assert not (out_dir / "c1.png").exists()
    else:
        xmin, _, xmax, _ = result["manifest"][0]["extent"]
        assert xmax - xmin < 0.1  # one 0.05° ward, not all three


def test_the_template_filter_survives_the_batch(headless, tmp_path):
    subset = "\"name\" <> 'minato'"
    # wards alone, so even the old fallback targets it
    qgz = _template(headless, tmp_path, wards_subset=subset, with_cover=False)
    _batch(headless, qgz, tmp_path / "out", ["chiyoda"])

    after = _run(headless, "from qgis.core import QgsProject\n"
                 "result = QgsProject.instance().mapLayersByName('wards')[0].subsetString()")
    assert after == subset


def test_unavailable_layers_are_reported(headless, tmp_path):
    gone = _geojson(tmp_path / "gone.geojson", [({"name": "x"}, _square(139.0, 35.0, 0.1))])
    qgz = _template(headless, tmp_path, extra=[(gone, "gone")])
    (tmp_path / "gone.geojson").unlink()

    loaded = headless.dispatch("project_load", {"qgz_path": qgz})
    assert loaded["unavailable_layers"] == ["gone"]
    _run(headless, "from qgis.core import QgsProject\nQgsProject.instance().clear()\nresult = True")
    batch = _batch(headless, qgz, tmp_path / "out", ["chiyoda"], layer="wards")
    assert batch["unavailable_layers"] == ["gone"]


def test_an_unavailable_top_layer_is_not_skipped_silently(headless, tmp_path):
    # "gone" is the top-most visible vector layer; filtering wards instead would
    # be a silent wrong pick (the missing-Dropbox-root case).
    gone = _geojson(tmp_path / "gone.geojson", [({"name": "x"}, _square(139.0, 35.0, 0.1))])
    qgz = _template(headless, tmp_path, extra=[(gone, "gone")])
    (tmp_path / "gone.geojson").unlink()

    with pytest.raises(ExecutorError, match=r"LAYER_UNAVAILABLE.*'gone'.*layer="):
        _batch(headless, qgz, tmp_path / "out", ["chiyoda"])


def test_values_with_a_backslash_match_and_stay_inside_output_dir(headless, tmp_path):
    # QgsExpression.quotedValue would double the backslash, which OGR's SQL
    # then compares literally and matches nothing. The value also used to be
    # formatted into the file name raw: "ko\to.png" is a path on Windows.
    out_dir = tmp_path / "out"
    result = _batch(headless, _template(headless, tmp_path), out_dir, ["ko\\to"])

    assert result["errors"] == []
    assert result["n_rendered"] == 1
    assert sorted(p.name for p in out_dir.iterdir()) == ["ko_to.png"]


DISMISS = r'''
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QDialog
from qgis_mcp_workflows_plugin.plugin import QgisMCPServer

class QgsHandleBadLayers(QDialog):  # same Qt class name as QGIS's dialog
    pass

dialog = QgsHandleBadLayers()
timer = QTimer()
timer.timeout.connect(QgisMCPServer._dismiss_unavailable_layers_dialog)
timer.start(50)
QTimer.singleShot(3000, dialog.accept)  # safety net, so a regression fails instead of hanging
result = dialog.exec()
timer.stop()
'''


def test_the_unavailable_layers_dialog_is_dismissed(headless):
    # QDialog.Rejected == 0: our timer callback closed it; Accepted (1) would
    # mean only the 3 s safety net did.
    assert _run(headless, DISMISS) == 0

"""Live tests: an unstyled layer draws in a colour fixed by its file name.

Reproduced 2026-10-20 on 1.15.2: one GeoPackage loaded twice with qgis_load_layer
and rendered twice came out green, then purple. add_vector_layer kept QGIS's
default renderer, whose colour is random, so no replay of such a figure matched.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless
from tests.test_palettes_live import SET2
from tests.test_plugin_live_fixes import DRAWN_COLORS, _points_geojson, _run

pytestmark = requires_headless
ZONES = Path(__file__).resolve().parent / "fixtures" / "tiny_zones.geojson"  # polygons


@pytest.fixture(scope="module")
def headless():
    ex = HeadlessExecutor()
    try:
        yield ex
    finally:
        ex.shutdown()


def _drawn(executor, path) -> set[str]:
    """Colours the features of a freshly loaded, unstyled layer draw with."""
    layer_id = executor.dispatch("add_vector_layer", {"path": path})["id"]
    try:
        return set(_run(executor, DRAWN_COLORS, LAYER_ID=layer_id, FIELD="k").values())
    finally:
        executor.dispatch("remove_layer", {"layer_id": layer_id})


def _expected(name: str) -> str:
    return SET2[zlib.crc32(name.encode("utf-8")) % len(SET2)]


def test_the_same_file_loaded_twice_draws_the_same_set2_colour(headless, tmp_path):
    path = _points_geojson(tmp_path / "wards.geojson", "k", ["a", "b", "c"])
    first, second = _drawn(headless, path), _drawn(headless, path)
    assert first == second == {_expected("wards.geojson")}


def test_the_colour_follows_the_file_name(headless, tmp_path):
    for name in ("stations.geojson", "rail.geojson", "zones.geojson"):
        path = _points_geojson(tmp_path / name, "k", ["a"])
        assert _drawn(headless, path) == {_expected(name)}, name


def test_a_second_qgis_process_draws_the_same_colour(headless, tmp_path):
    # The replay runs in a fresh QGIS: per-process randomness must not reach the colour.
    path = _points_geojson(tmp_path / "trips.geojson", "k", ["a", "b"])
    other = HeadlessExecutor()
    try:
        assert _drawn(other, path) == _drawn(headless, path) == {_expected("trips.geojson")}
    finally:
        other.shutdown()


SAVE_STYLE = r'''
from qgis.core import QgsVectorLayer, QgsMarkerSymbol, QgsSingleSymbolRenderer
layer = QgsVectorLayer(PATH, "styled", "ogr")
layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({"color": "#123456"})))
message, result = layer.saveDefaultStyle()
'''


def test_a_layer_with_its_own_default_style_keeps_it(headless, tmp_path):
    path = _points_geojson(tmp_path / "styled.geojson", "k", ["a", "b"])
    _run(headless, SAVE_STYLE, PATH=path)
    assert (tmp_path / "styled.qml").exists()
    assert _drawn(headless, path) == {"#123456"}


SAVE_GPKG_STYLE = r'''
from qgis.core import (QgsCoordinateTransformContext, QgsMarkerSymbol, QgsSingleSymbolRenderer,
                       QgsVectorFileWriter, QgsVectorLayer)
source = QgsVectorLayer(SOURCE, "src", "ogr")
options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
QgsVectorFileWriter.writeAsVectorFormatV3(source, GPKG, QgsCoordinateTransformContext(), options)
layer = QgsVectorLayer(GPKG, "styled", "ogr")
layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({"color": "#654321"})))
layer.saveStyleToDatabase("default", "", True, "")
result = True
'''


def test_a_style_stored_in_the_geopackage_is_kept(headless, tmp_path):
    source = _points_geojson(tmp_path / "src.geojson", "k", ["a", "b"])
    gpkg = str(tmp_path / "stored.gpkg")
    _run(headless, SAVE_GPKG_STYLE, SOURCE=source, GPKG=gpkg)
    assert _drawn(headless, gpkg) == {"#654321"}


RANDOMIZE = r'''
from qgis.core import QgsProject
QgsProject.instance().styleSettings().setRandomizeDefaultSymbolColor(VALUE)
result = QgsProject.instance().styleSettings().randomizeDefaultSymbolColor()
'''

QGIS_DEFAULT_POINT = r'''
from qgis.core import Qgis, QgsSymbol
result = QgsSymbol.defaultSymbol(Qgis.GeometryType.Point).color().name()
'''


def test_a_project_without_random_colours_keeps_its_own_default(headless, tmp_path):
    # A Desktop user who turned random colours off chose a default colour: keep it.
    path = _points_geojson(tmp_path / "fixed.geojson", "k", ["a"])
    assert _run(headless, RANDOMIZE, VALUE=False) is False
    try:
        assert _drawn(headless, path) == {_run(headless, QGIS_DEFAULT_POINT)}
    finally:
        _run(headless, RANDOMIZE, VALUE=True)


DOMINANT_COLOURS = r'''
from qgis.PyQt.QtGui import QImage
img = QImage(IMG)
seen = {}
for y in range(0, img.height(), 3):
    for x in range(0, img.width(), 3):
        name = img.pixelColor(x, y).name()
        seen[name] = seen.get(name, 0) + 1
result = [name for name, _ in sorted(seen.items(), key=lambda kv: -kv[1])[:2]]
'''


def test_compose_layout_fills_an_unstyled_layer_with_its_fixed_colour(headless, tmp_path):
    # Probe on 1.15.2: the same polygon layer composed twice filled pink, then red.
    zones = tmp_path / "wards.geojson"
    zones.write_bytes(ZONES.read_bytes())
    for i in range(2):
        out = str(tmp_path / f"layout{i}.png")
        headless.dispatch("compose_layout", {"layer_paths": [str(zones)], "output_path": out, "dpi": 72,
                                             "legend": False, "scale_bar": False, "north_arrow": False})
        dominant = _run(headless, DOMINANT_COLOURS, IMG=out)  # the fill and the white page
        assert _expected("wards.geojson") in dominant, (i, dominant)

"""Live tests: an unstyled layer draws in a colour fixed by its file name.

Reproduced 2026-10-20 on 1.15.2: one GeoPackage loaded twice with qgis_load_layer
and rendered twice came out green, then purple. add_vector_layer kept QGIS's
default renderer, whose colour is random, so no replay of such a figure matched.
"""

from __future__ import annotations

import zlib

import pytest

from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless
from tests.test_palettes_live import SET2
from tests.test_plugin_live_fixes import DRAWN_COLORS, _points_geojson, _run

pytestmark = requires_headless


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

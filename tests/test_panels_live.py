"""Live tests: compose_panels draws a grid of map panels on linked colour scales (TASK-22).

Two square zones, A and B; every render frames A alone (``extent``), so each
panel's centre pixel is A's fill colour and can be compared with the ramp.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qgis_mcp_workflows import executors
from qgis_mcp_workflows.errors import ExecutorError
from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless
from tests.test_plugin_live_fixes import _run

pytestmark = requires_headless

A_BBOX = [139.0, 35.0, 139.1, 35.1]
DPI = 72


@pytest.fixture(scope="module")
def headless():
    ex = HeadlessExecutor()
    try:
        yield ex
    finally:
        ex.shutdown()


@pytest.fixture
def zones(tmp_path):
    def square(key, x0):
        ring = [[x0, 35.0], [x0 + 0.1, 35.0], [x0 + 0.1, 35.1], [x0, 35.1], [x0, 35.0]]
        return {"type": "Feature", "properties": {"zone_id": key},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}

    path = tmp_path / "zones.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection",
                                "features": [square("A", 139.0), square("B", 139.5)]}), encoding="utf-8")
    return str(path)


def _group(palette="Reds", vmin=0.0, vmax=1.0, ticks=((0.0, "0"), (1.0, "1")), label=None):
    return {"palette": palette, "vmin": vmin, "vmax": vmax, "ticks": [list(t) for t in ticks], "label": label}


def _compose(headless, zones, out, panels, groups, rows=1, cols=None, **extra):
    params = {"zones_path": zones, "join_field": "zone_id", "rows": rows, "cols": cols or len(panels),
              "panels": panels, "groups": groups, "output_path": str(out), "extent": A_BBOX, "dpi": DPI,
              "width_mm": 120.0, "height_mm": 70.0}
    params.update(extra)
    return headless.dispatch("compose_panels", params)


CENTRE = r'''
from qgis.PyQt.QtGui import QImage
img = QImage(PNG)
x = (BOX[0] + BOX[2] / 2.0) / 25.4 * DPI
y = (BOX[1] + BOX[3] / 2.0) / 25.4 * DPI
result = img.pixelColor(int(x), int(y)).name()
'''

RAMP = r'''
from qgis.core import QgsStyle
result = QgsStyle.defaultStyle().colorRamp(PALETTE).color(T).name()
'''


def _centre(headless, png, box):
    return _run(headless, CENTRE, PNG=str(png), BOX=box, DPI=DPI)


def _expected(headless, palette, t):
    return _run(headless, RAMP, PALETTE=palette, T=t)


def test_each_panel_is_filled_at_its_scale_position(headless, zones, tmp_path):
    out = tmp_path / "two.png"
    res = _compose(headless, zones, out, [{"values": {"A": 0.0, "B": 0.5}, "group": 0},
                                          {"values": {"A": 1.0, "B": 0.5}, "group": 0}], [_group()])
    left, right = res["panel_boxes_mm"]
    assert _centre(headless, out, left) == _expected(headless, "Reds", 0.0)
    assert _centre(headless, out, right) == _expected(headless, "Reds", 1.0)


def test_values_above_the_cap_take_the_top_colour(headless, zones, tmp_path):
    out = tmp_path / "cap.png"
    res = _compose(headless, zones, out, [{"values": {"A": 5.0}, "group": 0}], [_group("Blues")])
    assert _centre(headless, out, res["panel_boxes_mm"][0]) == _expected(headless, "Blues", 1.0)


@pytest.mark.parametrize(("unmatched", "colour"), [("hide", "#ffffff"), ("nodata", "#e0e0e0")])
def test_unmatched_zones_hide_or_show_as_nodata(headless, zones, tmp_path, unmatched, colour):
    out = tmp_path / f"{unmatched}.png"
    res = _compose(headless, zones, out, [{"values": {"B": 0.3}, "group": 0}], [_group()], unmatched=unmatched)
    assert _centre(headless, out, res["panel_boxes_mm"][0]) == colour
    assert (res["n_matched"], res["n_unmatched"]) == (1, 1)


def test_a_flat_group_renders(headless, zones, tmp_path):
    out = tmp_path / "flat.png"
    res = _compose(headless, zones, out, [{"values": {"A": 0.0}, "group": 0}],
                   [_group(vmin=0.0, vmax=0.0, ticks=((0.0, "0.0"),))])
    assert _centre(headless, out, res["panel_boxes_mm"][0]) == _expected(headless, "Reds", 0.0)
    assert res["items"].count("tick:0") == 1


def test_colour_bars_titles_and_labels_are_placed(headless, zones, tmp_path):
    out = tmp_path / "grid.png"
    panels = [{"values": {"A": v}, "group": g} for v, g in ((0.1, 0), (0.2, 0), (0.3, 1), (0.4, 1))]
    res = _compose(headless, zones, out, panels, [_group("Reds"), _group("Blues")], rows=2, cols=2,
                   column_titles=["Simulated", "Observed (PT)"], row_labels=["Origins", "Destinations"],
                   width_mm=120.0, height_mm=110.0)
    for item in ("colorbar:0", "colorbar:1", "title:0", "title:1", "row_label:0", "row_label:1"):
        assert item in res["items"], item
    assert len(res["colorbar_boxes_mm"]) == 2 and len(res["panel_boxes_mm"]) == 4
    assert res["n_panels"] == 4 and res["n_layers"] == 4


def test_missing_fonts_are_reported(headless, zones, tmp_path):
    res = _compose(headless, zones, tmp_path / "font.png", [{"values": {"A": 0.5}, "group": 0}], [_group()],
                   font_family="NoSuchFont123")
    assert res["fonts_missing"] == ["NoSuchFont123"]


def _installed_font():
    """A font family this OS ships, or None (the test is then skipped)."""
    import os
    import sys

    if sys.platform == "win32":
        fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        return "Arial" if os.path.exists(os.path.join(fonts, "arial.ttf")) else None
    if sys.platform == "darwin":
        return "Helvetica"
    return None


def test_an_installed_font_is_found_headless(headless, zones, tmp_path):
    # Probe 2026-11-10: offscreen Qt sees only its bundled Open Sans until the
    # OS font folders are registered (724 files, 0.13 s on win).
    family = _installed_font()
    if family is None:
        pytest.skip("no known system font on this OS")
    res = _compose(headless, zones, tmp_path / "arial.png", [{"values": {"A": 0.5}, "group": 0}], [_group()],
                   font_family=family)
    assert res["fonts_missing"] == []


def test_stacked_colour_bars_leave_room_for_their_tick_labels(headless, zones, tmp_path):
    # Preview 2026-11-10: row 1's bottom tick collided with row 2's top tick and label.
    panels = [{"values": {"A": 0.5}, "group": g} for g in (0, 0, 1, 1)]
    res = _compose(headless, zones, tmp_path / "bars.png", panels,
                   [_group("Reds", label="Share"), _group("Blues", label="Share")], rows=2, cols=2,
                   width_mm=120.0, height_mm=110.0, tick_size_pt=7.0)
    upper, lower = res["colorbar_boxes_mm"]
    tick_h = 7.0 * 0.3528 * 1.6
    # half a tick label below the upper bar, half a tick plus the label above the lower one
    assert lower[1] - (upper[1] + upper[3]) >= 2 * tick_h


def test_unicode_labels_render(headless, zones, tmp_path):
    out = tmp_path / "unicode.png"
    _compose(headless, zones, out, [{"values": {"A": 0.5}, "group": 0}], [_group()],
             row_labels=["Origins\nR² = 0.79"], column_titles=["港区"])
    assert out.stat().st_size > 0


@pytest.mark.parametrize(("suffix", "magic"), [(".pdf", b"%PDF"), (".svg", b"<svg")])
def test_pdf_and_svg_are_vector(headless, zones, tmp_path, suffix, magic):
    out = tmp_path / f"vector{suffix}"
    res = _compose(headless, zones, out, [{"values": {"A": 0.5}, "group": 0}], [_group()])
    assert res["format"] == suffix[1:] and magic in out.read_bytes()[:4096]


def test_no_matching_keys_is_an_error(headless, zones, tmp_path):
    with pytest.raises(ExecutorError, match=r"JOIN_NO_MATCH on zone_id.*13101\.0.*Next:"):
        _compose(headless, zones, tmp_path / "none.png", [{"values": {"13101.0": 1.0}, "group": 0}], [_group()])


def test_get_field_values_returns_each_key_once_as_text(headless, tmp_path):
    # TASK-24: the server drops CSV keys outside this set before scaling. A NULL key
    # reads as "" on QGIS 3 (a NULL QVariant) as on QGIS 4 (None), as compose_panels joins it.
    def square(key, x0):
        ring = [[x0, 35.0], [x0 + 0.1, 35.0], [x0 + 0.1, 35.1], [x0, 35.1], [x0, 35.0]]
        return {"type": "Feature", "properties": {"zone_id": key},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}

    path = tmp_path / "keys.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        square("B", 139.0), square(None, 139.2), square("A", 139.4), square("A", 139.6)]}), encoding="utf-8")
    res = headless.dispatch("get_field_values", {"path": str(path), "field": "zone_id"})
    assert res == {"values": ["", "A", "B"]}


def test_get_field_values_names_a_missing_field(headless, zones):
    with pytest.raises(ExecutorError, match=r"'ward' not found.*zone_id.*Next:"):
        headless.dispatch("get_field_values", {"path": zones, "field": "ward"})


def test_the_default_extent_frames_only_the_drawn_zones(headless, zones, tmp_path):
    # fig_spatial draws 23 wards out of a larger szone layer: hidden zones must not set the frame.
    out = tmp_path / "frame.png"
    res = _compose(headless, zones, out, [{"values": {"A": 1.0}, "group": 0}], [_group()], extent=None)
    assert _centre(headless, out, res["panel_boxes_mm"][0]) == _expected(headless, "Reds", 1.0)


CUSTOM_CRS_ZONES = r'''
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsFeature, QgsGeometry,
                       QgsVectorFileWriter, QgsVectorLayer)
crs = QgsCoordinateReferenceSystem()
crs.createFromProj("+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=12345 +y_0=0 +ellps=GRS80 +units=m +no_defs")
mem = QgsVectorLayer("Polygon?field=zone_id:string", "z", "memory")
mem.setCrs(crs)
f = QgsFeature(mem.fields())
f.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0, 1000 0, 1000 1000, 0 1000, 0 0))"))
f.setAttributes(["A"])
mem.dataProvider().addFeatures([f])
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "ESRI Shapefile"
QgsVectorFileWriter.writeAsVectorFormatV3(mem, PATH, QgsCoordinateTransformContext(), opts)
back = QgsVectorLayer(PATH, "back", "ogr")
result = {"valid": back.crs().isValid(), "authid": back.crs().authid()}
'''


def test_zones_without_an_authority_crs_stay_in_place(headless, tmp_path):
    shp = str(tmp_path / "custom.shp")
    crs = _run(headless, CUSTOM_CRS_ZONES, PATH=shp)
    assert crs == {"valid": True, "authid": ""}  # the case under test: a CRS QGIS cannot name
    out = tmp_path / "custom.png"
    res = _compose(headless, shp, out, [{"values": {"A": 1.0}, "group": 0}], [_group()],
                   extent=[0.0, 0.0, 1000.0, 1000.0])
    assert _centre(headless, out, res["panel_boxes_mm"][0]) == _expected(headless, "Reds", 1.0)


JAPANESE_FAMILIES = r'''
from qgis.PyQt.QtGui import QFontDatabase
try:
    JP = QFontDatabase.WritingSystem.Japanese
except AttributeError:
    JP = QFontDatabase.Japanese
try:
    result = len(QFontDatabase.families(JP))
except TypeError:
    result = len(QFontDatabase().families(JP))
'''


def test_japanese_titles_get_a_font_headless(zones, tmp_path):
    # Probe 2026-11-10: a fresh headless QGIS sees one family (Open Sans) and no Japanese one,
    # so "港区" in the default font drew as empty boxes.
    if sys.platform not in ("win32", "darwin"):
        pytest.skip("no known Japanese system font on this OS")
    fresh = HeadlessExecutor()
    try:
        _compose(fresh, zones, tmp_path / "jp.png", [{"values": {"A": 0.5}, "group": 0}], [_group()],
                 column_titles=["港区"])
        assert _run(fresh, JAPANESE_FAMILIES) > 0
    finally:
        fresh.shutdown()


def test_a_grid_too_big_for_the_page_is_refused(headless, zones, tmp_path):
    panels = [{"values": {"A": 0.5}, "group": 0}] * 4
    with pytest.raises(ExecutorError, match=r"PAGE_TOO_SMALL.*Next:"):
        _compose(headless, zones, tmp_path / "tiny.png", panels, [_group()], width_mm=30.0)


def _overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize(("position", "colorbar", "groups_of"),
                         [("right", True, (0, 0, 1, 1)), ("bottom", True, (0, 1, 0, 1)),
                          ("right", False, (0, 0, 1, 1))])
def test_panels_and_colour_bars_stay_on_the_page_without_overlap(headless, zones, tmp_path, position,
                                                                 colorbar, groups_of):
    width, height = 120.0, 110.0
    panels = [{"values": {"A": 0.5}, "group": g} for g in groups_of]
    res = _compose(headless, zones, tmp_path / f"{position}{colorbar}.png", panels,
                   [_group("Reds", label="Share"), _group("Blues", label="Share")], rows=2, cols=2,
                   colorbar=colorbar, colorbar_position=position, width_mm=width, height_mm=height,
                   column_titles=["a", "b"], row_labels=["c", "d"])
    boxes, bars = res["panel_boxes_mm"], res["colorbar_boxes_mm"]
    assert len(bars) == (2 if colorbar else 0)
    for box in boxes + bars:
        x, y, w, h = box
        assert w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width and y + h <= height, box
    pairs = [(a, b) for i, a in enumerate(boxes + bars) for b in (boxes + bars)[i + 1:]]
    assert not any(_overlaps(a, b) for a, b in pairs)
    for g, bar in enumerate(bars):
        members = [boxes[i] for i, grp in enumerate(groups_of) if grp == g]
        if position == "right":
            assert bar[0] >= max(b[0] + b[2] for b in members)
        else:
            assert bar[1] >= max(b[1] + b[3] for b in members)


TINY_ZONES = str(Path(__file__).parent / "fixtures" / "tiny_zones.geojson")


@pytest.fixture
def server_on(headless, tmp_path, monkeypatch):
    """The MCP server module, dispatching to the module's one headless QGIS."""
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    executors.set_executor(headless)
    try:
        yield importlib.import_module("qgis_mcp_workflows.server")
    finally:
        executors.set_executor(None)


async def test_fig_spatial_shape_through_the_mcp_tool(server_on, tmp_path):
    shares = tmp_path / "shares.csv"
    shares.write_text("zone_id,origin_sim_s,origin_obs_s,dest_sim_s,dest_obs_s\n"
                      "Z01,0.10,0.12,0.30,0.25\nZ02,0.20,0.18,0.10,0.15\n"
                      "Z03,0.30,0.35,0.40,0.45\nZ04,0.40,0.35,0.20,0.15\n", encoding="utf-8")
    out = tmp_path / "fig_spatial.pdf"
    await server_on.mcp.call_tool("qgis_compose_panels", {
        "zones_path": TINY_ZONES, "value_csv": str(shares),
        "value_fields": ["origin_sim_s", "origin_obs_s", "dest_sim_s", "dest_obs_s"],
        "rows": 2, "cols": 2, "output_path": str(out), "share_scale": "row", "palettes": ["Reds", "Blues"],
        "cap_percentile": 98, "row_labels": ["Origins\nR² = 0.79", "Destinations\nR² = 0.69"],
        "column_titles": ["Simulated", "Observed (PT)"], "tick_format": ".1%",
        "width_mm": 152.4, "height_mm": 137.2})
    assert out.read_bytes()[:4] == b"%PDF"
    sidecar = json.loads(Path(str(out) + ".provenance.json").read_text(encoding="utf-8"))
    assert {e["argument"] for e in sidecar["inputs"]} == {"zones_path", "value_csv"}


async def test_a_panel_figure_replays_from_its_sidecar(server_on, tmp_path):
    zones = tmp_path / "zones.geojson"
    zones.write_bytes(Path(TINY_ZONES).read_bytes())
    shares = tmp_path / "shares.csv"
    shares.write_text("zone_id,a,b\nZ01,0.1,0.2\nZ02,0.3,0.4\nZ03,0.5,0.6\nZ04,0.7,0.8\n", encoding="utf-8")
    out = tmp_path / "panels.png"
    await server_on.mcp.call_tool("qgis_compose_panels", {
        "zones_path": str(zones), "value_csv": str(shares), "value_fields": ["a", "b"], "rows": 1, "cols": 2,
        "output_path": str(out), "dpi": DPI, "width_mm": 120.0, "height_mm": 70.0})
    script = tmp_path / "replay.py"
    server_on.qgis_export_session(output_py=str(script), figures=[str(out)])
    done = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=300, env={**os.environ},
                          stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stdout + done.stderr
    replayed = tmp_path / "out" / "DROPBOX_ROOT" / "panels.png"
    assert replayed.stat().st_size == out.stat().st_size


def test_a_total_row_does_not_stretch_the_scale(server_on, tmp_path):
    # TASK-24: TOTAL is on no zone of the layer, so it must neither be drawn nor scale the colours.
    shares = tmp_path / "shares.csv"
    shares.write_text("zone_id,v\nZ01,0.1\nZ02,0.2\nZ03,0.3\nZ04,0.4\nTOTAL,1.0\n", encoding="utf-8")
    res = server_on.qgis_compose_panels(
        zones_path=TINY_ZONES, value_csv=str(shares), value_fields=["v"], rows=1, cols=1,
        output_path=str(tmp_path / "total.png"), dpi=DPI, width_mm=120.0, height_mm=70.0)
    assert (res.groups[0].vmax, res.groups[0].data_max) == (0.4, 0.4)
    assert (res.n_csv_keys_unmatched, res.csv_keys_unmatched_sample) == (1, ["TOTAL"])
    assert (res.n_matched, res.n_unmatched) == (4, 0)


def test_a_group_left_without_values_still_renders(server_on, tmp_path):
    # North 2026-11-10: render and flag. Column a's only number is on TOTAL, so its
    # panel draws no zone and its scale is flat; column b draws normally.
    shares = tmp_path / "shares.csv"
    shares.write_text("zone_id,a,b\nZ01,,0.1\nZ02,,0.2\nZ03,,0.3\nZ04,,0.4\nTOTAL,1.0,1.0\n", encoding="utf-8")
    out = tmp_path / "empty_group.png"
    res = server_on.qgis_compose_panels(
        zones_path=TINY_ZONES, value_csv=str(shares), value_fields=["a", "b"], rows=1, cols=2,
        share_scale="column", output_path=str(out), dpi=DPI, width_mm=120.0, height_mm=70.0)
    assert out.stat().st_size > 0
    assert [g.empty for g in res.groups] == [True, False]
    assert res.items.count("tick:0") == 1  # a flat scale has one tick


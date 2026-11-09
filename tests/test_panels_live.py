"""Live tests: compose_panels draws a grid of map panels on linked colour scales (TASK-22).

Two square zones, A and B; every render frames A alone (``extent``), so each
panel's centre pixel is A's fill colour and can be compared with the ramp.
"""

from __future__ import annotations

import json

import pytest

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


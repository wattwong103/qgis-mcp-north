"""Live tests: palette names resolve to the colours they name, or are refused.

Found in the 2026-10-20 trial: QGIS's default style has no qualitative ColorBrewer
ramps (Set1/2/3, Paired, Dark2, Pastel1/2, Accent), so ``qgis_style_categorized``'s
own default "Set2" silently became Spectral, and trajectory modes were drawn in
QGIS's random default-symbol colours, so no replay of them matched.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest

from qgis_mcp_workflows import executors
from qgis_mcp_workflows.errors import ExecutorError
from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless
from tests.test_plugin_live_fixes import DRAWN_COLORS, OTHER, _class_colors, _points_geojson, _run

pytestmark = requires_headless

SET2 = ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"]
NINE = ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
TRAJECTORY = os.path.join(os.path.dirname(__file__), "fixtures", "tiny_trajectory.csv")


@pytest.fixture(scope="module")
def headless():
    ex = HeadlessExecutor()
    try:
        yield ex
    finally:
        ex.shutdown()


def _style(headless, path, field, **style):
    """Apply a style; return (response, {value: drawn color}). Always removes the layer."""
    layer_id = headless.dispatch("add_vector_layer", {"path": path})["id"]
    try:
        response = headless.dispatch("set_layer_style", {"layer_id": layer_id, "field": field, **style})
        return response, _run(headless, DRAWN_COLORS, LAYER_ID=layer_id, FIELD=field)
    finally:
        headless.dispatch("remove_layer", {"layer_id": layer_id})


def test_categorized_set2_draws_the_colorbrewer_set2_colours(headless, tmp_path):
    path = _points_geojson(tmp_path / "modes.geojson", "mode", ["bus", "rail", "walk", "bus"])
    response, drawn = _style(headless, path, "mode", style_type="categorized", color_ramp="Set2")

    assert [c["color"] for c in response["classes"]] == SET2[:3]
    assert drawn == {"bus": SET2[0], "rail": SET2[1], "walk": SET2[2]}


def test_categorized_refuses_more_categories_than_a_qualitative_palette_has(headless, tmp_path):
    path = _points_geojson(tmp_path / "nine.geojson", "k", NINE)
    with pytest.raises(ExecutorError, match=r"'Set2' has 8 colours for 9 categories") as err:
        _style(headless, path, "k", style_type="categorized", color_ramp="Set2")
    assert "classes=" in str(err.value)
    assert str(err.value).count("Next:") == 1  # the plugin's hint, not a second generic one


def test_categorized_subset_within_a_qualitative_palette_is_accepted(headless, tmp_path):
    path = _points_geojson(tmp_path / "nine.geojson", "k", NINE)
    response, drawn = _style(headless, path, "k", style_type="categorized", color_ramp="Set2",
                             classes_subset=["c", "a"])

    colors = _class_colors(response)
    assert [c["value"] for c in response["classes"]] == ["c", "a", OTHER]
    assert (colors["c"], colors["a"]) == (SET2[0], SET2[1])
    assert drawn["b"] == colors[OTHER]


@pytest.mark.parametrize("style", [
    {"style_type": "categorized"},
    {"style_type": "graduated", "classes": 3, "mode": "equal_interval"},
])
def test_an_unknown_palette_is_refused_not_replaced(headless, tmp_path, style):
    path = _points_geojson(tmp_path / "v.geojson", "v", [1, 2, 3, 4])
    with pytest.raises(ExecutorError, match=r"Unknown palette 'NoSuchRamp'.*Next:"):
        _style(headless, path, "v", color_ramp="NoSuchRamp", **style)


def test_no_palette_means_set2_for_a_few_categories(headless, tmp_path):
    path = _points_geojson(tmp_path / "modes.geojson", "mode", ["bus", "rail", "walk"])
    response, _ = _style(headless, path, "mode", style_type="categorized", color_ramp=None)
    assert [c["color"] for c in response["classes"]] == SET2[:3]


def test_no_palette_means_a_continuous_ramp_for_many_categories(headless, tmp_path):
    # 23 wards with no palette worked in 1.15.0 and must keep working.
    path = _points_geojson(tmp_path / "nine.geojson", "k", NINE)
    response, drawn = _style(headless, path, "k", style_type="categorized", color_ramp=None)
    colors = [c["color"] for c in response["classes"]]
    assert len(set(colors)) == 9
    assert drawn == dict(zip(NINE, colors, strict=True))


def test_graduated_refuses_more_classes_than_a_qualitative_palette_has(headless, tmp_path):
    path = _points_geojson(tmp_path / "v.geojson", "v", list(range(20)))
    with pytest.raises(ExecutorError, match=r"'Set2' has 8 colours for 9 classes.*Next: ask for at most 8"):
        _style(headless, path, "v", style_type="graduated", color_ramp="Set2", classes=9, mode="equal_interval")


def test_a_qgis_style_ramp_still_resolves(headless, tmp_path):
    path = _points_geojson(tmp_path / "v.geojson", "v", [1, 2, 3, 4])
    response, _ = _style(headless, path, "v", style_type="graduated", color_ramp="YlOrRd",
                         classes=3, mode="equal_interval")
    assert len(response["breaks"]) == 4


@pytest.fixture
def server_on(headless, tmp_path, monkeypatch):
    """The MCP server module, dispatching to the module's one headless QGIS."""
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    executors.set_executor(headless)
    try:
        yield importlib.import_module("qgis_mcp_workflows.server")
    finally:
        executors.set_executor(None)


async def test_trajectory_mode_colours_are_the_same_on_every_render(server_on, tmp_path):
    for name in ("first.png", "second.png"):
        await server_on.mcp.call_tool("qgis_render_trajectory", {
            "input_path": TRAJECTORY, "output_png": str(tmp_path / name),
            "mode_col": "transport_mode", "width": 400, "height": 300})
    digests = {hashlib.sha256(Path(tmp_path / n).read_bytes()).hexdigest() for n in ("first.png", "second.png")}
    assert len(digests) == 1


def _two_series(path):
    features = [
        {"type": "Feature", "properties": {"pop": i + 1, "jobs": 2 * i + 1},
         "geometry": {"type": "Point", "coordinates": [139.7 + i * 0.01, 35.7]}}
        for i in range(3)
    ]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return str(path)


async def test_diagram_map_renders_with_a_qualitative_palette(server_on, tmp_path):
    # Dark2 used to fall back to Spectral here too; now it resolves (or is refused).
    layer = _two_series(tmp_path / "series.geojson")
    await server_on.mcp.call_tool("qgis_render_diagram_map", {
        "layer_path": layer, "value_fields": ["pop", "jobs"], "palette": "Dark2",
        "output_png": str(tmp_path / "d.png"), "width": 400, "height": 300})
    assert (tmp_path / "d.png").stat().st_size > 0

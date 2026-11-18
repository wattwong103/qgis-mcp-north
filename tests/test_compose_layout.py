"""Tests for qgis_compose_layout — FakeExecutor, no QGIS.

The programmatic QgsPrintLayout building is QGIS-only and verified live; here we
lock down the MCP-side param threading + response translation.
"""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows.errors import InvalidArgumentError, PluginOutdatedError
from qgis_mcp_workflows.server import qgis_compose_layout

# Keys a v1.23 call sent. A call that sets none of the v1.24 controls sends these.
_V123_KEYS = {
    "layer_paths", "output_path", "title", "extent", "page",
    "legend", "scale_bar", "north_arrow", "dpi",
}


def _ok() -> dict:
    return {
        "output_path": "/tmp/fig.png",
        "format": "png",
        "n_layers": 2,
        "items": ["map", "title", "legend", "scalebar", "north_arrow"],
        "page_size_mm": [297.0, 210.0],
    }


def test_threads_params_and_returns_result(fake_executor):
    fake_executor.responses["compose_layout"] = _ok()
    r = qgis_compose_layout(
        layer_paths=["/a.gpkg", "/b.shp"],
        output_path="/tmp/fig.png",
        title="Net flux",
        page="a4_landscape",
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "compose_layout"
    assert params["title"] == "Net flux"
    assert params["page"] == "a4_landscape"
    assert r.n_layers == 2
    assert "legend" in r.items
    assert r.page_size_mm == [297.0, 210.0]


def test_paths_absolute_and_toggles(fake_executor):
    fake_executor.responses["compose_layout"] = _ok()
    qgis_compose_layout(
        layer_paths=["rel.gpkg"],
        output_path="rel.png",
        legend=False,
        scale_bar=False,
        north_arrow=False,
    )
    params = fake_executor.calls[0][1]
    assert all(os.path.isabs(p) for p in params["layer_paths"])
    assert os.path.isabs(params["output_path"])
    assert params["legend"] is False
    assert params["scale_bar"] is False
    assert params["north_arrow"] is False


def test_omitted_type_and_page_keep_the_v123_command_body(fake_executor):
    fake_executor.responses["compose_layout"] = _ok()
    qgis_compose_layout(layer_paths=["/a.gpkg"], output_path="/tmp/fig.png")
    assert set(fake_executor.calls[0][1]) == _V123_KEYS


def _applied(**applied):
    fonts = applied.pop("_fonts", [])
    body = _ok()
    body["layout_applied"] = applied
    body["fonts_missing"] = fonts
    body["page_size_mm"] = [applied.get("width_mm", 297.0), applied.get("height_mm", 210.0)]
    return body


def test_page_millimetres_replace_the_preset(fake_executor):
    fake_executor.responses["compose_layout"] = _applied(width_mm=180.0, height_mm=120.0)
    result = qgis_compose_layout(
        layer_paths=["/a.gpkg"], output_path="/tmp/fig.png",
        page="a4_landscape", width_mm=180, height_mm=120,
    )
    params = fake_executor.calls[0][1]
    assert params["width_mm"] == 180 and params["height_mm"] == 120
    assert params["page"] == "a4_landscape"
    assert set(params) == _V123_KEYS | {"width_mm", "height_mm"}
    assert result.layout_applied == {"width_mm": 180.0, "height_mm": 120.0}
    assert result.page_size_mm == [180.0, 120.0]
    assert result.fonts_missing == []


def test_one_page_side_is_refused(fake_executor):
    with pytest.raises(InvalidArgumentError, match=r"width_mm and height_mm.*Next:"):
        qgis_compose_layout(
            layer_paths=["/a.gpkg"], output_path="/tmp/fig.png", width_mm=180,
        )
    assert fake_executor.calls == []


@pytest.mark.parametrize("kwargs", [
    {"width_mm": 20, "height_mm": 120},
    {"width_mm": 1000.1, "height_mm": 120},
    {"width_mm": 180, "height_mm": 20},
    {"title_size_pt": 0, "title": "T"},
    {"title_size_pt": 72.1, "title": "T"},
    {"legend_size_pt": 0},
    {"legend_corner": "middle"},
    {"font_family": ""},
    {"font_family": "   "},
], ids=lambda k: ",".join(k))
def test_layout_control_ranges_are_refused(fake_executor, kwargs):
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_compose_layout(layer_paths=["/a.gpkg"], output_path="/tmp/fig.png", **kwargs)
    assert fake_executor.calls == []


def test_title_and_legend_type_need_the_item_they_draw(fake_executor):
    with pytest.raises(InvalidArgumentError, match=r"title_size_pt needs a title.*Next:"):
        qgis_compose_layout(
            layer_paths=["/a.gpkg"], output_path="/tmp/fig.png", title_size_pt=14,
        )
    with pytest.raises(InvalidArgumentError, match=r"legend_corner needs legend=True.*Next:"):
        qgis_compose_layout(
            layer_paths=["/a.gpkg"], output_path="/tmp/fig.png",
            legend=False, legend_corner="bottom_left",
        )
    with pytest.raises(InvalidArgumentError, match=r"legend_size_pt needs legend=True.*Next:"):
        qgis_compose_layout(
            layer_paths=["/a.gpkg"], output_path="/tmp/fig.png", legend=False, legend_size_pt=8,
        )
    with pytest.raises(InvalidArgumentError, match=r"font_family needs a title, a legend, or a scale bar.*Next:"):
        qgis_compose_layout(
            layer_paths=["/a.gpkg"], output_path="/tmp/fig.png",
            title=None, legend=False, scale_bar=False, font_family="Arial",
        )
    assert fake_executor.calls == []


def test_font_title_and_legend_are_dispatched_only_when_set(fake_executor):
    fake_executor.responses["compose_layout"] = _applied(
        font_family="Arial", title_size_pt=14.0, legend_size_pt=8.0, legend_corner="bottom_left",
        _fonts=["Nope"],
    )
    result = qgis_compose_layout(
        layer_paths=["/a.gpkg"], output_path="/tmp/fig.png", title="Net flux",
        font_family="Nope", title_size_pt=14, legend_size_pt=8, legend_corner="bottom_left",
    )
    params = fake_executor.calls[0][1]
    assert params["font_family"] == "Nope"
    assert params["title_size_pt"] == 14
    assert params["legend_size_pt"] == 8
    assert params["legend_corner"] == "bottom_left"
    assert "width_mm" not in params and "height_mm" not in params
    assert result.fonts_missing == ["Nope"]
    assert result.layout_applied["legend_corner"] == "bottom_left"


def test_a_plugin_that_ignores_layout_controls_is_outdated(fake_executor):
    fake_executor.responses["compose_layout"] = _ok()
    with pytest.raises(PluginOutdatedError, match="Next:"):
        qgis_compose_layout(
            layer_paths=["/a.gpkg"], output_path="/tmp/fig.png", width_mm=180, height_mm=120,
        )

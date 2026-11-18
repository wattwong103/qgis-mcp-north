"""Figure controls on choropleth and the two style tools.

Omitted arguments must leave the v1.17.0 command body unchanged. Set arguments
are validated before dispatch and echoed only when the plugin reports
``style_applied``.
"""

from __future__ import annotations

import math

import pytest

from qgis_mcp_workflows.errors import InvalidArgumentError, PluginOutdatedError
from qgis_mcp_workflows.server import (
    qgis_render_choropleth,
    qgis_style_categorized,
    qgis_style_graduated,
)

_GRADUATED_V117_KEYS = {
    "layer_id",
    "style_type",
    "field",
    "classes",
    "mode",
    "color_ramp",
    "diverging",
    "center",
}
_CATEGORIZED_V117_KEYS = {"layer_id", "style_type", "field", "color_ramp"}
_CHOROPLETH_V117_KEYS = {
    "zones_path",
    "value_field",
    "output_png",
    "value_dict",
    "join_field",
    "n_classes",
    "mode",
    "palette",
    "diverging",
    "center",
    "label_field",
    "title",
    "legend",
    "basemap_paths",
    "basemap_spec",
    "width",
    "height",
    "dpi",
    "scale_bar",
    "north_arrow",
}


def _graduated_ok(**overrides):
    base = {
        "ok": True,
        "n_classes": 5,
        "classes": [{"value": "0-1", "color": "#fff5eb", "n_features": 1}],
        "breaks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        "mode": "quantile",
    }
    base.update(overrides)
    return base


def _categorized_ok(**overrides):
    base = {
        "ok": True,
        "n_classes": 2,
        "classes": [
            {"value": "car", "color": "#1b9e77", "n_features": 3},
            {"value": "rail", "color": "#d95f02", "n_features": 1},
        ],
    }
    base.update(overrides)
    return base


def _choropleth_ok(**overrides):
    base = {
        "output_path": "/tmp/x.png",
        "width": 1600,
        "height": 1200,
        "dpi": 150,
        "extent": [0.0, 0.0, 1.0, 1.0],
        "crs": "EPSG:4326",
        "n_layers": 1,
        "field": "n",
        "n_classes": 5,
        "breaks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        "mode": "quantile",
        "min_value": 0.0,
        "max_value": 5.0,
        "n_features": 4,
        "n_matched": 4,
        "n_unmatched": 0,
    }
    base.update(overrides)
    return base


def test_omitted_controls_keep_the_v117_command_body(fake_executor):
    fake_executor.responses["set_layer_style"] = _graduated_ok()
    qgis_style_graduated(layer_id="L1", field="n")
    assert set(fake_executor.calls[0][1]) == _GRADUATED_V117_KEYS

    fake_executor.calls.clear()
    fake_executor.responses["set_layer_style"] = _categorized_ok()
    qgis_style_categorized(layer_id="L1", field="mode")
    assert set(fake_executor.calls[0][1]) == _CATEGORIZED_V117_KEYS

    fake_executor.calls.clear()
    fake_executor.responses["render_choropleth"] = _choropleth_ok()
    qgis_render_choropleth(zones_path="/z.gpkg", value_field="n", output_png="/o.png")
    assert set(fake_executor.calls[0][1]) == _CHOROPLETH_V117_KEYS


def test_set_controls_are_the_only_extra_keys(fake_executor):
    fake_executor.responses["set_layer_style"] = _graduated_ok(
        style_applied={"outline_color": "#cccccc", "outline_width_mm": 0.05, "fill_opacity": 0.8},
        mode="explicit",
        n_classes=2,
        breaks=[0.0, 10.0, 50.0],
        n_outside_breaks=1,
    )
    result = qgis_style_graduated(
        layer_id="L1",
        field="n",
        outline_color="#cccccc",
        outline_width_mm=0.05,
        fill_opacity=0.8,
        breaks=[0, 10, 50],
    )
    params = fake_executor.calls[0][1]
    assert set(params) == _GRADUATED_V117_KEYS | {
        "outline_color",
        "outline_width_mm",
        "fill_opacity",
        "breaks",
    }
    assert params["breaks"] == [0, 10, 50]
    assert result.mode == "explicit"
    assert result.n_classes == 2
    assert result.n_outside_breaks == 1
    assert result.style_applied["outline_width_mm"] == 0.05


def test_categorized_colors_follow_classes(fake_executor):
    fake_executor.responses["set_layer_style"] = _categorized_ok(
        style_applied={"colors": ["#111111", "#222222"]}
    )
    qgis_style_categorized(
        layer_id="L1",
        field="mode",
        classes=["car", "rail"],
        colors=["#111111", "#222222"],
        outline_color="#abc",
    )
    params = fake_executor.calls[0][1]
    assert params["classes_subset"] == ["car", "rail"]
    assert params["colors"] == ["#111111", "#222222"]
    assert params["outline_color"] == "#abc"
    assert "breaks" not in params


def test_choropleth_label_controls_require_a_field_and_are_forwarded(fake_executor):
    fake_executor.responses["render_choropleth"] = _choropleth_ok(
        style_applied={"label_size_pt": 11.0, "label_halo_mm": 0.4, "label_halo_color": "#ffffff"},
        fonts_missing=["Nope"],
    )
    result = qgis_render_choropleth(
        zones_path="/z.gpkg",
        value_field="n",
        output_png="/o.png",
        label_field="name",
        label_size_pt=11,
        label_font="Nope",
        label_halo_mm=0.4,
    )
    params = fake_executor.calls[0][1]
    assert params["label_field"] == "name"
    assert params["label_size_pt"] == 11
    assert params["label_font"] == "Nope"
    assert params["label_halo_mm"] == 0.4
    assert "label_color" not in params
    assert "label_halo_color" not in params
    assert result.fonts_missing == ["Nope"]
    assert result.style_applied["label_size_pt"] == 11.0


def test_missing_style_applied_means_the_plugin_is_old(fake_executor):
    fake_executor.responses["set_layer_style"] = _graduated_ok()
    with pytest.raises(PluginOutdatedError, match="Next:"):
        qgis_style_graduated(layer_id="L1", field="n", fill_opacity=0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"outline_color": "red"},
        {"outline_color": "#gg0000"},
        {"outline_color": "#12345"},
        {"outline_width_mm": -0.01},
        {"outline_width_mm": 5.01},
        {"fill_opacity": -0.1},
        {"fill_opacity": 1.1},
        {"fill_opacity": True},
        {"breaks": [1, 0, 2]},
        {"breaks": [0, 0, 1]},
        {"breaks": [0, math.nan, 1]},
        {"breaks": [0, math.inf, 1]},
        {"breaks": [0, 1], "diverging": True},
    ],
)
def test_graduated_rejects_bad_controls(fake_executor, kwargs):
    fake_executor.responses["set_layer_style"] = _graduated_ok(style_applied={})
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_style_graduated(layer_id="L1", field="n", **kwargs)
    assert fake_executor.calls == []


def test_breaks_bounds_and_explicit_length(fake_executor):
    fake_executor.responses["set_layer_style"] = _graduated_ok(style_applied={"breaks": [0.0, 1.0]})
    qgis_style_graduated(layer_id="L1", field="n", breaks=[0, 1])
    assert fake_executor.calls[-1][1]["breaks"] == [0, 1]

    edges = list(range(17))
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_style_graduated(layer_id="L1", field="n", breaks=edges)


def test_width_and_opacity_endpoints_are_allowed(fake_executor):
    fake_executor.responses["render_choropleth"] = _choropleth_ok(
        style_applied={"outline_width_mm": 0.0, "fill_opacity": 1.0}
    )
    qgis_render_choropleth(
        zones_path="/z.gpkg",
        value_field="n",
        output_png="/o.png",
        outline_width_mm=0,
        fill_opacity=1,
    )
    params = fake_executor.calls[0][1]
    assert params["outline_width_mm"] == 0
    assert params["fill_opacity"] == 1


def test_colors_need_classes_of_the_same_length(fake_executor):
    with pytest.raises(InvalidArgumentError, match="classes"):
        qgis_style_categorized(layer_id="L1", field="mode", colors=["#111111"])
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_style_categorized(
            layer_id="L1", field="mode", classes=["a"], colors=["#111111", "#222222"]
        )
    with pytest.raises(InvalidArgumentError, match="#RGB"):
        qgis_style_categorized(layer_id="L1", field="mode", classes=["a"], colors=["blue"])
    assert fake_executor.calls == []


def test_label_controls_without_label_field_are_refused(fake_executor):
    with pytest.raises(InvalidArgumentError, match="label_field"):
        qgis_render_choropleth(
            zones_path="/z.gpkg",
            value_field="n",
            output_png="/o.png",
            label_size_pt=8,
        )
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_render_choropleth(
            zones_path="/z.gpkg",
            value_field="n",
            output_png="/o.png",
            label_halo_color="#000000",
        )
    with pytest.raises(InvalidArgumentError, match="72"):
        qgis_render_choropleth(
            zones_path="/z.gpkg",
            value_field="n",
            output_png="/o.png",
            label_field="name",
            label_size_pt=0,
        )
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_render_choropleth(
            zones_path="/z.gpkg",
            value_field="n",
            output_png="/o.png",
            label_field="name",
            label_halo_mm=5.1,
        )
    assert fake_executor.calls == []


def test_short_hex_is_a_color(fake_executor):
    fake_executor.responses["set_layer_style"] = _categorized_ok(
        style_applied={"outline_color": "#abc"}
    )
    qgis_style_categorized(
        layer_id="L1", field="mode", classes=["a"], colors=["#abc"], outline_color="#ABC"
    )
    assert fake_executor.calls[0][1]["outline_color"] == "#ABC"
    assert fake_executor.calls[0][1]["colors"] == ["#abc"]


def test_plugin_outdated_hint_names_the_transport():
    plugin = PluginOutdatedError("plugin")
    headless = PluginOutdatedError("headless")
    assert "restart QGIS" in str(plugin)
    assert "restart the MCP server" in str(headless)
    assert str(plugin).count("Next:") == 1
    assert str(headless).count("Next:") == 1


def test_values_outside_explicit_breaks_are_counted():
    from qgis_mcp_workflows_plugin.figure_style import outside_breaks

    n_outside, data_min, data_max, all_outside = outside_breaks(
        [0.0, 1.0, 5.0, 9.0], [1.0, 4.0, 8.0]
    )
    assert (n_outside, data_min, data_max, all_outside) == (2, 0.0, 9.0, False)

    _n, lo, hi, all_out = outside_breaks([0.0, 10.0], [1.0, 2.0])
    assert all_out is True
    assert (lo, hi) == (0.0, 10.0)

    n_edge, _, _, edges_out = outside_breaks([1.0, 8.0], [1.0, 4.0, 8.0])
    assert n_edge == 0
    assert edges_out is False

    _n, _lo, _hi, empty = outside_breaks([], [0.0, 1.0])
    assert empty is True

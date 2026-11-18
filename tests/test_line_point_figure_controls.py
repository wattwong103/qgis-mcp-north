"""Stroke, opacity and label controls on the line and point renders.

Omitted arguments stay out of the command body. Set arguments are checked
before dispatch and echoed only when the plugin reports ``style_applied``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qgis_mcp_workflows.errors import InvalidArgumentError, PluginOutdatedError
from qgis_mcp_workflows.server import (
    qgis_render_catchment,
    qgis_render_diagram_map,
    qgis_render_from_duckdb,
    qgis_render_link_density,
    qgis_render_od_flows,
    qgis_render_trajectory,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TINY_TRAJ = FIXTURE_DIR / "tiny_trajectory.csv"
TINY_OD = FIXTURE_DIR / "tiny_od.csv"
TINY_ZONES = FIXTURE_DIR / "tiny_zones.geojson"

_TRAJECTORY_KEYS = {
    "input_path", "output_png", "render_mode", "basemap_paths", "basemap_spec",
    "extent", "width", "height", "dpi", "features", "mode_col", "used_movingpandas",
    "speed_field", "scale_bar", "north_arrow",
}
_OD_KEYS = {
    "zones_path", "output_png", "flows", "zone_id_field", "arc_style",
    "basemap_paths", "basemap_spec", "label_field", "width", "height", "dpi",
    "scale_bar", "north_arrow",
}
_LINK_KEYS = {
    "density", "drm_network_path", "output_png", "link_id_col", "aggregation",
    "value_col", "n_classes", "mode", "palette", "extent", "basemap_paths",
    "basemap_spec", "label_field", "width", "height", "dpi", "scale_bar", "north_arrow",
}
_DIAGRAM_KEYS = {
    "layer_path", "value_fields", "output_png", "diagram_type", "size", "palette",
    "extent", "basemap_spec", "width", "height", "dpi",
}
_CATCHMENT_KEYS = {
    "points_path", "output_png", "method", "extent", "basemap_spec",
    "width", "height", "dpi",
}
_DUCKDB_KEYS = {
    "features", "output_png", "crs", "value_field", "n_classes", "mode", "palette",
    "diverging", "center", "basemap_spec", "width", "height", "dpi",
}


def _traj(**overrides):
    base = {
        "output_path": "/tmp/traj.png", "width": 1600, "height": 1200, "dpi": 150,
        "extent": [139.68, 35.69, 139.74, 35.74], "crs": "EPSG:4326", "n_layers": 1,
        "n_trajectories": 1, "n_points_total": 1, "n_points_rendered": 1,
        "downsampled": False, "time_range": None, "modes": None, "used_movingpandas": False,
    }
    base.update(overrides)
    return base


def _od(**overrides):
    base = {
        "output_path": "/tmp/od.png", "width": 1600, "height": 1200, "dpi": 150,
        "extent": [0.0, 0.0, 1.0, 1.0], "crs": "EPSG:4326", "n_layers": 2,
        "n_flows": 1, "n_flows_rendered": 1, "n_zones": 1, "max_flow": 1.0,
        "min_flow_rendered": 1.0, "n_unmatched_origins": 0, "n_unmatched_destinations": 0,
    }
    base.update(overrides)
    return base


def _link(**overrides):
    base = {
        "output_path": "/tmp/links.png", "width": 1600, "height": 1200, "dpi": 150,
        "extent": [0.0, 0.0, 1.0, 1.0], "crs": "EPSG:4326", "n_layers": 1,
        "n_links_with_traffic": 1, "n_links_rendered": 1, "n_unmatched_link_ids": 0,
        "density_field": "n_points", "breaks": [1.0, 2.0], "mode": "quantile",
        "min_density": 1.0, "max_density": 2.0,
    }
    base.update(overrides)
    return base


def _diagram(**overrides):
    base = {
        "output_path": "/tmp/d.png", "width": 1600, "height": 1200, "dpi": 150,
        "extent": [0.0, 0.0, 1.0, 1.0], "crs": "EPSG:4326", "n_layers": 1,
        "diagram_type": "pie", "value_fields": ["a"], "n_features": 1,
    }
    base.update(overrides)
    return base


def _catchment(**overrides):
    base = {
        "output_path": "/tmp/c.png", "width": 1600, "height": 1200, "dpi": 150,
        "extent": [0.0, 0.0, 1.0, 1.0], "crs": "EPSG:4326", "n_layers": 2,
        "method": "voronoi", "n_points": 3, "n_catchments": 3,
    }
    base.update(overrides)
    return base


def _duck(**overrides):
    base = {
        "output_path": "/tmp/o.png", "width": 1600, "height": 1200, "dpi": 150,
        "extent": [0.0, 0.0, 1.0, 1.0], "crs": "EPSG:4326", "n_layers": 1,
        "geometry_type": "Point", "n_features": 1, "n_skipped": 0,
        "fields": ["trip_id"], "field": None, "breaks": None,
    }
    base.update(overrides)
    return base


def test_omitted_trajectory_sends_the_v120_body(fake_executor):
    fake_executor.responses["render_trajectory"] = _traj()
    qgis_render_trajectory(input_path=str(TINY_TRAJ), output_png="/tmp/traj.png")
    assert set(fake_executor.calls[0][1]) == _TRAJECTORY_KEYS


def test_omitted_od_sends_the_v120_body(fake_executor):
    fake_executor.responses["render_od_flows"] = _od()
    qgis_render_od_flows(
        od_csv=str(TINY_OD), zones_layer_path=str(TINY_ZONES), output_png="/tmp/od.png",
    )
    assert set(fake_executor.calls[0][1]) == _OD_KEYS


def test_omitted_link_density_sends_the_v120_body(fake_executor, tmp_path):
    load = tmp_path / "loads.csv"
    load.write_text("link_id,volume\n1,2\n", encoding="utf-8")
    drm = tmp_path / "drm.gpkg"
    drm.write_bytes(b"")
    fake_executor.responses["render_link_density"] = _link()
    qgis_render_link_density(
        drm_network_path=str(drm), output_png="/tmp/links.png", load_csv=str(load),
    )
    assert set(fake_executor.calls[0][1]) == _LINK_KEYS


def test_omitted_diagram_and_catchment_send_the_v120_body(fake_executor):
    fake_executor.responses["render_diagram_map"] = _diagram()
    qgis_render_diagram_map(
        layer_path="/z.gpkg", value_fields=["a"], output_png="/o.png",
    )
    assert set(fake_executor.calls[0][1]) == _DIAGRAM_KEYS

    fake_executor.calls.clear()
    fake_executor.responses["render_catchment"] = _catchment()
    qgis_render_catchment(points_path="/s.gpkg", output_png="/o.png")
    assert set(fake_executor.calls[0][1]) == _CATCHMENT_KEYS


def test_trajectory_dispatches_stroke_opacity_and_label(fake_executor):
    fake_executor.responses["render_trajectory"] = _traj(
        style_applied={
            "outline_color": "#cccccc", "outline_width_mm": 0.4, "fill_opacity": 0.5,
            "label_field": "transport_mode", "label_size_pt": 11.0,
            "label_halo_mm": 1.0, "label_halo_color": "#ffffff",
        },
        fonts_missing=["NoSuchFont"],
    )
    result = qgis_render_trajectory(
        input_path=str(TINY_TRAJ), output_png="/tmp/traj.png",
        outline_color="#cccccc", outline_width_mm=0.4, fill_opacity=0.5,
        label_field="transport_mode", label_size_pt=11, label_font="NoSuchFont",
    )
    params = fake_executor.calls[0][1]
    assert params["outline_color"] == "#cccccc"
    assert params["outline_width_mm"] == 0.4
    assert params["fill_opacity"] == 0.5
    assert params["label_field"] == "transport_mode"
    assert params["label_size_pt"] == 11
    assert params["label_font"] == "NoSuchFont"
    assert params["features"][0]["transport_mode"] == "taxi"
    assert result.style_applied["outline_width_mm"] == 0.4
    assert result.fonts_missing == ["NoSuchFont"]


def test_trajectory_copies_a_csv_label_column(fake_executor):
    fake_executor.responses["render_trajectory"] = _traj(style_applied={"label_field": "datetime"})
    qgis_render_trajectory(
        input_path=str(TINY_TRAJ), output_png="/tmp/traj.png", label_field="datetime",
    )
    feat = fake_executor.calls[0][1]["features"][0]
    assert feat["datetime"] == "2026-01-01 08:00:00"
    assert fake_executor.calls[0][1]["label_field"] == "datetime"


def test_line_trajectory_with_mode_refuses_a_colour_that_would_replace_modes(fake_executor):
    with pytest.raises(InvalidArgumentError, match="mode_col") as exc:
        qgis_render_trajectory(
            input_path=str(TINY_TRAJ), output_png="/tmp/traj.png",
            mode_col="transport_mode", outline_color="#112233",
        )
    assert "Next:" in str(exc.value)
    assert fake_executor.calls == []


def test_point_trajectory_keeps_mode_colours_and_sends_the_marker_stroke(fake_executor):
    fake_executor.responses["render_trajectory"] = _traj(style_applied={"outline_color": "#112233"})
    qgis_render_trajectory(
        input_path=str(TINY_TRAJ), output_png="/tmp/traj.png",
        mode_col="transport_mode", render_mode="points", outline_color="#112233",
        label_field="transport_mode",
    )
    params = fake_executor.calls[0][1]
    assert params["render_mode"] == "points"
    assert params["outline_color"] == "#112233"
    assert params["mode_col"] == "transport_mode"
    assert params["label_field"] == "mode"
    assert params["features"][0]["mode"] == "taxi"
    assert "transport_mode" not in params["features"][0]


def test_heatmap_refuses_stroke_and_opacity(fake_executor):
    with pytest.raises(InvalidArgumentError, match="heatmap") as exc:
        qgis_render_trajectory(
            input_path=str(TINY_TRAJ), output_png="/tmp/traj.png",
            render_mode="heatmap", fill_opacity=0.4,
        )
    assert "Next:" in str(exc.value)
    assert fake_executor.calls == []


def test_od_dispatches_stroke_and_keeps_flow_width_until_set(fake_executor):
    fake_executor.responses["render_od_flows"] = _od(
        style_applied={"outline_color": "#abc", "fill_opacity": 0.6},
    )
    result = qgis_render_od_flows(
        od_csv=str(TINY_OD), zones_layer_path=str(TINY_ZONES), output_png="/tmp/od.png",
        outline_color="#abc", fill_opacity=0.6,
    )
    params = fake_executor.calls[0][1]
    assert params["outline_color"] == "#abc"
    assert params["fill_opacity"] == 0.6
    assert "outline_width_mm" not in params
    assert result.style_applied["fill_opacity"] == 0.6


def test_od_label_size_requires_label_field_and_echoes_style(fake_executor):
    with pytest.raises(InvalidArgumentError, match="label_field"):
        qgis_render_od_flows(
            od_csv=str(TINY_OD), zones_layer_path=str(TINY_ZONES), output_png="/tmp/od.png",
            label_size_pt=12,
        )
    fake_executor.responses["render_od_flows"] = _od(
        style_applied={"label_size_pt": 12.0, "label_halo_mm": 1.0, "label_halo_color": "#ffffff"},
    )
    result = qgis_render_od_flows(
        od_csv=str(TINY_OD), zones_layer_path=str(TINY_ZONES), output_png="/tmp/od.png",
        label_field="origin", label_size_pt=12,
    )
    assert fake_executor.calls[0][1]["label_size_pt"] == 12
    assert result.style_applied["label_size_pt"] == 12.0


def test_link_density_label_and_width(fake_executor, tmp_path):
    load = tmp_path / "loads.csv"
    load.write_text("link_id,volume\n1,2\n", encoding="utf-8")
    drm = tmp_path / "drm.gpkg"
    drm.write_bytes(b"")
    with pytest.raises(InvalidArgumentError, match="label_field"):
        qgis_render_link_density(
            drm_network_path=str(drm), output_png="/tmp/links.png", load_csv=str(load),
            label_halo_mm=0.4,
        )
    fake_executor.responses["render_link_density"] = _link(
        style_applied={
            "outline_width_mm": 1.2, "label_size_pt": 7.0,
            "label_halo_mm": 0.4, "label_halo_color": "#ffffff",
        },
    )
    result = qgis_render_link_density(
        drm_network_path=str(drm), output_png="/tmp/links.png", load_csv=str(load),
        outline_width_mm=1.2, label_field="density", label_halo_mm=0.4,
    )
    params = fake_executor.calls[0][1]
    assert params["outline_width_mm"] == 1.2
    assert params["label_halo_mm"] == 0.4
    assert params["label_field"] == "density"
    assert result.style_applied["outline_width_mm"] == 1.2


def test_diagram_and_catchment_dispatch_controls(fake_executor):
    fake_executor.responses["render_diagram_map"] = _diagram(
        style_applied={"outline_color": "#ccc", "label_field": "name"},
    )
    diagram = qgis_render_diagram_map(
        layer_path="/z.gpkg", value_fields=["a"], output_png="/o.png",
        outline_color="#ccc", label_field="name",
    )
    assert fake_executor.calls[0][1]["outline_color"] == "#ccc"
    assert fake_executor.calls[0][1]["label_field"] == "name"
    assert fake_executor.calls[0][1]["size"] == 10.0
    assert diagram.style_applied["label_field"] == "name"

    fake_executor.calls.clear()
    fake_executor.responses["render_catchment"] = _catchment(
        style_applied={"fill_opacity": 0.4, "outline_width_mm": 0.2},
    )
    catchment = qgis_render_catchment(
        points_path="/s.gpkg", output_png="/o.png", fill_opacity=0.4, outline_width_mm=0.2,
    )
    params = fake_executor.calls[0][1]
    assert params["fill_opacity"] == 0.4
    assert params["outline_width_mm"] == 0.2
    assert catchment.style_applied["fill_opacity"] == 0.4


def test_duckdb_unstyled_path_sends_controls_only_when_set(fake_executor, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "pings.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE pings (trip_id VARCHAR, lon DOUBLE, lat DOUBLE)")
    con.execute("INSERT INTO pings VALUES ('T1', 139.7, 35.7)")
    con.close()

    fake_executor.responses["render_wkt_features"] = _duck()
    qgis_render_from_duckdb(
        db_path=str(path), query="SELECT trip_id, lon, lat FROM pings",
        output_png="/tmp/o.png", lon_column="lon", lat_column="lat",
    )
    assert set(fake_executor.calls[0][1]) == _DUCKDB_KEYS

    fake_executor.calls.clear()
    fake_executor.responses["render_wkt_features"] = _duck(
        style_applied={"outline_color": "#123456", "label_field": "trip_id"},
    )
    result = qgis_render_from_duckdb(
        db_path=str(path), query="SELECT trip_id, lon, lat FROM pings",
        output_png="/tmp/o.png", lon_column="lon", lat_column="lat",
        outline_color="#123456", label_field="trip_id",
    )
    params = fake_executor.calls[0][1]
    assert params["outline_color"] == "#123456"
    assert params["label_field"] == "trip_id"
    assert result.style_applied["label_field"] == "trip_id"


def test_bad_stroke_is_refused_before_dispatch(fake_executor):
    with pytest.raises(InvalidArgumentError, match="outline_color"):
        qgis_render_catchment(
            points_path="/s.gpkg", output_png="/o.png", outline_color="red",
        )
    with pytest.raises(InvalidArgumentError, match="outline_width_mm"):
        qgis_render_diagram_map(
            layer_path="/z.gpkg", value_fields=["a"], output_png="/o.png",
            outline_width_mm=5.1,
        )
    with pytest.raises(InvalidArgumentError, match="fill_opacity"):
        qgis_render_diagram_map(
            layer_path="/z.gpkg", value_fields=["a"], output_png="/o.png",
            fill_opacity=1.1,
        )
    with pytest.raises(InvalidArgumentError, match="label_field"):
        qgis_render_catchment(points_path="/s.gpkg", output_png="/o.png", label_color="#fff")
    assert fake_executor.calls == []


def test_missing_style_applied_means_the_plugin_is_old(fake_executor):
    fake_executor.responses["render_catchment"] = _catchment()
    with pytest.raises(PluginOutdatedError, match="Next:"):
        qgis_render_catchment(
            points_path="/s.gpkg", output_png="/o.png", fill_opacity=0.5,
        )

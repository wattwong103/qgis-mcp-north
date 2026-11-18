"""qgis_zonal_stats — FakeExecutor, no QGIS required.

Histogram columns are assembled without PyQGIS. The plugin counts cell
centers; these tests pin which class ids become integer fields.
"""

from __future__ import annotations

import ast
import os
import typing
from pathlib import Path

import pytest

from qgis_mcp_workflows.errors import InvalidArgumentError, LayerNotFoundError, ZonalStatsError
from qgis_mcp_workflows.server import _ZONAL_STATS, qgis_zonal_stats
from qgis_mcp_workflows_plugin.zonal_histogram import (
    STATS,
    histogram_columns,
    normalize_stats,
)

REPO = Path(__file__).resolve().parent.parent


def test_zonal_stats_dispatches(fake_executor, tmp_path):
    out = tmp_path / "zonal.gpkg"
    fake_executor.responses["zonal_stats"] = {
        "output_path": str(out),
        "n_zones": 47,
        "n_with_value": 47,
        "stats": ["count", "mean"],
        "fields_added": ["lulc_count", "lulc_mean"],
        "raster_band": 1,
        "prefix": "lulc_",
    }
    result = qgis_zonal_stats(
        zones_path="/tmp/zones.geojson",
        raster_path="/tmp/lulc.tif",
        output_path=str(out),
        stats=["count", "mean"],
        prefix="lulc_",
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "zonal_stats"
    assert os.path.isabs(params["zones_path"])
    assert os.path.isabs(params["raster_path"])
    assert params["stats"] == ["count", "mean"]
    assert params["prefix"] == "lulc_"
    assert params["raster_band"] == 1
    assert result.n_zones == 47
    assert result.fields_added == ["lulc_count", "lulc_mean"]
    assert os.path.isabs(result.output_path)


def test_zonal_stats_defaults_count_sum_mean(fake_executor, tmp_path):
    fake_executor.responses["zonal_stats"] = {
        "output_path": str(tmp_path / "z.csv"),
        "n_zones": 4,
        "n_with_value": 4,
        "stats": ["count", "sum", "mean"],
        "fields_added": ["count", "sum", "mean"],
        "raster_band": 1,
        "prefix": "",
    }
    qgis_zonal_stats(
        zones_path="/tmp/zones.geojson",
        raster_path="/tmp/lulc.tif",
        output_path=str(tmp_path / "z.csv"),
    )
    params = fake_executor.calls[0][1]
    assert params["stats"] == ["count", "sum", "mean"]


def test_zonal_stats_failed_raises(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(_params):
        raise ExecutorError("zonal_stats", "ZONAL_FAILED: zones layer must be polygon")

    fake_executor.responses["zonal_stats"] = fail
    with pytest.raises(ZonalStatsError, match="polygon"):
        qgis_zonal_stats(
            zones_path="/tmp/points.geojson",
            raster_path="/tmp/lulc.tif",
            output_path=str(tmp_path / "z.gpkg"),
        )


def test_zonal_stats_missing_raster_raises(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(_params):
        raise ExecutorError("zonal_stats", "LAYER_NOT_FOUND: /tmp/missing.tif")

    fake_executor.responses["zonal_stats"] = fail
    with pytest.raises(LayerNotFoundError):
        qgis_zonal_stats(
            zones_path="/tmp/zones.geojson",
            raster_path="/tmp/missing.tif",
            output_path=str(tmp_path / "z.gpkg"),
        )


def test_zonal_stats_error_has_next():
    err = ZonalStatsError("ZONAL_FAILED: raster invalid")
    assert "Next:" in str(err)
    assert "qgis_zonal_stats" in str(err)


def test_histogram_writes_built_up_and_skips_absent_classes():
    # JAXA built-up is class 2. Class 12 (solar) does not occur in these wards.
    # Ward 1 has water (1) and built-up. Ward 2 has only built-up, so its water
    # count is 0 and there is still no solar column.
    names, rows = histogram_columns(
        [{1: 3, 2: 10}, {2: 4}],
        "lulc_",
    )
    assert names == ["lulc_1", "lulc_2"]
    assert rows == [[3, 10], [0, 4]]
    assert all(isinstance(value, int) and not isinstance(value, bool) for row in rows for value in row)
    assert "lulc_12" not in names
    assert "lulc_mean" not in names
    assert "mean" not in names


def test_histogram_sorts_class_ids_and_drops_nodata():
    names, rows = histogram_columns(
        [{10: 1, 2: 5, 0: 9}],
        "lulc_",
        nodata=0,
        has_nodata=True,
    )
    assert names == ["lulc_2", "lulc_10"]
    assert rows == [[5, 1]]


def test_histogram_refuses_a_continuous_band_and_names_mean():
    with pytest.raises(ValueError, match="mean") as exc:
        histogram_columns([{1.5: 4, 2: 1}], "lulc_")
    assert "1.5" in str(exc.value)


def test_mean_stays_a_numeric_stat_and_majority_is_not_a_class_column():
    assert normalize_stats(None) == ["count", "sum", "mean"]
    cleaned = normalize_stats([" MEAN ", "majority", "histogram", "mean"])
    assert cleaned == ["mean", "majority", "histogram"]
    assert [name for name in cleaned if name != "histogram"] == ["mean", "majority"]
    with pytest.raises(ValueError, match="mode"):
        normalize_stats(["mode"])
    assert "mean" in STATS
    assert "majority" in STATS
    assert "histogram" in STATS
    assert _ZONAL_STATS == STATS


def _literal_stats() -> tuple[str, ...]:
    hint = typing.get_type_hints(qgis_zonal_stats)["stats"]
    list_type = next(arg for arg in typing.get_args(hint) if arg is not type(None))
    literal = typing.get_args(list_type)[0]
    return typing.get_args(literal)


def test_tool_accepts_majority_and_histogram_and_keeps_mean():
    allowed = _literal_stats()
    assert allowed == STATS
    assert "mean" in allowed


def test_majority_and_histogram_dispatch_in_order(fake_executor, tmp_path):
    out = tmp_path / "lulc.gpkg"
    fake_executor.responses["zonal_stats"] = {
        "output_path": str(out),
        "n_zones": 23,
        "n_with_value": 23,
        "stats": ["mean", "majority", "histogram"],
        "fields_added": ["lulc_mean", "lulc_majority", "lulc_1", "lulc_2"],
        "raster_band": 1,
        "prefix": "lulc_",
    }
    result = qgis_zonal_stats(
        zones_path="/tmp/zones_tokyo23.gpkg",
        raster_path="/tmp/2024JPN.tif",
        output_path=str(out),
        stats=[" MEAN ", "majority", "histogram", "mean"],
        prefix="lulc_",
    )
    params = fake_executor.calls[0][1]
    assert params["stats"] == ["mean", "majority", "histogram"]
    assert result.fields_added == ["lulc_mean", "lulc_majority", "lulc_1", "lulc_2"]
    assert all(isinstance(name, str) for name in result.fields_added)


def test_unknown_stat_is_refused_before_dispatch(fake_executor, tmp_path):
    with pytest.raises(InvalidArgumentError, match="mode") as exc:
        qgis_zonal_stats(
            zones_path="/tmp/zones.geojson",
            raster_path="/tmp/lulc.tif",
            output_path=str(tmp_path / "z.gpkg"),
            stats=["mean", "mode"],
        )
    assert "Next:" in str(exc.value)
    assert "histogram" in str(exc.value)
    assert "mean" in str(exc.value)
    assert fake_executor.calls == []


def _function_source(name: str) -> str:
    source = (REPO / "qgis_mcp_workflows_plugin" / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(name)


def test_plugin_keeps_mean_adds_majority_and_counts_classes_without_iface():
    flags = _function_source("_zonal_stat_flags")
    assert '"mean"' in flags
    assert '"majority"' in flags
    assert '"histogram"' not in flags
    handler = _function_source("zonal_stats")
    assert "histogram_columns" in handler
    assert "self.iface" not in handler

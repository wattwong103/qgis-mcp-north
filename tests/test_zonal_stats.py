"""qgis_zonal_stats — FakeExecutor, no QGIS required."""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows.errors import LayerNotFoundError, ZonalStatsError
from qgis_mcp_workflows.server import qgis_zonal_stats


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

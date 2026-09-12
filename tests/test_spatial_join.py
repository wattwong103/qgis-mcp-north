"""qgis_spatial_join — FakeExecutor, no QGIS required."""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows.errors import LayerNotFoundError, SpatialJoinEmptyError
from qgis_mcp_workflows.server import qgis_spatial_join


def test_spatial_join_dispatches(fake_executor, tmp_path):
    out = tmp_path / "joined.gpkg"
    fake_executor.responses["spatial_join"] = {
        "output_path": str(out),
        "n_target": 4,
        "n_join": 10,
        "n_matched": 3,
        "n_unmatched": 1,
        "n_output_features": 3,
        "predicate": "intersects",
        "method": "one_to_one",
        "joined_fields": ["zone_id"],
    }
    result = qgis_spatial_join(
        target_path="/tmp/points.geojson",
        join_path="/tmp/zones.geojson",
        output_path=str(out),
        join_fields=["zone_id"],
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "spatial_join"
    assert os.path.isabs(params["target_path"])
    assert os.path.isabs(params["join_path"])
    assert params["predicate"] == "intersects"
    assert params["method"] == "one_to_one"
    assert params["join_fields"] == ["zone_id"]
    assert result.n_matched == 3
    assert result.n_unmatched == 1
    assert result.joined_fields == ["zone_id"]
    assert os.path.isabs(result.output_path)


def test_spatial_join_empty_raises(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(_params):
        raise ExecutorError(
            "spatial_join",
            "SPATIAL_JOIN_EMPTY: predicate 'intersects' matched 0 of 4 target features against 10 join features.",
        )

    fake_executor.responses["spatial_join"] = fail
    with pytest.raises(SpatialJoinEmptyError, match="crs"):
        qgis_spatial_join(
            target_path="/tmp/a.geojson",
            join_path="/tmp/b.geojson",
            output_path=str(tmp_path / "out.gpkg"),
        )


def test_spatial_join_missing_layer_raises(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(_params):
        raise ExecutorError("spatial_join", "LAYER_NOT_FOUND: /tmp/missing.geojson")

    fake_executor.responses["spatial_join"] = fail
    with pytest.raises(LayerNotFoundError):
        qgis_spatial_join(
            target_path="/tmp/missing.geojson",
            join_path="/tmp/zones.geojson",
            output_path=str(tmp_path / "out.gpkg"),
        )


def test_spatial_join_empty_error_has_next():
    err = SpatialJoinEmptyError("SPATIAL_JOIN_EMPTY: matched 0")
    assert "Next:" in str(err)
    assert "qgis_layer_inspect" in str(err)

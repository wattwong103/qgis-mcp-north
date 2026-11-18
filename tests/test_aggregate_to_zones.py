"""qgis_aggregate_to_zones — count and sum every match, no QGIS required.

qgis_spatial_join copies the first match. These tests pin the reduction that
counts or sums every intersecting feature, and the server checks around it.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from qgis_mcp_workflows.errors import (
    AggregateEmptyError,
    FieldNotFoundError,
    InvalidArgumentError,
    LayerNotFoundError,
)
from qgis_mcp_workflows_plugin.aggregate import (
    collect_groups,
    count_with_value,
    default_field_name,
    empty_detail,
    geometries_ok,
    geometry_detail,
    matched_any,
    normalize_statistic,
    reduce_matches,
    unique_field_name,
)

REPO = Path(__file__).resolve().parent.parent


def test_sum_adds_every_match_not_the_first():
    # Three pickups in one ward. one_to_one spatial join would keep 1.
    assert reduce_matches([1, 2, 4], "sum") == 7
    assert reduce_matches([1, 2, 4], "count") == 3
    assert reduce_matches([1, 2, 4], "mean") == pytest.approx(7 / 3)


def test_a_feature_counts_once_per_polygon_it_intersects():
    # Point 0 sits on the boundary of both wards. Point 1 is only in ward 0.
    # Point 2 intersects nothing, so it is absent from pairs.
    values = [10, 5, 99]
    pairs = [(0, 0), (0, 1), (1, 0), (0, 0)]
    groups = collect_groups(2, values, pairs)
    assert groups == [[10, 5], [10]]
    assert [reduce_matches(group, "count") for group in groups] == [2, 1]
    assert [reduce_matches(group, "sum") for group in groups] == [15.0, 10.0]
    assert matched_any(groups) is True


def test_no_intersecting_feature_is_empty_and_polygons_stay_zero():
    groups = collect_groups(3, [1, 2], [])
    assert matched_any(groups) is False
    assert [reduce_matches(group, "count") for group in groups] == [0, 0, 0]
    assert [reduce_matches(group, "sum") for group in groups] == [0.0, 0.0, 0.0]
    assert [reduce_matches(group, "mean") for group in groups] == [None, None, None]
    assert count_with_value([0, 0.0, None]) == 2


def test_nulls_and_text_are_skipped_for_sum_and_mean_but_count():
    values = [None, "nope", "", True, "3.5", 1, float("nan")]
    assert reduce_matches(values, "count") == 7
    assert reduce_matches(values, "sum") == pytest.approx(4.5)
    assert reduce_matches(values, "mean") == pytest.approx(2.25)
    assert reduce_matches([None, "x"], "sum") == 0.0
    assert reduce_matches([None, "x"], "mean") is None


def test_field_name_defaults_and_avoids_existing_names():
    assert default_field_name("count") == "count"
    assert default_field_name("sum") == "sum"
    assert default_field_name("mean") == "mean"
    assert unique_field_name("count", []) == "count"
    assert unique_field_name("count", ["Count"]) == "count_2"
    assert unique_field_name("count", ["count", "count_2"]) == "count_3"


def test_empty_and_geometry_messages_name_both_layers():
    detail = empty_detail(4, "point", "EPSG:4326", 23, "polygon", "EPSG:3857")
    assert "EPSG:4326" in detail and "EPSG:3857" in detail
    assert "point" in detail and "polygon" in detail
    assert detail.startswith("AGGREGATE_EMPTY:")
    geom = geometry_detail("line", "EPSG:4326", "polygon", "EPSG:6668")
    assert geom.startswith("AGGREGATE_GEOMETRY:")
    assert "line" in geom and "polygon" in geom
    assert "EPSG:4326" in geom and "EPSG:6668" in geom
    assert geometries_ok("polygon", "point") is True
    assert geometries_ok("polygon", "line") is True
    assert geometries_ok("polygon", "polygon") is False
    assert geometries_ok("line", "point") is False


def test_normalize_statistic():
    assert normalize_statistic(" SUM ") == "sum"
    assert normalize_statistic("Count") == "count"
    assert normalize_statistic("mean") == "mean"
    assert normalize_statistic("median") is None
    assert normalize_statistic(None) == "count"


def test_count_dispatches_without_a_value_field(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_aggregate_to_zones

    out = tmp_path / "counts.gpkg"
    fake_executor.responses["aggregate_to_zones"] = {
        "output_path": str(out),
        "n_target": 23,
        "n_join": 100,
        "n_with_value": 23,
        "field_name": "count",
        "statistic": "count",
    }
    result = qgis_aggregate_to_zones(
        target_path="/tmp/wards.gpkg",
        join_path="/tmp/pickups.gpkg",
        output_path=str(out),
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "aggregate_to_zones"
    assert os.path.isabs(params["target_path"])
    assert os.path.isabs(params["join_path"])
    assert params["statistic"] == "count"
    assert "value_field" not in params
    assert "field_name" not in params
    assert result.n_target == 23
    assert result.n_join == 100
    assert result.n_with_value == 23
    assert result.field_name == "count"
    assert result.statistic == "count"
    assert os.path.isabs(result.output_path)


def test_sum_dispatches_the_value_field(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_aggregate_to_zones

    out = tmp_path / "sums.geojson"
    fake_executor.responses["aggregate_to_zones"] = {
        "output_path": str(out),
        "n_target": 2,
        "n_join": 3,
        "n_with_value": 2,
        "field_name": "n_trips",
        "statistic": "sum",
    }
    result = qgis_aggregate_to_zones(
        target_path="wards.gpkg",
        join_path="trips.gpkg",
        output_path=str(out),
        statistic="sum",
        value_field="trips",
        field_name="n_trips",
    )
    params = fake_executor.calls[0][1]
    assert params["statistic"] == "sum"
    assert params["value_field"] == "trips"
    assert params["field_name"] == "n_trips"
    assert result.field_name == "n_trips"
    assert os.path.isabs(params["output_path"])


def test_sum_and_mean_require_a_value_field(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_aggregate_to_zones

    out = str(tmp_path / "out.gpkg")
    for statistic in ("sum", "mean"):
        with pytest.raises(InvalidArgumentError, match="value_field") as exc:
            qgis_aggregate_to_zones(
                target_path="/tmp/wards.gpkg",
                join_path="/tmp/pickups.gpkg",
                output_path=out,
                statistic=statistic,
            )
        assert "Next:" in str(exc.value)
    assert fake_executor.calls == []


def test_unknown_statistic_and_output_are_refused(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_aggregate_to_zones

    with pytest.raises(InvalidArgumentError, match="statistic") as exc:
        qgis_aggregate_to_zones(
            target_path="/tmp/wards.gpkg",
            join_path="/tmp/pickups.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            statistic="median",
        )
    assert "Next:" in str(exc.value)
    with pytest.raises(InvalidArgumentError, match="output_path"):
        qgis_aggregate_to_zones(
            target_path="/tmp/wards.gpkg",
            join_path="/tmp/pickups.gpkg",
            output_path=str(tmp_path / "out.csv"),
            statistic="count",
        )
    assert fake_executor.calls == []


def test_empty_error_keeps_crs_and_geometry(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError
    from qgis_mcp_workflows.server import qgis_aggregate_to_zones

    detail = empty_detail(4, "point", "EPSG:4326", 23, "polygon", "EPSG:3857")

    def fail(_params):
        raise ExecutorError("aggregate_to_zones", detail)

    fake_executor.responses["aggregate_to_zones"] = fail
    with pytest.raises(AggregateEmptyError, match="EPSG:4326") as exc:
        qgis_aggregate_to_zones(
            target_path="/tmp/wards.gpkg",
            join_path="/tmp/pickups.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
        )
    text = str(exc.value)
    assert "EPSG:3857" in text
    assert "point" in text and "polygon" in text
    assert "Next:" in text
    assert "qgis_layer_inspect" in text


def test_missing_layer_and_field_and_geometry_map_to_typed_errors(fake_executor, tmp_path):
    from qgis_mcp_workflows.errors import ExecutorError
    from qgis_mcp_workflows.server import qgis_aggregate_to_zones

    out = str(tmp_path / "out.gpkg")

    def missing(_params):
        raise ExecutorError("aggregate_to_zones", "LAYER_NOT_FOUND: /tmp/wards.gpkg")

    fake_executor.responses["aggregate_to_zones"] = missing
    with pytest.raises(LayerNotFoundError):
        qgis_aggregate_to_zones(
            target_path="/tmp/wards.gpkg",
            join_path="/tmp/pickups.gpkg",
            output_path=out,
        )

    def no_field(_params):
        raise ExecutorError("aggregate_to_zones", "FIELD_NOT_FOUND: trips. Available: ['id']")

    fake_executor.responses["aggregate_to_zones"] = no_field
    with pytest.raises(FieldNotFoundError, match="trips"):
        qgis_aggregate_to_zones(
            target_path="/tmp/wards.gpkg",
            join_path="/tmp/pickups.gpkg",
            output_path=out,
            statistic="sum",
            value_field="trips",
        )

    def bad_geom(_params):
        raise ExecutorError(
            "aggregate_to_zones",
            geometry_detail("line", "EPSG:4326", "polygon", "EPSG:4326"),
        )

    fake_executor.responses["aggregate_to_zones"] = bad_geom
    with pytest.raises(InvalidArgumentError, match="polygon") as exc:
        qgis_aggregate_to_zones(
            target_path="/tmp/lines.gpkg",
            join_path="/tmp/wards.gpkg",
            output_path=out,
        )
    assert "Next:" in str(exc.value)


def test_result_is_a_recorded_file():
    from qgis_mcp_workflows.provenance import WRITTEN_BY_MODEL
    from qgis_mcp_workflows.server import AggregateToZonesResult

    out = "/tmp/counts.gpkg"
    result = AggregateToZonesResult(
        output_path=out,
        n_target=2,
        n_join=3,
        n_with_value=2,
        field_name="count",
        statistic="count",
    )
    assert WRITTEN_BY_MODEL["AggregateToZonesResult"](result) == [out]


def test_compound_mode_does_not_grow_an_aggregate_kind():
    text = (REPO / "src" / "qgis_mcp_workflows" / "compound.py").read_text(encoding="utf-8")
    assert "qgis_aggregate_to_zones" not in text
    assert "aggregate_to_zones" not in text


def test_plugin_handler_reduces_every_match_and_does_not_touch_iface():
    source = (REPO / "qgis_mcp_workflows_plugin" / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "aggregate_to_zones":
            method = node
            break
    assert method is not None
    body = ast.get_source_segment(source, method)
    assert "reduce_matches" in body
    assert "collect_groups" in body
    assert "one_to_one" not in body
    assert "matches[:1]" not in body
    assert "self.iface" not in body
    table = source[source.find("handlers = {") : source.find("handler = handlers.get")]
    assert '"aggregate_to_zones"' in table

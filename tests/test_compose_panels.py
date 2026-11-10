"""Tests for qgis_compose_panels — FakeExecutor, no QGIS (TASK-22).

The server reads the CSV, decides every colour scale and its ticks, and sends
the plugin finished panels and groups; these tests lock that half down. The
drawing half is covered live in tests/test_panels_live.py.
"""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows import provenance
from qgis_mcp_workflows.errors import (
    ExecutorError,
    FieldNotFoundError,
    InvalidArgumentError,
    JoinError,
)
from qgis_mcp_workflows.panels import group_scales, ticks
from qgis_mcp_workflows.server import qgis_compose_panels

COLUMNS = {"a": [0.1, 0.2], "b": [0.3, 0.4], "c": [1.0, 2.0], "d": [3.0, 8.0]}


def _layer_keys(*keys):
    """A get_field_values reply: the zones layer's join keys as text."""
    return {"values": sorted(keys)}


@pytest.fixture(autouse=True)
def zones_layer(fake_executor):
    """Every test's zones layer carries 13101 and 13102 unless it says otherwise."""
    fake_executor.responses["get_field_values"] = _layer_keys("13101", "13102")
    return fake_executor


def _rendered(fake_executor):
    return [c for c, _ in fake_executor.calls if c == "compose_panels"]


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "shares.csv"
    lines = ["zone_id,a,b,c,d"]
    for i, key in enumerate(("13101", "13102")):
        lines.append(",".join([key] + [str(COLUMNS[c][i]) for c in "abcd"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _plugin_result(params):
    n = len(params["panels"])
    return {
        "output_path": params["output_path"], "format": "png", "n_panels": n, "n_layers": n,
        "items": [f"panel:{i}" for i in range(n)], "page_size_mm": [160.0, 140.0], "fonts_missing": [],
        "n_matched": 2, "n_unmatched": 0, "panel_boxes_mm": [[0.0, 0.0, 10.0, 10.0]] * n,
        "colorbar_boxes_mm": [[12.0, 0.0, 3.5, 10.0]] * len(params["groups"]),
    }


def _call(csv_path, tmp_path, **overrides):
    args = {"zones_path": str(tmp_path / "zones.gpkg"), "value_csv": csv_path, "value_fields": list("abcd"),
            "rows": 2, "cols": 2, "output_path": str(tmp_path / "fig.png")}
    args.update(overrides)
    return qgis_compose_panels(**args)


def test_dispatches_one_panel_per_column_with_row_scales(fake_executor, csv_path, tmp_path):
    fake_executor.responses["compose_panels"] = _plugin_result
    _call(csv_path, tmp_path)
    command, params = fake_executor.calls[-1]
    assert command == "compose_panels"
    for i, field in enumerate("abcd"):
        assert params["panels"][i]["values"] == dict(zip(("13101", "13102"), COLUMNS[field], strict=True))
    assert [p["group"] for p in params["panels"]] == [0, 0, 1, 1]
    scales = group_scales([COLUMNS[c] for c in "abcd"], 2, 2, "row", None, 100.0)
    assert [g["ticks"] for g in params["groups"]] == [[list(t) for t in ticks(s, 5, ".3g")] for s in scales]
    assert [g["palette"] for g in params["groups"]] == ["YlOrRd", "YlOrRd"]
    assert params["colorbar_position"] == "right"


def test_column_scales_put_one_bar_under_each_column(fake_executor, csv_path, tmp_path):
    fake_executor.responses["compose_panels"] = _plugin_result
    _call(csv_path, tmp_path, share_scale="column", palettes=["Reds", "Blues"], colorbar_labels=["Share"])
    params = fake_executor.calls[-1][1]
    assert [p["group"] for p in params["panels"]] == [0, 1, 0, 1]
    assert [g["palette"] for g in params["groups"]] == ["Reds", "Blues"]
    assert [g["label"] for g in params["groups"]] == ["Share", "Share"]
    assert params["colorbar_position"] == "bottom"


@pytest.mark.parametrize("overrides", [
    {"value_fields": list("abc")},
    {"rows": 0, "cols": 2, "value_fields": []},
    {"row_labels": ["Origins"]},
    {"column_titles": ["Simulated", "Observed", "Extra"]},
    {"palettes": ["Reds", "Blues", "Greens"]},
    {"colorbar_labels": ["a", "b", "c"]},
    {"tick_format": "q"},
    {"tick_format": "{0}"},
    {"n_ticks": 1},
    {"cap_percentile": 0},
    {"cap_percentile": 101},
    {"output_path": "x.jpg"},
    {"extent": [139.0, 35.0, 139.1]},
], ids=lambda o: ",".join(o))
def test_argument_errors_are_refused(fake_executor, csv_path, tmp_path, overrides):
    with pytest.raises(InvalidArgumentError, match="Next:"):
        _call(csv_path, tmp_path, **overrides)
    assert fake_executor.calls == []


def test_a_vmin_above_the_data_is_refused(fake_executor, csv_path, tmp_path):
    # Row 0 tops out at 0.4: a scale from 0.5 down to 0.4 would run backwards.
    with pytest.raises(InvalidArgumentError, match=r"vmin.*row 0.*Next:"):
        _call(csv_path, tmp_path, vmin=0.5)
    assert _rendered(fake_executor) == []


def test_a_missing_csv_column_is_named(fake_executor, csv_path, tmp_path):
    with pytest.raises(FieldNotFoundError, match="'e'"):
        _call(csv_path, tmp_path, value_fields=list("abce"))


def test_blank_and_nan_cells_stay_out_of_the_scale(fake_executor, tmp_path):
    path = tmp_path / "gaps.csv"
    path.write_text("zone_id,v\n1,0.5\n2,\n3,nan\n4,x\n5,inf\n6,2.0\n", encoding="utf-8")
    fake_executor.responses["get_field_values"] = _layer_keys(*"123456")
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(str(path), tmp_path, value_fields=["v"], rows=1, cols=1)
    params = fake_executor.calls[-1][1]
    assert params["panels"][0]["values"] == {"1": 0.5, "6": 2.0}
    assert (result.groups[0].vmin, result.groups[0].vmax) == (0.0, 2.0)


def test_a_csv_that_is_not_utf8_is_refused(fake_executor, tmp_path):
    # Japanese CSVs are often CP932; the open() error would reach the client untyped.
    path = tmp_path / "cp932.csv"
    path.write_bytes("zone_id,name,v\n13101,千代田区,1.0\n".encode("cp932"))
    with pytest.raises(InvalidArgumentError, match=r"UTF-8.*Next:"):
        _call(str(path), tmp_path, value_fields=["v"], rows=1, cols=1)
    assert fake_executor.calls == []


def test_repeated_keys_are_refused(fake_executor, tmp_path):
    # One row per OD pair would otherwise keep each zone's last row and draw a wrong map.
    path = tmp_path / "long.csv"
    path.write_text("zone_id,v\n13101,1.0\n13102,2.0\n13101,3.0\n", encoding="utf-8")
    with pytest.raises(InvalidArgumentError, match=r"13101.*one row per zone.*Next:"):
        _call(str(path), tmp_path, value_fields=["v"], rows=1, cols=1)
    assert fake_executor.calls == []


def test_a_single_panel_works(fake_executor, csv_path, tmp_path):
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(csv_path, tmp_path, value_fields=["a"], rows=1, cols=1, share_scale="all")
    params = fake_executor.calls[-1][1]
    assert len(params["panels"]) == 1 and len(params["groups"]) == 1
    assert result.n_panels == 1 and result.groups[0].panels == [0]


def test_the_result_reports_scales_and_is_a_written_file(fake_executor, csv_path, tmp_path):
    def written(params):
        with open(params["output_path"], "wb") as f:
            f.write(b"\x89PNG")
        return _plugin_result(params)

    fake_executor.responses["compose_panels"] = written
    result = _call(csv_path, tmp_path, cap_percentile=50)
    assert result.groups[1].capped and result.groups[1].data_max == 8.0
    assert result.groups[1].vmax == pytest.approx(2.5)
    assert provenance.written_files(result) == [provenance.normalise(result.output_path)]
    assert (result.n_csv_keys_unmatched, result.csv_keys_unmatched_sample) == (0, [])


def _one_panel_csv(tmp_path, body):
    path = tmp_path / "one.csv"
    path.write_text("zone_id,v\n" + body, encoding="utf-8")
    return str(path)


def test_csv_keys_the_layer_lacks_stay_out_of_the_scale(fake_executor, tmp_path):
    # A total row and a negative key from another zone system: neither is drawn,
    # so neither may move vmin, vmax or data_max (review of PR #38).
    csv = _one_panel_csv(tmp_path, "13101,0.2\n13102,0.4\nTOTAL,1.0\n99999,-0.5\n88888,\n")
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(csv, tmp_path, value_fields=["v"], rows=1, cols=1)
    assert fake_executor.calls[0] == ("get_field_values",
                                      {"path": os.path.abspath(str(tmp_path / "zones.gpkg")), "field": "zone_id"})
    assert fake_executor.calls[-1][1]["panels"][0]["values"] == {"13101": 0.2, "13102": 0.4}
    group = result.groups[0]
    assert (group.vmin, group.vmax, group.data_max, group.capped) == (0.0, 0.4, 0.4, False)
    assert result.n_csv_keys_unmatched == 3
    assert result.csv_keys_unmatched_sample == ["88888", "99999", "TOTAL"]


def test_the_cap_percentile_is_taken_over_drawn_zones_only(fake_executor, tmp_path):
    fake_executor.responses["get_field_values"] = _layer_keys("1", "2", "3")
    csv = _one_panel_csv(tmp_path, "1,1.0\n2,2.0\n3,3.0\nTOTAL,100.0\n")
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(csv, tmp_path, value_fields=["v"], rows=1, cols=1, cap_percentile=50)
    assert result.groups[0].vmax == pytest.approx(2.0)  # 2.5 if TOTAL counted
    assert result.groups[0].data_max == 3.0


def test_the_unmatched_sample_holds_at_most_five_keys(fake_executor, tmp_path):
    csv = _one_panel_csv(tmp_path, "13101,0.2\n" + "".join(f"9{i},1.0\n" for i in range(7)))
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(csv, tmp_path, value_fields=["v"], rows=1, cols=1)
    assert result.n_csv_keys_unmatched == 7
    assert result.csv_keys_unmatched_sample == ["90", "91", "92", "93", "94"]


def test_a_csv_with_no_key_on_the_layer_is_refused_before_rendering(fake_executor, tmp_path):
    fake_executor.responses["get_field_values"] = _layer_keys("13101", "13102")
    csv = _one_panel_csv(tmp_path, "13101.0,0.2\n13102.0,0.4\n")
    with pytest.raises(JoinError, match=r"JOIN_NO_MATCH on zone_id.*13101\.0.*13101'.*Next:"):
        _call(csv, tmp_path, value_fields=["v"], rows=1, cols=1)
    assert _rendered(fake_executor) == []


def test_a_plugin_without_get_field_values_says_to_restart_qgis(fake_executor, csv_path, tmp_path):
    def old_plugin(params):
        raise ExecutorError("get_field_values", "Unknown command type: get_field_values")

    fake_executor.responses["get_field_values"] = old_plugin
    with pytest.raises(ExecutorError, match=r"older than this server.*Next: restart QGIS"):
        _call(csv_path, tmp_path)
    assert _rendered(fake_executor) == []


def test_other_plugin_errors_from_get_field_values_pass_through(fake_executor, csv_path, tmp_path):
    def missing_field(params):
        raise ExecutorError("get_field_values", "Field 'zone_id' not found on the layer. Next: pass join_field")

    fake_executor.responses["get_field_values"] = missing_field
    with pytest.raises(ExecutorError, match=r"Field 'zone_id' not found"):
        _call(csv_path, tmp_path)

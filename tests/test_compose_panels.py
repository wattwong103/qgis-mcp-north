"""Tests for qgis_compose_panels — FakeExecutor, no QGIS (TASK-22).

The server reads the CSV, decides every colour scale and its ticks, and sends
the plugin finished panels and groups; these tests lock that half down. The
drawing half is covered live in tests/test_panels_live.py.
"""

from __future__ import annotations

import pytest

from qgis_mcp_workflows import provenance
from qgis_mcp_workflows.errors import FieldNotFoundError, InvalidArgumentError
from qgis_mcp_workflows.panels import group_scales, ticks
from qgis_mcp_workflows.server import qgis_compose_panels

COLUMNS = {"a": [0.1, 0.2], "b": [0.3, 0.4], "c": [1.0, 2.0], "d": [3.0, 8.0]}


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
    command, params = fake_executor.calls[0]
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
    params = fake_executor.calls[0][1]
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
    assert fake_executor.calls == []


def test_a_missing_csv_column_is_named(fake_executor, csv_path, tmp_path):
    with pytest.raises(FieldNotFoundError, match="'e'"):
        _call(csv_path, tmp_path, value_fields=list("abce"))


def test_blank_and_nan_cells_stay_out_of_the_scale(fake_executor, tmp_path):
    path = tmp_path / "gaps.csv"
    path.write_text("zone_id,v\n1,0.5\n2,\n3,nan\n4,x\n5,inf\n6,2.0\n", encoding="utf-8")
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(str(path), tmp_path, value_fields=["v"], rows=1, cols=1)
    params = fake_executor.calls[0][1]
    assert params["panels"][0]["values"] == {"1": 0.5, "6": 2.0}
    assert (result.groups[0].vmin, result.groups[0].vmax) == (0.0, 2.0)


def test_a_single_panel_works(fake_executor, csv_path, tmp_path):
    fake_executor.responses["compose_panels"] = _plugin_result
    result = _call(csv_path, tmp_path, value_fields=["a"], rows=1, cols=1, share_scale="all")
    params = fake_executor.calls[0][1]
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

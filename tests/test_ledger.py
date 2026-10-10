"""The session state ledger (spec §6). Pure Python, no MCP."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from qgis_mcp_workflows import ledger


@pytest.fixture(autouse=True)
def clean():
    ledger.reset()
    yield
    ledger.reset()


def _entry(seq, tool, **arguments):
    return {"seq": seq, "call": {"tool": tool, "module": "server", "arguments": arguments},
            "inputs": [], "layer_id": None}


def _load(seq, layer="L1"):
    ledger.update("qgis_load_layer", {"path": "/z.gpkg"}, _entry(seq, "qgis_load_layer", path="/z.gpkg"),
                  SimpleNamespace(layer_id=layer))


def _style(seq, field, layer="L1"):
    args = {"layer_id": layer, "field": field}
    ledger.update("qgis_style_categorized", args, _entry(seq, "qgis_style_categorized", **args), None)


def test_kinds_cover_full_and_compound_names():
    assert ledger.kind("qgis_load_layer", {}) == "load"
    assert ledger.kind("qgis_inspect", {"kind": "layer", "register": True}) == "load"
    assert ledger.kind("qgis_inspect", {"kind": "layer", "register": False}) is None
    assert ledger.kind("qgis_style", {"type": "graduated"}) == "style"
    assert ledger.kind("qgis_inspect", {"kind": "project", "path": "/p.qgz"}) == "project"
    assert ledger.kind("qgis_export", {"kind": "batch"}) == "export"
    assert ledger.kind("qgis_export", {"kind": "pptx"}) is None
    assert ledger.kind("qgis_render", {"mode": "map"}) == "map"
    assert ledger.kind("qgis_render_choropleth", {}) is None


def test_style_a_b_a_keeps_every_call_in_order():
    _load(1)
    for seq, field in ((2, "a"), (3, "b"), (4, "a")):
        _style(seq, field)
    deps, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert [d["seq"] for d in deps] == [1, 2, 3, 4]
    assert deps[0]["layer_id"] == "L1" and deps[-1]["call"]["arguments"]["field"] == "a"
    assert notes == []


def test_unknown_layer_without_a_project_is_noted():
    deps, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["Lx"]})
    assert deps == [] and notes == ["layer Lx was not loaded through MCP in this session"]


def test_export_of_the_loaded_project_is_state_reading():
    ledger.update("qgis_project_load", {"qgz_path": "/p.qgz"}, _entry(1, "qgis_project_load", qgz_path="/p.qgz"),
                  None)
    _style(2, "a", layer="P1")
    deps, _ = ledger.snapshot("qgis_export_layout", {"qgz_path": "/p.qgz", "layout_name": "A4"})
    assert [d["call"]["tool"] for d in deps] == ["qgis_project_load", "qgis_style_categorized"]


def test_export_of_another_project_clears_the_ledger():
    ledger.update("qgis_project_load", {"qgz_path": "/p.qgz"}, _entry(1, "qgis_project_load", qgz_path="/p.qgz"),
                  None)
    _style(2, "a", layer="P1")
    ledger.update("qgis_export_layout", {"qgz_path": "/other.qgz"}, None, None)
    assert ledger.snapshot("qgis_export_layout", {"qgz_path": "/p.qgz"}) == ([], [])
    assert ledger.snapshot("qgis_render_map", {"layer_ids": ["P1"]})[0] == []


def test_failed_eval_is_not_recorded_and_successful_one_is():
    ledger.update("qgis_eval", {"code": "boom"}, _entry(1, "qgis_eval", code="boom"),
                  SimpleNamespace(exception="Traceback ..."))
    assert ledger.snapshot("qgis_render_choropleth", {}) == ([], [])
    ledger.update("qgis_eval", {"code": "x = 1"}, _entry(2, "qgis_eval", code="x = 1"),
                  SimpleNamespace(exception=None))
    assert ledger.snapshot("qgis_render_choropleth", {}) == ([], [ledger.EVAL_NOTE])
    _load(3)
    deps, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert [d["call"]["tool"] for d in deps] == ["qgis_eval", "qgis_load_layer"]
    assert notes == [ledger.EVAL_NOTE]


def test_eval_naming_a_layer_or_absolute_path_is_flagged():
    _load(1)
    ledger.update("qgis_eval", {"code": "lyr = QgsProject.instance().mapLayer('L1')"},
                  _entry(2, "qgis_eval", code="lyr = QgsProject.instance().mapLayer('L1')"),
                  SimpleNamespace(exception=None))
    _, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert ledger.EVAL_LITERAL_NOTE in notes


def test_snapshot_is_a_copy():
    _load(1)
    deps, _ = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    deps[0]["call"]["arguments"]["path"] = "changed"
    again, _ = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert again[0]["call"]["arguments"]["path"] == "/z.gpkg"

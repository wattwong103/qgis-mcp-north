"""Replay of state-reading figures (TASK-15): validation, eval trust, script shape. No QGIS needed."""

from __future__ import annotations

import hashlib
import json

import pytest

from qgis_mcp_workflows import provenance, session_export


def _dep(seq, tool, module="server", layer_id=None, inputs=(), **arguments):
    return {"seq": seq, "call": {"tool": tool, "module": module, "arguments": arguments},
            "inputs": list(inputs), "layer_id": layer_id}


def _in(argument, path):
    return {"argument": argument, "path": path, "sha256": None, "bytes": None, "made_by": None}


def _load(seq=1, layer="L1", path="${DROPBOX_ROOT}/z.gpkg"):
    return _dep(seq, "qgis_load_layer", layer_id=layer, inputs=[_in("path", path)], path=path)


def _style(seq, field, layer="L1"):
    # `layer_id` here is the call's argument; `_dep(layer_id=...)` would set the entry's produced id.
    entry = _dep(seq, "qgis_style_categorized", field=field)
    entry["call"]["arguments"] = {"layer_id": layer, "field": field}
    return entry


def _map_sidecar(tmp_path, name="m.png", deps=(), layer_ids=("L1",), seq=10, session="s1", figure=None):
    fig = tmp_path / name
    fig.write_bytes(b"png")
    figure = figure or str(fig)
    record = {
        "schema": provenance.SCHEMA, "figure": figure, "figure_sha256": hashlib.sha256(b"png").hexdigest(),
        "session_id": session, "seq": seq,
        "call": {"tool": "qgis_render_map", "module": "server",
                 "arguments": {"layer_ids": list(layer_ids), "output_png": figure}},
        "depends_on": list(deps), "inputs": [], "implicit_inputs": [], "outputs": [figure],
        "unrecorded_state": [], "machine_specific": [], "remote": [],
    }
    (tmp_path / f"{name}.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    return fig


def _reasons(skipped):
    return sorted(s["reason"] for s in skipped)


def test_a_covered_render_map_is_replayable(tmp_path):
    fig = _map_sidecar(tmp_path, deps=[_load(), _style(2, "a")])
    records, skipped, _ = session_export.collect([str(fig)], None)
    assert len(records) == 1 and skipped == []


@pytest.mark.parametrize("deps", [
    [],                                                            # nothing recorded
    [_style(2, "a")],                                              # styled but never loaded
    [_load(layer="L2")],                                           # loaded a different layer
])
def test_an_uncovered_render_map_still_needs_session_state(tmp_path, deps):
    _, skipped, _ = session_export.collect([str(_map_sidecar(tmp_path, deps=deps))], None)
    assert _reasons(skipped) == ["needs session state"]


@pytest.mark.parametrize("bad", [
    _dep(1, "qgis_render_choropleth"),                                      # a figure tool is not a dependency
    _dep(1, "qgis_load_layer", layer_id="L1", path="${DROPBOX_ROOT}/z.gpkg"),  # path not in inputs
    _dep(1, "qgis_inspect", module="compound", kind="basemaps"),            # inspect that changes nothing
    _dep(1, "qgis_eval", code="x", secret_flag=True),                       # unknown argument
    {"seq": True, "call": {"tool": "qgis_eval", "module": "server", "arguments": {"code": "x"}}},
    {"seq": 1, "call": {"tool": "qgis_eval", "module": "server", "arguments": {"code": "x"}}, "inputs": 5},
])
def test_invalid_dependencies_skip_the_figure(tmp_path, bad):
    fig = _map_sidecar(tmp_path, deps=[_load(), bad])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["invalid depends_on"]


def test_a_network_path_in_a_dependency_is_never_opened(tmp_path):
    unc = "//evil/share/z.gpkg"
    fig = _map_sidecar(tmp_path, deps=[_load(path=unc)])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["network path"]


def test_an_atomic_figure_may_not_carry_dependencies(tmp_path):
    fig = tmp_path / "c.png"
    fig.write_bytes(b"png")
    record = {
        "schema": provenance.SCHEMA, "figure": str(fig), "figure_sha256": hashlib.sha256(b"png").hexdigest(),
        "session_id": "s1", "seq": 5,
        "call": {"tool": "qgis_render_choropleth", "module": "server",
                 "arguments": {"zones_path": "/z.gpkg", "value_field": "n", "output_png": str(fig)}},
        "depends_on": [_dep(1, "qgis_eval", code="x = 1")],
        "inputs": [_in("zones_path", "/z.gpkg")], "implicit_inputs": [], "outputs": [str(fig)],
        "unrecorded_state": [], "machine_specific": [], "remote": [],
    }
    (tmp_path / "c.png.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["invalid depends_on"]


def _with_eval(tmp_path, figure=None):
    fig = _map_sidecar(tmp_path, deps=[_dep(1, "qgis_eval", code="x = 1"), _load(2)], figure=figure)
    records, _, _ = session_export.collect([str(fig)], None)
    return records


def test_include_evals_false_drops_every_eval(tmp_path):
    records, warnings = session_export.keep_evals(_with_eval(tmp_path), include_evals=False, trust_foreign=True)
    assert [d["call"]["tool"] for d in records[0]["depends_on"]] == ["qgis_load_layer"]
    assert len(warnings) == 1 and "include_evals=False" in warnings[0]


def test_foreign_evals_need_trust(tmp_path):
    records = _with_eval(tmp_path)                         # figure recorded as an absolute path: foreign
    kept, warnings = session_export.keep_evals(records, include_evals=True, trust_foreign=False)
    assert [d["call"]["tool"] for d in kept[0]["depends_on"]] == ["qgis_load_layer"]
    assert "trust_foreign=True" in warnings[0]
    kept, warnings = session_export.keep_evals(records, include_evals=True, trust_foreign=True)
    assert [d["call"]["tool"] for d in kept[0]["depends_on"]] == ["qgis_eval", "qgis_load_layer"]
    assert warnings == []

"""Replay of state-reading figures (TASK-15): validation, eval trust, script shape. No QGIS needed."""

from __future__ import annotations

import ast
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


def _record(seq, deps=(), layer_ids=("L1",), session="s1", out=None):
    out = out or f"${{DROPBOX_ROOT}}/m{seq}.png"
    return {"session_id": session, "seq": seq, "figure": out, "figure_sha256": None,
            "call": {"tool": "qgis_render_map", "module": "server",
                     "arguments": {"layer_ids": list(layer_ids), "output_png": out}},
            "depends_on": list(deps), "inputs": [], "implicit_inputs": [], "outputs": [out],
            "unrecorded_state": [], "machine_specific": [], "remote": []}


def _script(records):
    steps = session_export.plan_steps(records)
    return session_export.build_script(steps, session_export.source_inputs(steps),
                                       session_export.outputs_of(steps), [], "d",
                                       session_export.evals_of(steps))


def _body(source):
    tree = ast.parse(source)
    [steps_fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "steps"]
    return [ast.unparse(s) for s in steps_fn.body]


def test_layer_ids_are_rebound_to_the_replayed_load():
    body = _body(_script([_record(10, deps=[_load(), _style(2, "a")])]))
    assert body[0] == "layer_1 = server.qgis_load_layer(path=src('${DROPBOX_ROOT}/z.gpkg')).layer_id"
    assert body[1] == "server.qgis_style_categorized(layer_id=layer_1, field='a')"
    assert body[2].startswith("server.qgis_render_map(layer_ids=[layer_1], output_png=out(")


def test_each_state_reading_figure_replays_its_own_snapshot():
    first = _record(10, deps=[_load(), _style(2, "a")])
    second = _record(20, deps=[_load(), _style(2, "a"), _style(11, "b")])
    body = _body(_script([second, first]))
    assert body.index("replay.reset_session()") > 0          # second block starts from a fresh QGIS
    assert [line.split("field=")[1] for line in body if "field=" in line] == ["'a')", "'a')", "'b')"]


def test_sessions_are_separated_by_a_reset():
    body = _body(_script([_record(10, deps=[_load()], session="a"),
                          _record(10, deps=[_load()], session="b", out="${DROPBOX_ROOT}/b.png")]))
    assert body.count("replay.reset_session()") == 1


def test_project_layers_keep_their_ids():
    project = _dep(1, "qgis_project_load", inputs=[_in("qgz_path", "${DROPBOX_ROOT}/p.qgz")],
                   qgz_path="${DROPBOX_ROOT}/p.qgz")
    body = _body(_script([_record(10, deps=[project, _style(2, "a", layer="P1")], layer_ids=("P1",))]))
    assert body[0] == "server.qgis_project_load(qgz_path=src('${DROPBOX_ROOT}/p.qgz'))"
    assert body[1] == "server.qgis_style_categorized(layer_id='P1', field='a')"
    assert "layer_ids=['P1']" in body[2]


def test_dependency_inputs_are_checked_before_the_run():
    steps = session_export.plan_steps([_record(10, deps=[_load()])])
    assert [i["path"] for i in session_export.source_inputs(steps)] == ["${DROPBOX_ROOT}/z.gpkg"]


def test_evals_are_listed_for_the_refusal():
    code = "import os" + chr(10) + "x = 1" + chr(10) + "y = 2" + chr(10) + "z = 3"
    steps = session_export.plan_steps([_record(10, deps=[_dep(1, "qgis_eval", code=code), _load(2)])])
    [item] = session_export.evals_of(steps)
    assert item["sha256"] == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert item["first_lines"] == ["import os", "x = 1", "y = 2"]
    assert item["figures"] == ["${DROPBOX_ROOT}/m10.png"]
    source = _script([_record(10, deps=[_dep(1, "qgis_eval", code=code), _load(2)])])
    assert "server.qgis_eval(code=" in source and "evals=EVALS" in source


def test_a_block_with_evals_says_their_writes_are_not_remapped():
    steps = session_export.plan_steps([_record(10, deps=[_dep(1, "qgis_eval", code="x = 1"), _load(2)])])
    assert "step 1: files written inside replayed qgis_eval code are not remapped into --out-dir" \
        in session_export.notes_for(steps)


def test_hostile_dependency_values_stay_literal():
    hostile = _dep(1, "qgis_eval", code='"); import os #' + chr(10) + 'os.system("calc")')
    tree = ast.parse(_script([_record(10, deps=[hostile, _load(2)])]))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert [ast.unparse(n) for n in imports] == ["from qgis_mcp_workflows import compound, replay, server"]


# --- final-review fixes ------------------------------------------------------------


def test_a_step_after_a_rebuilt_block_starts_fresh():
    atomic = {"session_id": "s1", "seq": 30, "figure": "${DROPBOX_ROOT}/c.png", "figure_sha256": None,
              "call": {"tool": "qgis_render_choropleth", "module": "server",
                       "arguments": {"zones_path": "${DROPBOX_ROOT}/z.gpkg", "value_field": "n",
                                     "output_png": "${DROPBOX_ROOT}/c.png"}},
              "depends_on": [], "inputs": [_in("zones_path", "${DROPBOX_ROOT}/z.gpkg")], "implicit_inputs": [],
              "outputs": ["${DROPBOX_ROOT}/c.png"], "unrecorded_state": [], "machine_specific": [], "remote": []}
    body = _body(_script([_record(10, deps=[_load(), _style(2, "a")]), atomic]))
    assert body[-2] == "replay.reset_session()" and body[-1].startswith("server.qgis_render_choropleth(")


@pytest.mark.parametrize("bad", [
    {"seq": 1, "call": {"tool": ["qgis_eval"], "module": "server", "arguments": {"code": "x"}}},
    {"seq": 1, "call": {"tool": "qgis_eval", "module": {}, "arguments": {"code": "x"}}},
    _dep(1, "qgis_eval", code=["x = 1"]),
    _dep(1, "qgis_eval", code="x = " + chr(0xD800)),
])
def test_malformed_dependencies_are_skipped_not_fatal(tmp_path, bad):
    fig = _map_sidecar(tmp_path, deps=[_load(), bad])
    ok = _map_sidecar(tmp_path, name="ok.png", deps=[_load()])
    records, skipped, _ = session_export.collect([str(fig), str(ok)], None)
    assert len(records) == 1 and _reasons(skipped) == ["invalid depends_on"]


@pytest.mark.parametrize("layer_ids", [5, [["x"]], [{"a": 1}]])
def test_malformed_layer_ids_are_skipped_not_fatal(tmp_path, layer_ids):
    fig = _map_sidecar(tmp_path, deps=[_load()])
    side = tmp_path / "m.png.provenance.json"
    record = json.loads(side.read_text(encoding="utf-8"))
    record["call"]["arguments"]["layer_ids"] = layer_ids
    side.write_text(json.dumps(record), encoding="utf-8")
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["malformed sidecar"]


@pytest.mark.parametrize("path", [
    "/" + chr(92) + "attacker.invalid/share/z.gpkg",
    chr(92) + "/attacker.invalid/share/z.gpkg",
    chr(92) + "??" + chr(92) + "UNC" + chr(92) + "attacker.invalid" + chr(92) + "share" + chr(92) + "z.gpkg",
    chr(92) * 2 + "?" + chr(92) + "UNC" + chr(92) + "host" + chr(92) + "z.gpkg",
    "/vsicurl?url=https%3A%2F%2Fattacker.invalid%2Fz.gpkg",
])
def test_every_network_spelling_is_refused(tmp_path, path):
    fig = _map_sidecar(tmp_path, deps=[_load(path=path)])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["network path"]


def test_a_sidecar_found_outside_dropbox_root_is_foreign_whatever_it_claims(tmp_path, monkeypatch):
    root = tmp_path / "dropbox"
    root.mkdir()
    monkeypatch.setenv("DROPBOX_ROOT", str(root))
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    claimed = "${DROPBOX_ROOT}/colleague/m.png"
    _map_sidecar(downloads, deps=[_dep(1, "qgis_eval", code="x = 1"), _load(2)], figure=claimed)
    records, skipped, _ = session_export.collect([str(downloads / "m.png")], None)
    assert skipped == []
    kept, warnings = session_export.keep_evals(records, include_evals=True, trust_foreign=False)
    assert [d["call"]["tool"] for d in kept[0]["depends_on"]] == ["qgis_load_layer"]
    assert "trust_foreign=True" in warnings[0]


def test_a_dependency_cannot_route_its_inputs_through_out():
    smuggled = {**_load(), "replayed_inputs": ["${DROPBOX_ROOT}/z.gpkg"]}
    body = _body(_script([_record(10, deps=[smuggled])]))
    assert body[0] == "layer_1 = server.qgis_load_layer(path=src('${DROPBOX_ROOT}/z.gpkg')).layer_id"


def test_replayed_evals_must_succeed():
    body = _body(_script([_record(10, deps=[_dep(1, "qgis_eval", code="x = 1"), _load(2)])]))
    assert body[0] == "replay.require_ok(server.qgis_eval(code='x = 1'))"

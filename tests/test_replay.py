"""Replay runtime and export (TASK-13). No QGIS needed."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance, replay, session_export


def test_resolve_expands_dropbox_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    assert replay.resolve("${DROPBOX_ROOT}/gufm/z.gpkg") == provenance.normalise(str(tmp_path / "gufm" / "z.gpkg"))


def test_resolve_leaves_other_paths_alone():
    assert replay.resolve("H:/elsewhere/x.csv") == "H:/elsewhere/x.csv"
    assert replay.resolve("${HOME}/x.csv") == "${HOME}/x.csv"   # allow-list: DROPBOX_ROOT only


def test_resolve_needs_the_root(monkeypatch):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    with pytest.raises(replay.ReplayError, match="set DROPBOX_ROOT"):
        replay.resolve("${DROPBOX_ROOT}/x.csv")


def test_resolve_refuses_to_leave_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "root"))
    with pytest.raises(replay.ReplayError, match="leaves DROPBOX_ROOT"):
        replay.resolve("${DROPBOX_ROOT}/../../etc/passwd")


# --- Task 2: check_inputs ---------------------------------------------------------


def _entry(path, data):
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def test_check_inputs_accepts_unchanged(tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abc")
    assert replay.check_inputs([_entry(f, b"abc")]) == []


def test_check_inputs_reports_changed_content(tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abd")
    [line] = replay.check_inputs([_entry(f, b"abc")])
    assert line.startswith("changed") and "sha256" in line


def test_check_inputs_compares_size_when_unhashed(tmp_path):
    f = tmp_path / "big.duckdb"
    f.write_bytes(b"0123")
    [line] = replay.check_inputs([{"path": str(f), "sha256": None, "bytes": 9}])
    assert "size 9 -> 4" in line


def test_check_inputs_reports_missing(tmp_path):
    [line] = replay.check_inputs([{"path": "H:/other-machine/x.csv", "sha256": None, "bytes": 3}])
    assert line.startswith("missing")


# --- Task 3: output_mapper ---------------------------------------------------------


def test_out_dir_maps_portable_paths(tmp_path):
    out = replay.output_mapper(["${DROPBOX_ROOT}/gufm/fig.png"], [], str(tmp_path / "out"), False, False)
    target = out("${DROPBOX_ROOT}/gufm/fig.png")
    assert target == str((tmp_path / "out" / "DROPBOX_ROOT" / "gufm" / "fig.png").resolve())
    assert (tmp_path / "out" / "DROPBOX_ROOT" / "gufm").is_dir()   # parent created


def test_out_dir_maps_absolute_paths_by_drive(tmp_path):
    out = replay.output_mapper(["C:/tmp/a.png"], [], str(tmp_path / "out"), False, False)
    assert out("C:/tmp/a.png") == str((tmp_path / "out" / "_abs" / "C" / "tmp" / "a.png").resolve())


def test_out_dir_refuses_escape(tmp_path):
    out = replay.output_mapper([], [], str(tmp_path / "out"), False, False)
    with pytest.raises(replay.ReplayError, match="leaves --out-dir"):
        out("${DROPBOX_ROOT}/../../../x.png")


def test_in_place_refuses_overwriting_an_input(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    with pytest.raises(replay.ReplayError, match="recorded inputs"):
        replay.output_mapper(["${DROPBOX_ROOT}/a.csv"], ["${DROPBOX_ROOT}/a.csv"], "", True, True)


def test_in_place_needs_yes_to_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    (tmp_path / "fig.png").write_bytes(b"old")
    with pytest.raises(replay.ReplayError, match="--yes"):
        replay.output_mapper(["${DROPBOX_ROOT}/fig.png"], [], "", True, False)
    out = replay.output_mapper(["${DROPBOX_ROOT}/fig.png"], [], "", True, True)
    assert out("${DROPBOX_ROOT}/fig.png") == provenance.normalise(str(tmp_path / "fig.png"))


# --- Task 4: main ------------------------------------------------------------------


def _run_main(tmp_path, monkeypatch, inputs=(), argv=(), steps=None):
    ran = []

    class Executor:
        def shutdown(self):
            ran.append("shutdown")

    monkeypatch.setattr(replay, "_new_executor", Executor)
    code = replay.main(str(tmp_path / "replay.py"), list(inputs), ["${DROPBOX_ROOT}/fig.png"], ["a note"],
                       steps or (lambda out, src: ran.append(out("${DROPBOX_ROOT}/fig.png"))), list(argv))
    return code, ran


def test_main_runs_steps_into_out_dir_and_shuts_down(monkeypatch, tmp_path, capsys):
    code, ran = _run_main(tmp_path, monkeypatch, argv=["--out-dir", str(tmp_path / "o")])
    assert code == 0
    assert ran[0].endswith(str(Path("o") / "DROPBOX_ROOT" / "fig.png")) and ran[-1] == "shutdown"
    assert "note: a note" in capsys.readouterr().out


def test_main_stops_on_changed_inputs_unless_forced(monkeypatch, tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"new")
    stale = [{"path": str(f), "sha256": hashlib.sha256(b"old").hexdigest(), "bytes": 3}]
    assert _run_main(tmp_path, monkeypatch, stale, ["--out-dir", str(tmp_path / "o")])[0] == 2
    assert _run_main(tmp_path, monkeypatch, stale, ["--out-dir", str(tmp_path / "o"), "--force"])[0] == 0


def test_main_refuses_in_place_with_force(monkeypatch, tmp_path, capsys):
    code, ran = _run_main(tmp_path, monkeypatch, argv=["--in-place", "--force"])
    assert code == 2 and ran == []
    assert "cannot be combined" in capsys.readouterr().err


# --- Task 5: collect -----------------------------------------------------------------


def _in(argument, path, data=None, made_by=None):
    digest = hashlib.sha256(data).hexdigest() if data is not None else None
    return {"argument": argument, "path": str(path), "sha256": digest,
            "bytes": len(data) if data is not None else None, "made_by": made_by}


def _sidecar(tmp_path, name, *, tool="qgis_render_choropleth", module="server", args=None,
             data=b"png", seq=1, inputs=None, schema=None, session="s1"):
    fig = tmp_path / name
    fig.write_bytes(data)
    if inputs is None:  # a real recording lists every input argument
        inputs = [_in("zones_path", "/z.gpkg")] if args is None else []
    record = {
        "schema": schema or provenance.SCHEMA, "figure": str(fig),
        "figure_sha256": hashlib.sha256(data).hexdigest(), "figure_bytes": len(data),
        "session_id": session, "seq": seq,
        "call": {"tool": tool, "module": module,
                 "arguments": args if args is not None else {"zones_path": "/z.gpkg", "value_field": "n",
                                                             "output_png": str(fig)}},
        "inputs": list(inputs), "implicit_inputs": [], "outputs": [str(fig)],
        "unrecorded_state": [], "machine_specific": [], "remote": [],
    }
    (tmp_path / f"{name}.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    return fig


def _reasons(skipped):
    return sorted(s["reason"] for s in skipped)


def test_collect_reads_named_figures(tmp_path):
    fig = _sidecar(tmp_path, "a.png")
    records, skipped, _ = session_export.collect([str(fig)], None)
    assert len(records) == 1 and skipped == []


def test_collect_skips_with_fixed_reasons(tmp_path):
    ok = _sidecar(tmp_path, "ok.png")
    hostile = _sidecar(tmp_path, "h.png", tool="os.system")
    badkey = _sidecar(tmp_path, "k.png", args={"zones_path": "/z", "value_field": "n", "output_png": "x",
                                               "__import__": 1})
    unknown = _sidecar(tmp_path, "u.png", schema="other@9")
    state = _sidecar(tmp_path, "m.png", tool="qgis_render_map", args={"layer_ids": ["L1"], "output_png": "x"})
    stale = _sidecar(tmp_path, "s.png")
    stale.write_bytes(b"changed by a script")
    nofile = tmp_path / "none.png"
    records, skipped, _ = session_export.collect([str(p) for p in (ok, hostile, badkey, unknown, state, stale, nofile)], None)
    assert len(records) == 1
    assert _reasons(skipped) == sorted(["unknown tool", "unknown tool", "unknown schema",
                                        "needs session state", "stale sidecar: figure changed", "no sidecar"])


def test_deleted_figure_is_still_replayable(tmp_path):
    fig = _sidecar(tmp_path, "gone.png")
    fig.unlink()
    records, skipped, _ = session_export.collect([str(fig) + ".provenance.json"], None)
    assert len(records) == 1 and skipped == []


def test_folder_mode_skips_conflicted_copies(tmp_path):
    _sidecar(tmp_path, "a.png")
    (tmp_path / "a (North's conflicted copy).png.provenance.json").write_text("{}", encoding="utf-8")
    records, skipped, _ = session_export.collect(None, str(tmp_path))
    assert len(records) == 1 and _reasons(skipped) == ["conflicted copy"]


def _route(tmp_path, data=b"route"):
    return _sidecar(tmp_path, "route.csv", tool="qgis_route_on_network", data=data,
                    args={"input_csv": "/stops.csv", "network_path": "/net.tsv",
                          "output_csv": str(tmp_path / "route.csv")},
                    inputs=[_in("input_csv", "/stops.csv"), _in("network_path", "/net.tsv")])


def _reads_route(tmp_path, producer, read):
    fig = tmp_path / "fig.png"
    return _sidecar(tmp_path, "fig.png", seq=2,
                    args={"zones_path": str(producer), "value_field": "n", "output_png": str(fig)},
                    inputs=[_in("zones_path", producer, read, str(producer) + ".provenance.json")])


def test_collect_follows_made_by_to_producers(tmp_path):
    producer = _route(tmp_path)
    records, _, warnings = session_export.collect([str(_reads_route(tmp_path, producer, b"route"))], None)
    assert {r["call"]["tool"] for r in records} == {"qgis_route_on_network", "qgis_render_choropleth"}
    assert warnings == []


def test_collect_rejects_non_object_sidecars(tmp_path):
    fig = tmp_path / "x.png"
    fig.write_bytes(b"png")
    (tmp_path / "x.png.provenance.json").write_text("[]", encoding="utf-8")
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["no sidecar"]


# --- Task 6: plan_steps, inputs, outputs, notes ----------------------------------------


def _record(seq, outputs, inputs=(), tool="qgis_render_choropleth", session="s1", sha=None, **call):
    return {"session_id": session, "seq": seq, "outputs": list(outputs), "inputs": list(inputs),
            "figure": outputs[0] if outputs else None, "figure_sha256": sha,
            "implicit_inputs": [], "unrecorded_state": [], "machine_specific": [], "remote": [],
            "call": {"tool": tool, "module": "server", "arguments": call}}


def test_sibling_outputs_share_one_step():
    pages = [_record(5, ["a.png", "b.png"]), _record(5, ["a.png", "b.png"])]
    assert len(session_export.plan_steps(pages)) == 1


def test_identical_calls_stay_two_steps():
    assert len(session_export.plan_steps([_record(1, ["x.png"]), _record(2, ["x.png"])])) == 2


def test_producers_come_first():
    consumer = _record(1, ["fig.png"], inputs=[{"path": "route.csv", "sha256": "h", "bytes": 1}])
    producer = _record(9, ["route.csv"], tool="qgis_route_on_network", sha="h")
    assert [s["seq"] for s in session_export.plan_steps([consumer, producer])] == [9, 1]


def test_source_inputs_exclude_intermediates():
    steps = session_export.plan_steps([
        _record(1, ["route.csv"], inputs=[{"path": "stops.csv", "sha256": "a", "bytes": 1}], sha="b"),
        _record(2, ["fig.png"], inputs=[{"path": "route.csv", "sha256": "b", "bytes": 1}]),
    ])
    assert [i["path"] for i in session_export.source_inputs(steps)] == ["stops.csv"]
    assert session_export.outputs_of(steps) == ["route.csv", "fig.png"]


def test_notes_are_cleaned_and_capped():
    step = _record(1, ["x.png"])
    step["unrecorded_state"] = ['bad' + chr(10) + '"""' + chr(27) + 'y' * 500]
    [note] = [n for n in session_export.notes_for([step]) if n.startswith("step 1")]
    assert chr(10) not in note and chr(27) not in note and len(note) <= 260


def _choropleth(seq=1, out="${DROPBOX_ROOT}/fig.png", zones="${DROPBOX_ROOT}/z.gpkg"):
    return _record(seq, [out], zones_path=zones, value_field="n", output_png=out, mode="quantile")


def test_build_script_compiles_and_calls_the_tool():
    step = _choropleth()
    source = session_export.build_script([step], [{"path": "${DROPBOX_ROOT}/z.gpkg", "sha256": "h", "bytes": 1}],
                                 ["${DROPBOX_ROOT}/fig.png"], ["n"], "2026-10-09")
    compile(source, "replay.py", "exec")
    assert "server.qgis_render_choropleth(" in source
    assert "zones_path=src('${DROPBOX_ROOT}/z.gpkg')" in source
    assert "output_png=out('${DROPBOX_ROOT}/fig.png')" in source


def _chain_script(read_sha):
    producer = _record(1, ["${DROPBOX_ROOT}/route.csv"], tool="qgis_route_on_network", sha="r",
                       input_csv="/stops.csv", network_path="/net.tsv", output_csv="${DROPBOX_ROOT}/route.csv")
    consumer = _record(2, ["${DROPBOX_ROOT}/t.png"], tool="qgis_render_trajectory",
                       inputs=[{"path": "${DROPBOX_ROOT}/route.csv", "sha256": read_sha, "bytes": 1}],
                       input_path="${DROPBOX_ROOT}/route.csv", output_png="${DROPBOX_ROOT}/t.png")
    steps = session_export.plan_steps([consumer, producer])
    return session_export.build_script(steps, session_export.source_inputs(steps), session_export.outputs_of(steps), [], "d")


def test_intermediate_inputs_read_from_the_replayed_output():
    assert "input_path=out('${DROPBOX_ROOT}/route.csv')" in _chain_script("r")


def test_an_input_with_another_hash_reads_the_source():
    source = _chain_script("older")
    assert "input_path=src('${DROPBOX_ROOT}/route.csv')" in source
    assert "'sha256': 'older'" in source   # and the script checks it before running


def test_output_dir_argument_is_remapped():
    step = _record(1, ["${DROPBOX_ROOT}/b/x.png"], tool="qgis_batch_render", template_qgz="/t.qgz",
                   attribute="name", values=["x"], output_dir="${DROPBOX_ROOT}/b")
    assert "output_dir=out('${DROPBOX_ROOT}/b')" in session_export.build_script([step], [], ["${DROPBOX_ROOT}/b/x.png"], [], "d")


def test_hostile_values_stay_literal():
    step = _choropleth()
    step["call"]["arguments"]["value_field"] = '"); import os; os.system("calc'
    source = session_export.build_script([step], [], ["${DROPBOX_ROOT}/fig.png"], ['"""' + chr(10) + 'import os'], "d")
    tree = ast.parse(source)
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert [ast.unparse(n) for n in imports] == ["from qgis_mcp_workflows import compound, replay, server"]


# --- Review fixes: only replay what the recording vouches for -------------------------


@pytest.mark.parametrize("tool, args", [
    ("qgis_eval", {"code": "import os"}),
    ("qgis_export_session", {"output_py": "/elsewhere.py", "overwrite": True}),
    ("qgis_load_layer", {"path": "/z.gpkg"}),
    ("qgis_style_categorized", {"layer_id": "L1", "field": "n"}),
])
def test_tools_that_never_write_a_sidecar_are_not_replayed(tmp_path, tool, args):
    fig = _sidecar(tmp_path, "x.png", tool=tool, args=args)
    records, skipped, _ = session_export.collect([str(fig)], None)
    assert records == [] and _reasons(skipped) == ["unknown tool"]


@pytest.mark.parametrize("tool, module, extra", [
    ("qgis_render_from_duckdb", "server", {}),
    ("qgis_render", "compound", {"mode": "duckdb"}),
])
def test_recorded_queries_are_not_replayed(tmp_path, tool, module, extra):
    args = {"db_path": "/s.duckdb", "query": "SELECT 1", "output_png": str(tmp_path / "q.png"), **extra}
    fig = _sidecar(tmp_path, "q.png", tool=tool, module=module, args=args, inputs=[_in("db_path", "/s.duckdb")])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["runs a recorded query"]


def test_an_output_argument_must_be_a_recorded_output(tmp_path):
    fig = _sidecar(tmp_path, "r.csv", tool="qgis_route_on_network",
                   args={"input_csv": "/in.csv", "network_path": "/n.tsv", "output_csv": "/in.csv"},
                   inputs=[_in("input_csv", "/in.csv"), _in("network_path", "/n.tsv")])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["arguments disagree with sidecar"]


def test_an_input_argument_must_be_a_recorded_input(tmp_path):
    fig = _sidecar(tmp_path, "x.png", args={"zones_path": "/secret.gpkg", "value_field": "n",
                                             "output_png": str(tmp_path / "x.png")}, inputs=[])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["arguments disagree with sidecar"]


@pytest.mark.parametrize("template, values", [
    ("../x_{value}.png", ["a"]),
    ("{value}.png", ["../a"]),
    ("{value}.png", ["C:/a"]),
    ("{value}{value}", ["."]),
    ("{nope}.png", ["a"]),
])
def test_batch_file_names_must_stay_in_their_folder(tmp_path, template, values):
    args = {"template_qgz": "/t.qgz", "attribute": "name", "values": values,
            "output_dir": str(tmp_path), "filename_template": template}
    fig = _sidecar(tmp_path, "a.png", tool="qgis_batch_render", args=args, inputs=[_in("template_qgz", "/t.qgz")])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["unsafe file name"]


def test_network_paths_are_never_opened(tmp_path, monkeypatch):
    opened = []
    real = session_export._load_sidecar
    monkeypatch.setattr(session_export, "_load_sidecar", lambda p: opened.append(p) or real(p))
    unc = "//evil/share/z.gpkg"
    fig = _sidecar(tmp_path, "x.png", args={"zones_path": unc, "value_field": "n", "output_png": str(tmp_path / "x.png")},
                   inputs=[_in("zones_path", unc, b"z", unc + ".provenance.json")])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["network path"]
    assert all("evil" not in p for p in opened)


@pytest.mark.parametrize("field, value", [
    ("inputs", 5), ("inputs", {}), ("inputs", [{"path": ["x"]}]), ("outputs", [["a"]]),
    ("unrecorded_state", "abcdef"), ("unrecorded_state", 7), ("seq", True), ("session_id", 3),
    ("figure", None), ("call", {"tool": "qgis_render_choropleth", "module": [], "arguments": {}}),
])
def test_malformed_sidecars_are_skipped_not_fatal(tmp_path, field, value):
    fig = _sidecar(tmp_path, "x.png")
    side = tmp_path / "x.png.provenance.json"
    record = json.loads(side.read_text(encoding="utf-8"))
    record[field] = value
    side.write_text(json.dumps(record), encoding="utf-8")
    records, skipped, _ = session_export.collect([str(fig), str(_sidecar(tmp_path, "ok.png"))], None)
    assert len(records) == 1 and _reasons(skipped) == ["malformed sidecar"]


@pytest.mark.parametrize("bulk", ["deep", "wide"])
def test_oversized_arguments_are_malformed(tmp_path, bulk):
    value = list(range(20_000))
    if bulk == "deep":
        value = "x"
        for _ in range(300):
            value = [value]
    fig = _sidecar(tmp_path, "x.png", args={"zones_path": "/z.gpkg", "value_field": "n",
                                             "output_png": str(tmp_path / "x.png"), "classes": value},
                   inputs=[_in("zones_path", "/z.gpkg")])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["malformed sidecar"]


def test_a_re_made_intermediate_becomes_a_source_input(tmp_path):
    producer = _route(tmp_path, data=b"route v2")             # re-made after fig.png read v1
    records, _, warnings = session_export.collect([str(_reads_route(tmp_path, producer, b"route v1"))], None)
    assert [r["call"]["tool"] for r in records] == ["qgis_render_choropleth"]
    assert len(warnings) == 1 and "re-made" in warnings[0]
    steps = session_export.plan_steps(records)
    assert [i["path"] for i in session_export.source_inputs(steps)] == [str(producer)]


def test_every_recorded_fingerprint_of_a_source_is_checked():
    steps = session_export.plan_steps([
        _record(1, ["a.png"], session="f0", inputs=[{"path": "z.gpkg", "sha256": "v1", "bytes": 1}]),
        _record(1, ["b.png"], session="0b", inputs=[{"path": "z.gpkg", "sha256": "v2", "bytes": 1}]),
    ])
    assert sorted(i["sha256"] for i in session_export.source_inputs(steps)) == ["v1", "v2"]


def test_absolute_outputs_map_the_same_on_every_os(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert replay._relative_target("C:/tmp/a.png") == Path("_abs", "C", "tmp", "a.png")
    assert replay._relative_target("D:" + chr(92) + "data" + chr(92) + "b.png") == Path("_abs", "D", "data", "b.png")
    assert replay._relative_target("/Users/x/f.png") == Path("_abs", "root", "Users", "x", "f.png")


def test_in_place_writes_only_what_the_script_declares(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    out = replay.output_mapper(["${DROPBOX_ROOT}/b/x.png"], [], "", True, True)
    assert out("${DROPBOX_ROOT}/b/x.png") == provenance.normalise(str(tmp_path / "b" / "x.png"))
    assert out("${DROPBOX_ROOT}/b") == provenance.normalise(str(tmp_path / "b"))   # a batch's folder
    for undeclared in ("${DROPBOX_ROOT}/thesis/data.csv", str(tmp_path.parent / "elsewhere.txt")):
        with pytest.raises(replay.ReplayError, match="does not declare"):
            out(undeclared)


def test_check_inputs_falls_back_to_size_when_not_rehashed(tmp_path, monkeypatch):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abc")
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB", "0")   # this machine will not hash it
    assert replay.check_inputs([_entry(f, b"abc")]) == []
    f.write_bytes(b"abcd")
    [line] = replay.check_inputs([_entry(f, b"abc")])
    assert "size 3 -> 4" in line


def test_printed_text_has_no_control_characters(tmp_path):
    [line] = replay.check_inputs([{"path": str(tmp_path / ("x" + chr(27) + "]0;t" + chr(7))), "sha256": None,
                                   "bytes": 1}])
    assert chr(27) not in line and chr(7) not in line
    assert chr(0x9b) not in replay._clean("a" + chr(0x9b) + "b")


def test_main_restores_the_previous_executor(monkeypatch, tmp_path):
    from qgis_mcp_workflows import executors

    before = object()
    monkeypatch.setattr(executors, "_current", before)
    assert _run_main(tmp_path, monkeypatch, argv=["--out-dir", str(tmp_path / "o")])[0] == 0
    assert executors._current is before


def test_main_reports_a_missing_qgis_without_a_traceback(monkeypatch, tmp_path, capsys):
    from qgis_mcp_workflows.errors import HeadlessUnavailableError

    def no_qgis():
        raise HeadlessUnavailableError("no launcher found")

    monkeypatch.setattr(replay, "_new_executor", no_qgis)
    code = replay.main(str(tmp_path / "replay.py"), [], [], [], lambda out, src: None,
                       ["--out-dir", str(tmp_path / "o")])
    assert code == 1 and "no launcher found" in capsys.readouterr().err


def test_main_shuts_down_and_exits_1_when_a_step_fails(monkeypatch, tmp_path, capsys):
    from qgis_mcp_workflows.errors import QgisMcpWorkflowsError

    def failing(out, src):
        raise QgisMcpWorkflowsError("render failed. Next: check the layer.")

    code, ran = _run_main(tmp_path, monkeypatch, argv=["--out-dir", str(tmp_path / "o")], steps=failing)
    assert code == 1 and ran == ["shutdown"] and "render failed" in capsys.readouterr().err


# --- TASK-15: --allow-eval gate and session reset ----------------------------------

_EVAL = {"sha256": "ab" * 32, "first_lines": ["import os", "x = 1"], "figures": ["${DROPBOX_ROOT}/m.png"]}


def test_main_refuses_evals_without_allow_eval(monkeypatch, tmp_path, capsys):
    ran = []
    monkeypatch.setattr(replay, "_new_executor", lambda: ran.append("spawned"))
    code = replay.main(str(tmp_path / "r.py"), [], [], [], lambda out, src: ran.append("steps"),
                       ["--out-dir", str(tmp_path / "o")], evals=[_EVAL])
    err = capsys.readouterr().err
    assert code == 3 and ran == []
    assert "ab" * 32 in err and "import os" in err and "--allow-eval" in err


def test_main_runs_evals_with_allow_eval(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(replay, "_new_executor", lambda: None)
    code = replay.main(str(tmp_path / "r.py"), [], [], [], lambda out, src: ran.append("steps"),
                       ["--out-dir", str(tmp_path / "o"), "--allow-eval"], evals=[_EVAL])
    assert code == 0 and ran == ["steps"]


def test_eval_preview_is_cleaned_and_short(monkeypatch, tmp_path, capsys):
    item = {**_EVAL, "first_lines": ["a" + chr(27) + "]0;x" + chr(7) + "b"]}
    replay.main(str(tmp_path / "r.py"), [], [], [], lambda out, src: None, [], evals=[item])
    err = capsys.readouterr().err
    assert chr(27) not in err and chr(7) not in err


def test_reset_session_swaps_in_a_fresh_executor_and_shuts_the_old_one(monkeypatch):
    from qgis_mcp_workflows import executors

    events = []

    class Old:
        def shutdown(self):
            events.append("old shut down")

    fresh = object()
    monkeypatch.setattr(executors, "_current", Old())
    monkeypatch.setattr(replay, "_new_executor", lambda: fresh)
    replay.reset_session()
    assert executors._current is fresh and events == ["old shut down"]


def test_main_shuts_down_the_executor_left_by_a_reset(monkeypatch, tmp_path):
    from qgis_mcp_workflows import executors

    shut = []

    class Executor:
        def __init__(self, name):
            self.name = name

        def shutdown(self):
            shut.append(self.name)

    names = iter(["first", "second"])
    monkeypatch.setattr(replay, "_new_executor", lambda: Executor(next(names)))
    before = object()
    monkeypatch.setattr(executors, "_current", before)
    code = replay.main(str(tmp_path / "r.py"), [], [], [], lambda out, src: replay.reset_session(),
                       ["--out-dir", str(tmp_path / "o")])
    assert code == 0 and shut == ["first", "second"] and executors._current is before

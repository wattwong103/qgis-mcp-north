"""Replay runtime and export (TASK-13). No QGIS needed."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance, replay


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


def _sidecar(tmp_path, name, *, tool="qgis_render_choropleth", module="server", args=None,
             data=b"png", seq=1, inputs=(), schema=None, session="s1"):
    fig = tmp_path / name
    fig.write_bytes(data)
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
    records, skipped, _ = replay.collect([str(fig)], None)
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
    records, skipped, _ = replay.collect([str(p) for p in (ok, hostile, badkey, unknown, state, stale, nofile)], None)
    assert len(records) == 1
    assert _reasons(skipped) == sorted(["unknown tool", "unknown tool", "unknown schema",
                                        "needs session state", "stale sidecar: figure changed", "no sidecar"])


def test_deleted_figure_is_still_replayable(tmp_path):
    fig = _sidecar(tmp_path, "gone.png")
    fig.unlink()
    records, skipped, _ = replay.collect([str(fig) + ".provenance.json"], None)
    assert len(records) == 1 and skipped == []


def test_folder_mode_skips_conflicted_copies(tmp_path):
    _sidecar(tmp_path, "a.png")
    (tmp_path / "a (North's conflicted copy).png.provenance.json").write_text("{}", encoding="utf-8")
    records, skipped, _ = replay.collect(None, str(tmp_path))
    assert len(records) == 1 and _reasons(skipped) == ["conflicted copy"]


def test_collect_follows_made_by_to_producers(tmp_path):
    producer = _sidecar(tmp_path, "route.csv", tool="qgis_route_on_network",
                        args={"input_csv": "/stops.csv", "network_path": "/net.tsv",
                              "output_csv": str(tmp_path / "route.csv")})
    consumer = _sidecar(tmp_path, "fig.png", seq=2, inputs=[{
        "argument": "zones_path", "path": str(producer), "sha256": hashlib.sha256(b"png").hexdigest(),
        "bytes": 3, "made_by": str(producer) + ".provenance.json"}])
    records, _, _ = replay.collect([str(consumer)], None)
    assert {r["call"]["tool"] for r in records} == {"qgis_route_on_network", "qgis_render_choropleth"}


def test_collect_rejects_non_object_sidecars(tmp_path):
    fig = tmp_path / "x.png"
    fig.write_bytes(b"png")
    (tmp_path / "x.png.provenance.json").write_text("[]", encoding="utf-8")
    _, skipped, _ = replay.collect([str(fig)], None)
    assert _reasons(skipped) == ["no sidecar"]


# --- Task 6: plan_steps, inputs, outputs, notes ----------------------------------------


def _record(seq, outputs, inputs=(), tool="qgis_render_choropleth", session="s1", **call):
    return {"session_id": session, "seq": seq, "outputs": list(outputs), "inputs": list(inputs),
            "implicit_inputs": [], "unrecorded_state": [], "machine_specific": [], "remote": [],
            "call": {"tool": tool, "module": "server", "arguments": call}}


def test_sibling_outputs_share_one_step():
    pages = [_record(5, ["a.png", "b.png"]), _record(5, ["a.png", "b.png"])]
    assert len(replay.plan_steps(pages)) == 1


def test_identical_calls_stay_two_steps():
    assert len(replay.plan_steps([_record(1, ["x.png"]), _record(2, ["x.png"])])) == 2


def test_producers_come_first():
    consumer = _record(1, ["fig.png"], inputs=[{"path": "route.csv", "sha256": "h", "bytes": 1}])
    producer = _record(9, ["route.csv"], tool="qgis_route_on_network")
    assert [s["seq"] for s in replay.plan_steps([consumer, producer])] == [9, 1]


def test_source_inputs_exclude_intermediates():
    steps = replay.plan_steps([
        _record(1, ["route.csv"], inputs=[{"path": "stops.csv", "sha256": "a", "bytes": 1}]),
        _record(2, ["fig.png"], inputs=[{"path": "route.csv", "sha256": "b", "bytes": 1}]),
    ])
    assert [i["path"] for i in replay.source_inputs(steps)] == ["stops.csv"]
    assert replay.outputs_of(steps) == ["route.csv", "fig.png"]


def test_notes_are_cleaned_and_capped():
    step = _record(1, ["x.png"])
    step["unrecorded_state"] = ['bad' + chr(10) + '"""' + chr(27) + 'y' * 500]
    [note] = [n for n in replay.notes_for([step]) if n.startswith("step 1")]
    assert chr(10) not in note and chr(27) not in note and len(note) <= 260


def _choropleth(seq=1, out="${DROPBOX_ROOT}/fig.png", zones="${DROPBOX_ROOT}/z.gpkg"):
    return _record(seq, [out], zones_path=zones, value_field="n", output_png=out, mode="quantile")


def test_build_script_compiles_and_calls_the_tool():
    step = _choropleth()
    source = replay.build_script([step], [{"path": "${DROPBOX_ROOT}/z.gpkg", "sha256": "h", "bytes": 1}],
                                 ["${DROPBOX_ROOT}/fig.png"], ["n"], "2026-10-09")
    compile(source, "replay.py", "exec")
    assert "server.qgis_render_choropleth(" in source
    assert "zones_path=src('${DROPBOX_ROOT}/z.gpkg')" in source
    assert "output_png=out('${DROPBOX_ROOT}/fig.png')" in source


def test_intermediate_inputs_read_from_the_replayed_output():
    producer = _record(1, ["${DROPBOX_ROOT}/route.csv"], tool="qgis_route_on_network",
                       input_csv="/stops.csv", network_path="/net.tsv", output_csv="${DROPBOX_ROOT}/route.csv")
    consumer = _record(2, ["${DROPBOX_ROOT}/t.png"], tool="qgis_render_trajectory",
                       input_path="${DROPBOX_ROOT}/route.csv", output_png="${DROPBOX_ROOT}/t.png")
    source = replay.build_script([producer, consumer], [], replay.outputs_of([producer, consumer]), [], "d")
    assert "input_path=out('${DROPBOX_ROOT}/route.csv')" in source


def test_output_dir_argument_is_remapped():
    step = _record(1, ["${DROPBOX_ROOT}/b/x.png"], tool="qgis_batch_render", template_qgz="/t.qgz",
                   attribute="name", values=["x"], output_dir="${DROPBOX_ROOT}/b")
    assert "output_dir=out('${DROPBOX_ROOT}/b')" in replay.build_script([step], [], ["${DROPBOX_ROOT}/b/x.png"], [], "d")


def test_hostile_values_stay_literal():
    step = _choropleth()
    step["call"]["arguments"]["value_field"] = '"); import os; os.system("calc'
    source = replay.build_script([step], [], ["${DROPBOX_ROOT}/fig.png"], ['"""' + chr(10) + 'import os'], "d")
    tree = ast.parse(source)
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert [ast.unparse(n) for n in imports] == ["from qgis_mcp_workflows import compound, replay, server"]

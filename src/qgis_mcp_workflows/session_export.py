"""Write replay scripts from provenance sidecars (TASK-13, TASK-15).

The export half of figure replay: read sidecars, follow made_by chains, order
the calls and build a standalone script as an AST from a fixed skeleton — a
sidecar is unsigned input, so its values only ever become literal constants.
The runtime the script imports is ``qgis_mcp_workflows.replay``.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md §6-§7.
"""

from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import inspect
import itertools
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from qgis_mcp_workflows import ledger
from qgis_mcp_workflows.provenance import (
    INPUT_ARGS,
    INPUT_LIST_ARGS,
    OUTPUT_ARGS,
    ROOT_TOKEN,
    SCHEMA,
    SIDECAR_SUFFIX,
    _regular_stat,
    _relative_under,
    _writes_files,
    fingerprint,
    normalise,
    portable,
    sidecar_path,
)
from qgis_mcp_workflows.replay import ReplayError, _clean, _same, resolve

MAX_SIDECARS = 200
_SIDECAR_MAX_BYTES = 2 * 1024 * 1024
# A recorded SQL query is code: DuckDB can read files or fetch URLs from inside a
# SELECT, so it is not replayed from an unsigned sidecar.
_QUERY_TOOLS = frozenset({"qgis_render_from_duckdb"})
_MAX_DEPTH = 16
_MAX_ITEMS = 10_000
_NAME_BREAKERS = re.compile(r"[/\\:]")


def _load_sidecar(path: str) -> dict | None:
    st = _regular_stat(path)
    if st is None or st.st_size > _SIDECAR_MAX_BYTES:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _local(path: str) -> str:
    try:
        return resolve(path)
    except ReplayError:
        return path


def _tool_function(record: dict) -> Callable | None:
    """The tool a sidecar names — None unless it is one that records sidecars.

    Only tools whose result names a written file (``_writes_files``) can have
    produced a sidecar, so qgis_eval, qgis_export_session and the load/style
    tools are never replayable, whatever a sidecar claims. Every argument must
    be a real parameter.
    """
    from qgis_mcp_workflows import compound, server

    call = record.get("call")
    if not isinstance(call, dict) or not isinstance(call.get("arguments"), dict):
        return None
    module = {"server": server, "compound": compound}.get(call.get("module"))
    name = call.get("tool")
    if module is None or not isinstance(name, str) or not name.startswith("qgis_"):
        return None
    fn = getattr(module, name, None)
    if not inspect.isfunction(fn) or fn.__module__ != module.__name__ or not _writes_files(fn):
        return None
    return fn if set(call["arguments"]) <= set(inspect.signature(fn).parameters) else None


def _size_ok(value: Any) -> bool:
    """At most _MAX_DEPTH deep and _MAX_ITEMS values, checked without recursion."""
    stack, seen = [(value, 0)], 0
    while stack:
        node, depth = stack.pop()
        seen += 1
        if depth > _MAX_DEPTH or seen > _MAX_ITEMS:
            return False
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return True


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _file_entries(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(e, dict) and isinstance(e.get("path"), str) and isinstance(e.get("sha256"), str | None)
        and isinstance(e.get("bytes"), int | None) and isinstance(e.get("made_by"), str | None)
        for e in value
    )


def _well_formed(record: dict) -> bool:
    """Every field the export reads has the shape a real recording gives it."""
    call, seq = record.get("call"), record.get("seq")
    if not (isinstance(call, dict) and isinstance(call.get("tool"), str) and isinstance(call.get("module"), str)
            and isinstance(call.get("arguments"), dict) and _size_ok(call["arguments"])):
        return False
    remote = record.get("remote", [])
    return (isinstance(seq, int) and not isinstance(seq, bool) and isinstance(record.get("session_id"), str)
            and isinstance(record.get("figure"), str) and isinstance(record.get("figure_sha256"), str | None)
            and _str_list(record.get("outputs")) and record["figure"] in record["outputs"]
            and _str_list(record.get("unrecorded_state", [])) and _size_ok(record.get("unrecorded_state", []))
            and all(_file_entries(record.get(k, [])) for k in ("inputs", "implicit_inputs", "machine_specific"))
            and isinstance(remote, list) and all(isinstance(e, dict) for e in remote)
            and isinstance(record.get("depends_on", []), list) and _size_ok(record.get("depends_on", [])))


def _argument_paths(arguments: dict) -> tuple[list, list, list]:
    """(input values, output file values, output_dir values) a call names."""
    ins: list = []
    outs: list = []
    dirs: list = []
    for name, value in arguments.items():
        if value is None or value == "" or value == []:
            continue
        if name in INPUT_ARGS:
            ins.append(value)
        elif name in INPUT_LIST_ARGS:
            ins.extend(value if isinstance(value, list) else [value])
        elif name == "output_dir":
            dirs.append(value)
        elif name in OUTPUT_ARGS:
            outs.append(value)
    return ins, outs, dirs


def _arguments_agree(record: dict) -> bool:
    """The call reads only recorded inputs and writes only recorded outputs.

    The input check and the --in-place guards work from the recorded lists; a
    sidecar whose arguments name other files would slip past both.
    """
    ins, outs, dirs = _argument_paths(record["call"]["arguments"])
    read = {e["path"] for e in record.get("inputs", [])}
    written = record["outputs"]

    def inside(path: str, folder: str) -> bool:  # separators as _relative_target reads them
        return bool(_relative_under(path.replace("\\", "/"), folder.replace("\\", "/")))

    return (all(isinstance(p, str) and p in read for p in ins)
            and all(isinstance(p, str) and p in written for p in outs)
            and all(isinstance(d, str) and all(inside(o, d) for o in written) for d in dirs))


def _safe_file_names(arguments: dict) -> bool:
    """A batch's file names stay inside its output_dir (no separators, drives or dot-names)."""
    template = arguments.get("filename_template")
    if template is None:
        return True
    values = arguments.get("values") or ["x"]
    if not isinstance(template, str) or _NAME_BREAKERS.search(template) or not isinstance(values, list):
        return False
    for value in values:
        try:
            name = template.format(value=value)
        except (AttributeError, IndexError, KeyError, ValueError):
            return False
        if _NAME_BREAKERS.search(name) or not name.strip("."):
            return False
    return True


def _network(path: Any) -> bool:
    """UNC, NT-namespace, URL or GDAL virtual path: opening it could contact another host.

    Judged on the path with both separators unified, so Windows spellings such as
    \\/host, /\\host, \\\\?\\UNC\\ and \\??\\UNC\\ cannot slip past.
    """
    if not isinstance(path, str):
        return False
    unified = path.replace("\\", "/")
    return (unified.startswith(("//", "/??/")) or unified.lower().startswith("/vsi") or "://" in path)


def _record_paths(record: dict) -> list:
    paths: list = [record["figure"], *record["outputs"]]
    for key in ("inputs", "implicit_inputs"):
        for entry in record.get(key, []):
            paths += [entry["path"], entry.get("made_by")]
    for dep in record.get("depends_on", []):
        inputs = dep.get("inputs") if isinstance(dep, dict) else None
        if isinstance(inputs, list):
            paths += [e.get("path") for e in inputs if isinstance(e, dict)]
    ins, outs, dirs = _argument_paths(record["call"]["arguments"])
    return paths + ins + outs + dirs


# Calls a state-reading figure depends on (spec §6). A sidecar names them only
# inside depends_on — never as its own call (TASK-13's _writes_files rule).
_DEPENDENCY_TOOLS = frozenset({
    ("server", "qgis_load_layer"), ("server", "qgis_style_categorized"), ("server", "qgis_style_graduated"),
    ("server", "qgis_project_load"), ("server", "qgis_eval"),
    ("compound", "qgis_inspect"), ("compound", "qgis_style"),
})


def _utf8_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate from a hand-made sidecar
        return False
    return True


def _dependency_ok(entry: Any) -> bool:
    """A depends_on entry is a real ledger call whose paths are its recorded inputs."""
    from qgis_mcp_workflows import compound, server

    if not isinstance(entry, dict) or not isinstance(entry.get("call"), dict):
        return False
    call, seq, layer = entry["call"], entry.get("seq"), entry.get("layer_id")
    args = call.get("arguments")
    if not (isinstance(call.get("module"), str) and isinstance(call.get("tool"), str)):
        return False
    key = (call["module"], call["tool"])
    if key not in _DEPENDENCY_TOOLS or not isinstance(args, dict) or not _size_ok(args):
        return False
    if key[1] == "qgis_eval" and not _utf8_text(args.get("code")):
        return False
    if not (isinstance(seq, int) and not isinstance(seq, bool) and isinstance(layer, str | None)
            and _file_entries(entry.get("inputs", []))):
        return False
    fn = getattr({"server": server, "compound": compound}[key[0]], key[1])
    if not set(args) <= set(inspect.signature(fn).parameters):
        return False
    if ledger.kind(key[1], args) not in ("load", "style", "project", "eval"):
        return False
    ins, outs, dirs = _argument_paths(args)
    read = {e["path"] for e in entry.get("inputs", [])}
    return (not outs and not dirs
            and all(isinstance(p, str) and p in read for p in ins)
            and not any(_network(p) for p in read))


def _qgz(tool: str, args: dict) -> Any:
    batch = tool == "qgis_batch_render" or (tool == "qgis_export" and args.get("kind") == "batch")
    return args.get("template_qgz") if batch else (args.get("qgz_path") or args.get("path"))


def _covers_state(record: dict) -> bool:
    """depends_on rebuilds what the figure reads: its layers, or the project it exports."""
    tool, args = record["call"]["tool"], record["call"]["arguments"]
    deps = record.get("depends_on") or []
    projects = [d for d in deps if ledger.kind(d["call"]["tool"], d["call"]["arguments"]) == "project"]
    if ledger.kind(tool, args) == "map":
        loaded = {d.get("layer_id") for d in deps
                  if ledger.kind(d["call"]["tool"], d["call"]["arguments"]) == "load"}
        return bool(projects) or all(layer in loaded for layer in args.get("layer_ids") or [])
    target = _qgz(tool, args)
    return any(_qgz(d["call"]["tool"], d["call"]["arguments"]) == target for d in projects)


def _foreign(record: dict) -> bool:
    """Not under this machine's DROPBOX_ROOT, or not found where it claims to be.

    Decided from where collect() actually read the sidecar (``located_at``), not
    from the sidecar's own unsigned ``figure`` claim. A sidecar in a shared folder
    inside DROPBOX_ROOT still counts as local: --allow-eval is the real gate.
    """
    located = record.get("located_at")
    if not located:
        return not record["figure"].startswith(ROOT_TOKEN)
    if portable(located)[1] is not None:
        return True
    try:
        claimed = resolve(record["figure"])
    except ReplayError:
        return True
    return _same(claimed) != _same(located)


def keep_evals(records: list[dict], include_evals: bool, trust_foreign: bool) -> tuple[list[dict], list[str]]:
    """Drop recorded evals the caller did not ask for, or does not trust (spec §7 Trust)."""
    kept: list[dict] = []
    warnings: list[str] = []
    for record in records:
        deps = record.get("depends_on") or []
        evals = [d for d in deps if d["call"]["tool"] == "qgis_eval"]
        foreign = _foreign(record)
        if evals and (not include_evals or (foreign and not trust_foreign)):
            why = ("include_evals=False" if not include_evals
                   else "the sidecar is outside DROPBOX_ROOT or not where it says; "
                        "pass trust_foreign=True to keep them")
            warnings.append(f"{_clean(record['figure'])}: {len(evals)} qgis_eval call(s) left out ({why})")
            record = {**record, "depends_on": [d for d in deps if d["call"]["tool"] != "qgis_eval"]}
        kept.append(record)
    return kept, warnings


def _skip_reason(record: dict, figure: str) -> str | None:
    if record.get("schema") != SCHEMA:
        return "unknown schema"
    if not _well_formed(record):
        return "malformed sidecar"
    if _tool_function(record) is None:
        return "unknown tool"
    if any(_network(p) for p in _record_paths(record)):
        return "network path"
    tool, args = record["call"]["tool"], record["call"]["arguments"]
    deps = record.get("depends_on", [])
    if not all(_dependency_ok(d) for d in deps):
        return "invalid depends_on"
    state_kind = ledger.kind(tool, args)
    if state_kind == "map" and not _str_list(args.get("layer_ids") or []):
        return "malformed sidecar"
    if deps and state_kind not in ("map", "export"):
        return "invalid depends_on"
    if (state_kind == "map" or deps) and not _covers_state(record):
        return "needs session state"
    if tool in _QUERY_TOOLS or (tool == "qgis_render" and args.get("mode") == "duckdb"):
        return "runs a recorded query"
    if not _arguments_agree(record):
        return "arguments disagree with sidecar"
    if not _safe_file_names(args):
        return "unsafe file name"
    recorded = record.get("figure_sha256")
    now = fingerprint(figure)["sha256"] if recorded and os.path.exists(figure) else None
    if now and now != recorded:
        return "stale sidecar: figure changed"
    return None


def collect(figures: list[str] | None, folder: str | None) -> tuple[list[dict], list[dict], list[str]]:
    """(replayable records, skipped figures, warnings), following made_by links."""
    skipped: list[dict] = []
    warnings: list[str] = []
    # (sidecar, the sha256 its figure had when a consumer read it — None for requested figures)
    queue: list[tuple[str, str | None]] = []
    if folder is not None:
        for path in sorted(Path(folder).glob("*" + SIDECAR_SUFFIX)):
            if "conflicted copy" in path.name:
                skipped.append({"figure": path.name, "reason": "conflicted copy"})
            else:
                queue.append((str(path), None))
    else:
        queue = [(f if f.endswith(SIDECAR_SUFFIX) else sidecar_path(normalise(f)), None) for f in figures or []]
    records: list[dict] = []
    seen: set[str] = set()
    while queue:
        if len(seen) >= MAX_SIDECARS:
            warnings.append(f"stopped after {MAX_SIDECARS} sidecars; the rest were not read")
            break
        raw, read_as = queue.pop(0)
        side = normalise(raw)
        if side in seen:
            continue
        figure = side[: -len(SIDECAR_SUFFIX)]
        record = _load_sidecar(side)
        if read_as is not None and record is not None and record.get("figure_sha256") != read_as:
            # The producer ran again after the consumer read its file: this sidecar
            # describes a different file, so the consumer reads it as a source input.
            warnings.append(f"{_clean(figure)} was re-made after a recorded call read it; "
                            "that call reads it as a source input")
            continue
        seen.add(side)
        if record is None:
            skipped.append({"figure": figure, "reason": "no sidecar"})
            continue
        reason = _skip_reason(record, figure)
        if reason:
            skipped.append({"figure": figure, "reason": reason})
            continue
        records.append({**record, "located_at": figure})  # where it was read, for _foreign
        for entry in record.get("inputs", []):
            link, digest = entry.get("made_by"), entry.get("sha256")
            if link and digest and link.endswith(SIDECAR_SUFFIX):
                queue.append((_local(link), digest))
    return records, skipped, warnings


_PROJECT_TOOLS = frozenset({"qgis_export_layout", "qgis_export_atlas", "qgis_batch_render"})


def _call_key(record: dict) -> tuple[str, int]:
    seq = record.get("seq")
    return str(record.get("session_id")), seq if isinstance(seq, int) else 0


def plan_steps(records: list[dict]) -> list[dict]:
    """One step per (session_id, seq), producers before consumers, then by key.

    An input is read from a replayed step only when that step's recorded file
    has exactly the sha256 the input was read with; anything else (a re-made
    intermediate, a deck appended in place) stays a source input and is
    checked. Each step comes back with ``replayed_inputs`` for build_script.
    """
    by_key: dict[tuple[str, int], dict] = {}
    for record in records:
        by_key.setdefault(_call_key(record), record)
    made = {r.get("figure"): (_call_key(r), r.get("figure_sha256")) for r in records}
    steps: dict[tuple[str, int], dict] = {}
    needs: dict[tuple[str, int], set] = {}
    for key, rec in by_key.items():
        replayed = sorted({
            e["path"] for e in rec.get("inputs") or []
            if isinstance(e, dict) and e.get("sha256") and e.get("path") in made
            and made[e["path"]][1] == e["sha256"] and made[e["path"]][0] != key
        })
        steps[key] = {**rec, "replayed_inputs": replayed,
                      "dependencies": sorted(rec.get("depends_on") or [], key=lambda d: d["seq"])}
        needs[key] = {made[p][0] for p in replayed}
    ordered: list[tuple[str, int]] = []
    while len(ordered) < len(steps):
        waiting = sorted(k for k in steps if k not in ordered)
        ready = [k for k in waiting if needs[k] <= set(ordered)]
        ordered.append((ready or waiting)[0])  # a cycle needs identical bytes both ways; fall back to key order
    return [steps[k] for k in ordered]


def outputs_of(steps: list[dict]) -> list[str]:
    seen: list[str] = []
    for step in steps:
        seen.extend(o for o in step.get("outputs") or [] if o not in seen)
    return seen


def source_inputs(steps: list[dict]) -> list[dict]:
    """Inputs the script checks: every recorded fingerprint not read from a replayed step.

    Keyed by (path, sha256, bytes): two calls that read different versions of
    one file are both checked, so at least one of them reports the change.
    """
    found: dict[tuple, dict] = {}
    for step in steps:
        replayed = set(step.get("replayed_inputs") or [])
        dependency_inputs = [i for d in step.get("dependencies") or [] for i in d.get("inputs") or []]
        for entry in (step.get("inputs") or []) + (step.get("implicit_inputs") or []) + dependency_inputs:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or path in replayed:
                continue
            item = {"path": path, "sha256": entry.get("sha256"), "bytes": entry.get("bytes")}
            found.setdefault((path, item["sha256"], item["bytes"]), item)
    return list(found.values())


def notes_for(steps: list[dict]) -> list[str]:
    notes: list[str] = []
    for n, step in enumerate(steps, 1):
        tool = step["call"]["tool"]
        kind = step["call"]["arguments"].get("kind")
        if not step.get("dependencies") and (
                tool in _PROJECT_TOOLS or (tool == "qgis_export" and kind in ("layout", "atlas", "batch"))):
            notes.append(f"step {n}: reads its .qgz from disk; styling applied earlier in the session is not recorded")
        if any(d["call"]["tool"] == "qgis_eval" for d in step.get("dependencies") or []):
            notes.append(f"step {n}: files written inside replayed qgis_eval code are not remapped into --out-dir")
        for text in step.get("unrecorded_state") or []:
            notes.append(f"step {n}: {_clean(text)}")
        for entry in step.get("machine_specific") or []:
            if isinstance(entry, dict):
                notes.append(f"step {n}: machine-specific {_clean(entry.get('path'))}")
        for entry in step.get("remote") or []:
            if isinstance(entry, dict):
                if entry.get("argument") == "basemap":
                    notes.append(f"step {n}: {_clean(entry.get('argument'))} tiles are fetched live and not pinned")
                else:  # a project layer on a database or web service
                    notes.append(f"step {n}: {_clean(entry.get('argument'))}: {_clean(entry.get('note'))} "
                                 f"({_clean(entry.get('value'))})")
    return notes


_SKELETON = '''
"""DOC"""
from qgis_mcp_workflows import compound, replay, server

INPUTS = None
OUTPUTS = None
NOTES = None
EVALS = None


def steps(out, src):
    pass


if __name__ == "__main__":
    raise SystemExit(replay.main(__file__, INPUTS, OUTPUTS, NOTES, steps, evals=EVALS))
'''


def _literal(value: Any) -> ast.expr:
    if value is None or isinstance(value, bool | int | float | str):
        return ast.Constant(value)
    if isinstance(value, list):
        return ast.List([_literal(v) for v in value], ast.Load())
    if isinstance(value, dict):
        return ast.Dict([ast.Constant(str(k)) for k in value], [_literal(v) for v in value.values()])
    return ast.Constant(str(value))


def _argument(name: str, value: Any, replayed: set[str], layers: dict[str, str]) -> ast.expr:
    """Outputs go through out(); an input through out() only if a replayed step re-makes it.

    A layer id a replayed load produced becomes that load's variable.
    """
    def path(v: Any) -> ast.expr:
        if not isinstance(v, str):
            return _literal(v)
        func = "out" if name in OUTPUT_ARGS or v in replayed else "src"
        return ast.Call(ast.Name(func, ast.Load()), [ast.Constant(v)], [])

    def layer(v: Any) -> ast.expr:
        return ast.Name(layers[v], ast.Load()) if isinstance(v, str) and v in layers else _literal(v)

    if name == "layer_id":
        return layer(value)
    if name == "layer_ids" and isinstance(value, list):
        return ast.List([layer(v) for v in value], ast.Load())
    if name in OUTPUT_ARGS | INPUT_ARGS and value not in (None, ""):
        return path(value)
    if name in INPUT_LIST_ARGS and isinstance(value, list):
        return ast.List([path(v) for v in value], ast.Load())
    return _literal(value)


def _call(step: dict, layers: dict[str, str]) -> ast.Call:
    call = step["call"]  # tool and argument names were validated at collect time
    replayed = set(step.get("replayed_inputs") or [])
    func = ast.Attribute(ast.Name(call["module"], ast.Load()), call["tool"], ast.Load())
    keywords = [ast.keyword(k, _argument(k, v, replayed, layers)) for k, v in call["arguments"].items()]
    return ast.Call(func, [], keywords)


def _step_call(step: dict, layers: dict[str, str]) -> ast.stmt:
    return ast.Expr(_call(step, layers))


def _dependency_call(dep: dict, layers: dict[str, str], names: Any) -> ast.stmt:
    """A replayed load binds layer_N to the id it produces; later calls use the name."""
    call = _call({**dep, "replayed_inputs": []}, layers)  # a dependency reads only checked source inputs
    kind = ledger.kind(dep["call"]["tool"], dep["call"]["arguments"])
    produced = dep.get("layer_id")
    if kind == "load" and produced:
        name = f"layer_{next(names)}"
        layers[produced] = name
        return ast.Assign([ast.Name(name, ast.Store())], ast.Attribute(call, "layer_id", ast.Load()))
    if kind == "eval":  # only evals that succeeded were recorded: a failure at replay must stop it
        check = ast.Attribute(ast.Name("replay", ast.Load()), "require_ok", ast.Load())
        return ast.Expr(ast.Call(check, [call], []))
    return ast.Expr(call)


def _reset() -> ast.stmt:
    return ast.Expr(ast.Call(ast.Attribute(ast.Name("replay", ast.Load()), "reset_session", ast.Load()), [], []))


def evals_of(steps: list[dict]) -> list[dict]:
    """What the script shows before it refuses to run without --allow-eval."""
    found: dict[str, dict] = {}
    for step in steps:
        for dep in step.get("dependencies") or []:
            if dep["call"]["tool"] != "qgis_eval":
                continue
            code = str(dep["call"]["arguments"].get("code", ""))
            digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
            lines = code.splitlines()
            item = found.setdefault(digest, {"sha256": digest, "first_lines": [], "figures": [],
                                             "lines": len(lines), "bytes": len(code.encode("utf-8")), "code": code})
            item["first_lines"] = [_clean(line) for line in lines[:3]]
            item["figures"] += [_clean(o) for o in step.get("outputs") or [] if _clean(o) not in item["figures"]]
    return list(found.values())


def build_script(steps: list[dict], inputs: list[dict], outputs: list[str], notes: list[str],
                 generated_on: str, evals: Any = ()) -> str:
    tree = ast.parse(_SKELETON)
    doc, _imports, inputs_node, outputs_node, notes_node, evals_node, steps_def, _main = tree.body
    doc.value = ast.Constant(
        f"Replay of {len(outputs)} file(s) from {len(steps)} recorded call(s), "
        f"generated by qgis_export_session on {generated_on}.\n\n"
        "Run:  uv run --no-sync python <this file> [--out-dir DIR | --in-place --yes] [--force] [--allow-eval]\n")
    inputs_node.value = _literal(inputs)
    outputs_node.value = _literal(outputs)
    notes_node.value = _literal(notes)
    evals_node.value = _literal(list(evals))
    body: list[ast.stmt] = []
    names = itertools.count(1)
    previous = None
    previous_deps: list = []
    for step in steps:
        deps = step.get("dependencies") or []
        session = step.get("session_id")
        if previous is not None and (deps or previous_deps or session != previous):
            body.append(_reset())  # neither side may inherit the other's layers, styles or eval side effects
        layers: dict[str, str] = {}
        body += [_dependency_call(d, layers, names) for d in deps]
        body.append(_step_call(step, layers))
        previous, previous_deps = session, deps
    steps_def.body = body or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def _fail(message: str, next_step: str) -> NoReturn:
    from qgis_mcp_workflows.errors import QgisMcpWorkflowsError

    raise QgisMcpWorkflowsError(f"{message} Next: {next_step}.")


def export_session(output_py: str, figures: list[str] | None = None, folder: str | None = None,
                   include_evals: bool = True, trust_foreign: bool = False, overwrite: bool = False) -> dict:
    """Write a replay script for the given figures (or every figure in folder)."""
    if (figures is None) == (folder is None):
        _fail("qgis_export_session needs exactly one of figures or folder.",
              "pass figures=[...] or folder=...")
    target = Path(output_py).expanduser().resolve()
    if target.suffix != ".py":
        _fail(f"output_py must be a .py file, got {target.name!r}.", "pass output_py ending in .py")
    if target.exists() and not overwrite:
        _fail(f"{target} already exists.", "pick another output_py, or pass overwrite=True")
    if figures is not None and not figures:
        _fail("figures is empty.", "pass at least one figure path, or folder=...")
    if folder is not None:
        folder = str(Path(folder).expanduser())
        if not Path(folder).is_dir():
            _fail(f"{_clean(folder)} is not a folder.", "pass an existing folder, or figures=[...]")
    records, skipped, warnings = collect(figures, folder)
    records, eval_warnings = keep_evals(records, include_evals, trust_foreign)
    warnings = warnings + eval_warnings
    steps = plan_steps(records)
    if not steps:
        why = "; ".join(f"{_clean(s['figure'])}: {s['reason']}" for s in skipped[:5]) or "no sidecars found"
        _fail(f"nothing to replay ({why}).",
              "pick figures written through MCP that still have their .provenance.json beside them")
    outputs = outputs_of(steps)
    evals = evals_of(steps)
    source = build_script(steps, source_inputs(steps), outputs, notes_for(steps),
                          _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d"), evals)
    compile(source, str(target), "exec")  # the generator must never emit invalid code
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return {
        "output_path": str(target),
        "n_figures": len(outputs),
        "n_calls": len(steps),
        "n_evals": len(evals),
        "skipped": [{"figure": _clean(s["figure"]), "reason": s["reason"]} for s in skipped],
        "warnings": [_clean(w) for w in warnings],
    }

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
import inspect
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from qgis_mcp_workflows.provenance import (
    INPUT_ARGS,
    INPUT_LIST_ARGS,
    OUTPUT_ARGS,
    SCHEMA,
    SIDECAR_SUFFIX,
    _regular_stat,
    _relative_under,
    _writes_files,
    fingerprint,
    normalise,
    sidecar_path,
)
from qgis_mcp_workflows.replay import ReplayError, _clean, resolve

MAX_SIDECARS = 200
_SIDECAR_MAX_BYTES = 2 * 1024 * 1024
_STATE_READING_TOOLS = frozenset({"qgis_render_map"})
# A recorded SQL query is code: DuckDB can read files or fetch URLs from inside a
# SELECT, so it is not replayed from an unsigned sidecar.
_QUERY_TOOLS = frozenset({"qgis_render_from_duckdb"})
_MAX_DEPTH = 16
_MAX_ITEMS = 10_000
_NAME_BREAKERS = re.compile(r"[/\\:]")
_GDAL_NETWORK = re.compile(r"/vsi[a-z0-9_]*/")


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
            and isinstance(remote, list) and all(isinstance(e, dict) for e in remote))


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
    """UNC, URL or GDAL network path: opening it would contact another host."""
    return isinstance(path, str) and (
        path.startswith(("//", "\\\\")) or "://" in path or _GDAL_NETWORK.match(path) is not None)


def _record_paths(record: dict) -> list:
    paths: list = [record["figure"], *record["outputs"]]
    for key in ("inputs", "implicit_inputs"):
        for entry in record.get(key, []):
            paths += [entry["path"], entry.get("made_by")]
    ins, outs, dirs = _argument_paths(record["call"]["arguments"])
    return paths + ins + outs + dirs


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
    if tool in _STATE_READING_TOOLS or (tool == "qgis_render" and args.get("mode") == "map"):
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
        records.append(record)
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
        steps[key] = {**rec, "replayed_inputs": replayed}
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
        for entry in (step.get("inputs") or []) + (step.get("implicit_inputs") or []):
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
        if tool in _PROJECT_TOOLS or (tool == "qgis_export" and kind in ("layout", "atlas", "batch")):
            notes.append(f"step {n}: reads its .qgz from disk; styling applied earlier in the session is not recorded")
        for text in step.get("unrecorded_state") or []:
            notes.append(f"step {n}: {_clean(text)}")
        for entry in step.get("machine_specific") or []:
            if isinstance(entry, dict):
                notes.append(f"step {n}: machine-specific {_clean(entry.get('path'))}")
        for entry in step.get("remote") or []:
            if isinstance(entry, dict):
                notes.append(f"step {n}: {_clean(entry.get('argument'))} tiles are fetched live and not pinned")
    return notes


_SKELETON = '''
"""DOC"""
from qgis_mcp_workflows import compound, replay, server

INPUTS = None
OUTPUTS = None
NOTES = None


def steps(out, src):
    pass


if __name__ == "__main__":
    raise SystemExit(replay.main(__file__, INPUTS, OUTPUTS, NOTES, steps))
'''


def _literal(value: Any) -> ast.expr:
    if value is None or isinstance(value, bool | int | float | str):
        return ast.Constant(value)
    if isinstance(value, list):
        return ast.List([_literal(v) for v in value], ast.Load())
    if isinstance(value, dict):
        return ast.Dict([ast.Constant(str(k)) for k in value], [_literal(v) for v in value.values()])
    return ast.Constant(str(value))


def _argument(name: str, value: Any, replayed: set[str]) -> ast.expr:
    """Outputs go through out(); an input through out() only if a replayed step re-makes it."""
    def path(v: Any) -> ast.expr:
        if not isinstance(v, str):
            return _literal(v)
        func = "out" if name in OUTPUT_ARGS or v in replayed else "src"
        return ast.Call(ast.Name(func, ast.Load()), [ast.Constant(v)], [])

    if name in OUTPUT_ARGS | INPUT_ARGS and value not in (None, ""):
        return path(value)
    if name in INPUT_LIST_ARGS and isinstance(value, list):
        return ast.List([path(v) for v in value], ast.Load())
    return _literal(value)


def _step_call(step: dict) -> ast.stmt:
    call = step["call"]  # tool and argument names were validated by _tool_function
    replayed = set(step.get("replayed_inputs") or [])
    func = ast.Attribute(ast.Name(call["module"], ast.Load()), call["tool"], ast.Load())
    keywords = [ast.keyword(k, _argument(k, v, replayed)) for k, v in call["arguments"].items()]
    return ast.Expr(ast.Call(func, [], keywords))


def build_script(steps: list[dict], inputs: list[dict], outputs: list[str], notes: list[str],
                 generated_on: str) -> str:
    tree = ast.parse(_SKELETON)
    doc, _imports, inputs_node, outputs_node, notes_node, steps_def, _main = tree.body
    doc.value = ast.Constant(
        f"Replay of {len(outputs)} file(s) from {len(steps)} recorded call(s), "
        f"generated by qgis_export_session on {generated_on}.\n\n"
        "Run:  uv run --no-sync python <this file> [--out-dir DIR | --in-place --yes] [--force]\n")
    inputs_node.value = _literal(inputs)
    outputs_node.value = _literal(outputs)
    notes_node.value = _literal(notes)
    steps_def.body = [_step_call(s) for s in steps] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def _fail(message: str, next_step: str) -> NoReturn:
    from qgis_mcp_workflows.errors import QgisMcpWorkflowsError

    raise QgisMcpWorkflowsError(f"{message} Next: {next_step}.")


def export_session(output_py: str, figures: list[str] | None = None, folder: str | None = None,
                   overwrite: bool = False) -> dict:
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
    steps = plan_steps(records)
    if not steps:
        why = "; ".join(f"{_clean(s['figure'])}: {s['reason']}" for s in skipped[:5]) or "no sidecars found"
        _fail(f"nothing to replay ({why}).",
              "pick figures written through MCP that still have their .provenance.json beside them")
    outputs = outputs_of(steps)
    source = build_script(steps, source_inputs(steps), outputs, notes_for(steps),
                          _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d"))
    compile(source, str(target), "exec")  # the generator must never emit invalid code
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return {
        "output_path": str(target),
        "n_figures": len(outputs),
        "n_calls": len(steps),
        "skipped": [{"figure": _clean(s["figure"]), "reason": s["reason"]} for s in skipped],
        "warnings": [_clean(w) for w in warnings],
    }

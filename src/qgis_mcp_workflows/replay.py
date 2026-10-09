"""Replay figures from their provenance sidecars (TASK-13).

Export half (``export_session``): read sidecars, follow ``made_by`` chains,
order the calls and write a standalone script. The script is built as an AST
from a fixed skeleton — a sidecar is unsigned input, so its values only ever
become literal constants.

Runtime half (``main`` and helpers): what the generated script imports. It
resolves ``${DROPBOX_ROOT}``, checks source inputs, remaps outputs and runs the
steps through a headless executor.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md §7.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import inspect
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qgis_mcp_workflows.provenance import (
    INPUT_ARGS,
    INPUT_LIST_ARGS,
    OUTPUT_ARGS,
    ROOT_TOKEN,
    SCHEMA,
    SIDECAR_SUFFIX,
    _regular_stat,
    _relative_under,
    fingerprint,
    normalise,
    sidecar_path,
)


class ReplayError(Exception):
    """A replay precondition failed; ``main`` prints it and exits 2."""


def resolve(path: str) -> str:
    """Expand ``${DROPBOX_ROOT}`` (the only variable expanded) to this machine's root."""
    if not path.startswith(ROOT_TOKEN):
        return path
    root = os.environ.get("DROPBOX_ROOT", "").strip()
    if not root:
        raise ReplayError("set DROPBOX_ROOT to this machine's Dropbox folder, then re-run")
    full = normalise(os.path.join(root, path[len(ROOT_TOKEN):].lstrip("/")))
    if _relative_under(full, normalise(root)) is None:
        raise ReplayError(f"path leaves DROPBOX_ROOT: {path!r}")
    return full


def check_inputs(inputs: list[dict]) -> list[str]:
    """One line per source input that is missing or changed since it was recorded.

    sha256 when the recording hashed it; otherwise size (mtime is not compared:
    Dropbox sync rewrites it).
    """
    problems: list[str] = []
    for entry in inputs:
        now = fingerprint(resolve(entry["path"]))
        recorded_sha, recorded_bytes = entry.get("sha256"), entry.get("bytes")
        if now["missing"]:
            problems.append(f"missing  {entry['path']}")
        elif recorded_sha and now["sha256"] != recorded_sha:
            problems.append(f"changed  {entry['path']} (sha256 differs)")
        elif not recorded_sha and recorded_bytes is not None and now["bytes"] != recorded_bytes:
            problems.append(f"changed  {entry['path']} (size {recorded_bytes} -> {now['bytes']})")
    return problems


def _relative_target(path: str) -> Path:
    """Where a recorded output lands under --out-dir."""
    if path.startswith(ROOT_TOKEN):
        return Path("DROPBOX_ROOT", *path[len(ROOT_TOKEN):].lstrip("/").split("/"))
    drive, rest = os.path.splitdrive(normalise(path))
    head = drive.replace(":", "").strip("/").replace("/", "_") or "root"
    return Path("_abs", head, *[p for p in rest.split("/") if p])


def output_mapper(outputs: list[str], sources: list[str], out_dir: str,
                  in_place: bool, yes: bool) -> Callable[[str], str]:
    """The ``out(path)`` the steps call for every recorded output."""
    if in_place:
        source_paths = {normalise(resolve(s)) for s in sources}
        clash = sorted(o for o in outputs if normalise(resolve(o)) in source_paths)
        if clash:
            raise ReplayError("--in-place would overwrite recorded inputs: " + ", ".join(clash))
        existing = sorted(resolve(o) for o in outputs if os.path.exists(resolve(o)))
        if existing and not yes:
            raise ReplayError("--in-place would overwrite:\n  " + "\n  ".join(existing)
                              + "\nre-run with --yes to replace them")
        return resolve
    base = Path(out_dir).resolve()

    def out(path: str) -> str:
        target = (base / _relative_target(path)).resolve()
        if base != target and base not in target.parents:
            raise ReplayError(f"output leaves --out-dir: {path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)

    return out


def _new_executor():
    from qgis_mcp_workflows.executors.headless import HeadlessExecutor

    return HeadlessExecutor()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-make figures recorded by qgis-mcp-workflows.")
    parser.add_argument("--out-dir", help="where replayed files go (default: replay_<UTC date>/ beside this script)")
    parser.add_argument("--in-place", action="store_true", help="write to the original paths instead")
    parser.add_argument("--yes", action="store_true", help="with --in-place: replace existing files")
    parser.add_argument("--force", action="store_true", help="replay even if source inputs changed")
    return parser.parse_args(argv)


def main(script: str, inputs: list[dict], outputs: list[str], notes: list[str],
         steps: Callable[[Callable, Callable], None], argv: list[str] | None = None) -> int:
    """Entry point of a generated replay script. Returns the process exit code."""
    args = _parse(argv)
    for note in notes:
        print(f"note: {note}")
    try:
        if args.in_place and args.force:
            raise ReplayError("--in-place cannot be combined with --force")
        problems = check_inputs(inputs)
        if problems:
            print("source inputs differ from the recording:")
            for line in problems:
                print(f"  {line}")
            if not args.force:
                print("re-run with --force to replay anyway")
                return 2
        out_dir = args.out_dir or os.path.join(
            os.path.dirname(os.path.abspath(script)),
            "replay_" + _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d"))
        out = output_mapper(outputs, [i["path"] for i in inputs], out_dir, args.in_place, args.yes)
    except ReplayError as err:
        print(f"replay: {err}", file=sys.stderr)
        return 2
    from qgis_mcp_workflows import executors

    executor = _new_executor()
    executors.set_executor(executor)
    try:
        steps(out, resolve)
    except ReplayError as err:
        print(f"replay: {err}", file=sys.stderr)
        return 2
    finally:
        shutdown = getattr(executor, "shutdown", None)
        if callable(shutdown):
            shutdown()
    print(f"replayed {len(outputs)} file(s)" + ("" if args.in_place else f" into {out_dir}"))
    return 0


# --- export side ---------------------------------------------------------------------

MAX_SIDECARS = 200
_SIDECAR_MAX_BYTES = 2 * 1024 * 1024
_STATE_READING_TOOLS = frozenset({"qgis_render_map"})


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
    """The registered tool a sidecar names — None unless every argument is a real parameter."""
    from qgis_mcp_workflows import compound, server

    call = record.get("call")
    if not isinstance(call, dict) or not isinstance(call.get("arguments"), dict):
        return None
    module = {"server": server, "compound": compound}.get(call.get("module"))
    name = call.get("tool")
    if module is None or not isinstance(name, str) or not name.startswith("qgis_"):
        return None
    fn = getattr(module, name, None)
    if not inspect.isfunction(fn) or fn.__module__ != module.__name__:
        return None
    return fn if set(call["arguments"]) <= set(inspect.signature(fn).parameters) else None


def _skip_reason(record: dict, figure: str) -> str | None:
    if record.get("schema") != SCHEMA:
        return "unknown schema"
    if _tool_function(record) is None:
        return "unknown tool"
    call = record["call"]
    if call["tool"] in _STATE_READING_TOOLS or (
        call["tool"] == "qgis_render" and call["arguments"].get("mode") == "map"
    ):
        return "needs session state"
    recorded = record.get("figure_sha256")
    if recorded and os.path.exists(figure) and fingerprint(figure)["sha256"] != recorded:
        return "stale sidecar: figure changed"
    return None


def collect(figures: list[str] | None, folder: str | None) -> tuple[list[dict], list[dict], list[str]]:
    """(replayable records, skipped figures, warnings), following made_by links."""
    skipped: list[dict] = []
    warnings: list[str] = []
    queue: list[str] = []
    if folder is not None:
        for path in sorted(Path(folder).glob("*" + SIDECAR_SUFFIX)):
            if "conflicted copy" in path.name:
                skipped.append({"figure": path.name, "reason": "conflicted copy"})
            else:
                queue.append(str(path))
    else:
        queue = [f if f.endswith(SIDECAR_SUFFIX) else sidecar_path(normalise(f)) for f in figures or []]
    records: list[dict] = []
    seen: set[str] = set()
    while queue:
        if len(seen) >= MAX_SIDECARS:
            warnings.append(f"stopped after {MAX_SIDECARS} sidecars; the rest were not read")
            break
        side = normalise(queue.pop(0))
        if side in seen:
            continue
        seen.add(side)
        figure = side[: -len(SIDECAR_SUFFIX)]
        record = _load_sidecar(side)
        if record is None:
            skipped.append({"figure": figure, "reason": "no sidecar"})
            continue
        reason = _skip_reason(record, figure)
        if reason:
            skipped.append({"figure": figure, "reason": reason})
            continue
        records.append(record)
        for entry in record.get("inputs") or []:
            link = entry.get("made_by") if isinstance(entry, dict) else None
            if isinstance(link, str):
                queue.append(_local(link))
    return records, skipped, warnings


_TEXT_MAX = 200
_PROJECT_TOOLS = frozenset({"qgis_export_layout", "qgis_export_atlas", "qgis_batch_render"})


def _clean(text: Any) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(text))[:_TEXT_MAX]


def _call_key(record: dict) -> tuple[str, int]:
    seq = record.get("seq")
    return str(record.get("session_id")), seq if isinstance(seq, int) else 0


def plan_steps(records: list[dict]) -> list[dict]:
    """One step per (session_id, seq), producers before consumers, then by key."""
    by_key: dict[tuple[str, int], dict] = {}
    for record in records:
        by_key.setdefault(_call_key(record), record)
    produced = {out: key for key, rec in by_key.items() for out in rec.get("outputs") or []}
    needs = {
        key: {produced[i["path"]] for i in rec.get("inputs") or []
              if isinstance(i, dict) and produced.get(i.get("path")) not in (None, key)}
        for key, rec in by_key.items()
    }
    ordered: list[tuple[str, int]] = []
    while len(ordered) < len(by_key):
        waiting = sorted(k for k in by_key if k not in ordered)
        ready = [k for k in waiting if needs[k] <= set(ordered)]
        ordered.append((ready or waiting)[0])  # a cycle falls back to key order
    return [by_key[k] for k in ordered]


def outputs_of(steps: list[dict]) -> list[str]:
    seen: list[str] = []
    for step in steps:
        seen.extend(o for o in step.get("outputs") or [] if o not in seen)
    return seen


def source_inputs(steps: list[dict]) -> list[dict]:
    """Inputs the script checks: everything not produced by one of its own steps."""
    produced = set(outputs_of(steps))
    found: dict[str, dict] = {}
    for step in steps:
        for entry in (step.get("inputs") or []) + (step.get("implicit_inputs") or []):
            path = entry.get("path") if isinstance(entry, dict) else None
            if isinstance(path, str) and path not in produced and path not in found:
                found[path] = {"path": path, "sha256": entry.get("sha256"), "bytes": entry.get("bytes")}
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


def _argument(name: str, value: Any, outputs: set[str]) -> ast.expr:
    def path(v: Any) -> ast.expr:
        if not isinstance(v, str):
            return _literal(v)
        func = "out" if name in OUTPUT_ARGS or v in outputs else "src"
        return ast.Call(ast.Name(func, ast.Load()), [ast.Constant(v)], [])

    if name in OUTPUT_ARGS | INPUT_ARGS and value not in (None, ""):
        return path(value)
    if name in INPUT_LIST_ARGS and isinstance(value, list):
        return ast.List([path(v) for v in value], ast.Load())
    return _literal(value)


def _step_call(step: dict, outputs: set[str]) -> ast.stmt:
    call = step["call"]  # tool and argument names were validated by _tool_function
    func = ast.Attribute(ast.Name(call["module"], ast.Load()), call["tool"], ast.Load())
    keywords = [ast.keyword(k, _argument(k, v, outputs)) for k, v in call["arguments"].items()]
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
    produced = set(outputs)
    steps_def.body = [_step_call(s, produced) for s in steps] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def _fail(message: str, next_step: str) -> None:
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
    records, skipped, warnings = collect(figures, folder)
    steps = plan_steps(records)
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

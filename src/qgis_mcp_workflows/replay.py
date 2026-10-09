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
import datetime as _dt
import os
import sys
from collections.abc import Callable
from pathlib import Path

from qgis_mcp_workflows.provenance import ROOT_TOKEN, _relative_under, fingerprint, normalise


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

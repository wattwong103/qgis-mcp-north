"""Replay runtime: what a script written by qgis_export_session imports (TASK-13).

It resolves ``${DROPBOX_ROOT}``, checks source inputs, remaps outputs and runs
the steps through a headless executor. The export half (reading sidecars and
building the script) lives in ``session_export``.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md §7.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qgis_mcp_workflows.provenance import (
    ROOT_TOKEN,
    _relative_under,
    fingerprint,
    normalise,
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


_TEXT_MAX = 200


def _printable(text: Any) -> str:
    """Every character a terminal would not show as itself (controls, bidi overrides,
    zero-width and line-separator characters) becomes a space."""
    return "".join(c if c.isprintable() else " " for c in str(text))


def _clean(text: Any) -> str:
    """Printable and short."""
    return _printable(text)[:_TEXT_MAX]


def check_inputs(inputs: list[dict]) -> list[str]:
    """One line per source input that is missing or changed since it was recorded.

    sha256 when both the recording and this machine hashed it; otherwise size
    (mtime is not compared: Dropbox sync rewrites it).
    """
    problems: list[str] = []
    for entry in inputs:
        now = fingerprint(resolve(entry["path"]))
        shown = _clean(entry["path"])
        recorded_sha, recorded_bytes = entry.get("sha256"), entry.get("bytes")
        both_hashed = bool(recorded_sha and now["sha256"])
        if now["missing"]:
            problems.append(f"missing  {shown}")
        elif both_hashed and now["sha256"] != recorded_sha:
            problems.append(f"changed  {shown} (sha256 differs)")
        elif not both_hashed and recorded_bytes is not None and now["bytes"] != recorded_bytes:
            problems.append(f"changed  {shown} (size {recorded_bytes} -> {now['bytes']})")
    return problems


_DRIVE = re.compile(r"[A-Za-z]:/")


def _relative_target(path: str) -> Path:
    """Where a recorded output lands under --out-dir — the same on every OS.

    The path was recorded on some machine, maybe another OS, so it is parsed as
    text and never through this machine's abspath.
    """
    if path.startswith(ROOT_TOKEN):
        return Path("DROPBOX_ROOT", *path[len(ROOT_TOKEN):].lstrip("/").split("/"))
    raw = path.replace("\\", "/")
    head, rest = (raw[0], raw[3:]) if _DRIVE.match(raw) else ("root", raw)
    return Path("_abs", head, *[p for p in rest.split("/") if p])


def _same(path: str) -> str:
    """Comparison form: normalised, case-folded where the file system ignores case."""
    norm = normalise(path)
    return norm.casefold() if sys.platform in ("win32", "darwin") else norm


def output_mapper(outputs: list[str], sources: list[str], out_dir: str,
                  in_place: bool, yes: bool) -> Callable[[str], str]:
    """The ``out(path)`` the steps call for every recorded output."""
    if in_place:
        source_paths = {_same(resolve(s)) for s in sources}
        clash = sorted(_clean(o) for o in outputs if _same(resolve(o)) in source_paths)
        if clash:
            raise ReplayError("--in-place would overwrite recorded inputs: " + ", ".join(clash))
        existing = sorted(_clean(resolve(o)) for o in outputs if os.path.exists(resolve(o)))
        if existing and not yes:
            raise ReplayError("--in-place would overwrite:\n  " + "\n  ".join(existing)
                              + "\nre-run with --yes to replace them")
        declared = {_same(resolve(o)) for o in outputs}

        def write_back(path: str) -> str:
            full = resolve(path)
            here = _same(full)
            if here not in declared and not any(_relative_under(d, here) for d in declared):
                raise ReplayError(f"a step writes a path the script does not declare: {path!r}")
            return full

        return write_back
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
    parser.add_argument("--allow-eval", action="store_true",
                        help="run the recorded qgis_eval code (read it first with --show-evals)")
    parser.add_argument("--show-evals", action="store_true",
                        help="print the full recorded qgis_eval code, numbered, and run nothing")
    return parser.parse_args(argv)


def reset_session() -> None:
    """Continue in a fresh QGIS, so earlier steps' layers, styles and eval side effects do not carry over."""
    from qgis_mcp_workflows import executors

    old = executors._current
    executors.set_executor(_new_executor())
    shutdown = getattr(old, "shutdown", None)
    if callable(shutdown):
        shutdown()


def require_ok(result: Any) -> Any:
    """A replayed qgis_eval that raised inside QGIS fails the replay: only evals that
    succeeded were recorded, so a failure means the replay has diverged."""
    exception = getattr(result, "exception", None)
    if exception:
        from qgis_mcp_workflows.errors import QgisMcpWorkflowsError

        last = str(exception).strip().splitlines() or [""]
        raise QgisMcpWorkflowsError(
            f"a replayed qgis_eval failed: {_clean(last[-1])}. Next: read it with --show-evals; "
            "it may name a layer id or a path from the recording session.")
    return result


def _refuse_evals(evals: list[dict]) -> int:
    print("this replay runs qgis_eval code recorded in the sidecars; read all of it with --show-evals, "
          "then re-run with --allow-eval:", file=sys.stderr)
    for item in evals:
        figures = ", ".join(_clean(f) for f in item.get("figures") or [])
        lines, size = item.get("lines"), item.get("bytes")
        print(f"  sha256 {_clean(item.get('sha256'))}: {lines} lines, {size} bytes (used by {figures})",
              file=sys.stderr)
        shown = (item.get("first_lines") or [])[:3]
        for line in shown:
            print(f"    | {_clean(line)}", file=sys.stderr)
        if isinstance(lines, int) and lines > len(shown):
            print(f"    ... {lines - len(shown)} more line(s) not shown", file=sys.stderr)
    return 3


def _show_evals(evals: list[dict]) -> int:
    for item in evals:
        print(f"# qgis_eval sha256 {_clean(item.get('sha256'))}")
        for n, line in enumerate(str(item.get("code", "")).splitlines(), 1):
            print(f"{n:4} | {_printable(line)}")
    return 3


def main(script: str, inputs: list[dict], outputs: list[str], notes: list[str],
         steps: Callable[[Callable, Callable], None], argv: list[str] | None = None, *,
         evals: list[dict] | None = None) -> int:
    """Entry point of a generated replay script. Returns the process exit code."""
    args = _parse(argv)
    # Decide about evals before any sidecar text is printed: notes come from sidecars too.
    if evals and args.show_evals:
        return _show_evals(evals)
    if evals and not args.allow_eval:
        return _refuse_evals(evals)
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
    from qgis_mcp_workflows.errors import QgisMcpWorkflowsError

    previous = executors._current  # restored after: importers (tests, notebooks) keep theirs
    executor = None
    try:
        executor = _new_executor()
        executors.set_executor(executor)
        steps(out, resolve)
    except ReplayError as err:
        print(f"replay: {err}", file=sys.stderr)
        return 2
    except QgisMcpWorkflowsError as err:  # no QGIS here, or a tool refused: its message has the Next: step
        print(f"replay: {err}", file=sys.stderr)
        return 1
    finally:
        # A reset may have replaced the first executor: shut down whichever the run ended with, never the caller's.
        current = executors._current
        executors.set_executor(previous)
        if executor is not None and current is not previous:
            shutdown = getattr(current, "shutdown", None)
            if callable(shutdown):
                shutdown()
    print(f"replayed {len(outputs)} file(s)" + ("" if args.in_place else f" into {out_dir}"))
    return 0

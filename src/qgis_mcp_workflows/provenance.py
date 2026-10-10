"""Figure provenance: a sidecar next to every file an MCP tool call writes.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md (v2).
Only the registered (MCP) copy of a tool is wrapped (``with_provenance`` in
server._maybe_tool / _maybe_compound_tool); Python callers get the bare
function and record nothing.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import functools
import hashlib
import importlib.metadata
import importlib.util
import inspect
import itertools
import json
import logging
import math
import os
import platform
import stat
import subprocess
import tempfile
import threading
import time
import typing
import uuid
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qgis_mcp_workflows import datasources, ledger
from qgis_mcp_workflows.executors import executor_requests

logger = logging.getLogger("qgis_mcp_workflows.provenance")

SCHEMA = "qgis-mcp-workflows/provenance@1"
SIDECAR_SUFFIX = ".provenance.json"
ROOT_TOKEN = "${DROPBOX_ROOT}"


def _case_insensitive() -> bool:
    return os.name == "nt"


def normalise(path: str) -> str:
    """Absolute, ``~`` expanded, forward slashes — the one form paths are compared in."""
    return os.path.abspath(os.path.expanduser(str(path))).replace("\\", "/")


def _relative_under(path: str, root: str) -> str | None:
    """``path`` relative to ``root`` if root is a whole-component prefix, else None."""
    parts = path.rstrip("/").split("/")
    root_parts = root.rstrip("/").split("/")
    if len(parts) < len(root_parts):
        return None
    head = parts[: len(root_parts)]
    if _case_insensitive():
        same = [p.casefold() for p in head] == [r.casefold() for r in root_parts]
    else:
        same = head == root_parts
    return "/".join(parts[len(root_parts):]) if same else None


def portable(path: str) -> tuple[str, str | None]:
    """(stored form, machine-specific reason or None).

    Under DROPBOX_ROOT (or its realpath — the macOS CloudStorage folder behind
    ~/Dropbox) a path is stored as ``${DROPBOX_ROOT}/<rest>`` so it resolves on
    every machine. There is no ~/Dropbox fallback: on Windows that is a
    different Dropbox.
    """
    norm = normalise(path)
    root = os.environ.get("DROPBOX_ROOT", "").strip()
    if not root:
        return norm, "DROPBOX_ROOT unset"
    roots = {normalise(root), normalise(os.path.realpath(root))}
    for candidate in (norm, normalise(os.path.realpath(norm))):
        for r in roots:
            rest = _relative_under(candidate, r)
            if rest is not None:
                return (f"{ROOT_TOKEN}/{rest}" if rest else ROOT_TOKEN), None
    return norm, "outside DROPBOX_ROOT"


_hash_cache: dict[tuple[str, int, int], str] = {}
_cache_lock = threading.Lock()


_DEFAULT_HASH_MAX_MB = 256.0


def hash_cap_bytes() -> int:
    raw = os.environ.get("QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB", str(_DEFAULT_HASH_MAX_MB))
    try:
        megabytes = float(raw)
    except ValueError:
        megabytes = _DEFAULT_HASH_MAX_MB
    if not math.isfinite(megabytes) or megabytes < 0:  # "inf"/"nan" would raise in int()
        megabytes = _DEFAULT_HASH_MAX_MB
    return int(megabytes * 1024 * 1024)


def _utc(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, tz=_dt.UTC).isoformat().replace("+00:00", "Z")


def _regular_stat(path: str) -> os.stat_result | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def snapshot(path: str) -> tuple[int, int] | None:
    st = _regular_stat(path)
    return None if st is None else (st.st_size, st.st_mtime_ns)


def _sha256(path: str, st: os.stat_result) -> str | None:
    if st.st_size > hash_cap_bytes():
        return None
    key = (normalise(path), st.st_size, st.st_mtime_ns)
    with _cache_lock:
        cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _cache_lock:
        _hash_cache[key] = value
    return value


def fingerprint(path: str) -> dict:
    stored, _ = portable(path)
    st = _regular_stat(path)
    if st is None:
        return {"path": stored, "bytes": None, "mtime": None, "sha256": None,
                "hashed": False, "missing": True}
    try:
        digest = _sha256(path, st)
    except OSError as err:  # locked by Dropbox/QGIS: record it unhashed, never fail the call
        return {"path": stored, "bytes": st.st_size, "mtime": _utc(st.st_mtime), "sha256": None,
                "hashed": False, "missing": False, "error": str(err)}
    return {"path": stored, "bytes": st.st_size, "mtime": _utc(st.st_mtime), "sha256": digest,
            "hashed": digest is not None, "missing": False}


def reset_for_tests() -> None:
    global _static_facts
    with _cache_lock:
        _hash_cache.clear()
    _static_facts = None
    _qgis_facts.clear()
    ledger.reset()


INPUT_ARGS = frozenset({
    "path", "zones_path", "zones_layer_path", "value_csv", "input_path", "input_csv", "od_csv",
    "load_csv", "drm_network_path", "network_path", "rail_network_path", "db_path", "layer_path",
    "points_path", "join_path", "target_path", "raster_path", "qgz_path", "template_qgz",
    "template_pptx",
})
INPUT_LIST_ARGS = frozenset({"trajectory_csvs", "layer_paths", "figure_paths", "basemap_paths"})
OUTPUT_ARGS = frozenset({"output_png", "output_path", "output_csv", "output_dir", "pptx_path"})
NOT_PATH_ARGS = frozenset({"basemap", "query", "filename_template", "basemap_opacity"})
# A tool parameter whose name contains one of these must be in one of the sets
# above (drift guard in tests/test_provenance.py).
PATH_HINTS = ("path", "csv", "png", "dir", "qgz", "pptx", "db", "file", "basemap")

# Written files come from the result model (an atlas writes files its arguments
# never name; AtlasExportResult.output_path can be the directory itself).
# Looked up along the class MRO, so RenderResult covers every render result.
WRITTEN_BY_MODEL: dict[str, Callable[[Any], list[str]]] = {
    "AtlasExportResult": lambda r: list(r.files),
    "BatchRenderResult": lambda r: [m.output_path for m in r.manifest],
    "PptxResult": lambda r: [r.pptx_path],
    "RouteResult": lambda r: [r.output_csv],
    "SectionLoadResult": lambda r: [r.output_csv],
    "RenderResult": lambda r: [r.output_path],
    "ExportResult": lambda r: [r.output_path],
    "ComposeLayoutResult": lambda r: [r.output_path],
    "SpatialJoinResult": lambda r: [r.output_path],
    "ZonalStatsResult": lambda r: [r.output_path],
}
# Results that name a file which is not a figure: the replay script itself.
NOT_RECORDED_MODELS = frozenset({"SessionExportResult"})
_SUMMARY_SKIP = frozenset({"output_path", "output_csv", "output_dir", "pptx_path", "files", "manifest"})
_SUMMARY_MAX_LIST = 50


def input_paths(args: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name, value in args.items():
        if value is None or value == "" or value == []:
            continue
        if name in INPUT_ARGS:
            found.append((name, normalise(str(value))))
        elif name in INPUT_LIST_ARGS:
            found.extend((name, normalise(str(v))) for v in value)
    return found


def implicit_input_paths(tool: str, args: dict) -> list[tuple[str, str]]:
    """Files a tool reads without an argument naming them."""
    from qgis_mcp_workflows.helpers import bundled_asset

    deck_call = tool == "qgis_figures_to_pptx" or (tool == "qgis_export" and args.get("kind") == "pptx")
    if deck_call and not args.get("template_pptx"):
        bundled = bundled_asset("assets", "sekilab_blank.pptx")
        if bundled:
            return [("template_pptx (bundled)", normalise(bundled))]
    return []


def written_files(result: Any) -> list[str]:
    for cls in type(result).__mro__:
        extract = WRITTEN_BY_MODEL.get(cls.__name__)
        if extract is None:
            continue
        seen: list[str] = []
        for path in extract(result):
            if path and snapshot(path) is not None:
                norm = normalise(path)
                if norm not in seen:
                    seen.append(norm)
        return seen
    return []


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _summarisable(value: Any) -> bool:
    """A scalar, or a short list of scalars (breaks, colours) — never nested data."""
    if isinstance(value, list):
        return len(value) <= _SUMMARY_MAX_LIST and all(map(_scalar, value))
    return _scalar(value)


def _summary_key(key: str) -> bool:
    return key not in _SUMMARY_SKIP and key != "csv" and not key.endswith("_path")


def result_summary(result: Any) -> dict:
    """Decision fields of the result (breaks, counts, flags) for replay diffs.

    One nested level is flattened as ``parent.child`` (a choropleth's
    ``join.n_matched``); paths are never included.
    """
    summary: dict = {}
    for key, value in result.model_dump(mode="json").items():
        if not _summary_key(key):
            continue
        if isinstance(value, dict):
            summary.update({f"{key}.{sub}": v for sub, v in value.items()
                            if _summary_key(sub) and _summarisable(v)})
        elif _summarisable(value):
            summary[key] = value
    return summary


_EXTRAS = {"pptx": "pptx", "duckdb": "duckdb", "network": "networkx",
           "trajectory": "movingpandas", "drm": "geopandas"}
_static_facts: dict | None = None
_qgis_facts: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _git(root: Path, *args: str) -> str | None:
    """git output, or None if git failed. --no-optional-locks: never take
    index.lock away from another session committing in the same repo."""
    try:
        done = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                              capture_output=True, encoding="utf-8", errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _git_state() -> dict:
    unknown = {"git": None, "git_dirty": None}
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return unknown
    sha = _git(root, "rev-parse", "--short", "HEAD")
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if not sha or status is None:  # e.g. "dubious ownership": unknown, never "clean"
        return unknown
    return {"git": sha, "git_dirty": bool(status)}


def _static_environment() -> dict:
    global _static_facts
    if _static_facts is None:
        _static_facts = {
            "qgis_mcp_workflows": _package_version("qgis-mcp-workflows"),
            **_git_state(),
            "extras": sorted(e for e, module in _EXTRAS.items() if importlib.util.find_spec(module)),
            "mcp": _package_version("mcp"),
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
        }
    return dict(_static_facts)


def _qgis_versions(executor: Any) -> dict:
    cached = _qgis_facts.get(executor)
    if cached is not None:
        return cached
    try:
        response = executor.dispatch("diagnose", {}, timeout=10)
        checks = {c.get("name"): c for c in response.get("checks", [])}
    except Exception:  # degraded transport or a test fake: never cached
        return {"qgis": "unknown", "plugin": "unknown"}
    qgis = checks.get("qgis", {}).get("detail")
    versions = {
        "qgis": qgis.get("qgis_version", "unknown") if isinstance(qgis, dict) else "unknown",
        "plugin": str(checks.get("plugin_version", {}).get("detail", "unknown")),
    }
    _qgis_facts[executor] = versions
    return versions


def environment(dispatched: bool) -> dict:
    """Package/runtime facts; QGIS + plugin versions only if the call used QGIS.

    A pure-Python tool (figures_to_pptx, route_on_network, assign_section_load)
    must not spawn headless QGIS just to be recorded.
    """
    facts = _static_environment()
    if not dispatched:
        facts.update(transport="not queried", qgis="not queried", plugin="not queried")
        return facts
    from qgis_mcp_workflows import executors

    executor = executors.get_executor()
    facts["transport"] = type(executor).__name__.removesuffix("Executor").lower() or "unknown"
    facts.update(_qgis_versions(executor))
    return facts


_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY_S = 0.1


def sidecar_path(figure: str) -> str:
    return figure + SIDECAR_SUFFIX


def write_sidecar(figure: str, record: dict) -> bool:
    """Write atomically; on failure remove any stale sidecar and return False.

    The temp name is unique (two sessions may write into one Dropbox folder) and
    os.replace is retried briefly because Dropbox can hold the target open.
    """
    target = sidecar_path(figure)
    data = json.dumps(record, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target) or ".", prefix=".", suffix=".provenance.tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, target)
                tmp = None
                return True
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY_S)
        return False
    except OSError as err:
        logger.warning("provenance: could not write %s: %s", target, err)
        with contextlib.suppress(OSError):
            os.remove(target)  # a stale sidecar must not vouch for the new figure
        return False
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.remove(tmp)


SESSION_ID = uuid.uuid4().hex
_seq_counter = itertools.count(1)
_seq_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("QGIS_MCP_WORKFLOWS_PROVENANCE", "1").strip() != "0"


def _next_seq() -> int:
    with _seq_lock:
        return next(_seq_counter)


def _portable_arguments(args: dict) -> dict:
    out: dict = {}
    for name, value in args.items():
        if name in INPUT_ARGS | OUTPUT_ARGS and value not in (None, ""):
            out[name] = portable(str(value))[0]
        elif name in INPUT_LIST_ARGS and value:
            out[name] = [portable(str(v))[0] for v in value]
        else:
            out[name] = value
    return json.loads(json.dumps(out, default=str))


def _flag(machine_specific: list[dict], path: str) -> None:
    stored, reason = portable(path)
    entry = {"path": stored, "reason": reason}
    if reason and entry not in machine_specific:
        machine_specific.append(entry)


_SIDECAR_MAX_BYTES = 2 * 1024 * 1024


def _made_by(path: str, digest: str | None) -> str | None:
    """The producer's sidecar, only if it vouches for exactly these bytes.

    Anything else beside the input — a non-file, a huge file, JSON that is not
    an object — is ignored rather than allowed to abort the record.
    """
    if digest is None:
        return None
    side = sidecar_path(path)
    st = _regular_stat(side)
    if st is None or st.st_size > _SIDECAR_MAX_BYTES:
        return None
    try:
        with open(side, encoding="utf-8") as fh:
            producer = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(producer, dict) or producer.get("figure_sha256") != digest:
        return None
    return portable(side)[0]


def _remote(args: dict, machine_specific: list[dict]) -> list[dict]:
    basemap = args.get("basemap")
    # Every render tool defaults basemap="none", which apply_defaults() fills in.
    if not basemap or str(basemap).strip().lower() == "none":
        return []
    if str(basemap).startswith("qms:"):
        machine_specific.append({"path": str(basemap),
                                 "reason": "QuickMapServices source from the local QGIS profile"})
    return [{"argument": "basemap", "value": basemap, "note": "tiles are fetched live and not pinned"}]


def _settle(entries: list[dict], before: dict, figures: list[str], machine_specific: list[dict]) -> None:
    """Finish input fingerprints after the call: drift, producer link, machine-specific flag."""
    for entry in entries:
        path = entry.pop("_path")
        entry["changed_during_call"] = snapshot(path) != before[path]
        # An input this call overwrote must not link to the sidecar it is replacing.
        entry["made_by"] = None if path in figures else _made_by(path, entry["sha256"])
        _flag(machine_specific, path)


def _record(tool: str, module: str, call_args: dict, before: dict, prints: list[dict],
            implicit: list[dict], seq: int, requests_before: int, result: Any, elapsed: float,
            depends_on: list[dict], notes: list[str], extra_remote: list[dict]) -> None:
    figures = written_files(result)
    if not figures:
        return
    machine_specific: list[dict] = []
    _settle(prints + implicit, before, figures, machine_specific)
    written = {portable(f)[0] for f in figures}
    unrecorded = [f"input overwritten by this call: {e['argument']}" for e in prints if e["path"] in written]
    unrecorded += notes
    for figure in figures:
        _flag(machine_specific, figure)
    shared = {
        "session_id": SESSION_ID,
        "seq": seq,
        "created": _utc(time.time()),
        "call": {"tool": tool, "module": module, "arguments": _portable_arguments(call_args)},
        "result_summary": result_summary(result),
        "depends_on": depends_on,
        "inputs": prints,
        "implicit_inputs": implicit,
        "remote": _remote(call_args, machine_specific) + extra_remote,
        "unrecorded_state": unrecorded,
        "machine_specific": machine_specific,
        "outputs": [portable(f)[0] for f in figures],
        "environment": environment(executor_requests() > requests_before),
        "elapsed_s": round(elapsed, 3),
    }
    for figure in figures:
        fp = fingerprint(figure)
        write_sidecar(figure, {"schema": SCHEMA, "figure": fp["path"], "figure_sha256": fp["sha256"],
                               "figure_bytes": fp["bytes"], "figure_mtime": fp["mtime"], **shared})


def _drop_stale_sidecars(result: Any) -> None:
    """Recording failed: an old sidecar must not vouch for a freshly written file."""
    with contextlib.suppress(Exception):
        for figure in written_files(result):
            with contextlib.suppress(OSError):
                os.remove(sidecar_path(figure))


def _writes_files(fn: Callable) -> bool:
    """Whether ``fn``'s result model can name a written file (else: no wrapper at all).

    Read-only tools (layer_inspect, style_*) then pay nothing — not even input
    hashing. If the annotation cannot be resolved, wrap to be safe.
    """
    try:
        ret = inspect.signature(fn, eval_str=True).return_annotation
    except Exception:
        return True
    models = typing.get_args(ret) or (ret,)
    return any(
        isinstance(m, type) and any(c.__name__ in WRITTEN_BY_MODEL for c in m.__mro__)
        for m in models
    )


def _ledger_entry(tool: str, module: str, seq: int, call_args: dict, prints: list[dict],
                  before: dict, notes: list[str], remote: list[dict]) -> dict:
    _settle(prints, before, [], [])
    entry = {"seq": seq, "call": {"tool": tool, "module": module, "arguments": _portable_arguments(call_args)},
             "inputs": prints, "layer_id": None}
    if notes:
        entry["notes"] = list(notes)
    if remote:
        entry["remote"] = list(remote)
    return entry


def _discover(explicit: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str], list[dict]]:
    """Datasources of the call's inputs; a failure costs only them, never the record."""
    try:
        return datasources.discover(explicit)
    except Exception:
        logger.warning("provenance: datasource discovery failed", exc_info=True)
        return [], ["datasources not recorded: a project or shapefile input could not be read"], []


def _desktop() -> bool:
    """Recording over the plugin transport: the user can edit the map in QGIS between calls."""
    from qgis_mcp_workflows import executors

    return type(executors._current).__name__ == "PluginExecutor"


def with_provenance(fn: Callable) -> Callable:
    """Wrap a tool so each successful MCP call leaves a sidecar per written file
    and keeps the session state ledger (spec §6) current."""
    writes = _writes_files(fn)
    if not writes and fn.__name__ not in ledger.TRACKED:
        return fn
    signature = inspect.signature(fn)
    module = "compound" if fn.__module__.endswith(".compound") else "server"

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not enabled():
            return fn(*args, **kwargs)
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            call_args = dict(bound.arguments)
            explicit = input_paths(call_args)
            hidden = implicit_input_paths(fn.__name__, call_args)
            found, source_notes, source_remote = _discover(explicit)  # .shp siblings, project layers
            hidden += found
            before = {path: snapshot(path) for _, path in explicit + hidden}
            prints = [{"argument": name, **fingerprint(path), "_path": path} for name, path in explicit]
            implicit = [{"argument": name, **fingerprint(path), "_path": path} for name, path in hidden]
            seq = _next_seq()
            requests_before = executor_requests()
            depends_on, notes = ledger.snapshot(fn.__name__, call_args) if writes else ([], [])
            if depends_on and _desktop():
                notes.append(ledger.DESKTOP_NOTE)
            notes += source_notes
            for dep in depends_on:  # a loaded project's unpinned layers stay visible on later figures
                notes += [n for n in dep.get("notes", []) if n not in notes]
                source_remote += [r for r in dep.get("remote", []) if r not in source_remote]
        except Exception:
            logger.warning("provenance: could not prepare a record for %s", fn.__name__, exc_info=True)
            return fn(*args, **kwargs)
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)  # a failing call records nothing
        except Exception:
            # The plugin opens a project before it can fail (no such layout, bad file):
            # QGIS may now hold another project, so the ledger stops vouching for one.
            if ledger.kind(fn.__name__, call_args) in ("project", "export"):
                ledger.forget_project()
            raise
        if writes:
            try:
                _record(fn.__name__, module, call_args, before, prints, implicit, seq,
                        requests_before, result, time.monotonic() - started, depends_on, notes, source_remote)
            except Exception:
                logger.warning("provenance: recording failed for %s", fn.__name__, exc_info=True)
                _drop_stale_sidecars(result)
        try:
            entry = None if writes else _ledger_entry(fn.__name__, module, seq, call_args, prints + implicit,
                                                     before, source_notes, source_remote)
            ledger.update(fn.__name__, call_args, entry, result)
        except Exception:
            logger.warning("provenance: ledger update failed for %s", fn.__name__, exc_info=True)
        return result

    return wrapper

"""Figure provenance: a sidecar next to every file an MCP tool call writes.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md (v2).
Only the registered (MCP) copy of a tool is wrapped (``with_provenance`` in
server._maybe_tool / _maybe_compound_tool); Python callers get the bare
function and record nothing.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import stat
import threading
from collections.abc import Callable
from typing import Any

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


def hash_cap_bytes() -> int:
    raw = os.environ.get("QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB", "256")
    try:
        megabytes = float(raw)
    except ValueError:
        megabytes = 256.0
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
    digest = _sha256(path, st)
    return {"path": stored, "bytes": st.st_size, "mtime": _utc(st.st_mtime), "sha256": digest,
            "hashed": digest is not None, "missing": False}


def reset_for_tests() -> None:
    with _cache_lock:
        _hash_cache.clear()


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


def result_summary(result: Any) -> dict:
    """Decision fields of the result (breaks, counts, flags) for replay diffs."""
    return {
        key: value
        for key, value in result.model_dump(mode="json").items()
        if key not in _SUMMARY_SKIP and not key.endswith("_path") and _summarisable(value)
    }

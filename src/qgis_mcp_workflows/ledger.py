"""Session state ledger (TASK-15, spec §6).

Some figures read state an earlier call left in QGIS: ``qgis_render_map`` draws
layers loaded and styled before it, and a project export reuses the loaded,
possibly restyled project instead of re-reading the file. The ledger keeps, per
server process, the calls that built that state, so a state-reading figure's
sidecar can carry them in ``depends_on`` and a replay can rebuild it first.

An entry is the shape a sidecar stores: {"seq", "call": {"tool", "module",
"arguments"}, "inputs", "layer_id"}.
"""

from __future__ import annotations

import copy
import re
import threading
from typing import Any

TRACKED = frozenset({
    "qgis_load_layer", "qgis_style_categorized", "qgis_style_graduated", "qgis_project_load", "qgis_eval",
    "qgis_inspect", "qgis_style",
})
EVAL_NOTE = "an earlier qgis_eval may have changed shared state, e.g. colour ramps"
DESKTOP_NOTE = "Desktop session: manual edits in QGIS are not recorded"
EVAL_LITERAL_NOTE = "a qgis_eval names a layer id or an absolute path; the replay cannot remap it"
_ABSOLUTE = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]|/(?:Users|home|Volumes|mnt|tmp|private)/)")

_lock = threading.Lock()
_layers: dict[str, list[dict]] = {}
_project: dict | None = None  # {"path": normalised key, "entry": entry}
_evals: list[dict] = []


def reset() -> None:
    global _project
    with _lock:
        _layers.clear()
        _evals.clear()
        _project = None


def kind(tool: str, args: dict) -> str | None:
    """What a call does to session state (full and compound tool names)."""
    if tool == "qgis_load_layer" or (tool == "qgis_inspect" and args.get("kind") == "layer"
                                     and args.get("register")):
        return "load"
    if tool in ("qgis_style_categorized", "qgis_style_graduated", "qgis_style"):
        return "style"
    if tool == "qgis_project_load" or (tool == "qgis_inspect" and args.get("kind") == "project"):
        return "project"
    if tool == "qgis_eval":
        return "eval"
    if tool in ("qgis_export_layout", "qgis_export_atlas", "qgis_batch_render") or (
            tool == "qgis_export" and args.get("kind") in ("layout", "atlas", "batch")):
        return "export"
    if tool == "qgis_render_map" or (tool == "qgis_render" and args.get("mode") == "map"):
        return "map"
    return None


def _key(path: str) -> str:
    from qgis_mcp_workflows.provenance import _case_insensitive, normalise

    norm = normalise(path)
    return norm.casefold() if _case_insensitive() else norm


def project_path(tool: str, args: dict) -> str | None:
    """The .qgz a project load or a project export names, as a comparison key."""
    batch = tool == "qgis_batch_render" or (tool == "qgis_export" and args.get("kind") == "batch")
    raw = args.get("template_qgz") if batch else (args.get("qgz_path") or args.get("path"))
    return _key(str(raw)) if raw else None


def _names_state(entry: dict, layer_ids: set[str]) -> bool:
    code = str(entry["call"]["arguments"].get("code") or "")
    return bool(_ABSOLUTE.search(code)) or any(layer and layer in code for layer in layer_ids)


def snapshot(tool: str, args: dict) -> tuple[list[dict], list[str]]:
    """(depends_on, unrecorded notes) for a call about to run.

    depends_on is non-empty only for a state-reading figure. Every figure made
    after a successful eval gets the eval note, atomic ones included.
    """
    k = kind(tool, args)
    with _lock:
        notes = [EVAL_NOTE] if _evals else []
        if k == "map":
            deps = [_project["entry"]] if _project else []
            for layer in args.get("layer_ids") or []:
                if layer in _layers:
                    deps += _layers[layer]
                elif _project is None:
                    notes.append(f"layer {layer} was not loaded through MCP in this session")
        elif k == "export" and _project is not None and project_path(tool, args) == _project["path"]:
            deps = [_project["entry"]] + [e for entries in _layers.values() for e in entries]
        else:
            return [], notes
        deps += _evals
        ordered = sorted({e["seq"]: e for e in deps}.values(), key=lambda e: e["seq"])
        layer_ids = set(_layers) | {e["layer_id"] for e in ordered if e.get("layer_id")}
        flagged = any(_names_state(e, layer_ids) for e in ordered if e["call"]["tool"] == "qgis_eval")
        ordered = copy.deepcopy(ordered)
    if flagged:
        notes.append(EVAL_LITERAL_NOTE)
    return ordered, notes


def update(tool: str, args: dict, entry: dict | None, result: Any) -> None:
    """Apply a successful call to the ledger (spec §6 table)."""
    global _project
    k = kind(tool, args)
    with _lock:
        if k == "load" and entry is not None:
            layer = getattr(result, "layer_id", None)
            if layer:
                _layers[layer] = [{**entry, "layer_id": layer}]
        elif k == "style" and entry is not None:
            layer = args.get("layer_id")
            if layer:
                _layers.setdefault(layer, []).append(entry)
        elif k == "project" and entry is not None:
            _layers.clear()
            _project = {"path": project_path(tool, args), "entry": entry}
        elif k == "eval" and entry is not None:
            if getattr(result, "exception", None) is None:
                _evals.append(entry)
        elif k == "export":
            if _project is None or project_path(tool, args) != _project["path"]:
                _layers.clear()
                _project = None

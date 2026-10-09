# Provenance state ledger and eval replay (TASK-15) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Figures that read QGIS session state (`qgis_render_map`, and project exports of the loaded project) record the calls that built that state, and `qgis_export_session` replays them, running recorded `qgis_eval` code only when the user passes `--allow-eval`.

**Architecture:** A new `ledger.py` keeps per-process state: loaded layers with their style calls, the loaded project, and successful evals. `provenance.with_provenance` feeds it from the load, style, project and eval tools, and snapshots it into `depends_on` before a state-reading figure runs. The export side of `replay.py` moves to `session_export.py`. It validates `depends_on` entries against a separate allow-list of ledger tools, and emits each state-reading figure as one block: a session reset, the dependency calls with recorded layer ids rebound to `layer_N` variables, then the figure. The runtime `replay.main` refuses with exit 3 to run a script containing evals unless `--allow-eval` is given.

**Tech Stack:** Python 3.12, FastMCP (`mcp`), pydantic, `ast`, pytest with the repo's `FakeExecutor`; PyQGIS headless for the live test.

**Spec:** `docs/superpowers/specs/2026-10-08-figure-provenance-design.md` — §6 (state ledger and evals), §7 (the `include_evals` / `trust_foreign` / `n_evals` parts and eval generation), §11 (tests). TASK-13 (merged, PR #27) and its review fixes are the base: read `src/qgis_mcp_workflows/replay.py` before starting.

## Global Constraints

- Sidecar schema stays `qgis-mcp-workflows/provenance@1`; `depends_on` already exists (always `[]` until now).
- Only state-reading figures carry a non-empty `depends_on`: `qgis_render_map` / `qgis_render(mode="map")`, and `qgis_export_layout` / `qgis_export_atlas` / `qgis_batch_render` (and `qgis_export(kind="layout"|"atlas"|"batch")`) whose project path equals the ledger's current project.
- Ledger updates (spec §6 table): load → `layers[id] = [load]`; style → append to that layer's list (all kept, seq order); project load → clear layers, `project = {path, call}`; export of a *different* project path → clear layers, `project = None`; successful `qgis_eval` → append to evals. A failed eval (`EvalResult.exception` set) is not recorded.
- Honest gaps go to `unrecorded_state` with these exact texts: `layer <id> was not loaded through MCP in this session`; `Desktop session: manual edits in QGIS are not recorded`; `an earlier qgis_eval may have changed shared state, e.g. colour ramps`; `a qgis_eval names a layer id or an absolute path; the replay cannot remap it`.
- `qgis_eval` is registered through the provenance hook; the module keeps the plain function for Python callers.
- Replay runs evals only with `--allow-eval`. Without it the script prints each eval's sha256, its first three lines (control characters stripped) and the figures that use it, to stderr, and exits 3 before running anything. Foreign evals (figure not under `${DROPBOX_ROOT}`) are dropped unless `trust_foreign=True`; `include_evals=False` drops all; both add a warning naming the figure.
- The script stays AST-built from a fixed skeleton; skipped reasons and warnings stay fixed templates with echoed values passed through `_clean` (200 characters, no C0/C1 controls).
- TASK-13's top-level allow-list is unchanged: a sidecar's own `call` must be a tool that writes files (`provenance._writes_files`). Load, style, project and eval calls are accepted **only** inside `depends_on`, via a separate allow-list.
- No plugin changes (`qgis_mcp_workflows_plugin/` stays Python 3.9). The session reset replaces the headless executor (a fresh QGIS process), so no new plugin command is needed.
- Tests: pytest with `FakeExecutor`, no QGIS, except the live test (`requires_headless`). The suite must also pass on mcp 2.x: read result fields through `tests/mcp_compat.field`.

## Review Focus

1. Two state-reading figures from one session made at different style states (map after style A, restyle to B, map again) — each replays its own snapshot. Pinned in Task 5 (`test_each_state_reading_figure_replays_its_own_snapshot`).
2. `qgis_render_map` over layers of a loaded project (no `qgis_load_layer`) — the project load is replayed first and project layer ids stay literal. Pinned in Task 5 (`test_project_layers_keep_their_ids`).
3. Eval code with terminal escapes or hundreds of lines — the refusal prints a cleaned three-line preview and the sha256 of the full code. Pinned in Task 6 (`test_eval_preview_is_cleaned_and_short`).
4. An atomic figure made after an eval (e.g. a choropleth) — gets the eval note, no `depends_on`, and its replay never runs the eval. Pinned in Task 3 (`test_atomic_figure_after_eval_gets_a_note_only`).
5. Recording over the plugin transport — a state-reading figure says manual Desktop edits are not recorded. Pinned in Task 3 (`test_plugin_transport_state_reading_figure_is_flagged`).

---

### Task 1: Split `replay.py` into runtime and export modules

`replay.py` is 637 lines and this task set adds about 250 more. Generated scripts import `replay.main`, so the runtime stays in `replay.py`, and the export side moves to `session_export.py`. This is a pure move with no behaviour change.

**Files:**
- Create: `src/qgis_mcp_workflows/session_export.py`
- Modify: `src/qgis_mcp_workflows/replay.py`, `src/qgis_mcp_workflows/server.py` (the import inside `qgis_export_session`), `tests/test_replay.py`, `tests/test_session_export.py`

**Interfaces:**
- Produces: `qgis_mcp_workflows.session_export` with every export-side name that was in `replay.py` (`collect`, `plan_steps`, `outputs_of`, `source_inputs`, `notes_for`, `build_script`, `export_session`, `_load_sidecar`, `_tool_function`, `_skip_reason`, `_well_formed`, `_size_ok`, `_str_list`, `_file_entries`, `_argument_paths`, `_arguments_agree`, `_safe_file_names`, `_network`, `_record_paths`, `_literal`, `_argument`, `_step_call`, `_call_key`, `_fail`, `_SKELETON`, `MAX_SIDECARS`). `replay.py` keeps `ReplayError`, `resolve`, `check_inputs`, `_relative_target`, `_same`, `output_mapper`, `_new_executor`, `_parse`, `main`, and gains `_TEXT_MAX` and `_clean`, which move up from the export half because `check_inputs` uses `_clean`.

- [ ] **Step 1: Move the code.** In `replay.py`, cut everything from the line `# --- export side ---...` to the end of the file. Paste it into a new `session_export.py` under this header:

```python
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
    ROOT_TOKEN,
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
```

Then move `_TEXT_MAX = 200` and `def _clean(...)` out of `session_export.py` back into `replay.py`, directly above `def check_inputs`, and delete those two definitions from `session_export.py`. In `replay.py`, update the module docstring's first paragraph to say the export half now lives in `session_export.py`.

- [ ] **Step 2: Point the tool at the new module.** In `server.py`, inside `qgis_export_session`, replace `from qgis_mcp_workflows.replay import export_session` with `from qgis_mcp_workflows.session_export import export_session`.

- [ ] **Step 3: Update the tests.** In `tests/test_replay.py` and `tests/test_session_export.py`, change `from qgis_mcp_workflows import provenance, replay` to `from qgis_mcp_workflows import provenance, replay, session_export`. Then rename references to moved names from `replay.` to `session_export.`. Use the Edit tool, or save this script to your scratch folder and run it with `.venv/Scripts/python.exe <script>`. Do not paste it through a shell heredoc: the regex backslashes get mangled.

```python
import pathlib, re
moved = ("collect|plan_steps|outputs_of|source_inputs|notes_for|build_script|export_session|_load_sidecar|"
         "_tool_function|_skip_reason|_well_formed|_size_ok|_argument_paths|_arguments_agree|_safe_file_names|"
         "_network|_record_paths|_literal|_argument|_step_call|_call_key|MAX_SIDECARS")
for name in ("tests/test_replay.py", "tests/test_session_export.py"):
    p = pathlib.Path(name)
    p.write_text(re.sub(r"\breplay\.(" + moved + r")\b", r"session_export.\1", p.read_text(encoding="utf-8")),
                 encoding="utf-8")
```

`replay._new_executor`, `replay.main`, `replay.output_mapper`, `replay.check_inputs`, `replay.resolve`, `replay._relative_target`, `replay._clean` and `replay.ReplayError` keep their `replay.` prefix. Then grep both files for any remaining `replay.` + one of the moved names; there should be none.

- [ ] **Step 4: Run the tests and lint.**

Run: `uv run --no-sync pytest tests/test_replay.py tests/test_session_export.py tests/test_replay_live.py -q -p no:cacheprovider` → all pass (same count as before the move).
Run: `uv tool run ruff check src/ tests/` → remove whatever imports it reports as unused in `replay.py` or `session_export.py`, then re-run until it is clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/replay.py src/qgis_mcp_workflows/session_export.py src/qgis_mcp_workflows/server.py tests/test_replay.py tests/test_session_export.py
git commit -m "refactor(replay): move the export half to session_export.py"
```

---

### Task 2: The ledger module

**Files:**
- Create: `src/qgis_mcp_workflows/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `ledger.TRACKED: frozenset[str]` — the tool names that need a wrapper even though they write no file.
  - `ledger.kind(tool: str, args: dict) -> str | None` — one of `"load"`, `"style"`, `"project"`, `"eval"`, `"export"`, `"map"`, or `None`.
  - `ledger.project_path(tool: str, args: dict) -> str | None` — the normalised `.qgz` key.
  - `ledger.snapshot(tool: str, args: dict) -> tuple[list[dict], list[str]]` — `(depends_on, notes)`.
  - `ledger.update(tool: str, args: dict, entry: dict | None, result: Any) -> None`.
  - `ledger.reset() -> None`.
  - Constants `EVAL_NOTE`, `DESKTOP_NOTE`, `EVAL_LITERAL_NOTE`.
- An *entry* is `{"seq": int, "call": {"tool": str, "module": str, "arguments": dict}, "inputs": list[dict], "layer_id": str | None}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_ledger.py`:

```python
"""The session state ledger (spec §6). Pure Python, no MCP."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from qgis_mcp_workflows import ledger


@pytest.fixture(autouse=True)
def clean():
    ledger.reset()
    yield
    ledger.reset()


def _entry(seq, tool, **arguments):
    return {"seq": seq, "call": {"tool": tool, "module": "server", "arguments": arguments},
            "inputs": [], "layer_id": None}


def _load(seq, layer="L1"):
    ledger.update("qgis_load_layer", {"path": "/z.gpkg"}, _entry(seq, "qgis_load_layer", path="/z.gpkg"),
                  SimpleNamespace(layer_id=layer))


def _style(seq, field, layer="L1"):
    args = {"layer_id": layer, "field": field}
    ledger.update("qgis_style_categorized", args, _entry(seq, "qgis_style_categorized", **args), None)


def test_kinds_cover_full_and_compound_names():
    assert ledger.kind("qgis_load_layer", {}) == "load"
    assert ledger.kind("qgis_inspect", {"kind": "layer", "register": True}) == "load"
    assert ledger.kind("qgis_inspect", {"kind": "layer", "register": False}) is None
    assert ledger.kind("qgis_style", {"type": "graduated"}) == "style"
    assert ledger.kind("qgis_inspect", {"kind": "project", "path": "/p.qgz"}) == "project"
    assert ledger.kind("qgis_export", {"kind": "batch"}) == "export"
    assert ledger.kind("qgis_export", {"kind": "pptx"}) is None
    assert ledger.kind("qgis_render", {"mode": "map"}) == "map"
    assert ledger.kind("qgis_render_choropleth", {}) is None


def test_style_a_b_a_keeps_every_call_in_order():
    _load(1)
    for seq, field in ((2, "a"), (3, "b"), (4, "a")):
        _style(seq, field)
    deps, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert [d["seq"] for d in deps] == [1, 2, 3, 4]
    assert deps[0]["layer_id"] == "L1" and deps[-1]["call"]["arguments"]["field"] == "a"
    assert notes == []


def test_unknown_layer_without_a_project_is_noted():
    deps, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["Lx"]})
    assert deps == [] and notes == ["layer Lx was not loaded through MCP in this session"]


def test_export_of_the_loaded_project_is_state_reading():
    ledger.update("qgis_project_load", {"qgz_path": "/p.qgz"}, _entry(1, "qgis_project_load", qgz_path="/p.qgz"),
                  None)
    _style(2, "a", layer="P1")
    deps, _ = ledger.snapshot("qgis_export_layout", {"qgz_path": "/p.qgz", "layout_name": "A4"})
    assert [d["call"]["tool"] for d in deps] == ["qgis_project_load", "qgis_style_categorized"]


def test_export_of_another_project_clears_the_ledger():
    ledger.update("qgis_project_load", {"qgz_path": "/p.qgz"}, _entry(1, "qgis_project_load", qgz_path="/p.qgz"),
                  None)
    _style(2, "a", layer="P1")
    ledger.update("qgis_export_layout", {"qgz_path": "/other.qgz"}, None, None)
    assert ledger.snapshot("qgis_export_layout", {"qgz_path": "/p.qgz"}) == ([], [])
    assert ledger.snapshot("qgis_render_map", {"layer_ids": ["P1"]})[0] == []


def test_failed_eval_is_not_recorded_and_successful_one_is():
    ledger.update("qgis_eval", {"code": "boom"}, _entry(1, "qgis_eval", code="boom"),
                  SimpleNamespace(exception="Traceback ..."))
    assert ledger.snapshot("qgis_render_choropleth", {}) == ([], [])
    ledger.update("qgis_eval", {"code": "x = 1"}, _entry(2, "qgis_eval", code="x = 1"),
                  SimpleNamespace(exception=None))
    assert ledger.snapshot("qgis_render_choropleth", {}) == ([], [ledger.EVAL_NOTE])
    _load(3)
    deps, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert [d["call"]["tool"] for d in deps] == ["qgis_eval", "qgis_load_layer"]
    assert notes == [ledger.EVAL_NOTE]


def test_eval_naming_a_layer_or_absolute_path_is_flagged():
    _load(1)
    ledger.update("qgis_eval", {"code": "lyr = QgsProject.instance().mapLayer('L1')"},
                  _entry(2, "qgis_eval", code="lyr = QgsProject.instance().mapLayer('L1')"),
                  SimpleNamespace(exception=None))
    _, notes = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert ledger.EVAL_LITERAL_NOTE in notes


def test_snapshot_is_a_copy():
    _load(1)
    deps, _ = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    deps[0]["call"]["arguments"]["path"] = "changed"
    again, _ = ledger.snapshot("qgis_render_map", {"layer_ids": ["L1"]})
    assert again[0]["call"]["arguments"]["path"] == "/z.gpkg"
```

- [ ] **Step 2: Verify failure.** Run: `uv run --no-sync pytest tests/test_ledger.py -q -p no:cacheprovider`. Expected: `ModuleNotFoundError: No module named 'qgis_mcp_workflows.ledger'`.

- [ ] **Step 3: Implement** — `src/qgis_mcp_workflows/ledger.py`:

```python
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
```

- [ ] **Step 4: Run tests.** `uv run --no-sync pytest tests/test_ledger.py -q -p no:cacheprovider` → 8 passed. `uv tool run ruff check src/ tests/` → clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/ledger.py tests/test_ledger.py
git commit -m "feat(provenance): session state ledger for state-reading figures"
```

---

### Task 3: Feed the ledger from the provenance hook; record `depends_on`

**Files:**
- Modify: `src/qgis_mcp_workflows/provenance.py` (`reset_for_tests`, `_record`, `with_provenance`; new `_settle`, `_ledger_entry`, `_desktop`)
- Modify: `src/qgis_mcp_workflows/server.py` (register `qgis_eval` through the hook)
- Modify: `tests/test_provenance.py` (the wrapping guard at about line 379)
- Test: create `tests/test_ledger_hook.py`

**Interfaces:**
- Consumes: Task 2's `ledger.TRACKED`, `kind`, `snapshot`, `update`, `reset`, `DESKTOP_NOTE`.
- Produces:
  - Sidecars of state-reading figures carry `depends_on` (a list of entries) and the ledger notes in `unrecorded_state`.
  - `provenance._record(tool, module, call_args, before, prints, implicit, seq, requests_before, result, elapsed, depends_on, notes)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_ledger_hook.py`:

```python
"""The ledger through the registered MCP tools (full and compound mode). No QGIS needed."""

from __future__ import annotations

import importlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from qgis_mcp_workflows import ledger, provenance


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


@pytest.fixture
def server():
    return importlib.import_module("qgis_mcp_workflows.server")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PROVENANCE", raising=False)
    provenance.reset_for_tests()
    yield tmp_path
    provenance.reset_for_tests()


def _render(params):
    Path(params["output_png"]).write_bytes(_png())
    return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
            "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": len(params["layer_ids"])}


def _layer_responses(fake_executor, ids=("L1",)):
    queue = list(ids)
    fake_executor.responses["add_vector_layer"] = lambda p: {"id": queue.pop(0), "name": "zones"}
    fake_executor.responses["get_layer_info"] = {
        "type": "vector_2", "crs": "EPSG:4326", "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
        "feature_count": 4, "fields": [{"name": "zone_id", "type": "String", "n_unique": 4}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}
    fake_executor.responses["render_layers_to_path"] = _render


def _sidecar(path: Path) -> dict:
    return json.loads(Path(str(path) + ".provenance.json").read_text(encoding="utf-8"))


async def test_render_map_records_load_and_every_style_call(server, fake_executor, root):
    _layer_responses(fake_executor)
    zones = root / "zones.geojson"
    zones.write_text("{}", encoding="utf-8")
    await server.mcp.call_tool("qgis_load_layer", {"path": str(zones)})
    for field in ("a", "b", "a"):
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L1", "field": field})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "map.png")})
    deps = _sidecar(root / "map.png")["depends_on"]
    assert [d["call"]["tool"] for d in deps] == ["qgis_load_layer"] + ["qgis_style_categorized"] * 3
    assert [d["call"]["arguments"]["field"] for d in deps[1:]] == ["a", "b", "a"]
    assert deps[0]["layer_id"] == "L1"
    assert deps[0]["inputs"][0]["path"] == "${DROPBOX_ROOT}/zones.geojson"
    assert "_path" not in deps[0]["inputs"][0]


async def test_successful_eval_is_recorded_and_a_failed_one_is_not(server, fake_executor, root):
    _layer_responses(fake_executor)
    fake_executor.responses["execute_code"] = lambda p: (
        {"executed": False, "traceback": "boom"} if "boom" in p["code"] else {"executed": True, "stdout": ""})
    await server.mcp.call_tool("qgis_eval", {"code": "raise RuntimeError('boom')"})
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    record = _sidecar(root / "m.png")
    evals = [d for d in record["depends_on"] if d["call"]["tool"] == "qgis_eval"]
    assert [e["call"]["arguments"]["code"] for e in evals] == ["x = 1"]
    assert ledger.EVAL_NOTE in record["unrecorded_state"]


async def test_atomic_figure_after_eval_gets_a_note_only(server, fake_executor, root):
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": ""}

    def choropleth(params):
        Path(params["output_png"]).write_bytes(_png())
        return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
                "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1, "field": "n", "n_classes": 5,
                "breaks": [1.0, 2.0], "mode": "quantile", "min_value": 1.0, "max_value": 2.0,
                "n_features": 2, "n_matched": 2, "n_unmatched": 0}

    fake_executor.responses["render_choropleth"] = choropleth
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_render_choropleth", {"zones_path": str(root / "z.gpkg"), "value_field": "n",
                                                          "output_png": str(root / "c.png")})
    record = _sidecar(root / "c.png")
    assert record["depends_on"] == [] and ledger.EVAL_NOTE in record["unrecorded_state"]


async def test_plugin_transport_state_reading_figure_is_flagged(server, fake_executor, root, monkeypatch):
    from qgis_mcp_workflows import executors

    class PluginExecutor:  # only the class name matters to the hook
        def dispatch(self, command, params=None, timeout=None):
            return fake_executor.dispatch(command, params, timeout)

    _layer_responses(fake_executor)
    monkeypatch.setattr(executors, "_current", PluginExecutor())
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    assert ledger.DESKTOP_NOTE in _sidecar(root / "m.png")["unrecorded_state"]


async def test_project_export_after_restyle_is_state_reading(server, fake_executor, root):
    qgz = root / "p.qgz"
    qgz.write_bytes(b"qgz")
    fake_executor.responses["project_load"] = {
        "project_path": str(qgz), "crs": "EPSG:4326", "extent": [0, 0, 1, 1],
        "layers": [{"layer_id": "P1", "name": "zones", "geometry_type": "polygon", "visible": True}],
        "layouts": [{"name": "A4"}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}

    def export(params):
        Path(params["output_path"]).write_bytes(_png())
        return {"output_path": params["output_path"], "format": "png", "n_pages": 1, "layout_name": "A4"}

    fake_executor.responses["export_layout"] = export
    await server.mcp.call_tool("qgis_project_load", {"qgz_path": str(qgz)})
    await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "P1", "field": "zone_id"})
    await server.mcp.call_tool("qgis_export_layout", {"qgz_path": str(qgz), "layout_name": "A4",
                                                      "output_path": str(root / "a4.png")})
    deps = _sidecar(root / "a4.png")["depends_on"]
    assert [d["call"]["tool"] for d in deps] == ["qgis_project_load", "qgis_style_categorized"]
```

The compound-mode case goes into `tests/test_compound_mode.py`, where the `compound_module` fixture and its `_reimport_server` helper live. Append:

```python
async def test_compound_mode_feeds_the_ledger(compound_module, fake_executor, tmp_path, monkeypatch):
    import json

    from qgis_mcp_workflows import provenance

    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    provenance.reset_for_tests()
    server_module, _ = compound_module
    png = tmp_path / "m.png"
    fake_executor.responses["add_vector_layer"] = {"id": "L1", "name": "zones"}
    fake_executor.responses["get_layer_info"] = {
        "type": "vector_2", "crs": "EPSG:4326", "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
        "feature_count": 4, "fields": [{"name": "zone_id", "type": "String", "n_unique": 4}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}

    def render(params):
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return {"output_path": str(png), "width": 1, "height": 1, "dpi": 150,
                "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1}

    fake_executor.responses["render_layers_to_path"] = render
    await server_module.mcp.call_tool("qgis_inspect", {"kind": "layer", "path": str(tmp_path / "z.geojson"),
                                                       "register": True})
    await server_module.mcp.call_tool("qgis_style", {"type": "categorized", "layer_id": "L1", "field": "zone_id"})
    await server_module.mcp.call_tool("qgis_render", {"mode": "map", "layer_ids": ["L1"], "output_png": str(png)})
    deps = json.loads((tmp_path / "m.png.provenance.json").read_text(encoding="utf-8"))["depends_on"]
    assert [(d["call"]["module"], d["call"]["tool"]) for d in deps] == [("compound", "qgis_inspect"),
                                                                       ("compound", "qgis_style")]
    provenance.reset_for_tests()
```

Change the guard in `tests/test_provenance.py` (`test_tools_that_never_write_files_are_not_wrapped`) to:

```python
def test_only_state_tools_are_wrapped_without_writing_files():
    from qgis_mcp_workflows import server

    assert provenance.with_provenance(server.qgis_layer_inspect) is server.qgis_layer_inspect
    assert provenance.with_provenance(server.qgis_style_categorized) is not server.qgis_style_categorized
    assert provenance.with_provenance(server.qgis_render_choropleth) is not server.qgis_render_choropleth
```

- [ ] **Step 2: Verify failure.** `uv run --no-sync pytest tests/test_ledger_hook.py tests/test_provenance.py -q -p no:cacheprovider`. Expected failures: `depends_on` is `[]`, `qgis_eval` is not recorded, and the guard asserts the style tool is unwrapped.

- [ ] **Step 3: Implement.**

In `provenance.py`, add `from qgis_mcp_workflows import ledger` with the other package imports at the top. (`ledger` imports `provenance` only inside functions, so there is no import cycle.) Then call `ledger.reset()` as the last line of `reset_for_tests()`.

Replace the settling loop in `_record` with a shared helper, and give `_record` the two new parameters:

```python
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
            depends_on: list[dict], notes: list[str]) -> None:
    figures = written_files(result)
    if not figures:
        return
    machine_specific: list[dict] = []
    _settle(prints + implicit, before, figures, machine_specific)
    written = {portable(f)[0] for f in figures}
    unrecorded = [f"input overwritten by this call: {e['argument']}" for e in prints if e["path"] in written]
    unrecorded += notes
```

The rest of `_record` is unchanged, except `"depends_on": [],` becomes `"depends_on": depends_on,`.

Add these helpers above `with_provenance`:

```python
def _ledger_entry(tool: str, module: str, seq: int, call_args: dict, prints: list[dict],
                  before: dict) -> dict:
    _settle(prints, before, [], [])
    return {"seq": seq, "call": {"tool": tool, "module": module, "arguments": _portable_arguments(call_args)},
            "inputs": prints, "layer_id": None}


def _desktop() -> bool:
    """Recording over the plugin transport: the user can edit the map in QGIS between calls."""
    from qgis_mcp_workflows import executors

    return type(executors._current).__name__ == "PluginExecutor"
```

In `with_provenance`, replace the opening check:

```python
    writes = _writes_files(fn)
    if not writes and fn.__name__ not in ledger.TRACKED:
        return fn
```

Update the docstring to "Wrap a tool so each successful MCP call leaves a sidecar per written file and keeps the session state ledger (spec §6) current."

Inside `wrapper`, after `requests_before = executor_requests()` (still inside the first `try`), add:

```python
            depends_on, notes = ledger.snapshot(fn.__name__, call_args) if writes else ([], [])
            if depends_on and _desktop():
                notes.append(ledger.DESKTOP_NOTE)
```

Replace everything after `result = fn(*args, **kwargs)` with:

```python
        if writes:
            try:
                _record(fn.__name__, module, call_args, before, prints, implicit, seq,
                        requests_before, result, time.monotonic() - started, depends_on, notes)
            except Exception:
                logger.warning("provenance: recording failed for %s", fn.__name__, exc_info=True)
                _drop_stale_sidecars(result)
        try:
            entry = None if writes else _ledger_entry(fn.__name__, module, seq, call_args, prints, before)
            ledger.update(fn.__name__, call_args, entry, result)
        except Exception:
            logger.warning("provenance: ledger update failed for %s", fn.__name__, exc_info=True)
        return result
```

In `server.py`, remove the `@mcp.tool(annotations=...)` decorator lines above `def qgis_eval(`. Directly after the function's last line (`)` closing `return EvalResult(...)`) and before the `# Trigger compound-mode tool registration` comment, add:

```python
# Registered through the provenance hook so successful evals enter the state
# ledger (spec §6); the module keeps the plain function for Python callers.
mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=False, destructiveHint=True, openWorldHint=True
    )
)(with_provenance(qgis_eval))
```

- [ ] **Step 4: Run tests.** Run `uv run --no-sync pytest tests/test_ledger.py tests/test_ledger_hook.py tests/test_provenance.py tests/test_provenance_hook.py tests/test_eval.py tests/test_compound_mode.py -q -p no:cacheprovider` → all pass. Then run the full suite: `uv run --no-sync pytest tests/ -q -p no:cacheprovider` → only the known Windows-lock failure (`test_add_vector_layer_returns_layer_id`) may fail. Then `uv tool run ruff check src/ tests/` → clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/provenance.py src/qgis_mcp_workflows/server.py tests/test_ledger_hook.py tests/test_provenance.py tests/test_compound_mode.py
git commit -m "feat(provenance): state-reading figures record depends_on from the ledger"
```

---

### Task 4: Validate `depends_on` at export; drop untrusted evals

**Files:**
- Modify: `src/qgis_mcp_workflows/session_export.py`
- Test: create `tests/test_replay_state.py`

**Interfaces:**
- Consumes: `ledger.kind`.
- Produces:
  - `session_export._dependency_ok(entry) -> bool`.
  - `session_export._covers_state(record) -> bool`.
  - `session_export.keep_evals(records, include_evals: bool, trust_foreign: bool) -> tuple[list[dict], list[str]]`.
  - New fixed skip reason `invalid depends_on`. `needs session state` now means `depends_on` does not cover what the figure reads.

- [ ] **Step 1: Write the failing tests** — `tests/test_replay_state.py`:

```python
"""Replay of state-reading figures (TASK-15): validation, eval trust, script shape. No QGIS needed."""

from __future__ import annotations

import ast
import hashlib
import json

import pytest

from qgis_mcp_workflows import provenance, session_export


def _dep(seq, tool, module="server", layer_id=None, inputs=(), **arguments):
    return {"seq": seq, "call": {"tool": tool, "module": module, "arguments": arguments},
            "inputs": list(inputs), "layer_id": layer_id}


def _in(argument, path):
    return {"argument": argument, "path": path, "sha256": None, "bytes": None, "made_by": None}


def _load(seq=1, layer="L1", path="${DROPBOX_ROOT}/z.gpkg"):
    return _dep(seq, "qgis_load_layer", layer_id=layer, inputs=[_in("path", path)], path=path)


def _style(seq, field, layer="L1"):
    # `layer_id` here is the call's argument; `_dep(layer_id=...)` would set the entry's produced id.
    entry = _dep(seq, "qgis_style_categorized", field=field)
    entry["call"]["arguments"] = {"layer_id": layer, "field": field}
    return entry


def _map_sidecar(tmp_path, name="m.png", deps=(), layer_ids=("L1",), seq=10, session="s1", figure=None):
    fig = tmp_path / name
    fig.write_bytes(b"png")
    figure = figure or str(fig)
    record = {
        "schema": provenance.SCHEMA, "figure": figure, "figure_sha256": hashlib.sha256(b"png").hexdigest(),
        "session_id": session, "seq": seq,
        "call": {"tool": "qgis_render_map", "module": "server",
                 "arguments": {"layer_ids": list(layer_ids), "output_png": figure}},
        "depends_on": list(deps), "inputs": [], "implicit_inputs": [], "outputs": [figure],
        "unrecorded_state": [], "machine_specific": [], "remote": [],
    }
    (tmp_path / f"{name}.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    return fig


def _reasons(skipped):
    return sorted(s["reason"] for s in skipped)


def test_a_covered_render_map_is_replayable(tmp_path):
    fig = _map_sidecar(tmp_path, deps=[_load(), _style(2, "a")])
    records, skipped, _ = session_export.collect([str(fig)], None)
    assert len(records) == 1 and skipped == []


@pytest.mark.parametrize("deps", [
    [],                                                            # nothing recorded
    [_style(2, "a")],                                              # styled but never loaded
    [_load(layer="L2")],                                           # loaded a different layer
])
def test_an_uncovered_render_map_still_needs_session_state(tmp_path, deps):
    _, skipped, _ = session_export.collect([str(_map_sidecar(tmp_path, deps=deps))], None)
    assert _reasons(skipped) == ["needs session state"]


@pytest.mark.parametrize("bad", [
    _dep(1, "qgis_render_choropleth"),                                      # a figure tool is not a dependency
    _dep(1, "qgis_load_layer", layer_id="L1", path="${DROPBOX_ROOT}/z.gpkg"),  # path not in inputs
    _dep(1, "qgis_inspect", module="compound", kind="basemaps"),            # inspect that changes nothing
    _dep(1, "qgis_eval", code="x", secret_flag=True),                       # unknown argument
    {"seq": True, "call": {"tool": "qgis_eval", "module": "server", "arguments": {"code": "x"}}},
    {"seq": 1, "call": {"tool": "qgis_eval", "module": "server", "arguments": {"code": "x"}}, "inputs": 5},
])
def test_invalid_dependencies_skip_the_figure(tmp_path, bad):
    fig = _map_sidecar(tmp_path, deps=[_load(), bad])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["invalid depends_on"]


def test_a_network_path_in_a_dependency_is_never_opened(tmp_path):
    unc = "//evil/share/z.gpkg"
    fig = _map_sidecar(tmp_path, deps=[_load(path=unc)])
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["network path"]


def test_an_atomic_figure_may_not_carry_dependencies(tmp_path):
    fig = tmp_path / "c.png"
    fig.write_bytes(b"png")
    record = {
        "schema": provenance.SCHEMA, "figure": str(fig), "figure_sha256": hashlib.sha256(b"png").hexdigest(),
        "session_id": "s1", "seq": 5,
        "call": {"tool": "qgis_render_choropleth", "module": "server",
                 "arguments": {"zones_path": "/z.gpkg", "value_field": "n", "output_png": str(fig)}},
        "depends_on": [_dep(1, "qgis_eval", code="x = 1")],
        "inputs": [_in("zones_path", "/z.gpkg")], "implicit_inputs": [], "outputs": [str(fig)],
        "unrecorded_state": [], "machine_specific": [], "remote": [],
    }
    (tmp_path / "c.png.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    _, skipped, _ = session_export.collect([str(fig)], None)
    assert _reasons(skipped) == ["invalid depends_on"]


def _with_eval(tmp_path, figure=None):
    fig = _map_sidecar(tmp_path, deps=[_dep(1, "qgis_eval", code="x = 1"), _load(2)], figure=figure)
    records, _, _ = session_export.collect([str(fig)], None)
    return records


def test_include_evals_false_drops_every_eval(tmp_path):
    records, warnings = session_export.keep_evals(_with_eval(tmp_path), include_evals=False, trust_foreign=True)
    assert [d["call"]["tool"] for d in records[0]["depends_on"]] == ["qgis_load_layer"]
    assert len(warnings) == 1 and "include_evals=False" in warnings[0]


def test_foreign_evals_need_trust(tmp_path):
    records = _with_eval(tmp_path)                         # figure recorded as an absolute path: foreign
    kept, warnings = session_export.keep_evals(records, include_evals=True, trust_foreign=False)
    assert [d["call"]["tool"] for d in kept[0]["depends_on"]] == ["qgis_load_layer"]
    assert "trust_foreign=True" in warnings[0]
    kept, warnings = session_export.keep_evals(records, include_evals=True, trust_foreign=True)
    assert [d["call"]["tool"] for d in kept[0]["depends_on"]] == ["qgis_eval", "qgis_load_layer"]
    assert warnings == []
```

- [ ] **Step 2: Verify failure.** `uv run --no-sync pytest tests/test_replay_state.py -q -p no:cacheprovider`. Expected: `needs session state` for the covered map, `AttributeError: ... keep_evals`, and wrong reasons.

- [ ] **Step 3: Implement** in `session_export.py`.

Import `from qgis_mcp_workflows import ledger`. Remove `_STATE_READING_TOOLS`. Add:

```python
# Calls a state-reading figure depends on (spec §6). A sidecar names them only
# inside depends_on — never as its own call (TASK-13's _writes_files rule).
_DEPENDENCY_TOOLS = frozenset({
    ("server", "qgis_load_layer"), ("server", "qgis_style_categorized"), ("server", "qgis_style_graduated"),
    ("server", "qgis_project_load"), ("server", "qgis_eval"),
    ("compound", "qgis_inspect"), ("compound", "qgis_style"),
})


def _dependency_ok(entry: Any) -> bool:
    """A depends_on entry is a real ledger call whose paths are its recorded inputs."""
    from qgis_mcp_workflows import compound, server

    if not isinstance(entry, dict) or not isinstance(entry.get("call"), dict):
        return False
    call, seq, layer = entry["call"], entry.get("seq"), entry.get("layer_id")
    args = call.get("arguments")
    key = (call.get("module"), call.get("tool"))
    if key not in _DEPENDENCY_TOOLS or not isinstance(args, dict) or not _size_ok(args):
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


def keep_evals(records: list[dict], include_evals: bool, trust_foreign: bool) -> tuple[list[dict], list[str]]:
    """Drop recorded evals the caller did not ask for, or does not trust (spec §7 Trust)."""
    kept: list[dict] = []
    warnings: list[str] = []
    for record in records:
        deps = record.get("depends_on") or []
        evals = [d for d in deps if d["call"]["tool"] == "qgis_eval"]
        foreign = not record["figure"].startswith(ROOT_TOKEN)
        if evals and (not include_evals or (foreign and not trust_foreign)):
            why = ("include_evals=False" if not include_evals
                   else "the figure is outside DROPBOX_ROOT; pass trust_foreign=True to keep them")
            warnings.append(f"{_clean(record['figure'])}: {len(evals)} qgis_eval call(s) left out ({why})")
            record = {**record, "depends_on": [d for d in deps if d["call"]["tool"] != "qgis_eval"]}
        kept.append(record)
    return kept, warnings
```

In `_well_formed`, add one more conjunct to the final `return`:

```python
            and isinstance(record.get("depends_on", []), list) and _size_ok(record.get("depends_on", []))
```

In `_record_paths`, before `ins, outs, dirs = ...`, add the dependency paths. Entry shapes are not validated yet at this point (`_dependency_ok` runs later), so guard every access:

```python
    for dep in record.get("depends_on", []):
        inputs = dep.get("inputs") if isinstance(dep, dict) else None
        if isinstance(inputs, list):
            paths += [e.get("path") for e in inputs if isinstance(e, dict)]
```

In `_skip_reason`, replace the `needs session state` block:

```python
    if tool in _STATE_READING_TOOLS or (tool == "qgis_render" and args.get("mode") == "map"):
        return "needs session state"
```

with:

```python
    deps = record.get("depends_on", [])
    if not all(_dependency_ok(d) for d in deps):
        return "invalid depends_on"
    state_kind = ledger.kind(tool, args)
    if deps and state_kind not in ("map", "export"):
        return "invalid depends_on"
    if (state_kind == "map" or deps) and not _covers_state(record):
        return "needs session state"
```

Keep the `_network` check before this block. `_record_paths` now walks `depends_on`, and it does so before `_dependency_ok` validates entry shapes, so guard every access with `isinstance` as shown.

- [ ] **Step 4: Run tests.** Run `uv run --no-sync pytest tests/test_replay_state.py tests/test_replay.py tests/test_session_export.py -q -p no:cacheprovider` → all pass. The TASK-13 test `test_collect_skips_with_fixed_reasons` must still report `needs session state` for its dependency-less `qgis_render_map`. Then `uv tool run ruff check src/ tests/` → clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/session_export.py tests/test_replay_state.py
git commit -m "feat(replay): accept state-reading figures whose depends_on covers their state"
```

---

### Task 5: Emit dependency blocks, layer variables and session resets

**Files:**
- Modify: `src/qgis_mcp_workflows/session_export.py` (`plan_steps`, `source_inputs`, `notes_for`, `_SKELETON`, `_argument`, `_step_call`, `build_script`; new `_dependency_call`, `_reset`, `evals_of`)
- Test: `tests/test_replay_state.py`

**Interfaces:**
- Consumes: Task 4's validated records.
- Produces:
  - A step dict gains `"dependencies": list[dict]` (its `depends_on`, sorted by `seq`).
  - `build_script(steps, inputs, outputs, notes, generated_on, evals=())`.
  - `evals_of(steps) -> list[dict]`, where each item is `{"sha256": str, "first_lines": list[str], "figures": list[str]}`.
  - The skeleton defines `EVALS` and calls `replay.main(__file__, INPUTS, OUTPUTS, NOTES, steps, evals=EVALS)`.
  - Scripts call `replay.reset_session()`, which Task 6 defines.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_replay_state.py`:

```python
def _record(seq, deps=(), layer_ids=("L1",), session="s1", out=None):
    out = out or f"${{DROPBOX_ROOT}}/m{seq}.png"
    return {"session_id": session, "seq": seq, "figure": out, "figure_sha256": None,
            "call": {"tool": "qgis_render_map", "module": "server",
                     "arguments": {"layer_ids": list(layer_ids), "output_png": out}},
            "depends_on": list(deps), "inputs": [], "implicit_inputs": [], "outputs": [out],
            "unrecorded_state": [], "machine_specific": [], "remote": []}


def _script(records):
    steps = session_export.plan_steps(records)
    return session_export.build_script(steps, session_export.source_inputs(steps),
                                       session_export.outputs_of(steps), [], "d",
                                       session_export.evals_of(steps))


def _body(source):
    tree = ast.parse(source)
    [steps_fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "steps"]
    return [ast.unparse(s) for s in steps_fn.body]


def test_layer_ids_are_rebound_to_the_replayed_load():
    body = _body(_script([_record(10, deps=[_load(), _style(2, "a")])]))
    assert body[0] == "layer_1 = server.qgis_load_layer(path=src('${DROPBOX_ROOT}/z.gpkg')).layer_id"
    assert body[1] == "server.qgis_style_categorized(layer_id=layer_1, field='a')"
    assert body[2].startswith("server.qgis_render_map(layer_ids=[layer_1], output_png=out(")


def test_each_state_reading_figure_replays_its_own_snapshot():
    first = _record(10, deps=[_load(), _style(2, "a")])
    second = _record(20, deps=[_load(), _style(2, "a"), _style(11, "b")])
    body = _body(_script([second, first]))
    assert body.index("replay.reset_session()") > 0          # second block starts from a fresh QGIS
    assert [line.split("field=")[1] for line in body if "field=" in line] == ["'a')", "'a')", "'b')"]


def test_sessions_are_separated_by_a_reset():
    body = _body(_script([_record(10, deps=[_load()], session="a"),
                          _record(10, deps=[_load()], session="b", out="${DROPBOX_ROOT}/b.png")]))
    assert body.count("replay.reset_session()") == 1


def test_project_layers_keep_their_ids():
    project = _dep(1, "qgis_project_load", inputs=[_in("qgz_path", "${DROPBOX_ROOT}/p.qgz")],
                   qgz_path="${DROPBOX_ROOT}/p.qgz")
    body = _body(_script([_record(10, deps=[project, _style(2, "a", layer="P1")], layer_ids=("P1",))]))
    assert body[0] == "server.qgis_project_load(qgz_path=src('${DROPBOX_ROOT}/p.qgz'))"
    assert body[1] == "server.qgis_style_categorized(layer_id='P1', field='a')"
    assert "layer_ids=['P1']" in body[2]


def test_dependency_inputs_are_checked_before_the_run():
    steps = session_export.plan_steps([_record(10, deps=[_load()])])
    assert [i["path"] for i in session_export.source_inputs(steps)] == ["${DROPBOX_ROOT}/z.gpkg"]


def test_evals_are_listed_for_the_refusal():
    code = "import os" + chr(10) + "x = 1" + chr(10) + "y = 2" + chr(10) + "z = 3"
    steps = session_export.plan_steps([_record(10, deps=[_dep(1, "qgis_eval", code=code), _load(2)])])
    [item] = session_export.evals_of(steps)
    assert item["sha256"] == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert item["first_lines"] == ["import os", "x = 1", "y = 2"]
    assert item["figures"] == ["${DROPBOX_ROOT}/m10.png"]
    source = _script([_record(10, deps=[_dep(1, "qgis_eval", code=code), _load(2)])])
    assert "server.qgis_eval(code=" in source and "evals=EVALS" in source


def test_a_block_with_evals_says_their_writes_are_not_remapped():
    steps = session_export.plan_steps([_record(10, deps=[_dep(1, "qgis_eval", code="x = 1"), _load(2)])])
    assert "step 1: files written inside replayed qgis_eval code are not remapped into --out-dir" \
        in session_export.notes_for(steps)


def test_hostile_dependency_values_stay_literal():
    hostile = _dep(1, "qgis_eval", code='"); import os #' + chr(10) + 'os.system("calc")')
    tree = ast.parse(_script([_record(10, deps=[hostile, _load(2)])]))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert [ast.unparse(n) for n in imports] == ["from qgis_mcp_workflows import compound, replay, server"]
```

- [ ] **Step 2: Verify failure.** `uv run --no-sync pytest tests/test_replay_state.py -q -p no:cacheprovider`. Expected: `TypeError` (`build_script` takes no `evals`) and `AttributeError` (`evals_of`).

- [ ] **Step 3: Implement** in `session_export.py`. Add `import hashlib` and `import itertools`.

In `plan_steps`, where each step is built, attach its dependencies:

```python
        steps[key] = {**rec, "replayed_inputs": replayed,
                      "dependencies": sorted(rec.get("depends_on") or [], key=lambda d: d["seq"])}
```

In `source_inputs`, include dependency inputs. Change the inner loop's iterable to:

```python
        dependency_inputs = [i for d in step.get("dependencies") or [] for i in d.get("inputs") or []]
        for entry in (step.get("inputs") or []) + (step.get("implicit_inputs") or []) + dependency_inputs:
```

In `notes_for`, add the project note only to exports that do not rebuild their state, and state that eval writes are not remapped (spec §7):

```python
        if not step.get("dependencies") and (
                tool in _PROJECT_TOOLS or (tool == "qgis_export" and kind in ("layout", "atlas", "batch"))):
            notes.append(f"step {n}: reads its .qgz from disk; styling applied earlier in the session is not recorded")
        if any(d["call"]["tool"] == "qgis_eval" for d in step.get("dependencies") or []):
            notes.append(f"step {n}: files written inside replayed qgis_eval code are not remapped into --out-dir")
```

(The first `if` replaces the existing one, and its `notes.append` line is unchanged.)

Change the skeleton so it defines `EVALS` and passes it to `main`:

```python
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
```

Replace `_argument` and `_step_call` with versions that substitute layer ids, and add the dependency and reset builders:

```python
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
    call = _call(dep, layers)
    produced = dep.get("layer_id")
    if ledger.kind(dep["call"]["tool"], dep["call"]["arguments"]) == "load" and produced:
        name = f"layer_{next(names)}"
        layers[produced] = name
        return ast.Assign([ast.Name(name, ast.Store())], ast.Attribute(call, "layer_id", ast.Load()))
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
            item = found.setdefault(digest, {"sha256": digest, "first_lines": [], "figures": []})
            item["first_lines"] = [_clean(line) for line in code.splitlines()[:3]]
            item["figures"] += [_clean(o) for o in step.get("outputs") or [] if _clean(o) not in item["figures"]]
    return list(found.values())
```

Replace the body of `build_script`:

```python
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
    for step in steps:
        deps = step.get("dependencies") or []
        session = step.get("session_id")
        if previous is not None and (deps or session != previous):
            body.append(_reset())  # rebuilt state must not inherit layers or eval side effects
        layers: dict[str, str] = {}
        body += [_dependency_call(d, layers, names) for d in deps]
        body.append(_step_call(step, layers))
        previous = session
    steps_def.body = body or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"
```

Update `export_session` so it calls `build_script(..., evals)`. Task 7 adds the `include_evals`/`trust_foreign` parameters; for now compute `evals = evals_of(steps)` and pass it.

- [ ] **Step 4: Run tests.** Run `uv run --no-sync pytest tests/test_replay_state.py tests/test_replay.py tests/test_session_export.py -q -p no:cacheprovider` → all pass. The TASK-13 test `test_build_script_compiles_and_calls_the_tool` checks `zones_path=src(...)` and `output_png=out(...)`; it must still pass. Then `uv tool run ruff check src/ tests/` → clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/session_export.py tests/test_replay_state.py
git commit -m "feat(replay): rebuild recorded state before each state-reading figure"
```

---

### Task 6: Runtime — `--allow-eval` gate and `reset_session`

**Files:**
- Modify: `src/qgis_mcp_workflows/replay.py` (`_parse`, `main`; new `reset_session`)
- Test: `tests/test_replay.py`

**Interfaces:**
- Consumes: the `EVALS` items from Task 5.
- Produces:
  - `replay.main(script, inputs, outputs, notes, steps, argv=None, *, evals=None) -> int`, which returns 3 when evals are present and `--allow-eval` is absent.
  - `replay.reset_session() -> None`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_replay.py`:

```python
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
```

- [ ] **Step 2: Verify failure.** `uv run --no-sync pytest tests/test_replay.py -q -p no:cacheprovider`. Expected: `TypeError: main() got an unexpected keyword argument 'evals'`, `unrecognized arguments: --allow-eval`, and `AttributeError: ... reset_session`.

- [ ] **Step 3: Implement** in `replay.py`.

In `_parse`, add:

```python
    parser.add_argument("--allow-eval", action="store_true",
                        help="run the recorded qgis_eval code (read it first: it is printed when this flag is missing)")
```

Add above `main`:

```python
def reset_session() -> None:
    """Continue in a fresh QGIS, so earlier steps' layers, styles and eval side effects do not carry over."""
    from qgis_mcp_workflows import executors

    old = executors._current
    executors.set_executor(_new_executor())
    shutdown = getattr(old, "shutdown", None)
    if callable(shutdown):
        shutdown()


def _refuse_evals(evals: list[dict]) -> int:
    print("this replay runs qgis_eval code recorded in the sidecars; read it, then re-run with --allow-eval:",
          file=sys.stderr)
    for item in evals:
        figures = ", ".join(_clean(f) for f in item.get("figures") or [])
        print(f"  sha256 {_clean(item.get('sha256'))} (used by {figures})", file=sys.stderr)
        for line in (item.get("first_lines") or [])[:3]:
            print(f"    | {_clean(line)}", file=sys.stderr)
    return 3
```

Change `main`'s signature to `def main(script: str, inputs: list[dict], outputs: list[str], notes: list[str], steps: Callable[[Callable, Callable], None], argv: list[str] | None = None, *, evals: list[dict] | None = None) -> int:`. Directly after printing the notes, add:

```python
    if evals and not args.allow_eval:
        return _refuse_evals(evals)
```

Replace `main`'s `finally` block so it shuts down whatever executor the run ended with (a reset may have replaced the first one) and never the caller's:

```python
    finally:
        current = executors._current
        executors.set_executor(previous)
        if executor is not None and current is not previous:
            shutdown = getattr(current, "shutdown", None)
            if callable(shutdown):
                shutdown()
```

`reset_session` has already shut down the executors it replaced. In `test_main_shuts_down_the_executor_left_by_a_reset`, "first" is shut down by the reset and "second" by `main`.

- [ ] **Step 4: Run tests.** Run `uv run --no-sync pytest tests/test_replay.py tests/test_session_export.py -q -p no:cacheprovider` → all pass. The TASK-13 test `test_main_shuts_down_and_exits_1_when_a_step_fails` must still see `ran == ["shutdown"]`. Then `uv tool run ruff check src/ tests/` → clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/replay.py tests/test_replay.py
git commit -m "feat(replay): --allow-eval gate (exit 3) and session reset between blocks"
```

---

### Task 7: Tool parameters, `n_evals`, and FakeExecutor round trips

**Files:**
- Modify: `src/qgis_mcp_workflows/session_export.py` (`export_session`), `src/qgis_mcp_workflows/server.py` (`SessionExportResult`, `qgis_export_session`)
- Test: `tests/test_session_export.py`

**Interfaces:**
- Produces:
  - `export_session(output_py, figures=None, folder=None, include_evals=True, trust_foreign=False, overwrite=False) -> dict`, which gains the key `n_evals`.
  - `SessionExportResult.n_evals: int`.
  - `qgis_export_session(..., include_evals: bool = True, trust_foreign: bool = False, overwrite: bool = False)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_session_export.py` (it already has `PNG`, `server`, `root`, `_run_script`):

```python
def _layer_responses(fake_executor, ids):
    queue = list(ids)
    fake_executor.responses["add_vector_layer"] = lambda p: {"id": queue.pop(0), "name": "zones"}
    fake_executor.responses["get_layer_info"] = {
        "type": "vector_2", "crs": "EPSG:4326", "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
        "feature_count": 4, "fields": [{"name": "zone_id", "type": "String", "n_unique": 4}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}

    def render(params):
        Path(params["output_png"]).write_bytes(PNG)
        return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
                "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1}

    fake_executor.responses["render_layers_to_path"] = render


async def test_render_map_round_trip_restyles_a_b_a_on_the_new_layer(server, fake_executor, root, monkeypatch):
    _layer_responses(fake_executor, ["L1", "L9"])            # the replay's load returns a different id
    zones = root / "zones.geojson"
    zones.write_text("{}", encoding="utf-8")
    await server.mcp.call_tool("qgis_load_layer", {"path": str(zones)})
    for field in ("a", "b", "a"):
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L1", "field": field})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "map.png")})
    script = root / "replay.py"
    result = server.qgis_export_session(output_py=str(script), figures=[str(root / "map.png")])
    assert (result.n_calls, result.n_evals, result.skipped) == (1, 0, [])
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out")) == 0
    styles = [p for c, p in fake_executor.calls if c == "set_layer_style"]
    assert [(p["layer_id"], p["field"]) for p in styles] == [("L9", "a"), ("L9", "b"), ("L9", "a")]
    [render] = [p for c, p in fake_executor.calls if c == "render_layers_to_path"]
    assert render["layer_ids"] == ["L9"]
    assert render["output_png"] == str((root / "out" / "DROPBOX_ROOT" / "map.png").resolve())


async def test_eval_round_trip_needs_allow_eval(server, fake_executor, root, monkeypatch, capsys):
    _layer_responses(fake_executor, ["L1", "L2", "L3"])
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": ""}
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    script = root / "replay.py"
    assert server.qgis_export_session(output_py=str(script), figures=[str(root / "m.png")]).n_evals == 1
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out")) == 3
    assert fake_executor.calls == [] and "x = 1" in capsys.readouterr().err
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out"), "--allow-eval") == 0
    assert [c for c, _ in fake_executor.calls][0] == "execute_code"


async def test_include_evals_false_writes_a_script_without_them(server, fake_executor, root):
    _layer_responses(fake_executor, ["L1"])
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": ""}
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    result = server.qgis_export_session(output_py=str(root / "r.py"), figures=[str(root / "m.png")],
                                        include_evals=False)
    assert result.n_evals == 0 and "server.qgis_eval(" not in (root / "r.py").read_text(encoding="utf-8")
    assert any("include_evals=False" in w for w in result.warnings)
```

- [ ] **Step 2: Verify failure.** `uv run --no-sync pytest tests/test_session_export.py -q -p no:cacheprovider`. Expected: `ValidationError` / `AttributeError` for `n_evals` and an unexpected keyword `include_evals`.

- [ ] **Step 3: Implement.**

In `session_export.export_session`, change the signature to `def export_session(output_py: str, figures: list[str] | None = None, folder: str | None = None, include_evals: bool = True, trust_foreign: bool = False, overwrite: bool = False) -> dict:`. Directly after `records, skipped, warnings = collect(figures, folder)`, add:

```python
    records, eval_warnings = keep_evals(records, include_evals, trust_foreign)
    warnings = warnings + eval_warnings
```

Compute `evals = evals_of(steps)` after `outputs = outputs_of(steps)`, pass it to `build_script`, and add `"n_evals": len(evals),` to the returned dict after `"n_calls"`.

In `server.py`, add `n_evals: int` to `SessionExportResult` after `n_calls`. Give `qgis_export_session` two parameters between `folder` and `overwrite`:

```python
    include_evals: Annotated[bool, Field(description="Replay recorded qgis_eval calls (the script still refuses to run them without --allow-eval).")] = True,
    trust_foreign: Annotated[bool, Field(description="Keep evals from figures outside DROPBOX_ROOT (shared or other-machine sidecars).")] = False,
```

Pass them through: `export_session(output_py, figures=figures, folder=folder, include_evals=include_evals, trust_foreign=trust_foreign, overwrite=overwrite)`. Add one docstring sentence after the "writes to replay_<date>/" sentence: "State-reading figures (render_map, exports of the loaded project) replay the recorded loads, styles and evals first; a script with evals exits 3 unless run with --allow-eval."

- [ ] **Step 4: Run tests.** Run `uv run --no-sync pytest tests/test_session_export.py tests/test_replay.py tests/test_replay_state.py tests/test_ledger.py tests/test_ledger_hook.py -q -p no:cacheprovider` → all pass. Check mcp 2.x: `uv run --no-sync --with "mcp[cli]==2.3.0" pytest tests/test_session_export.py tests/test_ledger_hook.py -q -p no:cacheprovider` → all pass (this adds a temporary overlay and leaves `.venv` unchanged). Then `uv tool run ruff check src/ tests/` → clean.

- [ ] **Step 5: Commit.**

```bash
git add src/qgis_mcp_workflows/session_export.py src/qgis_mcp_workflows/server.py tests/test_session_export.py
git commit -m "feat: qgis_export_session replays state-reading figures; include_evals, trust_foreign, n_evals"
```

---

### Task 8: Live headless replay of a styled `render_map`

**Files:**
- Modify: `tests/test_replay_live.py`

- [ ] **Step 1: Write the test** — append:

```python
ZONES = os.path.join(os.path.dirname(__file__), "fixtures", "tiny_zones.geojson")


async def test_styled_map_replays_to_the_same_size(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    provenance.reset_for_tests()
    server = importlib.import_module("qgis_mcp_workflows.server")
    zones = tmp_path / "zones.geojson"
    zones.write_bytes(Path(ZONES).read_bytes())
    executor = HeadlessExecutor()
    executors.set_executor(executor)
    try:
        await server.mcp.call_tool("qgis_load_layer", {"path": str(zones)})
        [layer_id] = list(ledger._layers)  # reset_for_tests() emptied the ledger: this is the one just loaded
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": layer_id, "field": "zone_id"})
        await server.mcp.call_tool("qgis_render_map", {"layer_ids": [layer_id], "output_png": str(tmp_path / "m.png")})
    finally:
        executor.shutdown()
        executors.set_executor(None)
    script = tmp_path / "replay.py"
    result = server.qgis_export_session(output_py=str(script), figures=[str(tmp_path / "m.png")])
    assert result.skipped == []
    done = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=300, env={**os.environ})
    assert done.returncode == 0, done.stdout + done.stderr
    replayed = tmp_path / "out" / "DROPBOX_ROOT" / "m.png"
    assert replayed.stat().st_size == (tmp_path / "m.png").stat().st_size
```

Change the package import line to `from qgis_mcp_workflows import executors, ledger, provenance`, and add `from pathlib import Path` if it is not imported yet. The layer id is read from the ledger rather than the `call_tool` return value, whose shape differs between mcp 1.x and 2.x.

- [ ] **Step 2: Run.** `uv run --no-sync pytest tests/test_replay_live.py -q -p no:cacheprovider` → 2 passed on a machine with QGIS LTR (skipped elsewhere). If the sizes differ only because the render is not byte-deterministic, rule it in the ledger and compare the PNG dimensions (IHDR, bytes 16–24) instead.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_replay_live.py
git commit -m "test(replay): live headless replay of a styled render_map"
```

---

### Task 9: Docs and task status

**Files:** `docs/DESIGN.md`, `CHANGELOG.md`, `CLAUDE.md`, `backlog/tasks/task-15 - Provenance-state-ledger-and-eval-replay.md`

- [ ] **Step 1: DESIGN §4.** In the `qgis_export_session` entry:
  - Change the signature to `qgis_export_session(output_py: str, figures: list[str] | None = None, folder: str | None = None, include_evals: bool = True, trust_foreign: bool = False, overwrite: bool = False) → SessionExportResult`.
  - Add `"n_evals": int` to the result block.
  - Replace "Until TASK-15, `qgis_render_map` figures are skipped (`needs session state`)." with: "State-reading figures (`qgis_render_map`, exports of the loaded project) carry `depends_on` and replay as a block: a fresh QGIS, the recorded loads/styles/evals (loaded layer ids rebound to `layer_N`), then the figure; a figure whose `depends_on` does not cover its layers or project is skipped (`needs session state`). Evals run only with `--allow-eval` (exit 3 without, after printing each eval's sha256 and first lines); foreign evals need `trust_foreign=True`."
  - Add `invalid depends_on` to the list of skip reasons.
- [ ] **Step 2: DESIGN §5.** After the **Provenance.** paragraph, add: "**State ledger.** `ledger.py` keeps, per server process, the loaded layers with every style call, the loaded project and successful evals (spec §6). A state-reading figure's sidecar snapshots the relevant calls into `depends_on` before the call runs; any figure made after an eval is noted in `unrecorded_state`, and so is a state-reading figure recorded over the plugin transport."
- [ ] **Step 3: CHANGELOG.** Under a new `## Unreleased — provenance state ledger and eval replay` heading at the top, add an `### Added` paragraph summarising Steps 1–2: depends_on, render_map and stateful exports replayable, the `--allow-eval` gate with exit 3, `include_evals` / `trust_foreign`, and `n_evals`.
- [ ] **Step 4: CLAUDE.md.** In the `qgis_export_session` row, append "; state-reading figures replay their recorded loads/styles/evals (`--allow-eval` for evals)".
- [ ] **Step 5: Task file.** Set `status: In Progress`, tick acceptance criteria #1–#5, and note in Implementation Notes:
  - The session reset replaces the headless executor; there is no plugin command.
  - Foreign or excluded evals are reported as warnings, per spec §7 Trust, not as skipped figures.
  - Dependency calls are re-emitted per state-reading figure, so each figure replays its own snapshot.
- [ ] **Step 6: Full suite, ruff, docs test, commit.** Run `uv run --no-sync pytest tests/ -q -p no:cacheprovider` (only the known lock failure is allowed) and `uv tool run ruff check src/ tests/`, then commit with `git commit -m "docs: provenance state ledger and eval replay"`.

---

### Task 10: Verify, review, PR

- [ ] **Step 1:** Run the `verification-loop` skill: full suite, ruff, and the secrets/scaffold/machine-path scans against `origin/main`. Also run the touched test files on mcp 2.3 via `uv run --no-sync --with "mcp[cli]==2.3.0" pytest ...`.
- [ ] **Step 2:** Run fresh-context reviews on the most capable model: `code-reviewer`, `python-reviewer`, and `security-reviewer`. The security review should focus on eval gating, `depends_on` validation, and the layer-id substitution being limited to `layer_id`/`layer_ids`. Brief all of them: no `uv`/`pip`, no DuckDB `INSTALL`, nothing that downloads. Do one fix pass for CRITICAL/HIGH findings, each fix red-first; MEDIUM/LOW go to the PR's parked list.
- [ ] **Step 3:** Push `task/task-15-state-ledger`, then run `gh pr create --repo wattwong103/qgis-mcp-north --base main` with the verification report, reviews, parked list and status line.

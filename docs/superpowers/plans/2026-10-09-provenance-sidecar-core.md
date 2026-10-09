# Provenance Sidecar Core (TASK-12) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every file an MCP tool call writes gets a `<file>.provenance.json` sidecar that traces it to its exact call, inputs and environment, with paths portable across North's machines.

**Architecture:** A new module `src/qgis_mcp_workflows/provenance.py` provides `with_provenance(fn)`, applied only to the *registered* copy of each tool inside `_maybe_tool` / `_maybe_compound_tool` (`server.py`), under `with_png_preview`. It binds arguments, fingerprints inputs before the call, runs the tool, then extracts the written files from the result model, fingerprints them and writes one sidecar per file atomically. Python callers get the bare function and record nothing.

**Tech Stack:** Python 3.12, stdlib only (`hashlib`, `inspect`, `tempfile`, `weakref`, `subprocess` for `git`), Pydantic v2 result models, FastMCP (mcp `>=1.21.1,<3`), pytest with the repo's `FakeExecutor`.

**Spec:** `docs/superpowers/specs/2026-10-08-figure-provenance-design.md` (v2) — §3, §4, §5 (not the TASK-16 items), §8, §10, §11 "TASK-12".

## Global Constraints

- Schema id: `"qgis-mcp-workflows/provenance@1"`; sidecar name `<file>.provenance.json`.
- `QGIS_MCP_WORKFLOWS_PROVENANCE=0` disables recording, read **per call**; default `1`.
- `QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB`, default `256`: inputs above it get size + mtime only (`hashed: false`).
- All timestamps UTC (`…Z`).
- Portable paths: `${DROPBOX_ROOT}/<rest>`, whole-component match, case-insensitive on Windows only, `realpath` of path and root also tried; otherwise absolute and listed in `machine_specific`; **no `~/Dropbox` fallback**.
- A failed tool call writes nothing; a failed sidecar write never fails the tool and deletes a stale sidecar for that figure; **tool response schemas do not change**.
- Pure-Python tools must not spawn headless QGIS just to be recorded.
- No plugin changes (`qgis_mcp_workflows_plugin/` untouched).
- Tests must pass under the `FakeExecutor` dispatch guard (`tests/plugin_contract.py`, `PluginContractError`).
- Checklist: `uv run --no-sync pytest tests/ -q` and `uv tool run ruff check src/ tests/` green (the pre-existing Windows file-lock failure in `tests/test_headless_executor.py::test_add_vector_layer_returns_layer_id` is known and not ours).
- Deviation from spec §12 item 1 (intentional): the `qgis_eval` registration path moves to TASK-15, where it is first used (no dead code in TASK-12); `profile_folder` is not recorded (one `diagnose` dispatch carries QGIS + plugin versions; `get_qgis_info` would be a second dispatch).

## Review Focus

1. **An input path that does not exist** (tests and agents pass `/zones.gpkg`): recorded with `"missing": true`, no crash, no hash. → Task 3 test `test_fingerprint_missing_file`.
2. **A figure folder where the sidecar cannot be written** (Dropbox lock, permissions): the tool result comes back unchanged and any old sidecar is removed. → Task 6 tests `test_write_sidecar_replace_failure_removes_stale` / `test_write_sidecar_mkstemp_failure`.
3. **Arguments that are not JSON** (tuples like `extent`, `Path`): stored as JSON lists/strings. → Task 7 test `test_portable_arguments_serialises_tuples_and_paths`.
4. **A tool that writes no file** (`qgis_style_categorized` through MCP): no sidecar, no error. → Task 7 test `test_tool_without_output_writes_no_sidecar`.
5. **Concurrent calls** (mcp 2.x worker threads): `seq` stays unique. → Task 7 test `test_seq_unique_across_threads`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/qgis_mcp_workflows/provenance.py` (create) | Everything on the record side: paths, fingerprints, classification, written-file extraction, environment, atomic write, the decorator |
| `src/qgis_mcp_workflows/executors/__init__.py` (modify) | `executor_requests()` counter, so the recorder knows whether a call touched QGIS |
| `src/qgis_mcp_workflows/server.py` (modify `_maybe_tool`, `_maybe_compound_tool`) | Apply `with_provenance` to registered copies |
| `install.py` (modify `_server_entry`, claude-code fallback) | Pass `DROPBOX_ROOT` into JSON / Zed client configs, never into the tracked `.mcp.json` |
| `tests/test_provenance.py` (create) | Unit tests for the module |
| `tests/test_provenance_hook.py` (create) | Through-MCP tests (`server.mcp.call_tool`) in full mode |
| `tests/test_compound_mode.py` (modify) | One compound-mode sidecar test (reuses its `compound_module` fixture) |
| `tests/test_install.py` (modify) | `DROPBOX_ROOT` in client configs |
| `docs/DESIGN.md`, `CLAUDE.md`, `CHANGELOG.md`, `.gitignore`, `backlog/tasks/task-12 - Figure-provenance-sidecar-core.md` (modify) | Docs, env table, ignore sidecars, task status |

---

### Task 1: Executor request counter

**Files:**
- Modify: `src/qgis_mcp_workflows/executors/__init__.py:26-46`
- Test: `tests/test_provenance.py` (create)

**Interfaces:**
- Produces: `qgis_mcp_workflows.executors.executor_requests() -> int` — number of `get_executor()` calls so far in this process.

- [ ] **Step 1: Write the failing test**

Create `tests/test_provenance.py`:

```python
"""Unit tests for figure provenance (TASK-12). No QGIS needed."""

from __future__ import annotations

from qgis_mcp_workflows import executors


def test_executor_requests_counts_get_executor_calls(fake_executor):
    before = executors.executor_requests()
    executors.get_executor()
    executors.get_executor()
    assert executors.executor_requests() == before + 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: FAIL with `AttributeError: module 'qgis_mcp_workflows.executors' has no attribute 'executor_requests'`

- [ ] **Step 3: Implement**

In `src/qgis_mcp_workflows/executors/__init__.py` replace the block from `_current: Executor | None = None` to the end with:

```python
_current: Executor | None = None
# Tools call get_executor() right before dispatching, so this tells the
# provenance recorder whether a call touched QGIS (provenance.environment).
_requests = 0


def get_executor() -> Executor:
    """Return the active executor; lazily creates a ``PluginExecutor`` default."""
    global _current, _requests
    _requests += 1
    if _current is None:
        from qgis_mcp_workflows.executors.plugin import PluginExecutor

        _current = PluginExecutor()
    return _current


def executor_requests() -> int:
    """How many times get_executor() has been called in this process."""
    return _requests


def set_executor(executor: Executor | None) -> None:
    """Override the active executor (test hook). Pass None to reset."""
    global _current
    _current = executor


__all__ = ["Executor", "executor_requests", "get_executor", "set_executor"]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/qgis_mcp_workflows/executors/__init__.py tests/test_provenance.py
git commit -m "feat(provenance): count executor requests"
```

---

### Task 2: Portable paths

**Files:**
- Create: `src/qgis_mcp_workflows/provenance.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Produces:
  - `normalise(path: str) -> str` — `expanduser` + `abspath`, `/` separators.
  - `portable(path: str) -> tuple[str, str | None]` — `(stored form, machine-specific reason or None)`; reasons are exactly `"DROPBOX_ROOT unset"` or `"outside DROPBOX_ROOT"`.
  - `ROOT_TOKEN = "${DROPBOX_ROOT}"`, `SCHEMA`, `SIDECAR_SUFFIX = ".provenance.json"`.
  - `_case_insensitive() -> bool` (monkeypatchable).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_provenance.py`)

```python
import os

import pytest

from qgis_mcp_workflows import provenance


def test_normalise_uses_forward_slashes_and_absolute(monkeypatch, tmp_path):
    # chdir first: os.path.relpath raises across Windows drives (C: temp vs H: repo).
    monkeypatch.chdir(tmp_path)
    expected = os.path.realpath(str(tmp_path / "a" / "b.csv")).replace("\\", "/")
    assert provenance.normalise(os.path.join("a", "b.csv")).casefold() == expected.casefold()


def test_portable_under_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    stored, reason = provenance.portable(str(tmp_path / "gufm" / "fig.png"))
    assert stored == "${DROPBOX_ROOT}/gufm/fig.png"
    assert reason is None


def test_portable_matches_whole_components_only(monkeypatch, tmp_path):
    root = tmp_path / "Dropbox"
    monkeypatch.setenv("DROPBOX_ROOT", str(root))
    stored, reason = provenance.portable(str(tmp_path / "Dropbox-old" / "x.png"))
    assert stored == provenance.normalise(str(tmp_path / "Dropbox-old" / "x.png"))
    assert reason == "outside DROPBOX_ROOT"


def test_portable_unset_root_is_machine_specific(monkeypatch, tmp_path):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    stored, reason = provenance.portable(str(tmp_path / "x.png"))
    assert stored == provenance.normalise(str(tmp_path / "x.png"))
    assert reason == "DROPBOX_ROOT unset"


@pytest.mark.parametrize("insensitive, expected", [(True, "${DROPBOX_ROOT}/x.png"), (False, None)])
def test_portable_case_rule(monkeypatch, tmp_path, insensitive, expected):
    monkeypatch.setattr(provenance, "_case_insensitive", lambda: insensitive)
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "Root"))
    stored, _ = provenance.portable(str(tmp_path / "ROOT" / "x.png"))
    if expected is None:
        assert not stored.startswith("${DROPBOX_ROOT}")
    else:
        assert stored == expected
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: FAIL with `ImportError: cannot import name 'provenance'`

- [ ] **Step 3: Create the module with the path helpers**

Create `src/qgis_mcp_workflows/provenance.py`:

```python
"""Figure provenance: a sidecar next to every file an MCP tool call writes.

Spec: docs/superpowers/specs/2026-10-08-figure-provenance-design.md (v2).
Only the registered (MCP) copy of a tool is wrapped (``with_provenance`` in
server._maybe_tool / _maybe_compound_tool); Python callers get the bare
function and record nothing.
"""

from __future__ import annotations

import logging
import os

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
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/qgis_mcp_workflows/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): portable DROPBOX_ROOT paths"
```

---

### Task 3: Fingerprints

**Files:**
- Modify: `src/qgis_mcp_workflows/provenance.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Consumes: `normalise`, `portable` (Task 2).
- Produces:
  - `hash_cap_bytes() -> int`
  - `fingerprint(path: str) -> dict` with keys `path` (portable), `bytes`, `mtime` (UTC str), `sha256` (str or None), `hashed` (bool), `missing` (bool).
  - `snapshot(path: str) -> tuple[int, int] | None` — `(size, mtime_ns)` of a regular file.
  - `reset_for_tests() -> None` — clears the hash cache (Task 5 extends it).
  - `_utc(ts: float) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
import hashlib


def test_fingerprint_hashes_small_file(tmp_path):
    f = tmp_path / "zones.gpkg"
    f.write_bytes(b"abc")
    fp = provenance.fingerprint(str(f))
    assert fp["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert fp["hashed"] is True and fp["bytes"] == 3 and fp["missing"] is False
    assert fp["mtime"].endswith("Z")


def test_fingerprint_above_cap_is_not_hashed(monkeypatch, tmp_path):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB", "0.000001")  # ~1 byte
    f = tmp_path / "big.duckdb"
    f.write_bytes(b"0123456789")
    fp = provenance.fingerprint(str(f))
    assert fp["sha256"] is None and fp["hashed"] is False and fp["bytes"] == 10


def test_fingerprint_missing_file(tmp_path):
    fp = provenance.fingerprint(str(tmp_path / "nope.csv"))
    assert fp["missing"] is True and fp["bytes"] is None and fp["sha256"] is None


def test_fingerprint_cache_avoids_second_hash(monkeypatch, tmp_path):
    provenance.reset_for_tests()
    f = tmp_path / "a.csv"
    f.write_bytes(b"x")
    provenance.fingerprint(str(f))
    hashed = []
    real_sha256 = provenance.hashlib.sha256
    monkeypatch.setattr(provenance.hashlib, "sha256", lambda *a: (hashed.append(1), real_sha256(*a))[1])
    assert provenance.fingerprint(str(f))["sha256"] == real_sha256(b"x").hexdigest()
    assert hashed == []


def test_snapshot_changes_when_file_changes(tmp_path):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x")
    before = provenance.snapshot(str(f))
    f.write_bytes(b"xy")
    assert provenance.snapshot(str(f)) != before
    assert provenance.snapshot(str(tmp_path / "none")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: FAIL with `AttributeError: module 'qgis_mcp_workflows.provenance' has no attribute 'fingerprint'`

- [ ] **Step 3: Implement** — add to `provenance.py` (imports at top, functions after `portable`):

```python
import datetime as _dt
import hashlib
import stat
import threading

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
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


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
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add src/qgis_mcp_workflows/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): input fingerprints with size cap and cache"
```

---

### Task 4: Classify inputs and extract written files (+ both drift guards)

**Files:**
- Modify: `src/qgis_mcp_workflows/provenance.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Consumes: `normalise`, `snapshot` (Tasks 2–3).
- Produces:
  - Constants `INPUT_ARGS`, `INPUT_LIST_ARGS`, `OUTPUT_ARGS`, `NOT_PATH_ARGS`, `PATH_HINTS`, `WRITTEN_BY_MODEL: dict[str, Callable]`.
  - `input_paths(args: dict) -> list[tuple[str, str]]` — `(argument, normalised path)`.
  - `implicit_input_paths(tool: str, args: dict) -> list[tuple[str, str]]`.
  - `written_files(result) -> list[str]` — normalised, existing regular files, de-duplicated, in order.
  - `result_summary(result) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
import inspect
import typing

from pydantic import BaseModel


def _tool_functions():
    from qgis_mcp_workflows import compound, server

    for module in (server, compound):
        for name, fn in vars(module).items():
            if name.startswith("qgis_") and inspect.isfunction(fn) and fn.__module__ == module.__name__:
                yield fn


def test_every_path_like_parameter_is_classified():
    known = (provenance.INPUT_ARGS | provenance.INPUT_LIST_ARGS
             | provenance.OUTPUT_ARGS | provenance.NOT_PATH_ARGS)
    missing = sorted(
        f"{fn.__name__}({param})"
        for fn in _tool_functions()
        for param in inspect.signature(fn).parameters
        if any(h in param for h in provenance.PATH_HINTS) and param not in known
    )
    assert missing == []


def _result_models():
    for fn in _tool_functions():
        ret = typing.get_type_hints(fn).get("return")
        for cls in (typing.get_args(ret) or (ret,)):
            if isinstance(cls, type) and issubclass(cls, BaseModel):
                yield cls


def test_every_result_model_with_a_path_field_has_an_extractor():
    path_fields = {"output_path", "output_csv", "pptx_path", "files", "manifest"}
    uncovered = sorted({
        cls.__name__ for cls in _result_models()
        if path_fields & set(cls.model_fields)
        and not any(c.__name__ in provenance.WRITTEN_BY_MODEL for c in cls.__mro__)
    })
    assert uncovered == []


def test_input_paths_flattens_lists_and_skips_empty(tmp_path):
    args = {"zones_path": str(tmp_path / "z.gpkg"), "trajectory_csvs": ["a.csv", "b.csv"],
            "value_csv": None, "output_png": "o.png", "palette": "gufm"}
    names = [name for name, _ in provenance.input_paths(args)]
    assert names == ["zones_path", "trajectory_csvs", "trajectory_csvs"]


def test_implicit_bundled_deck_template():
    found = provenance.implicit_input_paths("qgis_figures_to_pptx", {"template_pptx": None})
    assert [name for name, _ in found] == ["template_pptx (bundled)"]
    assert provenance.implicit_input_paths("qgis_figures_to_pptx", {"template_pptx": "t.pptx"}) == []


def test_written_files_atlas_uses_files_not_directory(tmp_path):
    from qgis_mcp_workflows.server import AtlasExportResult

    pages = [tmp_path / "a.png", tmp_path / "b.png"]
    for p in pages:
        p.write_bytes(b"png")
    result = AtlasExportResult(output_dir=str(tmp_path), output_path=str(tmp_path), format="png",
                               n_pages=2, layout_name="L", files=[str(p) for p in pages])
    assert provenance.written_files(result) == [provenance.normalise(str(p)) for p in pages]


def test_written_files_skips_missing_outputs(tmp_path):
    from qgis_mcp_workflows.server import ExportResult

    result = ExportResult(output_path=str(tmp_path / "never.pdf"), format="pdf", n_pages=1, layout_name="L")
    assert provenance.written_files(result) == []


def test_result_summary_keeps_scalars_drops_paths():
    from qgis_mcp_workflows.server import ExportResult

    summary = provenance.result_summary(
        ExportResult(output_path="/x.pdf", format="pdf", n_pages=3, layout_name="L"))
    # Subset, not equality: #25 adds unavailable_layers ([] — a short scalar list, kept).
    assert {"format": "pdf", "n_pages": 3, "layout_name": "L"}.items() <= summary.items()
    assert "output_path" not in summary
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: FAIL with `AttributeError: … has no attribute 'INPUT_ARGS'`

- [ ] **Step 3: Implement** — add to `provenance.py`:

```python
from collections.abc import Callable
from typing import Any

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


def result_summary(result: Any) -> dict:
    """Decision fields of the result (breaks, counts, flags) for replay diffs."""
    summary: dict = {}
    for key, value in result.model_dump(mode="json").items():
        if key in _SUMMARY_SKIP or key.endswith("_path"):
            continue
        if _scalar(value):
            summary[key] = value
        elif isinstance(value, list) and len(value) <= _SUMMARY_MAX_LIST and all(map(_scalar, value)):
            summary[key] = value
    return summary
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: PASS. If the drift guard lists a parameter or model, classify it in the constant it belongs to (inputs read, outputs written) — never widen `NOT_PATH_ARGS` to silence it.

- [ ] **Step 5: Commit**

```bash
git add src/qgis_mcp_workflows/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): classify inputs, extract written files, drift guards"
```

---

### Task 5: Environment block

**Files:**
- Modify: `src/qgis_mcp_workflows/provenance.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Consumes: `executors.get_executor()` (Task 1).
- Produces: `environment(dispatched: bool) -> dict` with keys `qgis_mcp_workflows`, `git`, `git_dirty`, `extras`, `mcp`, `python`, `platform`, `transport`, `qgis`, `plugin`. `reset_for_tests()` also clears the environment caches.

- [ ] **Step 1: Write the failing tests**

```python
def test_environment_not_queried_when_tool_did_not_dispatch(fake_executor):
    provenance.reset_for_tests()
    env = provenance.environment(dispatched=False)
    assert env["qgis"] == "not queried" and env["plugin"] == "not queried"
    assert fake_executor.calls == []
    assert {"qgis_mcp_workflows", "git", "git_dirty", "extras", "mcp", "python", "platform"} <= set(env)


def test_environment_reads_diagnose_and_caches_success(fake_executor):
    provenance.reset_for_tests()
    fake_executor.responses["diagnose"] = {"checks": [
        {"name": "qgis", "status": "ok", "detail": {"qgis_version": "3.40.14-Bratislava"}},
        {"name": "plugin_version", "status": "ok", "detail": "1.14.0"},
    ]}
    env = provenance.environment(dispatched=True)
    assert (env["qgis"], env["plugin"], env["transport"]) == ("3.40.14-Bratislava", "1.14.0", "fake")
    provenance.environment(dispatched=True)
    assert [c for c, _ in fake_executor.calls] == ["diagnose"]  # cached


def test_environment_never_caches_a_failure(fake_executor):
    provenance.reset_for_tests()

    def down(params):
        raise ConnectionError("degraded")

    fake_executor.responses["diagnose"] = down
    assert provenance.environment(dispatched=True)["qgis"] == "unknown"
    provenance.environment(dispatched=True)
    assert [c for c, _ in fake_executor.calls] == ["diagnose", "diagnose"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_provenance.py -q -k environment`
Expected: FAIL with `AttributeError: … has no attribute 'environment'`

- [ ] **Step 3: Implement** — add to `provenance.py`:

```python
import importlib.metadata
import importlib.util
import platform
import subprocess
import weakref
from pathlib import Path

_EXTRAS = {"pptx": "pptx", "duckdb": "duckdb", "network": "networkx",
           "trajectory": "movingpandas", "drm": "geopandas"}
_static_env: dict | None = None
_qgis_env: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _git_state() -> dict:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return {"git": None, "git_dirty": None}
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"git": None, "git_dirty": None}
    return {"git": sha or None, "git_dirty": bool(dirty)}


def _static_environment() -> dict:
    global _static_env
    if _static_env is None:
        _static_env = {
            "qgis_mcp_workflows": _package_version("qgis-mcp-workflows"),
            **_git_state(),
            "extras": sorted(e for e, module in _EXTRAS.items() if importlib.util.find_spec(module)),
            "mcp": _package_version("mcp"),
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
        }
    return dict(_static_env)


def _qgis_versions(executor: Any) -> dict:
    cached = _qgis_env.get(executor)
    if cached is not None:
        return cached
    try:
        checks = {c.get("name"): c for c in executor.dispatch("diagnose", {}, timeout=10).get("checks", [])}
    except Exception:  # degraded transport or a test fake: never cached
        return {"qgis": "unknown", "plugin": "unknown"}
    qgis = checks.get("qgis", {}).get("detail")
    versions = {
        "qgis": qgis.get("qgis_version", "unknown") if isinstance(qgis, dict) else "unknown",
        "plugin": str(checks.get("plugin_version", {}).get("detail", "unknown")),
    }
    _qgis_env[executor] = versions
    return versions


def environment(dispatched: bool) -> dict:
    """Package/runtime facts; QGIS + plugin versions only if the call used QGIS.

    A pure-Python tool (figures_to_pptx, route_on_network, assign_section_load)
    must not spawn headless QGIS just to be recorded.
    """
    env = _static_environment()
    if not dispatched:
        env.update(transport="not queried", qgis="not queried", plugin="not queried")
        return env
    from qgis_mcp_workflows import executors

    executor = executors.get_executor()
    env["transport"] = type(executor).__name__.removesuffix("Executor").lower() or "unknown"
    env.update(_qgis_versions(executor))
    return env
```

And extend `reset_for_tests`:

```python
def reset_for_tests() -> None:
    global _static_env
    with _cache_lock:
        _hash_cache.clear()
    _static_env = None
    _qgis_env.clear()
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/qgis_mcp_workflows/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): environment block without spawning QGIS for pure-Python tools"
```

---

### Task 6: Atomic sidecar write

**Files:**
- Modify: `src/qgis_mcp_workflows/provenance.py`
- Test: `tests/test_provenance.py`

**Interfaces:**
- Produces: `sidecar_path(figure: str) -> str`; `write_sidecar(figure: str, record: dict) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
import json


def test_write_sidecar_writes_json_and_no_temp(tmp_path):
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"png")
    assert provenance.write_sidecar(str(fig), {"schema": provenance.SCHEMA}) is True
    side = tmp_path / "fig.png.provenance.json"
    assert json.loads(side.read_text(encoding="utf-8")) == {"schema": provenance.SCHEMA}
    assert [p.name for p in tmp_path.iterdir()] == sorted(["fig.png", "fig.png.provenance.json"])


def test_write_sidecar_replace_failure_removes_stale(monkeypatch, tmp_path):
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"png")
    stale = tmp_path / "fig.png.provenance.json"
    stale.write_text("{}", encoding="utf-8")

    def locked(src, dst):
        raise PermissionError("Dropbox holds the file")

    monkeypatch.setattr(provenance.os, "replace", locked)
    monkeypatch.setattr(provenance, "_REPLACE_DELAY_S", 0)
    assert provenance.write_sidecar(str(fig), {"schema": "x"}) is False
    assert not stale.exists()                     # cannot vouch for the new figure
    assert [p.name for p in tmp_path.iterdir()] == ["fig.png"]  # no temp left


def test_write_sidecar_mkstemp_failure(monkeypatch, tmp_path):
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"png")

    def no_space(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(provenance.tempfile, "mkstemp", no_space)
    assert provenance.write_sidecar(str(fig), {"schema": "x"}) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_provenance.py -q -k write_sidecar`
Expected: FAIL with `AttributeError: … has no attribute 'write_sidecar'`

- [ ] **Step 3: Implement** — add to `provenance.py`:

```python
import json
import tempfile
import time

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
        try:
            os.remove(target)  # a stale sidecar must not vouch for the new figure
        except OSError:
            pass
        return False
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_provenance.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/qgis_mcp_workflows/provenance.py tests/test_provenance.py
git commit -m "feat(provenance): atomic sidecar write that never leaves a stale sidecar"
```

---

### Task 7: The decorator and the hook

**Files:**
- Modify: `src/qgis_mcp_workflows/provenance.py`
- Modify: `src/qgis_mcp_workflows/server.py:44` (import) and `:272-295` (`_maybe_tool`, `_maybe_compound_tool`)
- Test: `tests/test_provenance.py` (units), `tests/test_provenance_hook.py` (create), `tests/test_compound_mode.py` (one test)

**Interfaces:**
- Consumes: everything above; `executors.executor_requests()`.
- Produces: `with_provenance(fn: Callable) -> Callable`; module attributes `SESSION_ID: str`, `_next_seq() -> int`, `enabled() -> bool`, `_portable_arguments(args: dict) -> dict`.

- [ ] **Step 1: Write the failing unit tests** (append to `tests/test_provenance.py`)

```python
import threading
from pathlib import Path


def test_enabled_reads_env_per_call(monkeypatch):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE", "0")
    assert provenance.enabled() is False
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE", "1")
    assert provenance.enabled() is True


def test_seq_unique_across_threads():
    seen: list[int] = []
    lock = threading.Lock()

    def take():
        for _ in range(200):
            value = provenance._next_seq()
            with lock:
                seen.append(value)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == len(set(seen)) == 1600


def test_portable_arguments_serialises_tuples_and_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    out = provenance._portable_arguments(
        {"extent": (1, 2, 3, 4), "zones_path": Path(tmp_path / "z.gpkg"), "palette": "gufm"})
    assert out == {"extent": [1, 2, 3, 4], "zones_path": "${DROPBOX_ROOT}/z.gpkg", "palette": "gufm"}
```

- [ ] **Step 2: Write the failing through-MCP tests**

Create `tests/test_provenance_hook.py`:

```python
"""Sidecars through the registered MCP tools (full mode). No QGIS needed."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def server():
    # Not `from qgis_mcp_workflows import server`: test_compound_mode reloads the
    # package and the attribute can point at the compound-mode copy.
    return importlib.import_module("qgis_mcp_workflows.server")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PROVENANCE", raising=False)
    provenance.reset_for_tests()
    return tmp_path


def _choropleth_response(params):
    Path(params["output_png"]).write_bytes(PNG)
    return {
        "output_path": params["output_png"], "width": 800, "height": 600, "dpi": 150,
        "extent": [139.5, 35.5, 140.0, 35.9], "crs": "EPSG:4326", "n_layers": 1,
        "field": "n", "n_classes": 5, "breaks": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "mode": "quantile",
        "min_value": 1.0, "max_value": 6.0, "n_features": 23, "n_matched": 23, "n_unmatched": 0,
    }


def _sidecar(path: Path) -> dict:
    return json.loads(Path(str(path) + ".provenance.json").read_text(encoding="utf-8"))


async def _render(server, root: Path, name: str = "fig.png") -> Path:
    zones = root / "zones.gpkg"
    zones.write_bytes(b"zones")
    png = root / name
    await server.mcp.call_tool(
        "qgis_render_choropleth",
        {"zones_path": str(zones), "value_field": "n", "output_png": str(png)},
    )
    return png


async def test_render_through_mcp_writes_a_sidecar(server, fake_executor, root):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)

    record = _sidecar(png)
    assert record["schema"] == provenance.SCHEMA
    assert record["figure"] == "${DROPBOX_ROOT}/fig.png"
    assert record["figure_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert record["call"]["tool"] == "qgis_render_choropleth" and record["call"]["module"] == "server"
    assert record["call"]["arguments"]["mode"] == "quantile"            # default recorded
    assert record["call"]["arguments"]["zones_path"] == "${DROPBOX_ROOT}/zones.gpkg"
    zones = record["inputs"][0]
    assert zones["argument"] == "zones_path"
    assert zones["sha256"] == hashlib.sha256(b"zones").hexdigest()
    assert zones["changed_during_call"] is False and zones["made_by"] is None
    assert record["outputs"] == ["${DROPBOX_ROOT}/fig.png"]
    assert record["result_summary"]["n_matched"] == 23
    assert record["session_id"] == provenance.SESSION_ID and isinstance(record["seq"], int)
    assert record["environment"]["transport"] == "fake"
    assert record["machine_specific"] == []


async def test_direct_python_call_writes_no_sidecar(server, fake_executor, root):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    zones = root / "zones.gpkg"
    zones.write_bytes(b"zones")
    server.qgis_render_choropleth(zones_path=str(zones), value_field="n", output_png=str(root / "d.png"))
    assert not (root / "d.png.provenance.json").exists()


async def test_disabled_writes_no_sidecar(server, fake_executor, root, monkeypatch):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE", "0")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    assert not Path(str(png) + ".provenance.json").exists()


async def test_failed_call_writes_no_sidecar(server, fake_executor, root):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(params):
        Path(params["output_png"]).write_bytes(PNG)  # even a half-written figure
        raise ExecutorError("render_choropleth", "boom")

    fake_executor.responses["render_choropleth"] = fail
    with pytest.raises(Exception):
        await _render(server, root)
    assert not (root / "fig.png.provenance.json").exists()


async def test_tool_without_output_writes_no_sidecar(server, fake_executor, root):
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}
    await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L1", "field": "mode"})
    assert not list(root.glob("*.provenance.json"))


async def test_deck_links_its_figure_and_skips_qgis(server, fake_executor, root):
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    calls_before = len(fake_executor.calls)
    deck = root / "deck.pptx"
    await server.mcp.call_tool("qgis_figures_to_pptx", {"figure_paths": [str(png)], "pptx_path": str(deck)})

    record = _sidecar(deck)
    figure_input = next(i for i in record["inputs"] if i["argument"] == "figure_paths")
    assert figure_input["made_by"] == "${DROPBOX_ROOT}/fig.png.provenance.json"
    assert record["environment"]["qgis"] == "not queried"
    assert len(fake_executor.calls) == calls_before                    # no diagnose dispatch
    # assets/sekilab_blank.pptx ships in the repo, so the default template is recorded.
    assert [i["argument"] for i in record["implicit_inputs"]] == ["template_pptx (bundled)"]
    assert record["implicit_inputs"][0]["hashed"] is True


async def test_overwritten_producer_breaks_the_link(server, fake_executor, root):
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    png.write_bytes(PNG + b"edited by a script")                         # no new sidecar
    deck = root / "deck.pptx"
    await server.mcp.call_tool("qgis_figures_to_pptx", {"figure_paths": [str(png)], "pptx_path": str(deck)})
    figure_input = next(i for i in _sidecar(deck)["inputs"] if i["argument"] == "figure_paths")
    assert figure_input["made_by"] is None


async def test_in_place_deck_append_is_flagged(server, fake_executor, root):
    pptx = pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    deck = root / "deck.pptx"
    pptx.Presentation().save(str(deck))
    await server.mcp.call_tool("qgis_figures_to_pptx", {
        "figure_paths": [str(png)], "pptx_path": str(deck), "template_pptx": str(deck)})
    record = _sidecar(deck)
    assert "input overwritten by this call: template_pptx" in record["unrecorded_state"]
    template = next(i for i in record["inputs"] if i["argument"] == "template_pptx")
    assert template["changed_during_call"] is True
```

Append to `tests/test_compound_mode.py`:

```python
async def test_compound_render_writes_a_sidecar(compound_module, fake_executor, tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    server_module, _ = compound_module
    png = tmp_path / "c.png"

    def respond(params):
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return {
            "output_path": str(png), "width": 800, "height": 600, "dpi": 150,
            "extent": [139.5, 35.5, 140.0, 35.9], "crs": "EPSG:4326", "n_layers": 1,
            "field": "fid", "n_classes": 5, "breaks": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "mode": "quantile", "min_value": 1.0, "max_value": 6.0,
            "n_features": 23, "n_matched": 23, "n_unmatched": 0,
        }

    fake_executor.responses["render_choropleth"] = respond
    await server_module.mcp.call_tool(
        "qgis_render",
        {"mode": "choropleth", "zones_path": "/zones.gpkg", "value_field": "fid", "output_png": str(png)},
    )
    record = json.loads((tmp_path / "c.png.provenance.json").read_text(encoding="utf-8"))
    assert record["call"]["tool"] == "qgis_render" and record["call"]["module"] == "compound"
    assert record["call"]["arguments"]["mode"] == "choropleth"
    zones = next(i for i in record["inputs"] if i["argument"] == "zones_path")
    assert zones["missing"] is True                                     # a path that does not exist
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_provenance.py tests/test_provenance_hook.py tests/test_compound_mode.py -q`
Expected: FAIL — `AttributeError: … has no attribute 'enabled'` / `_next_seq`, and the hook tests fail with `FileNotFoundError` on `fig.png.provenance.json`.

- [ ] **Step 4: Implement the decorator** — add to `provenance.py`:

```python
import functools
import inspect
import itertools
import uuid

from qgis_mcp_workflows.executors import executor_requests

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


def _made_by(path: str, digest: str | None) -> str | None:
    """The producer's sidecar, only if it vouches for exactly these bytes."""
    if digest is None:
        return None
    side = sidecar_path(path)
    try:
        with open(side, encoding="utf-8") as fh:
            producer = json.load(fh)
    except (OSError, ValueError):
        return None
    return portable(side)[0] if producer.get("figure_sha256") == digest else None


def _remote(args: dict, machine_specific: list[dict]) -> list[dict]:
    basemap = args.get("basemap")
    if not basemap:
        return []
    if str(basemap).startswith("qms:"):
        machine_specific.append({"path": str(basemap),
                                 "reason": "QuickMapServices source from the local QGIS profile"})
    return [{"argument": "basemap", "value": basemap, "note": "tiles are fetched live and not pinned"}]


def _record(tool: str, module: str, call_args: dict, before: dict, prints: list[dict],
            implicit: list[dict], seq: int, requests_before: int, result: Any, elapsed: float) -> None:
    figures = written_files(result)
    if not figures:
        return
    machine_specific: list[dict] = []
    for entry in prints + implicit:
        path = entry.pop("_path")
        entry["changed_during_call"] = snapshot(path) != before[path]
        entry["made_by"] = _made_by(path, entry["sha256"])
        _flag(machine_specific, path)
    unrecorded = [f"input overwritten by this call: {e['argument']}"
                  for e in prints if e["path"] in {portable(f)[0] for f in figures}]
    for figure in figures:
        _flag(machine_specific, figure)
    shared = {
        "session_id": SESSION_ID,
        "seq": seq,
        "created": _utc(time.time()),
        "call": {"tool": tool, "module": module, "arguments": _portable_arguments(call_args)},
        "result_summary": result_summary(result),
        "depends_on": [],
        "inputs": prints,
        "implicit_inputs": implicit,
        "remote": _remote(call_args, machine_specific),
        "unrecorded_state": unrecorded,
        "machine_specific": machine_specific,
        "outputs": [portable(f)[0] for f in figures],
        "environment": environment(executor_requests() > requests_before),
        "elapsed_s": round(elapsed, 3),
    }
    for figure in figures:
        fp = fingerprint(figure)
        write_sidecar(figure, {"schema": SCHEMA, "figure": fp["path"], "figure_sha256": fp["sha256"],
                               "figure_bytes": fp["bytes"], **shared})


def with_provenance(fn: Callable) -> Callable:
    """Wrap a tool so each successful MCP call leaves a sidecar per written file."""
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
            before = {path: snapshot(path) for _, path in explicit + hidden}
            prints = [{"argument": name, **fingerprint(path), "_path": path} for name, path in explicit]
            implicit = [{"argument": name, **fingerprint(path), "_path": path} for name, path in hidden]
            seq = _next_seq()
            requests_before = executor_requests()
        except Exception:
            logger.warning("provenance: could not prepare a record for %s", fn.__name__, exc_info=True)
            return fn(*args, **kwargs)
        started = time.monotonic()
        result = fn(*args, **kwargs)  # a failing call records nothing
        try:
            _record(fn.__name__, module, call_args, before, prints, implicit, seq,
                    requests_before, result, time.monotonic() - started)
        except Exception:
            logger.warning("provenance: recording failed for %s", fn.__name__, exc_info=True)
        return result

    return wrapper
```

- [ ] **Step 5: Apply it to registered copies** — in `src/qgis_mcp_workflows/server.py`, after line 44 (`from qgis_mcp_workflows.helpers import with_png_preview`) add:

```python
from qgis_mcp_workflows.provenance import with_provenance
```

and in both `_maybe_tool` and `_maybe_compound_tool` replace

```python
            mcp.tool(*args, **kwargs)(with_png_preview(f))
```

with

```python
            mcp.tool(*args, **kwargs)(with_png_preview(with_provenance(f)))
```

and extend `_maybe_tool`'s docstring: `Provenance sidecars likewise: only MCP calls are recorded.`

- [ ] **Step 6: Run the new tests, then the whole suite**

Run: `uv run --no-sync pytest tests/test_provenance.py tests/test_provenance_hook.py tests/test_compound_mode.py -q`
Expected: PASS

Run: `uv run --no-sync pytest tests/ -q`
Expected: everything passes except the known `test_add_vector_layer_returns_layer_id` (Windows file lock). In particular `tests/test_preview.py` and the compound preview test must still pass: the image preview and structured output are unchanged, a sidecar simply appears next to their PNG in `tmp_path`.

- [ ] **Step 7: Lint and commit**

Run: `uv tool run ruff check src/ tests/`
Expected: `All checks passed!`

```bash
git add src/qgis_mcp_workflows/provenance.py src/qgis_mcp_workflows/server.py tests/test_provenance.py tests/test_provenance_hook.py tests/test_compound_mode.py
git commit -m "feat(provenance): sidecar per file written by an MCP tool call"
```

---

### Task 8: install.py passes DROPBOX_ROOT to GUI clients

**Files:**
- Modify: `install.py` — `_server_entry` (~line 219) and the claude-code `.mcp.json` fallback in `_configure_cli_client` (~line 290)
- Test: `tests/test_install.py`

**Interfaces:**
- Produces: `_server_entry(client: str, remote: bool, pass_env: bool = True) -> dict`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_install.py`)

```python
def test_client_config_carries_dropbox_root(install_mod, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", "H:/Dropbox")
    install_mod.configure_client("claude-desktop", remote=False)
    cfg_path = install_mod._client_registry()["claude-desktop"]["path"]
    entry = json.loads(Path(cfg_path).read_text(encoding="utf-8"))["mcpServers"]["qgis-workflows"]
    assert entry["env"]["DROPBOX_ROOT"] == "H:/Dropbox"


def test_client_config_without_dropbox_root_has_no_env(install_mod, monkeypatch):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    assert "env" not in install_mod._server_entry("claude-desktop", remote=False)


def test_zed_entry_carries_dropbox_root(install_mod, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", "/Users/north/Dropbox")
    entry = install_mod._server_entry("zed", remote=False)
    assert entry["command"]["env"]["DROPBOX_ROOT"] == "/Users/north/Dropbox"


def test_tracked_mcp_json_never_gets_a_machine_path(install_mod, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", "H:/Dropbox")
    assert "env" not in install_mod._server_entry("cursor", remote=False, pass_env=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync pytest tests/test_install.py -q -k dropbox`
Expected: FAIL (`KeyError: 'env'` / `TypeError: … unexpected keyword argument 'pass_env'`)

- [ ] **Step 3: Implement** — in `install.py` replace `_server_entry` with:

```python
def _with_dropbox_root(entry: dict) -> dict:
    """GUI clients do not inherit the shell environment; pass DROPBOX_ROOT through.

    The provenance recorder stores paths under it so figures replay on every
    machine. CLI clients (claude, codex, grok) inherit it from their shell.
    """
    root = os.environ.get("DROPBOX_ROOT", "").strip()
    if not root:
        return entry
    holder = entry["command"] if isinstance(entry.get("command"), dict) else entry
    holder.setdefault("env", {})["DROPBOX_ROOT"] = root
    return entry


def _server_entry(client: str, remote: bool, pass_env: bool = True) -> dict:
    if client == "zed":
        entry = _zed_remote_entry() if remote else _zed_local_entry()
    else:
        entry = _remote_entry() if remote else _local_entry()
    return _with_dropbox_root(entry) if pass_env else entry
```

and in `_configure_cli_client`'s claude-code fallback change

```python
            config["mcpServers"][SERVER_NAME] = _server_entry("cursor", remote)
```

to

```python
            # .mcp.json is tracked: no machine-specific DROPBOX_ROOT in it.
            config["mcpServers"][SERVER_NAME] = _server_entry("cursor", remote, pass_env=False)
```

Confirm `import os` is already at the top of `install.py` (it is used elsewhere); add it if not.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_install.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install.py tests/test_install.py
git commit -m "feat(install): pass DROPBOX_ROOT into GUI MCP client configs"
```

---

### Task 9: Docs, ignore, task status

**Files:**
- Modify: `docs/DESIGN.md` (§5 Cross-cutting), `CLAUDE.md` (Environment Variables table), `CHANGELOG.md`, `.gitignore`, `backlog/tasks/task-12 - Figure-provenance-sidecar-core.md`

- [ ] **Step 1: DESIGN §5** — insert before `**Idempotency annotations.**`:

```markdown
**Provenance.** Every file a tool writes through MCP gets `<file>.provenance.json`: the call with every argument (defaults filled in), inputs fingerprinted before the call (sha256 up to `QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB`, default 256; size + mtime above it), the file's own sha256, `made_by` links to producer sidecars whose fingerprint matches, a result summary, and the environment. Paths under `$DROPBOX_ROOT` are stored as `${DROPBOX_ROOT}/…`; others are listed in `machine_specific`. Python callers (scripts, tests) are not recorded. `QGIS_MCP_WORKFLOWS_PROVENANCE=0` disables it. Spec: `docs/superpowers/specs/2026-10-08-figure-provenance-design.md`.
```

- [ ] **Step 2: CLAUDE.md env table** — add three rows after `QGIS_MCP_WORKFLOWS_LOG_LEVEL`:

```markdown
| `QGIS_MCP_WORKFLOWS_PROVENANCE` | `1` | `0` disables `<file>.provenance.json` sidecars (read per call) |
| `QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB` | `256` | Inputs above this get size + mtime instead of sha256 |
| `DROPBOX_ROOT` | (per machine) | Provenance stores paths under it as `${DROPBOX_ROOT}/…`; `install.py` passes it into GUI client configs |
```

- [ ] **Step 3: .gitignore** — append:

```gitignore
# Figure provenance sidecars hold machine paths and query/eval text; keep them local.
*.provenance.json
```

- [ ] **Step 4: CHANGELOG** — insert above the first `## Unreleased` heading:

```markdown
## Unreleased — figure provenance (sidecar core)

### Added

- Every file a tool writes through MCP gets `<file>.provenance.json`: the call
  with all arguments, inputs fingerprinted before the call (sha256 up to 256 MB),
  the file's own sha256 (a later overwrite shows as a stale sidecar), `made_by`
  links verified by fingerprint, a result summary and the environment (package,
  git commit, extras, QGIS/plugin version when the call used QGIS). Paths under
  `$DROPBOX_ROOT` are portable across machines. Disable with
  `QGIS_MCP_WORKFLOWS_PROVENANCE=0`. Replay (`qgis_export_session`) follows in
  TASK-13.
- `install.py` writes `DROPBOX_ROOT` into GUI MCP client configs (never into the
  tracked `.mcp.json`).
```

- [ ] **Step 5: Task file** — set `status: In Progress` and tick the acceptance criteria that the tests above prove (all six once Task 10 passes).

- [ ] **Step 6: Commit**

```bash
git add docs/DESIGN.md CLAUDE.md CHANGELOG.md .gitignore "backlog/tasks/task-12 - Figure-provenance-sidecar-core.md"
git commit -m "docs: figure provenance sidecars"
```

---

### Task 10: Verify, review, PR

- [ ] **Step 1: Verification loop** — invoke the `verification-loop` skill: `uv run --no-sync pytest tests/ -q`, `uv tool run ruff check src/ tests/`, secrets/scaffold/machine-path scans, diff review. Expected: only the known Windows file-lock failure.
- [ ] **Step 2: Live smoke (headless QGIS LTR, local only)** — with `DROPBOX_ROOT` set, call one real render through the MCP server (e.g. `qgis_render_choropleth` on `assets/zones_tokyo23.gpkg` via `server.mcp.call_tool` against `HeadlessExecutor`) and check the sidecar's `environment.qgis` is the real version and `figure_sha256` matches the PNG. Record the output in the PR.
- [ ] **Step 3: Fresh-context reviews** — `code-reviewer`, `python-reviewer` and `security-reviewer` (file writes, paths) on the branch diff; fix CRITICAL/HIGH; park MEDIUM/LOW in the PR body.
- [ ] **Step 4: PR** — push `task/task-12-provenance-core`, open the PR with `gh pr create --repo wattwong103/qgis-mcp-north --base main`, body = task, verification report, reviews, status line.

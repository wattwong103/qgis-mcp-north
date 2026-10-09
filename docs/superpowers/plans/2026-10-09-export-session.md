# qgis_export_session (TASK-13) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `qgis_export_session(output_py, figures=…|folder=…)` writes a standalone script that re-makes the chosen figures from source data, using the provenance sidecars TASK-12 writes.

**Architecture:** One new module `src/qgis_mcp_workflows/replay.py` with two halves. The **export** half reads and validates sidecars, follows `made_by` chains, orders the calls producer-first and writes the script — built as a Python AST from a fixed skeleton, never by formatting sidecar text into code. The **runtime** half (`replay.main` and helpers) is what the generated script imports: it resolves `${DROPBOX_ROOT}`, checks source inputs, remaps outputs into `--out-dir` and runs the steps through a headless executor. The MCP tool in `server.py` is a thin wrapper.

**Tech Stack:** Python 3.12 stdlib (`ast`, `argparse`, `inspect`), Pydantic v2, FastMCP; pytest with `FakeExecutor`; live test on headless QGIS LTR.

**Spec:** `docs/superpowers/specs/2026-10-08-figure-provenance-design.md` (v2) §7 and §11 "TASK-13" — minus the eval / `depends_on` parts, which land in TASK-15.

## Global Constraints

- Stacked on TASK-12 (`task/task-12-provenance-core`, PR #26); the PR targets `main` and shows only TASK-13's commits once #26 merges.
- Generated scripts are built with `ast` + `ast.unparse` from a fixed skeleton; sidecar values enter only as literal constants; tool names must be registered workflow tools of `server`/`compound`, argument keys must be parameters of that function.
- `${NAME}` expansion is an allow-list: `DROPBOX_ROOT` only; an expanded path must stay inside the root; unset root → "set DROPBOX_ROOT to this machine's Dropbox folder".
- Skipped reasons are fixed templates: `no sidecar`, `unknown schema`, `unknown tool`, `stale sidecar: figure changed`, `needs session state`, `conflicted copy`. Warnings strip control characters and cap echoed text at 200 characters. At most 200 sidecars, each ≤ 2 MB.
- Calls are identified by `(session_id, seq)`; producers before consumers, then `(session_id, seq)`.
- Script exit codes: `0` ok, `2` precondition failed (changed/missing inputs without `--force`, unset root, bad `--in-place`). (`3` is reserved for TASK-15's eval gate.)
- Replay runs headless only; no compound `qgis_export(kind="session")`; the tool registers in full mode only.
- `--in-place` refuses outputs that are recorded inputs, lists files it would overwrite and needs `--yes`, and cannot be combined with `--force`. Default `--out-dir` is `replay_<UTC date>/` beside the script; outputs map to `<out>/DROPBOX_ROOT/<rest>` or `<out>/_abs/<drive>/<rest>` and must resolve inside `<out>`.
- Until TASK-15, figures from state-reading calls (`qgis_render_map`, compound `qgis_render(mode="map")`) are skipped as `needs session state`; project-based figures (`export_layout`, `export_atlas`, `batch_render`) replay from the `.qgz` on disk with a note.
- Rulings carried from TASK-12: argument errors use `QgisMcpWorkflowsError` with a `Next:` hint (this branch predates #24's `InvalidArgumentError`); `include_evals` / `trust_foreign` / `n_evals` are added in TASK-15 where they mean something.
- Checklist: `uv run --no-sync pytest tests/ -q`, `uv tool run ruff check src/ tests/` (known pre-existing failure: `tests/test_headless_executor.py::test_add_vector_layer_returns_layer_id`).

## Review Focus

1. **Inputs at another machine's absolute path** (`machine_specific`): replay must report them missing (exit 2), and their outputs remap under `_abs/<drive>/`. → Task 2 test `test_check_inputs_reports_missing`, Task 3 test `test_out_dir_maps_absolute_paths_by_drive`.
2. **A figure deleted after recording** is not stale — it is exactly what replay re-makes. → Task 5 test `test_deleted_figure_is_still_replayable`.
3. **Directory outputs** (`output_dir` of batch/atlas) remap like files. → Task 7 test `test_output_dir_argument_is_remapped`.
4. **Dropbox conflicted-copy sidecars** in folder mode are skipped, not replayed twice. → Task 5 test `test_folder_mode_skips_conflicted_copies`.
5. **Several figures from one call** (atlas pages) requested together become one step. → Task 6 test `test_sibling_outputs_share_one_step`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/qgis_mcp_workflows/replay.py` (create) | Runtime (`resolve`, `check_inputs`, `output_mapper`, `main`) and export (`collect`, `plan_steps`, `build_script`, `export_session`) |
| `src/qgis_mcp_workflows/provenance.py` (modify) | `NOT_RECORDED_MODELS` — the replay script is not a figure |
| `src/qgis_mcp_workflows/server.py` (modify) | `SkippedFigure`, `SessionExportResult`, `qgis_export_session`, `SERVER_INSTRUCTIONS` line |
| `tests/test_replay.py` (create) | Runtime + export unit tests |
| `tests/test_session_export.py` (create) | Through-the-tool tests and the round trip |
| `tests/test_replay_live.py` (create) | Live headless replay |
| `tests/test_provenance.py` (modify) | Drift guard honours `NOT_RECORDED_MODELS` |
| `docs/DESIGN.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `backlog/tasks/task-13 - …md` (modify) | Tool counts (27 total / 26 workflow), §4 entry, tool row, changelog, task status |

---

### Task 1: `resolve` and `ReplayError`

**Files:** Create `src/qgis_mcp_workflows/replay.py`; create `tests/test_replay.py`.

**Interfaces:**
- Consumes: `provenance.ROOT_TOKEN`, `provenance.normalise`, `provenance._relative_under`.
- Produces: `class ReplayError(Exception)`; `resolve(path: str) -> str`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_replay.py`:

```python
"""Replay runtime and export (TASK-13). No QGIS needed."""

from __future__ import annotations

import pytest

from qgis_mcp_workflows import provenance, replay


def test_resolve_expands_dropbox_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    assert replay.resolve("${DROPBOX_ROOT}/gufm/z.gpkg") == provenance.normalise(str(tmp_path / "gufm" / "z.gpkg"))


def test_resolve_leaves_other_paths_alone():
    assert replay.resolve("H:/elsewhere/x.csv") == "H:/elsewhere/x.csv"
    assert replay.resolve("${HOME}/x.csv") == "${HOME}/x.csv"   # allow-list: DROPBOX_ROOT only


def test_resolve_needs_the_root(monkeypatch):
    monkeypatch.delenv("DROPBOX_ROOT", raising=False)
    with pytest.raises(replay.ReplayError, match="set DROPBOX_ROOT"):
        replay.resolve("${DROPBOX_ROOT}/x.csv")


def test_resolve_refuses_to_leave_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "root"))
    with pytest.raises(replay.ReplayError, match="leaves DROPBOX_ROOT"):
        replay.resolve("${DROPBOX_ROOT}/../../etc/passwd")
```

- [ ] **Step 2: Run to verify failure** — `uv run --no-sync pytest tests/test_replay.py -q` → FAIL: `ImportError: cannot import name 'replay'`.

- [ ] **Step 3: Implement** — create `src/qgis_mcp_workflows/replay.py`:

```python
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

import os

from qgis_mcp_workflows.provenance import ROOT_TOKEN, _relative_under, normalise


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
```

- [ ] **Step 4: Run tests** — PASS (4 passed).
- [ ] **Step 5: Commit** — `git add src/qgis_mcp_workflows/replay.py tests/test_replay.py && git commit -m "feat(replay): resolve DROPBOX_ROOT paths with an allow-list"`

---

### Task 2: `check_inputs`

**Files:** Modify `replay.py`, `tests/test_replay.py`.

**Interfaces:**
- Consumes: `resolve` (Task 1), `provenance.fingerprint`.
- Produces: `check_inputs(inputs: list[dict]) -> list[str]` — one human-readable line per missing/changed source input; each input dict has `path` (portable), `sha256` (str|None), `bytes` (int|None).

- [ ] **Step 1: Failing tests**

```python
import hashlib


def _entry(path, data):
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def test_check_inputs_accepts_unchanged(tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abc")
    assert replay.check_inputs([_entry(f, b"abc")]) == []


def test_check_inputs_reports_changed_content(tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"abd")
    [line] = replay.check_inputs([_entry(f, b"abc")])
    assert line.startswith("changed") and "sha256" in line


def test_check_inputs_compares_size_when_unhashed(tmp_path):
    f = tmp_path / "big.duckdb"
    f.write_bytes(b"0123")
    [line] = replay.check_inputs([{"path": str(f), "sha256": None, "bytes": 9}])
    assert "size 9 -> 4" in line


def test_check_inputs_reports_missing(tmp_path):
    [line] = replay.check_inputs([{"path": "H:/other-machine/x.csv", "sha256": None, "bytes": 3}])
    assert line.startswith("missing")
```

- [ ] **Step 2: Verify failure** — FAIL: `AttributeError: … no attribute 'check_inputs'`.
- [ ] **Step 3: Implement** — add to `replay.py` (import `fingerprint` from provenance):

```python
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
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `feat(replay): check source inputs against their recording`

---

### Task 3: `output_mapper` (out-dir and in-place)

**Files:** Modify `replay.py`, `tests/test_replay.py`.

**Interfaces:**
- Consumes: `resolve`, `normalise`.
- Produces: `output_mapper(outputs: list[str], sources: list[str], out_dir: str, in_place: bool, yes: bool) -> Callable[[str], str]`.

- [ ] **Step 1: Failing tests**

```python
def test_out_dir_maps_portable_paths(monkeypatch, tmp_path):
    out = replay.output_mapper(["${DROPBOX_ROOT}/gufm/fig.png"], [], str(tmp_path / "out"), False, False)
    target = out("${DROPBOX_ROOT}/gufm/fig.png")
    assert target == str((tmp_path / "out" / "DROPBOX_ROOT" / "gufm" / "fig.png").resolve())
    assert (tmp_path / "out" / "DROPBOX_ROOT" / "gufm").is_dir()   # parent created


def test_out_dir_maps_absolute_paths_by_drive(tmp_path):
    out = replay.output_mapper(["C:/tmp/a.png"], [], str(tmp_path / "out"), False, False)
    assert out("C:/tmp/a.png") == str((tmp_path / "out" / "_abs" / "C" / "tmp" / "a.png").resolve())


def test_out_dir_refuses_escape(tmp_path):
    out = replay.output_mapper([], [], str(tmp_path / "out"), False, False)
    with pytest.raises(replay.ReplayError, match="leaves --out-dir"):
        out("${DROPBOX_ROOT}/../../../x.png")


def test_in_place_refuses_overwriting_an_input(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    with pytest.raises(replay.ReplayError, match="recorded inputs"):
        replay.output_mapper(["${DROPBOX_ROOT}/a.csv"], ["${DROPBOX_ROOT}/a.csv"], "", True, True)


def test_in_place_needs_yes_to_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    (tmp_path / "fig.png").write_bytes(b"old")
    with pytest.raises(replay.ReplayError, match="--yes"):
        replay.output_mapper(["${DROPBOX_ROOT}/fig.png"], [], "", True, False)
    out = replay.output_mapper(["${DROPBOX_ROOT}/fig.png"], [], "", True, True)
    assert out("${DROPBOX_ROOT}/fig.png") == provenance.normalise(str(tmp_path / "fig.png"))
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — add to `replay.py` (imports `from collections.abc import Callable`, `from pathlib import Path`):

```python
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
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `feat(replay): remap outputs into --out-dir; guarded --in-place`

---

### Task 4: `replay.main` (the script's entry point)

**Files:** Modify `replay.py`, `tests/test_replay.py`.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `main(script: str, inputs: list[dict], outputs: list[str], notes: list[str], steps: Callable[[Callable, Callable], None], argv: list[str] | None = None) -> int`; `_new_executor()` (monkeypatchable factory, returns a `HeadlessExecutor`).

- [ ] **Step 1: Failing tests**

```python
def _run_main(tmp_path, monkeypatch, inputs=(), argv=(), steps=None):
    ran = []
    monkeypatch.setattr(replay, "_new_executor", lambda: type("E", (), {"shutdown": lambda self: ran.append("shutdown")})())
    code = replay.main(str(tmp_path / "replay.py"), list(inputs), ["${DROPBOX_ROOT}/fig.png"], ["a note"],
                       steps or (lambda out, src: ran.append(out("${DROPBOX_ROOT}/fig.png"))), list(argv))
    return code, ran


def test_main_runs_steps_into_out_dir_and_shuts_down(monkeypatch, tmp_path, capsys):
    code, ran = _run_main(tmp_path, monkeypatch, argv=["--out-dir", str(tmp_path / "o")])
    assert code == 0
    assert ran[0].endswith(str(Path("o") / "DROPBOX_ROOT" / "fig.png")) and ran[-1] == "shutdown"
    assert "note: a note" in capsys.readouterr().out


def test_main_stops_on_changed_inputs_unless_forced(monkeypatch, tmp_path):
    f = tmp_path / "z.csv"
    f.write_bytes(b"new")
    stale = [{"path": str(f), "sha256": hashlib.sha256(b"old").hexdigest(), "bytes": 3}]
    assert _run_main(tmp_path, monkeypatch, stale, ["--out-dir", str(tmp_path / "o")])[0] == 2
    assert _run_main(tmp_path, monkeypatch, stale, ["--out-dir", str(tmp_path / "o"), "--force"])[0] == 0


def test_main_refuses_in_place_with_force(monkeypatch, tmp_path, capsys):
    code, ran = _run_main(tmp_path, monkeypatch, argv=["--in-place", "--force"])
    assert code == 2 and ran == []
    assert "cannot be combined" in capsys.readouterr().err
```

(Add `from pathlib import Path` to the test imports.)

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — add to `replay.py` (imports `argparse`, `datetime as _dt`, `sys`):

```python
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
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `feat(replay): script entry point with input check and exit codes`

---

### Task 5: Collect and validate sidecars

**Files:** Modify `replay.py`, `tests/test_replay.py`.

**Interfaces:**
- Consumes: `provenance.SCHEMA`, `SIDECAR_SUFFIX`, `sidecar_path`, `_regular_stat`, `fingerprint`; `resolve`.
- Produces: `collect(figures: list[str] | None, folder: str | None) -> tuple[list[dict], list[dict], list[str]]` — `(records, skipped, warnings)`; `skipped` items `{"figure": str, "reason": str}`; `_tool_function(record) -> Callable | None`; constants `MAX_SIDECARS = 200`, `_SIDECAR_MAX_BYTES`.

- [ ] **Step 1: Failing tests** — a helper writes sidecars shaped like TASK-12's:

```python
import json


def _sidecar(tmp_path, name, *, tool="qgis_render_choropleth", module="server", args=None,
             data=b"png", seq=1, inputs=(), schema=None, session="s1"):
    fig = tmp_path / name
    fig.write_bytes(data)
    record = {
        "schema": schema or provenance.SCHEMA, "figure": str(fig),
        "figure_sha256": hashlib.sha256(data).hexdigest(), "figure_bytes": len(data),
        "session_id": session, "seq": seq,
        "call": {"tool": tool, "module": module,
                 "arguments": args if args is not None else {"zones_path": "/z.gpkg", "value_field": "n",
                                                             "output_png": str(fig)}},
        "inputs": list(inputs), "implicit_inputs": [], "outputs": [str(fig)],
        "unrecorded_state": [], "machine_specific": [], "remote": [],
    }
    (tmp_path / f"{name}.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    return fig


def _reasons(skipped):
    return sorted(s["reason"] for s in skipped)


def test_collect_reads_named_figures(tmp_path):
    fig = _sidecar(tmp_path, "a.png")
    records, skipped, _ = replay.collect([str(fig)], None)
    assert len(records) == 1 and skipped == []


def test_collect_skips_with_fixed_reasons(tmp_path):
    ok = _sidecar(tmp_path, "ok.png")
    hostile = _sidecar(tmp_path, "h.png", tool="os.system")
    badkey = _sidecar(tmp_path, "k.png", args={"zones_path": "/z", "value_field": "n", "output_png": "x", "__import__": 1})
    unknown = _sidecar(tmp_path, "u.png", schema="other@9")
    state = _sidecar(tmp_path, "m.png", tool="qgis_render_map", args={"layer_ids": ["L1"], "output_png": "x"})
    stale = _sidecar(tmp_path, "s.png")
    stale.write_bytes(b"changed by a script")
    nofile = tmp_path / "none.png"
    records, skipped, _ = replay.collect([str(p) for p in (ok, hostile, badkey, unknown, state, stale, nofile)], None)
    assert len(records) == 1
    assert _reasons(skipped) == sorted(["unknown tool", "unknown tool", "unknown schema",
                                        "needs session state", "stale sidecar: figure changed", "no sidecar"])


def test_deleted_figure_is_still_replayable(tmp_path):
    fig = _sidecar(tmp_path, "gone.png")
    fig.unlink()
    records, skipped, _ = replay.collect([str(fig) + ".provenance.json"], None)
    assert len(records) == 1 and skipped == []


def test_folder_mode_skips_conflicted_copies(tmp_path):
    _sidecar(tmp_path, "a.png")
    (tmp_path / "a (North's conflicted copy).png.provenance.json").write_text("{}", encoding="utf-8")
    records, skipped, _ = replay.collect(None, str(tmp_path))
    assert len(records) == 1 and _reasons(skipped) == ["conflicted copy"]


def test_collect_follows_made_by_to_producers(tmp_path):
    producer = _sidecar(tmp_path, "route.csv", tool="qgis_route_on_network",
                        args={"input_csv": "/stops.csv", "network_path": "/net.tsv", "output_csv": str(tmp_path / "route.csv")})
    consumer = _sidecar(tmp_path, "fig.png", seq=2, inputs=[{
        "argument": "zones_path", "path": str(producer), "sha256": hashlib.sha256(b"png").hexdigest(),
        "bytes": 3, "made_by": str(producer) + ".provenance.json"}])
    records, _, _ = replay.collect([str(consumer)], None)
    assert {r["call"]["tool"] for r in records} == {"qgis_route_on_network", "qgis_render_choropleth"}


def test_collect_rejects_oversized_or_non_object_sidecars(tmp_path):
    fig = tmp_path / "x.png"
    fig.write_bytes(b"png")
    (tmp_path / "x.png.provenance.json").write_text("[]", encoding="utf-8")
    _, skipped, _ = replay.collect([str(fig)], None)
    assert _reasons(skipped) == ["no sidecar"]
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — add to `replay.py` (imports `inspect`, `json`, `re`; from provenance add `SCHEMA`, `SIDECAR_SUFFIX`, `_regular_stat`, `sidecar_path`):

```python
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
    if call["tool"] in _STATE_READING_TOOLS or (call["tool"] == "qgis_render" and call["arguments"].get("mode") == "map"):
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
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `feat(replay): collect and validate sidecars, follow made_by`

---

### Task 6: Order steps; assemble inputs, outputs and notes

**Files:** Modify `replay.py`, `tests/test_replay.py`.

**Interfaces:**
- Consumes: records from `collect`.
- Produces: `plan_steps(records: list[dict]) -> list[dict]`; `source_inputs(steps: list[dict]) -> list[dict]`; `outputs_of(steps: list[dict]) -> list[str]`; `notes_for(steps: list[dict]) -> list[str]`; `_clean(text: str) -> str`.

- [ ] **Step 1: Failing tests**

```python
def _record(seq, outputs, inputs=(), tool="qgis_render_choropleth", session="s1", **call):
    return {"session_id": session, "seq": seq, "outputs": list(outputs), "inputs": list(inputs),
            "implicit_inputs": [], "unrecorded_state": [], "machine_specific": [], "remote": [],
            "call": {"tool": tool, "module": "server", "arguments": call}}


def test_sibling_outputs_share_one_step():
    pages = [_record(5, ["a.png", "b.png"]), _record(5, ["a.png", "b.png"])]
    assert len(replay.plan_steps(pages)) == 1


def test_identical_calls_stay_two_steps():
    assert len(replay.plan_steps([_record(1, ["x.png"]), _record(2, ["x.png"])])) == 2


def test_producers_come_first():
    consumer = _record(1, ["fig.png"], inputs=[{"path": "route.csv", "sha256": "h", "bytes": 1}])
    producer = _record(9, ["route.csv"], tool="qgis_route_on_network")
    assert [s["seq"] for s in replay.plan_steps([consumer, producer])] == [9, 1]


def test_source_inputs_exclude_intermediates():
    steps = replay.plan_steps([
        _record(1, ["route.csv"], inputs=[{"path": "stops.csv", "sha256": "a", "bytes": 1}]),
        _record(2, ["fig.png"], inputs=[{"path": "route.csv", "sha256": "b", "bytes": 1}]),
    ])
    assert [i["path"] for i in replay.source_inputs(steps)] == ["stops.csv"]
    assert replay.outputs_of(steps) == ["route.csv", "fig.png"]


def test_notes_are_cleaned_and_capped():
    step = _record(1, ["x.png"])
    step["unrecorded_state"] = ['bad\n"""\x1b' + "y" * 500]
    [note] = [n for n in replay.notes_for([step]) if n.startswith("step 1")]
    assert "\n" not in note and "\x1b" not in note and len(note) <= 260
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — add to `replay.py`:

```python
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
```

(Add `from typing import Any` to the imports.)

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `feat(replay): order steps producer-first; inputs, outputs, notes`

---

### Task 7: Build the script as an AST

**Files:** Modify `replay.py`, `tests/test_replay.py`.

**Interfaces:**
- Consumes: Tasks 5–6; `provenance.INPUT_ARGS`, `INPUT_LIST_ARGS`, `OUTPUT_ARGS`.
- Produces: `build_script(steps: list[dict], inputs: list[dict], outputs: list[str], notes: list[str], generated_on: str) -> str`.

- [ ] **Step 1: Failing tests**

```python
import ast


def _choropleth(seq=1, out="${DROPBOX_ROOT}/fig.png", zones="${DROPBOX_ROOT}/z.gpkg"):
    return _record(seq, [out], zones_path=zones, value_field="n", output_png=out, mode="quantile")


def test_build_script_compiles_and_calls_the_tool():
    step = _choropleth()
    source = replay.build_script([step], [{"path": "${DROPBOX_ROOT}/z.gpkg", "sha256": "h", "bytes": 1}],
                                 ["${DROPBOX_ROOT}/fig.png"], ["n"], "2026-10-09")
    compile(source, "replay.py", "exec")
    assert "server.qgis_render_choropleth(" in source
    assert "zones_path=src('${DROPBOX_ROOT}/z.gpkg')" in source
    assert "output_png=out('${DROPBOX_ROOT}/fig.png')" in source


def test_intermediate_inputs_read_from_the_replayed_output():
    producer = _record(1, ["${DROPBOX_ROOT}/route.csv"], tool="qgis_route_on_network",
                       input_csv="/stops.csv", network_path="/net.tsv", output_csv="${DROPBOX_ROOT}/route.csv")
    consumer = _record(2, ["${DROPBOX_ROOT}/t.png"], tool="qgis_render_trajectory",
                       input_path="${DROPBOX_ROOT}/route.csv", output_png="${DROPBOX_ROOT}/t.png")
    source = replay.build_script([producer, consumer], [], replay.outputs_of([producer, consumer]), [], "d")
    assert "input_path=out('${DROPBOX_ROOT}/route.csv')" in source


def test_output_dir_argument_is_remapped():
    step = _record(1, ["${DROPBOX_ROOT}/b/x.png"], tool="qgis_batch_render", template_qgz="/t.qgz",
                   attribute="name", values=["x"], output_dir="${DROPBOX_ROOT}/b")
    assert "output_dir=out('${DROPBOX_ROOT}/b')" in replay.build_script([step], [], ["${DROPBOX_ROOT}/b/x.png"], [], "d")


def test_hostile_values_stay_literal():
    step = _choropleth()
    step["call"]["arguments"]["value_field"] = '"); import os; os.system("calc'
    source = replay.build_script([step], [], ["${DROPBOX_ROOT}/fig.png"], ['"""\nimport os'], "d")
    tree = ast.parse(source)
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert [ast.unparse(n) for n in imports] == ["from qgis_mcp_workflows import compound, replay, server"]
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — add to `replay.py` (import `ast`; from provenance `INPUT_ARGS`, `INPUT_LIST_ARGS`, `OUTPUT_ARGS`):

```python
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
```

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** — `feat(replay): build the replay script as an AST`

---

### Task 8: `export_session` and the MCP tool (+ round trip)

**Files:** Modify `replay.py`, `provenance.py`, `server.py`, `tests/test_provenance.py`; create `tests/test_session_export.py`.

**Interfaces:**
- Produces: `replay.export_session(output_py: str, figures: list[str] | None = None, folder: str | None = None, overwrite: bool = False) -> dict` (keys `output_path`, `n_figures`, `n_calls`, `skipped`, `warnings`); `provenance.NOT_RECORDED_MODELS`; `server.SkippedFigure`, `server.SessionExportResult`, `server.qgis_export_session(...)`.

- [ ] **Step 1: Failing tests** — create `tests/test_session_export.py`:

```python
"""qgis_export_session through the tool, and the replay round trip. No QGIS needed."""

from __future__ import annotations

import importlib
import runpy
import struct
import sys
import zlib
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance, replay
from qgis_mcp_workflows.errors import QgisMcpWorkflowsError


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


PNG = _png()


@pytest.fixture
def server():
    return importlib.import_module("qgis_mcp_workflows.server")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PROVENANCE", raising=False)
    provenance.reset_for_tests()
    return tmp_path


def _choropleth(params):
    Path(params["output_png"]).write_bytes(PNG)
    return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
            "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1, "field": "n", "n_classes": 5,
            "breaks": [1.0, 2.0], "mode": "quantile", "min_value": 1.0, "max_value": 2.0,
            "n_features": 2, "n_matched": 2, "n_unmatched": 0}


async def _record_session(server, fake_executor, root):
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth
    zones = root / "zones.gpkg"
    zones.write_bytes(b"zones")
    await server.mcp.call_tool("qgis_render_choropleth",
                               {"zones_path": str(zones), "value_field": "n", "output_png": str(root / "fig.png")})
    await server.mcp.call_tool("qgis_figures_to_pptx",
                               {"figure_paths": [str(root / "fig.png")], "pptx_path": str(root / "deck.pptx")})
    return [(c, p) for c, p in fake_executor.calls if c != "diagnose"]


async def test_export_follows_the_chain_and_writes_a_script(server, fake_executor, root):
    await _record_session(server, fake_executor, root)
    result = server.qgis_export_session(output_py=str(root / "replay.py"), figures=[str(root / "deck.pptx")])
    assert (result.n_calls, result.n_figures, result.skipped) == (2, 2, [])
    compile((root / "replay.py").read_text(encoding="utf-8"), "replay.py", "exec")


async def test_round_trip_dispatches_the_same_calls(server, fake_executor, root, monkeypatch):
    original = await _record_session(server, fake_executor, root)
    script = root / "replay.py"
    server.qgis_export_session(output_py=str(script), figures=[str(root / "deck.pptx")])
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    monkeypatch.setattr(sys, "argv", [str(script), "--out-dir", str(root / "out")])
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(script), run_name="__main__")
    assert done.value.code == 0
    remapped = str((root / "out" / "DROPBOX_ROOT" / "fig.png").resolve())
    expected = [(c, {**p, "output_png": remapped}) for c, p in original]
    assert [(c, p) for c, p in fake_executor.calls if c != "diagnose"] == expected
    assert (root / "out" / "DROPBOX_ROOT" / "deck.pptx").is_file()   # pure-Python step ran for real


def test_export_needs_exactly_one_source(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match="exactly one of figures or folder"):
        server.qgis_export_session(output_py=str(root / "r.py"))


def test_export_refuses_an_existing_script(server, root):
    (root / "r.py").write_text("# mine", encoding="utf-8")
    with pytest.raises(QgisMcpWorkflowsError, match="overwrite=True"):
        server.qgis_export_session(output_py=str(root / "r.py"), folder=str(root))


def test_export_needs_a_py_file(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match=r"\.py"):
        server.qgis_export_session(output_py=str(root / "r.txt"), folder=str(root))


async def test_export_session_writes_no_sidecar_of_its_own(server, fake_executor, root):
    await _record_session(server, fake_executor, root)
    await server.mcp.call_tool("qgis_export_session",
                               {"output_py": str(root / "replay.py"), "figures": [str(root / "deck.pptx")]})
    assert not (root / "replay.py.provenance.json").exists()
```

In `tests/test_provenance.py`, change the drift guard's condition so exempt models pass — replace

```python
        if path_fields & set(cls.model_fields)
        and not any(c.__name__ in provenance.WRITTEN_BY_MODEL for c in cls.__mro__)
```

with

```python
        if path_fields & set(cls.model_fields)
        and cls.__name__ not in provenance.NOT_RECORDED_MODELS
        and not any(c.__name__ in provenance.WRITTEN_BY_MODEL for c in cls.__mro__)
```

- [ ] **Step 2: Verify failure** — `AttributeError: module 'qgis_mcp_workflows.server' has no attribute 'qgis_export_session'`.

- [ ] **Step 3: Implement**

`provenance.py`, after `WRITTEN_BY_MODEL`:

```python
# Results that name a file which is not a figure: the replay script itself.
NOT_RECORDED_MODELS = frozenset({"SessionExportResult"})
```

`replay.py`:

```python
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
```

`server.py` — models next to `PptxResult`:

```python
class SkippedFigure(BaseModel):
    figure: str
    reason: str


class SessionExportResult(BaseModel):
    output_path: str
    n_figures: int
    n_calls: int
    skipped: list[SkippedFigure]
    warnings: list[str]
```

and the tool after `qgis_figures_to_pptx`:

```python
@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=False
    )
)
def qgis_export_session(
    output_py: Annotated[str, Field(description="Absolute path for the replay script (.py). Refused if it exists unless overwrite=True.")],
    figures: Annotated[list[str] | None, Field(description="Figure files (or their .provenance.json sidecars) to replay. Exactly one of figures / folder.")] = None,
    folder: Annotated[str | None, Field(description="Replay every figure with a sidecar in this folder (not recursive).")] = None,
    overwrite: Annotated[bool, Field(description="Replace an existing output_py.")] = False,
) -> SessionExportResult:
    """Write a script that re-makes figures from their provenance sidecars. Delivery tool.

    When to use: to make paper or deck figures reproducible, or to re-make them
    after source data changed. Follows made_by chains back to source data.
    The script checks source inputs (exit 2 if they changed, unless --force),
    writes to replay_<date>/ beside itself unless --in-place, and resolves
    ${DROPBOX_ROOT} on whichever machine runs it.

    Returns: ``SessionExportResult`` — script path, files and calls replayed,
    and skipped figures with fixed reasons (no sidecar, stale sidecar, unknown
    tool, needs session state, ...).
    """
    from qgis_mcp_workflows.replay import export_session

    return SessionExportResult(**export_session(output_py, figures=figures, folder=folder, overwrite=overwrite))
```

- [ ] **Step 4: Run** — `uv run --no-sync pytest tests/test_session_export.py tests/test_replay.py tests/test_provenance.py -q` → PASS. Then the full suite: the docs-consistency tests fail on the counts until Task 10 — that is expected here and fixed there; everything else passes.
- [ ] **Step 5: Commit** — `feat: qgis_export_session writes a replay script from sidecars`

---

### Task 9: Live headless replay

**Files:** Create `tests/test_replay_live.py`.

- [ ] **Step 1: Write the test**

```python
"""Record a real headless render, export it, replay it in a fresh process."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest

from qgis_mcp_workflows import executors, provenance
from qgis_mcp_workflows.executors.headless import HeadlessExecutor
from tests.conftest import requires_headless

pytestmark = requires_headless

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "tiny_trajectory.csv")


async def test_trajectory_replays_to_the_same_size(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    provenance.reset_for_tests()
    server = importlib.import_module("qgis_mcp_workflows.server")
    data = tmp_path / "traj.csv"
    data.write_bytes(open(FIXTURE, "rb").read())
    executor = HeadlessExecutor()
    executors.set_executor(executor)
    try:
        await server.mcp.call_tool("qgis_render_trajectory",
                                   {"input_path": str(data), "output_png": str(tmp_path / "t.png")})
    finally:
        executor.shutdown()
        executors.set_executor(None)
    script = tmp_path / "replay.py"
    server.qgis_export_session(output_py=str(script), figures=[str(tmp_path / "t.png")])
    done = subprocess.run([sys.executable, str(script), "--out-dir", str(tmp_path / "out")],
                          capture_output=True, text=True, timeout=300, env={**os.environ})
    assert done.returncode == 0, done.stdout + done.stderr
    replayed = tmp_path / "out" / "DROPBOX_ROOT" / "t.png"
    assert replayed.stat().st_size == (tmp_path / "t.png").stat().st_size
```

- [ ] **Step 2: Run** — `uv run --no-sync pytest tests/test_replay_live.py -q` → PASS on a machine with QGIS LTR (skips elsewhere). If sizes differ only because the renderer is not byte-deterministic, rule it in the ledger and compare image dimensions instead (read the PNG IHDR: bytes 16–24).
- [ ] **Step 3: Commit** — `test(replay): live headless record → export → replay`

---

### Task 10: Docs, counts, instructions, task status

**Files:** `docs/DESIGN.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `src/qgis_mcp_workflows/server.py` (`SERVER_INSTRUCTIONS`), `backlog/tasks/task-13 - qgis-export-session-replay-script-from-provenance-sidecars.md`.

- [ ] **Step 1: Counts** — 27 tools (26 workflow + `qgis_eval`):
  - `CLAUDE.md`: `- 26 workflow tools + 1 escape hatch` and `## MCP Tools (27 total as of v1.14; …`.
  - `README.md`: `## Tools (27 standalone — 26 workflow + \`qgis_eval\`; 5 grouped in compound mode)`.
  - `docs/DESIGN.md`: `**26 workflow tools + 1 escape hatch` and `## 4. Tool surface (26 workflow + 1 escape hatch)`.
  Run `uv run --no-sync pytest tests/test_docs_consistency.py -q` → PASS.
- [ ] **Step 2: DESIGN §4 entry** after `qgis_figures_to_pptx`:

```markdown
#### `qgis_export_session(output_py: str, figures: list[str] | None = None, folder: str | None = None, overwrite: bool = False) → SessionExportResult`

**Delivery tool.** Writes a standalone replay script from provenance sidecars (spec `docs/superpowers/specs/2026-10-08-figure-provenance-design.md` §7): follows `made_by` chains to source data, one step per recorded call (producers first), source inputs checked before anything runs (exit 2 unless `--force`), outputs under `replay_<date>/` beside the script unless `--in-place --yes`. Built as an AST; tool and argument names must be real. Skipped figures carry fixed reasons. Until TASK-15, `qgis_render_map` figures are skipped (`needs session state`). Full mode only.

```python
SessionExportResult = {"output_path": str, "n_figures": int, "n_calls": int,
                       "skipped": [{"figure": str, "reason": str}], "warnings": [str]}
```
```

- [ ] **Step 3: CLAUDE.md tool row** after `qgis_figures_to_pptx`:

```markdown
| `qgis_export_session` | ✅ v1.15 | Replay script from provenance sidecars (AST-built; input check; `--out-dir`/`--in-place`); follows `made_by` chains; full mode only |
```

- [ ] **Step 4: README** — add the tool to its tool list next to `qgis_figures_to_pptx` (one line, same style as its neighbours).
- [ ] **Step 5: SERVER_INSTRUCTIONS** — after the `qgis_figures_to_pptx` line add `- qgis_export_session to write a replay script for figures that have .provenance.json sidecars.`
- [ ] **Step 6: CHANGELOG** — under the TASK-12 provenance block's heading, add an `### Added` bullet: `qgis_export_session(output_py, figures=…|folder=…)` writes an AST-built replay script from provenance sidecars …(one paragraph).
- [ ] **Step 7: Task file** — `status: In Progress`, tick the acceptance criteria the tests prove.
- [ ] **Step 8: Full suite + ruff, commit** — `docs: qgis_export_session`

---

### Task 11: Verify, review, PR

- [ ] **Step 1:** `verification-loop`: full suite, ruff, scans.
- [ ] **Step 2:** Fresh-context reviews on the most capable model: `code-reviewer` (whole branch from the TASK-12 tip), `python-reviewer`, `security-reviewer` (generated code, path handling). One fix pass for CRITICAL/IMPORTANT, each fix red-first.
- [ ] **Step 3:** Push `task/task-13-export-session`; `gh pr create --repo wattwong103/qgis-mcp-north --base main` — body states it is stacked on #26 (merge #26 first), verification report, reviews, status line.

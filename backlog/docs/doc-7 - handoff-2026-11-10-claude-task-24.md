---
id: doc-7
title: handoff-2026-11-10-claude-task-24
type: other
created_date: '2026-11-10 21:00'
---


## Goal
TASK-24, the MEDIUM parked from #38's review: in `qgis_compose_panels`, CSV rows whose key no zone carries (a total row, another zone system) set every colour scale without being drawn. North asked for this to start once #38 had merged (it merged 2026-11-09 20:44 UTC as 339034d), and for the design to be confirmed first because it adds a plugin command.

## Done
- Branch `task/task-24-panel-csv-keys`, worktree `.claude/worktrees/zen-brahmagupta-dc2e5f`, 3 commits on 339034d: tests (RED), fix plus docs and version 1.16.2, review round.
- Plugin: `_open_keyed_layer` and `_join_keys` (NULL as `""` via `_is_null`) are shared by `compose_panels` and the new command `get_field_values(path, field)`.
- Server:
  - The CSV is read first.
  - The tool then asks for the layer's keys, drops CSV keys outside them before `group_scales`, and refuses a CSV with no key on the layer (`JoinError`) or with no number for any on-layer zone (`InvalidArgumentError`).
  - The result reports `n_csv_keys_unmatched` and `csv_keys_unmatched_sample` (up to 5).
  - An old plugin's "Unknown command type" becomes a restart hint.
- Numbering: a parallel session (layout unit enums through compat) took TASK-23 / v1.16.1 / doc-6 and opened #39. Agreed by message: this work is TASK-24 / v1.16.2 / doc-7. Whichever PR merges second resolves the version lines, CHANGELOG, the metadata.txt changelog and uv.lock.
- Reviews (fresh context): code-reviewer APPROVE, python-reviewer APPROVE, security-reviewer no new exposure. No CRITICAL or HIGH. Fixed in one pass, the new behaviour test-first: the all-blank refusal, single attribute read, `from err`, a test pinning the plugin's "Unknown command type" wording, CHANGELOG "Fixed" line for NULL keys.
- Codex (cross-harness): the first run could not execute any command (`helper_unknown_error: setup refresh had errors` in its read-only sandbox on this PC) and returned REQUEST_CHANGES as "incomplete verification, not an identified defect". It was rerun with the full diff and context on stdin (`codex exec -s read-only`, gpt-6.1-sol) and returned **APPROVE**: one MEDIUM, one LOW, both parked below.

## Evidence
- RED before any production edit: 10 failed, 43 passed (`tests/test_compose_panels.py`, `tests/test_panels_live.py`). Live: a TOTAL row set vmax and data_max to 1.0 instead of 0.4.
- Final: `uv run --no-sync pytest tests/ -q`: 750 passed, 3 skipped, 1 failed (the known Windows file lock in `test_headless_executor.py::test_add_vector_layer_returns_layer_id`, same on main per doc-2 and doc-5). `uv tool run ruff@0.16.5 check src/ tests/`: clean. Plugin parses with `feature_version=(3, 9)`.
- Live tests ran on this PC's QGIS LTR (QGIS 3, PyQt5). QGIS 4 not run.
- The worktree had no `.venv`; rebuilt with `uv sync --frozen --offline --all-extras`.

## Files changed
- `qgis_mcp_workflows_plugin/plugin.py`, `qgis_mcp_workflows_plugin/metadata.txt`
- `src/qgis_mcp_workflows/server.py`
- `tests/test_compose_panels.py`, `tests/test_panels_live.py`
- `CHANGELOG.md`, `CLAUDE.md`, `docs/DESIGN.md`, `pyproject.toml`, `uv.lock`
- `backlog/tasks/task-24 - Panel-scales-ignore-CSV-keys-the-layer-lacks.md`, this doc

## Verify next
- On the Mac's QGIS 4: `uv run --no-sync pytest tests/test_panels_live.py -q` (the NULL-key test reads `None` there instead of a NULL QVariant).
- After #39 or this PR merges, rebase the other and rerun the full suite; expect conflicts only in version lines, CHANGELOG, metadata.txt changelog and uv.lock.

## Open questions (North)
- The design answers were lost when the session ended; "goon" was taken as approval of: drop and report (not refuse behind a flag), and `_is_null` for NULL keys. Both are labelled ASSUMPTION in the task and the PR.
- Decided (Codex MEDIUM): filtering can empty one scale group while others keep values (row 0's only numbers belong to TOTAL). North chose "render and flag" on 2026-11-10: `GroupScale.empty` / `PanelGroup.empty`, and a warning names the group. The unit and FakeExecutor tests failed first; a live test confirms the figure renders with a flat bar. Commit after the PR opened, so Codex has not reviewed it.
- Parked, not fixed:
  - Codex LOW: `test_other_plugin_errors_from_get_field_values_pass_through` raises a fake message the real executor mapping would turn into `LayerNotFoundError`; use the plugin's real wording.
  - A CSV row with a blank key joins NULL-keyed zones (`""` on both sides), as before on QGIS 4.
  - `get_field_values` reads geometry it does not need (no `NoGeometry` request).
  - A short CSV row's key reads as "None".
  - The spec doc under `docs/superpowers/specs/` is not updated (DESIGN.md is).
  - Tests index `fake_executor.calls` by position.
- CI "plugin compiles on py3.9" fails at setup on every PR (Ubuntu 26.04 has no Python 3.9); the fix is proposed in #39's body (operator-owned).

## Who owns next
North: review and merge (this PR and #39, in either order). Codex approved the PR as opened (450100e); the empty-group commit after it had fresh-context review only.

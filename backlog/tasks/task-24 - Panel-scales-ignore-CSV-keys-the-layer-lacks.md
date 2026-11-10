---
id: TASK-24
title: Panel scales ignore CSV keys the zones layer lacks (qgis_compose_panels)
status: Done
assignee: []
created_date: '2026-11-10 12:00'
labels:
  - bug
  - pflow
dependencies:
  - TASK-22
priority: medium
ordinal: 24000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Parked MEDIUM from PR #38's review. `qgis_compose_panels` computes every group scale over all CSV values before the plugin sees the zones layer. CSV rows whose key matches no zone (a total row, keys from another zone system, a larger area) are never drawn but still set vmin, vmax, the cap percentile and `data_max`, so the colours are compressed and `PanelGroup.data_max` can report a value no panel shows. The spec (`docs/superpowers/specs/2026-10-30-panel-grid-design.md`) names "a figure that silently differs from the numbers" as the failure to avoid.

Plan:
1. Plugin: one helper derives a layer's join keys as text; `compose_panels` and a new command `get_field_values(path, field)` both use it, so they cannot disagree. Python 3.9 / PyQt6 compatible.
2. Server: `qgis_compose_panels` dispatches `get_field_values` first, drops CSV keys not on the layer from every column before `group_scales`, refuses a CSV with no key on the layer (JoinError) before rendering, and reports `n_csv_keys_unmatched` plus `csv_keys_unmatched_sample` (up to 5, sorted) in `PanelsResult`. An older plugin without the command gets an actionable error (restart QGIS).
3. Tests: FakeExecutor tests in `tests/test_compose_panels.py`; live tests in `tests/test_panels_live.py`; `plugin_contract` checks every dispatch.
4. Docs (DESIGN.md §4, CLAUDE.md), version 1.16.2 in pyproject.toml and metadata.txt.

Validation: `uv run --no-sync pytest tests/ -q`, `uv tool run ruff@0.16.5 check src/ tests/`.

ASSUMPTION (North's answer to the design question was lost when the session ended; "goon" taken as approval of the recommended options): CSV keys off the layer are dropped and reported, not refused; the shared key helper reads a NULL key as "" through the plugin's `_is_null`, so QGIS 3 (PyQt5 NULL QVariant) and QGIS 4 agree. `n_csv_keys_unmatched` counts distinct CSV keys (every row, whether or not it has a numeric value) that no zone carries.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A CSV key that no zone carries does not enter any group's vmin, vmax, cap percentile or data_max
- [x] #2 `PanelsResult` reports `n_csv_keys_unmatched` and a sorted sample of up to 5 such keys
- [x] #3 Plugin command `get_field_values(path, field)` returns the layer's keys from the same helper `compose_panels` uses; NULL keys read as ""
- [x] #4 A CSV with no key on the layer is refused before `compose_panels` is dispatched; an older plugin gets a restart hint
- [x] #5 FakeExecutor and live tests; full suite and ruff green; version 1.16.2; docs updated
- [x] #6 A scale group left without any value renders and is flagged `empty` in the result (North, 2026-11-10)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built 2026-11-10 test-first on `task/task-24-panel-csv-keys`. Numbered TASK-24 / v1.16.2 because a parallel session took TASK-23 / v1.16.1 for #39 (agreed by message).

Review (fresh context, read-only): code-reviewer APPROVE, python-reviewer APPROVE, security-reviewer no new exposure; no CRITICAL or HIGH. Fixed in one pass: a CSV whose on-layer keys carry no number is refused before rendering (test failed first); `_join_keys` reads each attribute once; the old-plugin hint chains its cause and a test pins the plugin's "Unknown command type" wording; CHANGELOG notes the QGIS 3 NULL-key fix.

Cross-harness: Codex (read-only, diff on stdin after its sandbox could not run commands) APPROVE.

Codex MEDIUM (an empty scale group after filtering reported data_max 0.0 with no data behind it): North chose "render and flag" on 2026-11-10. `GroupScale` and `PanelGroup` carry `empty`, a warning names the group; tests failed first (unit and FakeExecutor), plus a live render test.

Parked: a pass-through test uses a fake error wording (Codex LOW); a blank CSV key joins NULL-keyed zones (as before on QGIS 4); `get_field_values` reads geometry it does not need; a short CSV row's key reads "None"; the panel-grid spec doc is not updated (DESIGN.md is); tests index `fake_executor.calls` by position.
<!-- SECTION:NOTES:END -->

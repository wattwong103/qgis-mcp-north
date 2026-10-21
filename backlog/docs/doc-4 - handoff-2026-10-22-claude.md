---
id: doc-4
title: handoff-2026-10-22-claude
type: other
created_date: '2026-10-22 10:30'
---


## Goal
Continue from doc-3 (2026-10-20 live trial): merge the trial fixes, clean up, then fix the parked "random default colour for unstyled layers" and the missing-output-folder bug found while reproducing it, and wrap up.

## Done
- North merged #33 (1.15.0), #34 (1.15.1), #35 (1.15.2); main synced; all merged/superseded local and remote branches deleted by North (GitHub has only `main`); worktrees gone.
- Venv repaired: duckdb reinstalled (the earlier failed plain `uv sync` had removed its stubs and, in a later attempt, the project package itself). Venv now at the branch version via `uv sync --frozen --offline --all-extras`.
- PR #36 TASK-20 (1.15.3, task/task-20-deterministic-default-colour): reproduced live (same gpkg rendered twice: green, then purple); unstyled file layers now draw in ColorBrewer Set2 at crc32 of the NFC file name (+ `|layername=`); `.qml` / GeoPackage-stored styles and projects with random colours off are respected; compose_layout covered. Live after: byte-identical renders. Reviews (code, python): no CRITICAL/HIGH, one round fixed. CI 3/3.
- TASK-21 (1.15.4, task/task-21-output-folder, stacked on #36): `_save_map_image` (all render tools) and `qgis_figures_to_pptx` create their output folder; a failing save says what to check. Layout exporters already created folders (tests pin it). PR number in the PR list once opened.

## Files changed
#36: plugin.py (`_new_vector_layer`, `_apply_stable_default_color`, compose_layout loader), colormaps.py (`layer_color_key`, `stable_index`), tests/test_default_colour_live.py, tests/test_colormaps.py, DESIGN, CHANGELOG, version files. TASK-21: plugin.py (`_save_map_image`), server.py (`qgis_figures_to_pptx`), tests/test_output_folder_live.py, tests/test_figures_to_pptx.py, CHANGELOG, version files, this handoff.

## Verify next
- After #36 and the TASK-21 PR merge: pull main, `uv sync --frozen --all-extras` (venv to 1.15.4), reconnect the qgis-workflows MCP server.
- If the MCP server times out at startup again: `.mcp.json` runs plain `uv run`, which syncs first; a venv behind pyproject's version rebuilds the package on every start.

## Open questions
- TASK-20's colour rule (Set2 by file name) is Claude's call; North skipped the design questions.
- Parked: test helpers shared across live test modules (move to `tests/_live_helpers.py`); a `.qml` without a renderer keeps QGIS's random colour; same-name files in different folders share a colour; payload size check before the plugin's 10 MB cap; `shutil.which("git")`; DECIMAL > 2^53; server-side palette validation before link-density aggregation; replay stops at the first failing block; mcp 1.x runs sync tools on the event loop; style_qml (tranche E) not designed.

## Owner next
North: merge #36, then the TASK-21 PR (merge commits; the second shrinks to its own commits once #36 is in). Claude: next session picks a parked item or style_qml.

Status: DONE_WITH_CONCERNS (Codex cross-harness review not run on #36 / TASK-21; TASK-20 colour rule is Claude's call).

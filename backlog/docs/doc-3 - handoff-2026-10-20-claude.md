---
id: doc-3
title: handoff-2026-10-20-claude
type: other
created_date: '2026-10-20 06:00'
---


## Goal
"Finish, fix and try out our new things": run the v1.15 features (provenance sidecars, qgis_export_session replay, state ledger and eval gate, datasources, DuckDB lockdown) live against QGIS 3.40.14 headless on Windows, fix what breaks, release.

## Done
- Live trial in `tmp/trial-2026-10-20/` (git-ignored; inputs copied from fixtures/assets, figures, sidecars, `replay_trial.py`). Worked: sidecars with `${DROPBOX_ROOT}` paths; depends_on load → style → eval → render_map; .shp siblings and .qgz datasources; export of 7 figures, none skipped; eval gate (exit 3, --show-evals, --allow-eval, failed eval exit 1 with hint); replayed choropleth and styled render_map byte-identical; DuckDB lockdown refused read_csv, a bare-file scan, two statements and ATTACH.
- Found four bugs: B1 first sidecar of a session hangs the MCP server (git inherits the stdio stdin; root cause reproduced in scratch scripts); B2 QGIS switches a GeoPackage to WAL (header-only) so replays exit 2; B3 qualitative palettes (Set2 etc.) missing from QGIS's default style, silent Spectral and random trajectory colours; B4 render_from_duckdb crashes on DECIMAL/DATE/TIMESTAMP.
- PR #33 chore/release-1.15.0: version 1.15.0, CHANGELOG folded, TASK-15/16/17 Done, doc-2 stale line, this handoff. CI green.
- PR #34 task/task-18-trial-fixes (stacked on #33): B1, B3, B4 → 1.15.1. Reviews (code, python, security): no CRITICAL/HIGH; one review round fixed (default palette now auto, LIST/STRUCT/BLOB, qualitative graduated refusal, bounded _git, stricter stdin guard). DONE_WITH_CONCERNS (default-palette ASSUMPTION; Codex not run).
- PR #35 task/task-19-sqlite-content-hash (stacked on #34): B2 → 1.15.2, plan approved by North in-session. `sqlite_sha256` with header bytes 18-19, 24-27, 92-99 zeroed; replay accepts a match with a note only if the format bytes are 1/2 and no unrecorded -wal/-journal writes exist; -wal implicit input. Reviews: one HIGH (code + security independently: an edit pending in an unrecorded -wal passed as "data as recorded") fixed with a red test; MEDIUM/LOW fixed (export dedup key, gone -wal note, stat-first is_sqlite). DONE_WITH_CONCERNS (Codex not run; pre-1.15.2 sidecars need re-rendering).
- Local, not in git: AGENTS.md updated to 26 + 1 tools / v1.15.0 (test_docs_consistency reads it).

## Files changed
#33: pyproject.toml, uv.lock, metadata.txt, CHANGELOG.md, CLAUDE.md, docs/DESIGN.md, backlog tasks 15-17, doc-2, doc-3. #34: provenance.py (_git), server.py (_wire_value, style_categorized palette), errors.py (ExecutorError hint), plugin.py (_resolve_color_ramp, _check_palette_size, _category_colors, _default_category_palette, categorized/diagram/trajectory/graduated/link density), tests/conftest.py (FakeExecutor json check), tests/test_subprocess_stdin.py, tests/test_palettes_live.py, + tests for duckdb, style_categorized, tool_errors, provenance. TASK-19: provenance.py (_hashes, is_sqlite), replay.py (check_inputs notes), session_export.py, datasources.py (pending_wal), tests/test_sqlite_fingerprint.py, DESIGN + spec.

## Evidence
- #33 branch: 618 passed, 2 failed (known temp-file lock; AGENTS.md count, fixed locally → docs test 9 passed); ruff clean; CI 3/3.
- #34: 647 passed, 1 failed (known lock), ruff clean, plugin parses as 3.9; red-first runs for every bug (live Windows repro of the hang under pytest).
- #35: 668 passed, 1 failed (known lock), ruff clean; live end-to-end replay of a WAL-switched GeoPackage exits 0 with the note; the review HIGH has its own red test.
- Worktrees left for the open PRs: .worktrees/task-18-trial-fixes, .worktrees/task-19-sqlite-content-hash (remove after merge). Tests there run with the main venv and PYTHONPATH="<worktree>/src;<worktree>" (the editable install points at the main checkout's src).

## Verify next
- Restart the MCP server after #34 merges: the running one still has the old _git (it only hangs on a session's first sidecar).
- With the MCP server stopped, repair the venv: a plain `uv sync` (no --all-extras) removed duckdb's `_duckdb-stubs/*.pyi` before failing on the locked DLL; `uv sync --frozen --all-extras --reinstall-package duckdb`. Always sync with `--all-extras` here.
- Re-run `tmp/trial-2026-10-20/replay_trial.py --allow-eval --out-dir ...` on 1.15.2: expect the gpkg header note instead of exit 2 (the `c_` block still fails by design: its eval names a literal layer id).

## Open questions
- Default palette ASSUMPTION in #34 (none → Set2 up to 8 categories, else Spectral) vs refusing a bare call: North to confirm.
- Parked (TASK-18 notes): payload size check before the plugin's 10 MB cap; shutil.which("git"); DECIMAL > 2^53; fixed mode→colour map; server-side palette check before link-density aggregation; unstyled layers (load_layer → render_map, basemap_paths) still get QGIS's random default colour; replay stops at the first failing block; mcp 1.x runs sync tools on the event loop (one slow call freezes the server).
- style_qml (tranche E remainder) still not designed.

## Owner next
North: merge #33, then #34, then #35 (each shrinks to its own commits once the one below is in; use merge commits, not squash, or the stacked PRs conflict). Delete merged local branches (hook-blocked for Claude). Claude: next session removes the two worktrees after merge, restarts nothing itself, and picks a parked item (random default colour for unstyled layers is the closest to today's work) or style_qml.

Status: DONE_WITH_CONCERNS (Codex cross-harness review not run on #33/#34/#35; #34's default-palette ASSUMPTION awaits North; venv stubs to repair with the MCP server stopped).

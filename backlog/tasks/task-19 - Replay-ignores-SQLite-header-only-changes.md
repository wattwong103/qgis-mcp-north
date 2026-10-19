---
id: TASK-19
title: Replay ignores SQLite header-only changes (GeoPackage WAL switch)
status: In Progress
assignee: []
created_date: '2026-10-20 05:00'
labels:
  - provenance
  - replay
  - schema
dependencies:
  - TASK-18
priority: high
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the 2026-10-20 live trial: `qgis_batch_render` on a QGIS project made QGIS open its GeoPackage for update, which switched the file to WAL journal mode. Only the 100-byte SQLite header changed (read/write version 1→2, change counter 31→32, the library version stamp); every table was identical (sqlite compare, page 1 the only differing page). The byte sha256 changed, so the replay of that figure, and of every earlier figure that read the gpkg, exits 2 forever unless --force. QGIS Desktop opening a file for editing does the same, so any GeoPackage input is exposed.

North's decision (2026-10-20): record a second hash for SQLite inputs with the volatile header fields masked, accept a replay when that one matches, and fingerprint a non-empty `-wal` file.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An input that starts with the SQLite magic (`SQLite format 3\0`, any extension: .gpkg, .sqlite, .db, .mbtiles) gets `sqlite_sha256`: sha256 of the file with header bytes 18-19, 24-27 and 92-99 zeroed; under the same size cap and cache as `sha256`; absent for other files
- [x] #2 The replay input check passes an input whose `sha256` differs but whose recorded and current `sqlite_sha256` agree, and prints one note naming it ("header-only change"); a data change still fails with exit 2; sidecars without the field keep the byte comparison
- [x] #3 `qgis_export_session` carries `sqlite_sha256` into the script's INPUTS
- [x] #4 A SQLite input with a non-empty `<file>-wal` gets that file as an implicit input (`<argument>:wal`, raw hash) and a note that a later checkpoint changes the main file
- [x] #5 Live test: the trial's scenario (rollback-journal gpkg → batch_render through a QGIS-written project → replay) exits 0 with the note
- [x] #6 DESIGN §5 and the provenance spec describe the field; CHANGELOG; full suite and ruff
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. `provenance.py`: `_sqlite_sha256(path, st)`: read the first 16 bytes; if not the SQLite magic, None. Otherwise stream-hash with the first 100 bytes copied and the five fields zeroed. Cache key `(path, size, mtime_ns, "sqlite")` in `_hash_cache`. `fingerprint()` adds `sqlite_sha256` only when not None (old readers ignore unknown keys; schema stays `provenance@1`).
2. `replay.check_inputs`: when `both_hashed` and the sha256 differ, accept if `entry.get("sqlite_sha256")` and the current one are equal; collect the accepted paths and return them as notes beside the problems (signature becomes `(problems, notes)`; `main` prints the notes).
3. `session_export.source_inputs`: copy `sqlite_sha256` into each INPUTS item (dedup key unchanged).
4. `provenance` wrapper (where `.shp` siblings are added): for each SQLite input with a non-empty `-wal`, append `(f"{arg}:wal", path + "-wal")` to the implicit inputs and the note.
5. Tests first, in this order: (a) unit, two copies differing only in the masked fields have equal `sqlite_sha256` and different `sha256`; a changed data page changes both; a CSV gets no field; the cap applies. (b) unit, `check_inputs` accept/reject/legacy cases. (c) unit, export INPUTS carry the field. (d) unit, the `-wal` implicit input. (e) live, the trial scenario.
6. Docs: DESIGN §5 input-entry table, spec §9 note, CHANGELOG 1.15.2.

Considered and not chosen: a logical content hash read through SQLite (sees WAL content and survives VACUUM, but costs a full table scan per call and needs the `-wal` stat in its cache key); stopping QGIS from switching to WAL (headless-only, undocumented).

Known limit: writes still sitting in a `-wal` at record time are checkpointed into the main file later, which changes data pages, so such a figure's check fails after a checkpoint even if the data is logically the same (the note says so; `--force` after checking).

Estimate: ~120 lines of code, ~180 lines of tests.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Reviews (code, python, security; fresh context, read-only; 2026-10-20). HIGH (code and security, independently): a header-only acceptance passed an edit committed after the WAL switch that still sat in an unrecorded `-wal`, and printed "its data is as recorded". Fixed: the acceptance also requires header format bytes 1 or 2 and no non-empty `-wal`/`-journal` unless recorded with the same hash; otherwise "changed ... writes may be pending". MEDIUM fixed: export dedup key includes sqlite_sha256 (a co-exported sidecar could swap in a forged one); a recorded `-wal` that is gone is a note; `is_sqlite` stats first (FIFO/device never opened) and `_regular_stat` treats a NUL path as missing; cache resets in mtime-sensitive tests; tests for a project datasource's `-wal`, a non-SQLite `-wal`, unknown format bytes, and main printing the note. LOW fixed: `accepted=` keyword-only, per-input WAL notes, helpers before `discover`, `closing()` for sqlite3, teardown reset, a `-wal` link never followed.
Parked: a symlinked database whose WAL lives beside the link target; masking 24-27/92-95 changes whether SQLite trusts the in-header page count (28-31 still hashed; only matters for files whose size disagrees with it); a stale non-empty `-wal` of a checkpointed, still-open database reads as pending (conservative).
<!-- SECTION:NOTES:END -->

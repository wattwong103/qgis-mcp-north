# Figure provenance — design

Status: proposed, v2 (2026-10-08) — revised after feasibility, adversarial and security reviews.
Tasks, in delivery order: TASK-12 (sidecar core) → TASK-13 (`qgis_export_session`) → TASK-15 (state ledger
and evals) → TASK-16 (project and shapefile datasources).
Origin: 2026-10-06 comparison with nkarasiak/qgis-mcp v0.15.0, whose `export_session` journals plugin
commands. Ours records MCP-side so a figure replays from source data, not from the derived payload.

## 1. Goal

Any figure in a paper or deck can be **traced** to its exact inputs and settings, and **re-made** from
source data later — on another of North's machines (win `H:/Dropbox`, ws `D:/Dropbox`, Mac `~/Dropbox`) or
after an MCP server restart. When that cannot be guaranteed, the sidecar and the replay say so.

Non-goals (v1): pinning remote tile basemaps; provenance for figures made by plain-Python callers
(`scripts/weekly_figures.py`, `scripts/demo_w17.py` — out of scope by decision; they appear as `skipped`);
sidecars for replayed outputs; a whole-session dump; extra named path roots; a redacted "share" export;
replay over the plugin transport; a compound `qgis_export(kind="session")`.

## 2. Decisions (North, 2026-10-07/08)

| Question | Decision |
|---|---|
| Purpose | Trace **and** reproduce |
| Input fingerprint | sha256 up to 256 MB, size + mtime above it (`hashed: false`) |
| When recorded | Always on for MCP calls; `QGIS_MCP_WORKFLOWS_PROVENANCE=0` disables |
| Replay source | Sidecars of chosen figures (or a folder) |
| Granularity | One sidecar per written file |
| Chains | Inputs link to their producer's sidecar (`made_by`) when fingerprints agree |
| `qgis_eval` in replay | Emitted, but the script runs them only with `--allow-eval`; evals from foreign sidecars are emitted only with `trust_foreign=True` |
| Replay outputs | Separate folder by default (`--out-dir`); `--in-place` opt-in |
| Paths | Stored under `${DROPBOX_ROOT}`; if unset, absolute and flagged — no `~/Dropbox` fallback; `install.py` passes `DROPBOX_ROOT` into MCP client configs |
| Architecture | Record MCP-side in the tool-registration wrapper |
| Delivery | Four PRs (§12); scope cuts in §1 |
| Scripts | Plain-Python callers are not recorded |

## 3. Recording architecture (TASK-12)

New module `src/qgis_mcp_workflows/provenance.py` (imports `server` lazily — `server` imports it).

**Hook.** `with_provenance(fn)` uses `functools.wraps` (FastMCP builds the schema from the innermost
signature via `__wrapped__`, as it already does for `with_png_preview`). Applied only to registered copies:
`mcp.tool(...)(with_png_preview(with_provenance(f)))` in `_maybe_tool` and `_maybe_compound_tool`, and an
explicit registration path for `qgis_eval` (today a bare `@mcp.tool`, `server.py:2910`; used by TASK-15).
`qgis_ping`, `qgis_diagnose`, `qgis_layer_inspect` and `qgis_export_session` are exempt (no figure, or the
output is the replay script itself). Plain-Python callers get the bare function and record nothing.

**Per call:**
1. Read `QGIS_MCP_WORKFLOWS_PROVENANCE` (per call, so tests can monkeypatch); `0` → call the tool and return.
2. Bind arguments: `inspect.signature(fn).bind(**kwargs).apply_defaults()` (mcp 1.26 passes every field,
   defaults included). Normalise path arguments (§8).
3. **Before the call:** stat + fingerprint every input (§5) and, for TASK-15, snapshot the ledger. Assign
   `session_id` (one per server process, a UUID) and `seq` (a per-process counter, under a lock).
4. Run the tool; time it. If it raises, re-raise and record nothing. (TASK-15: an eval result with
   `exception` set is a failure too.)
5. **After the call:** re-stat inputs; any size/mtime change → `changed_during_call: true` on that input.
   Find written files from the result (§5); fingerprint each output; write one sidecar per output.
6. Any exception in step 5 is caught and logged at WARNING with the figure path; the tool result is
   returned unchanged (response schemas do not change). If a sidecar cannot be written, an existing
   (now stale) sidecar for that figure is deleted so it cannot vouch for the new file.

**Atomic write:** `tempfile.mkstemp(dir=<figure dir>, prefix=".", suffix=".provenance.tmp")`, write,
`fsync`, `os.replace`; on Windows `PermissionError` (Dropbox holding the target) retry up to 5 × 100 ms; the
temp file is unlinked in `finally`.

**Environment** (per server process, keyed by executor instance; a failure is never cached): QGIS and
plugin version from the `diagnose` dispatch (`plugin.py:401-433`) — fetched only when that executor has
already dispatched for this call, so pure-Python tools (`figures_to_pptx`, `route_on_network`,
`assign_section_load`) never spawn headless QGIS just to be recorded; otherwise `"not queried"`. Also:
package version, git commit + dirty flag of the package source when it is a checkout, installed optional
extras, transport, mcp, python, platform, QGIS profile folder.

## 4. Sidecar schema (`<file>.provenance.json`)

```json
{
  "schema": "qgis-mcp-workflows/provenance@1",
  "figure": "${DROPBOX_ROOT}/gufm/figs/fig3_choropleth.png",
  "figure_sha256": "51ab…", "figure_bytes": 182344,
  "session_id": "7c1e…", "seq": 14,
  "created": "2026-10-07T01:31:05Z",
  "call": {"tool": "qgis_render_choropleth", "module": "server",
           "arguments": {"zones_path": "${DROPBOX_ROOT}/qgis-mcp-north/assets/zones_tokyo23.gpkg",
                         "value_field": "n_persons", "palette": "gufm", "...": "every argument, defaults filled in"}},
  "result_summary": {"breaks": [0, 120, 340, 910, 2400], "n_matched": 23, "basemap_spec": null},
  "depends_on": [],
  "inputs": [
    {"argument": "zones_path", "path": "${DROPBOX_ROOT}/…/zones_tokyo23.gpkg", "bytes": 614400,
     "mtime": "2026-10-01T00:12:44Z", "sha256": "9f2c…", "hashed": true, "made_by": null,
     "changed_during_call": false}
  ],
  "implicit_inputs": [],
  "remote": [{"argument": "basemap", "value": "light", "note": "tiles are fetched live and not pinned"}],
  "unrecorded_state": [],
  "machine_specific": [],
  "outputs": ["${DROPBOX_ROOT}/gufm/figs/fig3_choropleth.png"],
  "environment": {"qgis_mcp_workflows": "1.14.0", "git": "c6afca1", "git_dirty": false,
                  "extras": ["duckdb", "network", "pptx"], "plugin": "1.14.0", "qgis": "3.40.14-Bratislava",
                  "transport": "headless", "mcp": "1.26.0", "python": "3.12.12", "platform": "Windows-11"},
  "elapsed_s": 2.41
}
```

- All timestamps UTC. `figure_sha256` ties the sidecar to the file beside it: a figure later overwritten by
  anything else (a script, another tool) no longer matches and is reported as a **stale sidecar**.
- `result_summary`: scalar decision fields of the result (`breaks`, `colors`, `n_matched`, `downsampled`,
  `used_movingpandas`, resolved `basemap_spec`, …) — whatever the result model carries, minus paths and
  lists longer than 50 items — so a replay can be diffed against what the original produced.
- `implicit_inputs`: files a tool used without an argument naming them (the bundled `sekilab_blank.pptx` when
  `template_pptx=None`), fingerprinted like inputs.
- `call.module` is `server` or `compound`; compound calls keep the compound tool and its discriminator.
- Free-text fields come from our own code, never from file contents.

## 5. Inputs and outputs

| Class | Names |
|---|---|
| Input file | `path`, `zones_path`, `zones_layer_path`, `value_csv`, `input_path`, `input_csv`, `od_csv`, `load_csv`, `drm_network_path`, `network_path`, `rail_network_path`, `db_path`, `layer_path`, `points_path`, `join_path`, `target_path`, `raster_path`, `qgz_path`, `template_qgz`, `template_pptx` |
| Input file list | `trajectory_csvs`, `layer_paths`, `figure_paths`, `basemap_paths` |
| Output argument | `output_png`, `output_path`, `output_csv`, `output_dir`, `pptx_path` |
| Not a path | `basemap` (→ `remote` when set; `qms:` sources also `machine_specific`), `query`, `filename_template`, `basemap_opacity` |

- **Written files are extracted per result model**, not by a generic rule: `RenderResult`-family and
  `ExportResult`/`ComposeLayoutResult`/`SpatialJoinResult`/`ZonalStatsResult` → `output_path`;
  `RouteResult`/`SectionLoadResult` → `output_csv`; `PptxResult` → `pptx_path`; `AtlasExportResult` →
  `files` (never `output_path`, which can be the directory); `BatchRenderResult` → manifest `output_path`s.
  Only regular files that exist after the call count.
- **Input equal to an output** (a deck appended in place: `template_pptx == pptx_path`) is fingerprinted
  before the call and listed in `unrecorded_state` ("input overwritten by this call").
- **Chains:** an input gets `made_by: <producer sidecar>` only if that sidecar exists and its
  `figure_sha256` equals the input's sha256 (both hashed). Otherwise the input is a source input.
- **Drift guards (tests):** every path-like `qgis_*` parameter (name contains `path`, `csv`, `png`, `dir`,
  `qgz`, `pptx`, `db`, `file`, `basemap`) is in one of the four sets; every result model with a path-valued
  field is in the extraction table.
- **TASK-16** adds `.shp` siblings (`.dbf .shx .prj .cpg`) and `.qgz`/`.qgs` datasources (§9).

## 6. State ledger and evals (TASK-15)

The ledger is module state with a per-test reset hook; every entry carries its `seq`.

| Call (full or compound name) | Ledger update |
|---|---|
| `qgis_load_layer`; `qgis_inspect(kind="layer", register=True)` | `layers[id] = [load call + produced layer_id + its inputs]` |
| `qgis_style_categorized` / `_graduated`; `qgis_style(type=…)` | append to that layer's style calls (all kept, in `seq` order) |
| `qgis_project_load`; `qgis_inspect(kind="project")` | clear `layers`; `project = {path, call}` |
| `qgis_export_layout` / `qgis_export_atlas` / `qgis_batch_render` with a *different* project path | clear `layers`; `project = None` (the plugin replaced the project) |
| `qgis_eval` (successful) | append to `evals` |

**State-reading figures:** `qgis_render_map(layer_ids=…)` (`qgis_render(mode="map")`), and
`qgis_export_layout` / `qgis_export_atlas` / `qgis_batch_render` whose project path **equals** the ledger's
current project (the plugin reuses the loaded, possibly restyled project instead of re-reading the file).
Their `depends_on` is the snapshot (taken before the call) of: the project call, every load/style call for
the layers used (all layers, for project-based exports), and every eval — merged and sorted by `seq`.
Each entry keeps its own `inputs`, which the export copies into the input check.

**Honest gaps → `unrecorded_state`:** a `layer_id` the ledger never saw; plugin transport + state-reading
figure ("Desktop session: manual edits in QGIS are not recorded"); any figure made after an eval in the
same session, even an atomic one ("an earlier qgis_eval may have changed shared state, e.g. colour ramps");
eval code containing literal layer ids or absolute paths.

## 7. `qgis_export_session` (TASK-13; ledger-aware in TASK-15)

```
qgis_export_session(output_py, figures=None, folder=None, include_evals=True, trust_foreign=False,
                    overwrite=False) -> SessionExportResult
  {output_path, n_figures, n_calls, n_evals, skipped: [{figure, reason}], warnings: [str]}
```

**Collecting.** Exactly one of `figures` / `folder` (non-recursive `*.provenance.json`; Dropbox
"conflicted copy" sidecars are reported and skipped). `output_py` must end in `.py` and must not exist
unless `overwrite=True`. Follow `made_by` links (cycle-safe, at most 200 sidecars, each ≤ 2 MB).
`skipped` reasons are fixed templates (`no sidecar`, `unknown schema`, `stale sidecar: figure changed`,
`unknown tool`, `foreign eval not trusted`, `needs session state`); `warnings` likewise, with any echoed value capped at 200
characters and control characters stripped. The tool never runs the script it writes.

**Ordering and de-duplication.** Producers before consumers (`made_by`), then `(session_id, seq)`; a call
is identified by `(session_id, seq)`, so sibling outputs of one call (atlas pages) share one step and two
identical calls stay two steps. Between calls from different sessions the script emits a project reset.

**Trust.** A sidecar is unsigned: recorded hashes detect drift, not tampering. A sidecar whose `figure`
resolves outside the local `DROPBOX_ROOT` is *foreign*; its evals are dropped (and the figure listed in
`warnings`) unless `trust_foreign=True`. `include_evals=False` drops all evals.

**Generation.** The script is built as a Python AST and rendered with `ast.unparse` — no string templates:
- `call.tool` must be a registered workflow tool of `call.module`, and every argument key must be a
  parameter of that function (`inspect.signature`); otherwise the figure is skipped (`unknown tool`).
- Argument values become `ast.Constant`/list/dict literals; recorded `layer_id`s become `layer_N` names
  bound from `server.qgis_load_layer(...).layer_id` (substituted in the AST).
- All free text (sources, warnings, notes) goes into one `NOTES = [...]` literal printed at start-up.
- Evals become `if args.allow_eval: server.qgis_eval(code=...)` blocks. Without `--allow-eval` the script
  prints each eval's sha256 and first three lines to stderr, lists the figures that depend on them, and
  exits 3 before running anything.

**Running the script** (headless only):
- `check_inputs(INPUTS)`: every source input (incl. `depends_on` and implicit inputs; not intermediates
  produced by an earlier step) is compared — sha256 when hashed, else size (mtime mismatches are a warning
  row only: Dropbox sync rewrites mtimes). Mismatch → table and exit 2 unless `--force`.
- `--out-dir DIR` (default `replay_<UTC date>/` beside the script): outputs are remapped to
  `DIR/<ROOTNAME>/<rest>` for portable paths and `DIR/_abs/<drive>/<rest>` otherwise; every remapped path is
  checked to lie under `DIR` after `resolve()`. Consumer inputs that were remapped outputs follow. Writes
  done inside replayed evals are not remapped (stated in `NOTES`).
- `--in-place`: refuses any output that is also a recorded input anywhere in the script; lists the existing
  files it would overwrite and requires `--yes`; cannot be combined with `--force`.
- `${NAME}` expansion is an allow-list (`DROPBOX_ROOT` only); after expansion the path is normalised and
  must stay inside the root. Unset `DROPBOX_ROOT` stops with "set DROPBOX_ROOT to this machine's Dropbox
  folder".

## 8. Paths across machines

- Normalise every recorded path: `expanduser`, `abspath`, separators to `/`.
- Portable form: if the path, or its `realpath`, lies under `DROPBOX_ROOT` (or that root's `realpath` —
  catches the macOS `~/Library/CloudStorage/Dropbox` behind `~/Dropbox`), matched on **whole path
  components** (so `H:/Dropbox-old` does not match `H:/Dropbox`) and case-insensitively on Windows only,
  it is stored as `${DROPBOX_ROOT}/<rest>`. A path containing `..` after normalisation is not made portable.
- Anything else stays absolute and is listed in `machine_specific`. If `DROPBOX_ROOT` is unset, every path
  is machine-specific and the sidecar says why; there is no `~/Dropbox` fallback (on win that is another
  Dropbox).
- `install.py` writes `DROPBOX_ROOT` into the generated MCP client config env when it is set (TASK-12), so
  GUI clients that do not inherit the shell environment still see it.
- Privacy: sidecars name folders, files, query text and eval code. They are local artefacts — keep
  `*.provenance.json` out of git (added to `.gitignore`) and review them before sharing.

## 9. Project and shapefile datasources (TASK-16)

- `.shp` inputs also fingerprint existing `.dbf .shx .prj .cpg` siblings.
- `.qgz` = zip: read only its single `.qgs` member, at most 64 MB, never `extractall`; `.qgs` = plain XML,
  same cap. Refuse documents containing `<!DOCTYPE` or `<!ENTITY` (record as `unrecorded_state`), except the
  fixed `<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>` QGIS writes into every project (no internal
  subset; found by the TASK-16 live test).
- Datasources: strip `|layername=…` suffixes; parse `file:///…?…` (delimited text), `dbname='…'` (SQLite
  / GeoPackage), and paths relative to the project folder (`./`, `../`). Local regular files (`S_ISREG`) under
  the project folder or `DROPBOX_ROOT` are fingerprinted (`argument: "qgz_path:datasource"`). UNC and
  `file://host/` paths are never opened. Anything unparsed or outside those roots → `unrecorded_state`;
  databases and web services → `remote`, with `password`, `user`, `apikey`, `token`, URL userinfo and query
  strings redacted. GDAL/OGR connection strings (`PG:`, `MSSQL:`, `OCI:` …) count as databases. Links
  (symlinks, junctions) below a root are never followed.

## 10. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `QGIS_MCP_WORKFLOWS_PROVENANCE` | `1` | `0` disables sidecars and the ledger (read per call) |
| `QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB` | `256` | inputs above this get size + mtime only |
| `DROPBOX_ROOT` | per machine | the portable root |

## 11. Testing

FakeExecutor-based unless marked live. FakeExecutor responses that stand for file-writing tools create the
file (response lambdas), so output fingerprints and chains are real.

- **TASK-12:** sidecar per written file through `mcp.call_tool` (incl. compound mode); none for direct
  calls, failures, `PROVENANCE=0`, exempt tools; defaults bound; inputs fingerprinted before the call
  (in-place deck append → `unrecorded_state`); `changed_during_call`; hash cap; output fingerprint; per-model
  extraction (atlas `files`, batch manifest); both drift guards; portable paths (component match,
  `H:/Dropbox-old`, unset root, `..`); atomic write leaves no temp file and a failed write deletes the stale
  sidecar; environment never spawns headless for pure-Python tools and never caches a failure;
  `install.py` writes `DROPBOX_ROOT`.
- **TASK-13:** script compiles (AST); hostile sidecar (`"""`, newline, `tool: "os.system"`, unknown argument
  key) is skipped or neutralised; stale sidecar detected; `(session_id, seq)` dedupe keeps two identical
  calls; producer-first order; out-dir remap incl. two drives and the containment check; `--in-place`
  refusals; input check exit 2 / `--force`; unset root; **round trip**: the script run against
  FakeExecutor reproduces the original `(command, params)` sequence (environment dispatch filtered, output
  and intermediate paths remapped, FakeExecutor returning *different* layer ids on replay); live headless:
  choropleth → export → replay, PNG size equal.
- **TASK-15:** eval recorded through `call_tool` (and failed eval not); style A→B→A replays A last;
  export after `project_load` + style is state-reading; export of another path clears the ledger; compound
  ledger; `depends_on` inputs reach `check_inputs`; eval gating (`--allow-eval`, exit 3, foreign evals
  dropped without `trust_foreign`).
- **TASK-16:** `.shp` siblings; `.qgz` and `.qgs` datasources incl. delimited text, `dbname=`, `../`;
  DOCTYPE refusal; size cap; UNC never opened; credential redaction.

## 12. Delivery

1. **TASK-12 — sidecar core:** `provenance.py` record side, hook (incl. eval registration path, unused
   until TASK-15), schema §4, §5 without TASK-16 items, §8, `install.py` env, `.gitignore`, DESIGN §5,
   CHANGELOG.
2. **TASK-13 — `qgis_export_session`** for atomic figures and chains (§7 minus ledger/eval parts). Until
   TASK-15, figures from state-reading calls (§6) are listed as `skipped: needs session state` rather than
   exported as a replay that would silently differ. Tool count bumped in CLAUDE.md, README, AGENTS.md,
   DESIGN, `SERVER_INSTRUCTIONS`.
3. **TASK-15 — ledger and evals** (§6) and their export/replay parts (§7 eval gating, `depends_on`).
4. **TASK-16 — datasources** (§9).

Each is its own branch and PR, in this order.

## 13. Risks

- Hashing (≤ 256 MB per input, cached) runs inline on the event loop under mcp 1.26; very large hashed
  inputs add latency to that call.
- Replay fidelity is bounded by remote tiles, Desktop-side manual edits, QGIS/profile differences and
  plain-Python writes; each is surfaced (`remote`, `unrecorded_state`, `environment`, stale sidecars), not
  hidden.
- Parked separately (not provenance): `qgis_render_from_duckdb` should open DuckDB with
  `enable_external_access=false`, since a read-only connection can still read other files or load
  extensions from the query text.

# Figure provenance — design

Status: proposed (2026-10-08). Tasks: TASK-12 (sidecars + ledger), TASK-13 (`qgis_export_session`).
Origin: 2026-10-06 comparison with nkarasiak/qgis-mcp v0.15.0, whose `export_session` journals plugin
commands. Ours records MCP-side so a figure replays from source data, not from the derived payload.

## 1. Goal

Any figure in a paper or deck can be **traced** to its exact inputs and settings, and **re-made** from
source data later — on another of North's machines (win `H:/Dropbox`, ws `D:/Dropbox`, Mac `~/Dropbox`) or
after an MCP server restart.

Non-goals: pinning remote tile basemaps; provenance for outputs written by `qgis_eval` itself; sidecars for
replayed outputs (the replay script and the original sidecars are their provenance); a whole-session dump.

## 2. Decisions (North, 2026-10-07/08)

| Question | Decision |
|---|---|
| Purpose | Trace **and** reproduce |
| Input fingerprint | sha256 up to a size cap (256 MB), size + mtime above it, flagged `hashed: false` |
| When recorded | Always on; `QGIS_MCP_WORKFLOWS_PROVENANCE=0` disables |
| Replay source | Sidecars of chosen figures (or a folder); each sidecar carries the state its figure needed |
| Sidecar granularity | One sidecar per written file |
| Chains | Inputs with their own sidecar link to it (`made_by`); export follows the links |
| `qgis_eval` in replay | Replayed, flagged with a banner |
| Replay output location | Separate folder by default (`--out-dir`, default `replay_<date>/`); `--in-place` opt-in |
| Paths | Stored under `${DROPBOX_ROOT}` when inside it, so sidecars replay on every machine |
| Architecture | Record MCP-side in the tool-registration wrapper (approach 1) |

## 3. Architecture

New module `src/qgis_mcp_workflows/provenance.py`:

- `with_provenance(fn)` — decorator applied only to the **registered** copy of each tool, inside
  `with_png_preview`: `mcp.tool(...)(with_png_preview(with_provenance(f)))` in both `_maybe_tool` and
  `_maybe_compound_tool` (`server.py`). Python callers (tests, `scripts/demo_w17.py`, replay scripts) get
  the bare function and record nothing.
- `Ledger` — in-memory session state (Section 6), guarded by a `threading.Lock` (mcp 2.x runs sync tools
  in worker threads).
- `fingerprint(path)` — hashing with an in-process cache keyed by `(abs path, size, mtime_ns)`.
- `portable(path)` / `resolve(path)` — `${DROPBOX_ROOT}` substitution (Section 8).
- `write_sidecar(figure, record)` — atomic write (`<figure>.provenance.json.tmp` then `os.replace`).
- `check_inputs(inputs)` — used by generated replay scripts (Section 7).

`with_provenance` flow per call:

1. Bind arguments: `inspect.signature(fn).bind(**kwargs)`, `apply_defaults()` — defaults are recorded so a
   later default change cannot alter a replay.
2. Run the tool; time it. If it raises, re-raise and record nothing.
3. Update the ledger (state-setting tools) and, if the result names written files, build one record and
   write one sidecar per written file.
4. Any exception in step 3 is caught, logged at WARNING with the figure path, and the tool result is
   returned unchanged. Response schemas do not change.

## 4. Sidecar schema (`<file>.provenance.json`)

```json
{
  "schema": "qgis-mcp-workflows/provenance@1",
  "figure": "${DROPBOX_ROOT}/gufm/figs/fig3_choropleth.png",
  "created": "2026-10-07T10:31:05+09:00",
  "call": {"tool": "qgis_render_choropleth", "module": "server",
           "arguments": {"zones_path": "${DROPBOX_ROOT}/qgis-mcp-north/assets/zones_tokyo23.gpkg",
                         "value_field": "n_persons", "palette": "gufm", "mode": "quantile", "...": "all args"}},
  "depends_on": [],
  "inputs": [
    {"argument": "zones_path", "path": "${DROPBOX_ROOT}/…/zones_tokyo23.gpkg", "bytes": 614400,
     "mtime": "2026-10-01T09:12:44+09:00", "sha256": "9f2c…", "hashed": true, "made_by": null},
    {"argument": "db_path", "path": "${DROPBOX_ROOT}/…/kichijoji.duckdb", "bytes": 4123456789,
     "mtime": "…", "sha256": null, "hashed": false, "made_by": null}
  ],
  "remote": [{"argument": "basemap", "value": "light", "note": "tiles are fetched live and not pinned"}],
  "unrecorded_state": [],
  "machine_specific": [],
  "outputs": ["${DROPBOX_ROOT}/gufm/figs/fig3_choropleth.png"],
  "environment": {"qgis_mcp_workflows": "1.14.0", "plugin": "1.14.0", "qgis": "3.40.14-Bratislava",
                  "transport": "headless", "mcp": "1.26.0", "python": "3.12.12", "platform": "Windows-11"},
  "elapsed_s": 2.41
}
```

- `call.module` is `server` or `compound`; compound calls are recorded as the compound tool with its
  discriminator (`qgis_render`, `mode="choropleth"`).
- `outputs` lists every file the call wrote; each of them gets this same record with its own `figure`.
- `environment.qgis` / `plugin` come from one `get_qgis_info` dispatch per server process, cached; on
  failure (degraded transport) they are `"unknown"`.
- Arguments are JSON values; tuples become lists. `qgis_eval` code is stored verbatim.

## 5. Inputs and outputs

Classification by argument name (names are consistent across tools):

| Class | Names |
|---|---|
| Input file | `path`, `zones_path`, `zones_layer_path`, `value_csv`, `input_path`, `input_csv`, `od_csv`, `load_csv`, `drm_network_path`, `network_path`, `rail_network_path`, `db_path`, `layer_path`, `points_path`, `join_path`, `target_path`, `raster_path`, `qgz_path`, `template_qgz`, `template_pptx` |
| Input file list | `trajectory_csvs`, `layer_paths`, `figure_paths`, `basemap_paths` |
| Output argument | `output_png`, `output_path`, `output_csv`, `output_dir`, `pptx_path` |
| Not a path | `basemap` (→ `remote` when set), `query`, `filename_template`, `basemap_opacity` |

- **Written files come from the result**, not the arguments: `output_path`, `output_csv`, `pptx_path`,
  atlas `files`, batch-render manifest entries' `output_path`. Only paths that exist after the call count.
- **Drift guard:** a test enumerates every `qgis_*` tool parameter whose name contains `path`, `csv`, `png`,
  `dir`, `qgz`, `pptx`, `db`, `file` or `basemap` and fails unless it is in one of the four sets.
- **Shapefiles:** a `.shp` input also fingerprints existing `.dbf .shx .prj .cpg` siblings (one input entry
  each, same `argument`).
- **Projects:** for a `.qgz`/`.qgs` input, read the project XML (`zipfile` + `xml.etree`, no QGIS), take every
  `<datasource>`, strip `|layername=…` style suffixes, resolve `./` against the project folder; local files
  that exist become input entries (`argument: "qgz_path:datasource"`), anything else goes to `remote`.
- **Chains:** if `<input>.provenance.json` exists, the input entry's `made_by` is that sidecar's (portable)
  path.
- Inputs that do not exist at record time are recorded with `bytes: null` (the tool already succeeded, so
  this only happens for optional inputs the tool ignored).

## 6. Ledger (state-reading figures)

| Call | Ledger update |
|---|---|
| `qgis_load_layer` | `layers[layer_id] = [load call with "produced": {"layer_id": id}]` |
| `qgis_style_categorized` / `qgis_style_graduated` | replace that layer's previous style call (last style wins) |
| `qgis_project_load` | clear `layers` and `evals`; `project = the call` |
| `qgis_eval` | append to `evals` (cleared on project load) |

State-reading figures: `qgis_render_map(layer_ids=…)`, and `qgis_export_layout` / `qgis_export_atlas` called
without `qgz_path`. Their `depends_on` is a copy, in recorded order, of: the project call (if any), the load
and last style call for each `layer_id` used, then every eval (`"kind": "eval"`). Atomic tools never get
`depends_on`.

Honest gaps, recorded in `unrecorded_state`:
- a `layer_id` the ledger never saw ("loaded before this server started" — plugin transport keeps Desktop
  layers across MCP restarts);
- plugin transport + state-reading figure: "Desktop session: manual edits in QGIS are not recorded".

## 7. `qgis_export_session` (TASK-13)

```
qgis_export_session(output_py, figures=None, folder=None) -> SessionExportResult
  {output_path, n_figures, n_calls, n_evals, skipped: [{figure, reason}], warnings: [str]}
```

- Exactly one of `figures` (figure or sidecar paths) / `folder` (non-recursive `*.provenance.json`);
  otherwise `InvalidArgumentError`.
- Follow `made_by` links (cycle-safe); order producers before consumers, then by `created`.
- De-duplicate calls by `(module, tool, arguments)` — atlas pages share one call; a layer loaded for three
  figures is loaded once.
- Emit a standalone script:
  - header docstring: sources, date, `n_evals`, a WARNING banner if any eval is present, and every
    `unrecorded_state` / `machine_specific` note;
  - `INPUTS = [...]` fingerprints of **source** inputs only; `check_inputs(INPUTS)` stops with a table of
    changed/missing inputs (exit 2) unless `--force`. An input produced by an earlier step of the same
    script (its `made_by` sidecar is part of the export) is not checked — it does not exist yet and is
    regenerated. An input whose `made_by` sidecar no longer exists is treated as a source input;
  - `--out-dir DIR` (default `replay_<YYYY-MM-DD>/` beside the script) or `--in-place`; every recorded
    output, including intermediates feeding later steps (`route.csv`), is remapped under the out dir,
    preserving its path relative to the common root of all outputs;
  - `executors.set_executor(HeadlessExecutor())`, or `--transport plugin`;
  - one call per step as Python literals (`repr`), producers first; `layer_N = server.qgis_load_layer(...)
    .layer_id` and recorded `layer_id`s replaced by those variables; evals as `server.qgis_eval(code=…)` with
    a banner comment.
- Figures without a sidecar, or with an unknown `schema`, go to `skipped`; figures with `unrecorded_state`
  are included and listed in `warnings`.
- Generated scripts use `provenance.resolve()` for `${DROPBOX_ROOT}` paths; an unset variable stops with
  "set DROPBOX_ROOT to this machine's Dropbox folder".

## 8. Paths across machines

- Record: a path under `os.environ["DROPBOX_ROOT"]` (compared case-insensitively on Windows, separators
  normalised to `/`) is stored as `${DROPBOX_ROOT}/<rest>`. Other absolute paths stay as they are and are
  listed in `machine_specific`.
- `QGIS_MCP_WORKFLOWS_PROVENANCE_ROOTS="NAME=path;NAME2=path"` adds named roots (longest prefix wins).
- If `DROPBOX_ROOT` is unset when recording, paths stay absolute and are flagged; nothing falls back to
  `~/Dropbox` (on win that is a different Dropbox).
- Privacy: sidecars still name folders and files under the root; they are local artefacts, not meant for
  public supplementary material without review.

## 9. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `QGIS_MCP_WORKFLOWS_PROVENANCE` | `1` | `0` disables sidecars and the ledger |
| `QGIS_MCP_WORKFLOWS_PROVENANCE_HASH_MAX_MB` | `256` | inputs above this get size + mtime only |
| `QGIS_MCP_WORKFLOWS_PROVENANCE_ROOTS` | (none) | extra named roots, `NAME=path;…` |
| `DROPBOX_ROOT` | per machine | the default portable root |

## 10. Testing

All CI-safe (FakeExecutor) unless marked live.

TASK-12:
- a render called through `mcp.call_tool` writes `<png>.provenance.json` with bound defaults, inputs,
  portable paths, environment; a direct Python call, a failing call and `PROVENANCE=0` write none;
- hash cap (monkeypatched small cap) gives `hashed: false`; the hash cache avoids a second read;
- `.shp` siblings and `.qgz` datasources (a hand-built `.qgz` zip in `tmp_path`) are fingerprinted;
- `made_by` set when the input has a sidecar; atlas `files` and batch manifest give one sidecar per file;
- sidecar write failure (read-only folder) logs and returns the normal result; no `.tmp` left behind;
- ledger: `render_map` after `load_layer` + two styles + eval gets load, last style, eval; `project_load`
  clears; an unseen `layer_id` lands in `unrecorded_state`;
- drift guard over every tool parameter; compound mode records `qgis_render` with `mode`.

TASK-13:
- generated script compiles; calls de-duplicated and producer-first; layer ids rebound; outputs remapped
  under `--out-dir`; `--in-place` keeps original paths;
- input check: changed sha256 / size stops with exit 2; `--force` continues; unset `DROPBOX_ROOT` stops
  with the hint;
- **round trip:** run the generated script against FakeExecutor and assert the dispatched
  `(command, params)` sequence equals the original session's, with output paths remapped;
- live (headless): render a choropleth via `mcp.call_tool`, export the session, run the script, the replayed
  PNG exists.

## 11. Delivery

1. **TASK-12** — `provenance.py` (record side), wrapper hook, ledger, drift guard, DESIGN.md §5 + CHANGELOG.
2. **TASK-13** — `qgis_export_session` tool (+ compound `qgis_export(kind="session")`), script generator,
   `check_inputs`, DESIGN.md §4 entry, SERVER_INSTRUCTIONS line.

Each is its own branch and PR; TASK-13 depends on TASK-12.

## 12. Risks

- Recording cost on hot paths: hashing is capped and cached; the `get_qgis_info` call is once per process.
- Large argument values (long `values` lists, eval code) inflate sidecars — accepted; they are what replay
  needs.
- Replay fidelity is bounded by remote tiles, Desktop-side manual edits, and QGIS version differences; each
  is surfaced in the sidecar rather than hidden.

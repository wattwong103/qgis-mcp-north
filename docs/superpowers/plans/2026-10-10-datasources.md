# Provenance — project and shapefile datasources (TASK-16) Implementation Plan

> Executed inline (superpowers:executing-plans, Native) in the session that wrote it. North asked to "go on with TASK-16 and wrap up" (2026-10-10), so this plan carries decisions and test lists rather than full code.

**Goal:** a sidecar fingerprints every local file a figure actually depends on: the `.dbf .shx .prj .cpg` siblings of a `.shp`, and the local datasources a `.qgz`/`.qgs` project references. Databases and web services are recorded as redacted `remote` entries, and anything else becomes an honest `unrecorded_state` note.

**Architecture:** a new module, `datasources.py`, with no top-level import of `provenance` (no cycle; see `ledger.py`). `discover(explicit)` takes the explicit `(argument, path)` inputs of a call and returns `(extra_inputs, notes, remote)`.
- The provenance hook adds `extra_inputs` to the implicit inputs, `notes` to `unrecorded_state` and `remote` to `remote`.
- Ledger entries (`depends_on`) keep implicit inputs too, so a replayed `qgis_project_load` checks the project's datasources.
- The replay already checks implicit inputs and dependency inputs (`source_inputs`), so a changed datasource stops a replay with exit 2.

**Spec:** `docs/superpowers/specs/2026-10-08-figure-provenance-design.md` §9 (and §5 for implicit inputs).

## Global Constraints (spec §9, verbatim where possible)

- `.shp` inputs also fingerprint existing `.dbf .shx .prj .cpg` siblings.
- A `.qgz` is a zip: read only its single `.qgs` member, at most 64 MB, never `extractall`. A `.qgs` is plain XML with the same cap. Refuse documents containing `<!DOCTYPE` or `<!ENTITY` and record that as `unrecorded_state`.
- Datasources: strip `|layername=…` suffixes. Parse `file:///…?…` (delimited text), `dbname='…'` (SQLite / GeoPackage), and paths relative to the project folder (`./`, `../`).
- Only local regular files (`S_ISREG`) under the project folder or `DROPBOX_ROOT` are fingerprinted, with `argument: "<arg>:datasource"`. UNC and `file://host/` paths are never opened. Anything unparsed or outside those roots goes to `unrecorded_state`.
- Databases and web services go to `remote`, with `password`, `user`, `apikey`, `token`, URL userinfo and query strings redacted.
- The read cap is enforced by reading at most cap+1 bytes. A zip member's declared size is not trusted.
- At most 200 datasources per project are fingerprinted; beyond that, one note.
- Notes echo at most 200 characters of any path or source.

## Review Focus

1. A zip member whose declared size is small but which inflates past the cap → refused by the read limit, not by `file_size`.
2. `file:///C:/data/x%20y.csv?type=csv` on Windows → URL-decoded, drive kept, query dropped.
3. A relative datasource that climbs out of the project folder (`../../elsewhere.gpkg`) → fingerprinted only if it lands under `DROPBOX_ROOT`; otherwise a note.
4. A datasource that is a directory or does not exist → not fingerprinted; a missing one gets a note.
5. A WMS or XYZ source with `apikey=` or `token=` in a nested URL → redacted in `remote`; nothing secret reaches the sidecar.

## Tasks

### Task 1: `datasources.py` with unit tests (`tests/test_datasources.py`)
- `shapefile_siblings(argument, path)` returns the siblings that exist as regular files, as `f"{argument}:{ext}"` pairs.
- `read_project_xml(path) -> (text | None, note | None)`. For a zip with exactly one `.qgs` member, read up to cap+1. For `.qgs`, the same cap. Refuse `<!DOCTYPE`/`<!ENTITY` (case-insensitive) and content that is not UTF-8.
- `project_sources(xml) -> list[(name, provider, source)]` reads every `maplayer` with `datasource` and `provider`. It uses `xml.etree.ElementTree`, which is safe once DOCTYPE is refused.
- `classify(provider, source, project_dir) -> ("file", path) | ("remote", redacted) | ("note", text)`:
  - ogr/gdal: strip `|...`; resolve relative paths against the project folder; network spellings give a note.
  - delimitedtext: a `file:` URL with empty netloc is unquoted, `/C:/` becomes `C:/`, the query is dropped; a non-empty netloc gives a note.
  - spatialite / sqlite-style `dbname='…'`: the file.
  - postgres / mssql / oracle / wms / wfs / wcs / arcgis* / xyz / vectortile: remote.
  - memory / virtual / unknown: note.
- `redact(source)` blanks `password|user|username|apikey|api_key|token|authcfg=…` (quoted or bare), URL userinfo and query strings, and caps the result at 200 characters.
- `discover(explicit)`:
  - shapefile siblings for `.shp` inputs;
  - project datasources for `.qgz`/`.qgs` inputs, fingerprinting files only under the project folder or `DROPBOX_ROOT` and only `S_ISREG`;
  - siblings of datasource `.shp` files too;
  - deduplication, and the 200 cap.
- **Tests:** siblings; each provider kind; `|layername`; `./` and `../`; `file:///` with percent-encoding and a drive letter; `file://host/` and UNC never opened (stat spy); outside roots → note; directory or missing → no input; DOCTYPE and ENTITY refused; cap (monkeypatched small) on `.qgs` and on an inflating zip member; zero or two `.qgs` members → note; postgres and WMS credentials redacted; dedup; the 200 cap.

### Task 2: hook integration (`provenance.py`, `session_export.py`), tests in `tests/test_provenance_hook.py`
- In the wrapper: `extra, ds_notes, ds_remote = datasources.discover(explicit)`, then `hidden += extra`. `before` must include the new paths.
- `_record` gains `extra_remote`. `notes` gains `ds_notes`.
- `_ledger_entry` keeps `prints + implicit`, so dependency inputs carry the datasources.
- `notes_for` renders a remote entry's own `note` when it has one; the basemap wording is unchanged.
- **Tests:**
  - an export of a `.qgz` that references a GeoPackage and a postgres layer: the sidecar's `implicit_inputs` include the GeoPackage (`qgz_path:datasource`), `remote` holds the postgres source with the password redacted, and `unrecorded_state` notes an out-of-root file;
  - a `.shp` input carries its siblings;
  - a replay round trip: change the GeoPackage after recording, and the script exits 2.

### Task 3: live headless test (`tests/test_replay_live.py`)
QGIS writes a `.qgz` referencing `zones.geojson` with a relative path. `qgis_batch_render` on it through MCP must produce a sidecar whose implicit inputs include `${DROPBOX_ROOT}/zones.geojson` as a `template_qgz:datasource`.

### Task 4: docs
DESIGN §5 (provenance paragraph), CHANGELOG, and the task file (status, ACs, notes).

### Task 5: verify, fresh-context reviews (code, python, security), one fix pass, PR (base main; stacked on #30 until it merges).

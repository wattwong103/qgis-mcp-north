# Panel grid figures (`qgis_compose_panels`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tool that draws `rows × cols` map panels of one polygon layer, each coloured by a CSV column on shared continuous scales with colour bars, custom fonts and line weights, exported as vector PDF/SVG or PNG — first used for PFLOW's BigData 2026 `fig_spatial`.

**Architecture:** The MCP server reads the CSV, validates arguments and computes each scale group (vmin, vmax, cap, ticks) in a pure module (`panels.py`); the QGIS plugin builds a print layout: one memory layer per panel with a precomputed per-zone colour, one locked map item per panel on a shared extent, gradient-filled colour bars with tick labels, rotated row labels and column titles, then exports. Two stacked PRs: Tasks 1–2 (scales + renderer), Tasks 3–4 (tool + docs).

**Tech Stack:** Python 3.12 server (FastMCP, pydantic), PyQGIS 3.40 print layouts in the plugin (Python 3.9 / PyQt6-safe), pytest with live headless QGIS tests.

**Spec:** `docs/superpowers/specs/2026-10-30-panel-grid-design.md`

## Global Constraints

- Plugin code (`qgis_mcp_workflows_plugin/`) parses with `ast.parse(..., feature_version=(3, 9))`; scoped Qt enums only.
- Every error ends with one `Next:` hint; argument errors are `InvalidArgumentError`, missing CSV columns `FieldNotFoundError`.
- `tick_format` is applied only as `format(value, tick_format)` — never `str.format` with caller text.
- Percentile = numpy's default linear interpolation; `cap_percentile` in (0, 100], default 100.
- Unknown palettes are refused (existing `_resolve_color_ramp` behaviour); no silent fallback.
- Defaults: `share_scale="row"`, `width_mm=160`, `height_mm=140`, `title_size_pt=9`, `label_size_pt=9`, `tick_size_pt=7`, `outline_width_mm=0.05`, `outline_color="#cccccc"`, `n_ticks=5`, `tick_format=".3g"`, `unmatched="hide"`, `dpi=300`, nodata fill `#e0e0e0`.
- Version 1.16.0 (new tool); CLAUDE.md, README.md and local AGENTS.md tool counts go to 28 (27 workflow + `qgis_eval`).

## Review Focus

1. A group whose values are all equal (e.g. all zero shares): no division by zero; every zone takes the ramp's start colour and the colour bar shows one label. → Task 1 `test_a_flat_group_has_one_tick`, Task 2 `test_a_flat_group_renders`.
2. CSV keys that match no zone (int vs `"13101.0"` from a pandas export): an error naming sample CSV and layer keys, not a blank figure. → Task 2 `test_no_matching_keys_is_an_error`.
3. `R²` and Japanese text in titles and labels render without error. → Task 2 `test_unicode_labels_render`.
4. Empty / `nan` / non-numeric CSV cells: skipped for that panel and kept out of the scale. → Task 3 `test_blank_and_nan_cells_stay_out_of_the_scale`.
5. A 1 × 1 grid works as a single map with its colour bar. → Task 3 `test_a_single_panel_works`.

---

### Task 1: Scale groups and ticks (pure)

**Files:**
- Create: `src/qgis_mcp_workflows/panels.py`
- Test: `tests/test_panels.py`

**Interfaces:**
- Produces:
  - `panel_group(index: int, rows: int, cols: int, share: str) -> int` — `row`: `index // cols`; `column`: `index % cols`; `all`: `0`.
  - `percentile(values: list[float], p: float) -> float` — numpy linear.
  - `@dataclass(frozen=True) class GroupScale: vmin: float; vmax: float; data_max: float; capped: bool; panels: tuple[int, ...]`
  - `group_scales(columns: list[list[float]], rows: int, cols: int, share: str, vmin: float | None, cap_percentile: float) -> list[GroupScale]` — `columns[i]` are panel i's numeric values; groups ordered by group index; `vmin` default `min(0, group min)`; `vmax = percentile(group values, cap_percentile)`; `capped = vmax < data_max`.
  - `ticks(scale: GroupScale, n: int, spec: str) -> list[tuple[float, str]]` — `n` evenly spaced `(position 0..1, format(value, spec))` from vmin to vmax; last label prefixed `"≥ "` when `capped`; a flat scale (`vmax == vmin`) gives `[(0.0, label)]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_panel_group_by_row_column_and_all():
    assert [panel_group(i, 2, 2, "row") for i in range(4)] == [0, 0, 1, 1]
    assert [panel_group(i, 2, 2, "column") for i in range(4)] == [0, 1, 0, 1]
    assert {panel_group(i, 2, 2, "all") for i in range(4)} == {0}

def test_percentile_matches_numpy_linear():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([0, 10], 98) == pytest.approx(9.8)
    assert percentile([5], 98) == 5

def test_row_groups_share_one_capped_scale():
    cols = [[0, 1, 2, 100], [0, 1, 2, 3], [0, 4], [0, 8]]          # 2 x 2, row-major
    a, b = group_scales(cols, 2, 2, "row", None, 98)
    assert a.panels == (0, 1) and b.panels == (2, 3)
    assert a.vmin == 0 and a.vmax == pytest.approx(percentile([0, 1, 2, 100, 0, 1, 2, 3], 98))
    assert a.capped and a.data_max == 100
    assert b.vmax == pytest.approx(percentile([0, 4, 0, 8], 98)) and b.capped

def test_vmin_defaults_to_zero_or_the_negative_minimum():
    assert group_scales([[2, 3]], 1, 1, "all", None, 100)[0].vmin == 0
    assert group_scales([[-2, 3]], 1, 1, "all", None, 100)[0].vmin == -2

def test_ticks_mark_a_capped_top():
    s = GroupScale(vmin=0.0, vmax=0.04, data_max=0.09, capped=True, panels=(0,))
    assert ticks(s, 3, ".0%") == [(0.0, "0%"), (0.5, "2%"), (1.0, "≥ 4%")]

def test_a_flat_group_has_one_tick():
    s = group_scales([[0, 0], [0, 0]], 1, 2, "row", None, 100)[0]
    assert s.vmin == s.vmax == 0 and ticks(s, 5, ".1f") == [(0.0, "0.0")]
```

- [ ] **Step 2: Run** `uv run --no-sync pytest tests/test_panels.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement the five names above in `src/qgis_mcp_workflows/panels.py`** (stdlib only).
- [ ] **Step 4: Run** `uv run --no-sync pytest tests/test_panels.py -q` — Expected: 6 passed.
- [ ] **Step 5: Commit** `feat(panels): shared scale groups and colour-bar ticks (TASK-22)`

### Task 2: Plugin renderer `compose_panels`

**Files:**
- Modify: `qgis_mcp_workflows_plugin/plugin.py` (new handler beside `compose_layout`, registered in the command table)
- Test: `tests/test_panels_live.py` (requires headless QGIS)

**Interfaces:**
- Consumes: the `groups` / `ticks` shapes from Task 1 (serialised as lists).
- Produces (dispatch command `compose_panels`, the contract Task 3 calls):

```python
def compose_panels(self, zones_path, join_field, rows, cols, panels, groups, output_path,
                   column_titles=None, row_labels=None, unmatched="hide", colorbar=True,
                   colorbar_position="right", extent=None, width_mm=160.0, height_mm=140.0,
                   font_family=None, title_size_pt=9.0, label_size_pt=9.0, tick_size_pt=7.0,
                   outline_width_mm=0.05, outline_color="#cccccc", dpi=300, **kwargs)
# panels: [{"values": {key: float}, "group": int}] row-major, len == rows * cols
# groups: [{"palette": str, "vmin": float, "vmax": float, "ticks": [[pos, label], ...], "label": str | None}]
# returns {"output_path", "format", "n_panels", "items", "page_size_mm", "fonts_missing",
#          "n_matched", "n_unmatched", "panel_boxes_mm", "colorbar_boxes_mm"}
```

Approach: one memory polygon layer per panel holding the zones (unmatched dropped for `hide`) and a `_color` text attribute set from the group ramp (`_resolve_color_ramp(palette)`) at `t = clip((v - vmin) / (vmax - vmin), 0, 1)` (`t = 0` when `vmax == vmin`; `#e0e0e0` for `nodata`); a simple fill with data-defined fill colour `"_color"` and the given outline; one `QgsLayoutItemMap` per panel with `setLayers([panel_layer])`, `setKeepLayerSet(True)` and the shared extent (union of drawn zones + 2 %, or `extent`). Colour bar: a `QgsLayoutItemShape` filled with a `QgsGradientFillSymbolLayer` on the same ramp (vertical for `right`, horizontal for `bottom`) plus one `QgsLayoutItemLabel` per tick. Text: `QgsTextFormat` with `font_family` (reported in `fonts_missing` when `QFontDatabase().families()` lacks it) and the point sizes; row labels rotated -90°. Output format from the extension (`.pdf` / `.svg` / `.png`, PNG at `dpi`); the folder is created. `items` lists `"panel:i"`, `"colorbar:g"`, `"title:c"`, `"row_label:r"`. Zero matches over all panels raises `JOIN_NO_MATCH on <join_field>` with five sample CSV and layer keys and a `Next:` hint.

- [ ] **Step 1: Write the failing live tests** (fixtures: a one-square GeoJSON with `zone_id="A"` so each panel's centre pixel is the zone fill; helper `expected(palette, t)` via `execute_code` returning `_resolve_color_ramp`'s colour; `centre_pixel(png, box_mm, dpi)`):
  - `test_each_panel_is_filled_at_its_scale_position`: 1 × 2, values `{"A": 0.0}` / `{"A": 1.0}`, one group Reds 0..1 → centre pixels `== expected("Reds", 0.0)` and `expected("Reds", 1.0)`.
  - `test_values_above_the_cap_take_the_top_colour`: value 5 with vmax 1 → `expected(palette, 1.0)`.
  - `test_unmatched_zones_hide_or_show_as_nodata`: values `{}` with a second matched zone elsewhere; `hide` → centre white `#ffffff`; `nodata` → `#e0e0e0`.
  - `test_a_flat_group_renders`: vmin == vmax == 0 → centre `== expected(palette, 0.0)`, one tick label in `items`.
  - `test_colour_bars_titles_and_labels_are_placed`: 2 × 2, two groups → `items` contains `colorbar:0`, `colorbar:1`, `title:0`, `title:1`, `row_label:0`, `row_label:1`; `len(colorbar_boxes_mm) == 2`.
  - `test_missing_fonts_are_reported`: `font_family="NoSuchFont123"` → `fonts_missing == ["NoSuchFont123"]`.
  - `test_unicode_labels_render`: row label `"Origins\nR² = 0.79"`, column title `"港区"` → output written.
  - `test_pdf_and_svg_are_vector`: `.pdf` starts with `b"%PDF"`; `.svg` contains `b"<svg"`.
  - `test_no_matching_keys_is_an_error`: values keyed `"13101.0"` vs zone `"13101"` → `ExecutorError` matching `JOIN_NO_MATCH.*Next:`.
- [ ] **Step 2: Run** `uv run --no-sync pytest tests/test_panels_live.py -q` — Expected: FAIL (unknown command).
- [ ] **Step 3: Implement `compose_panels` in `plugin.py`** as above; check `ast.parse(..., feature_version=(3, 9))`.
- [ ] **Step 4: Run** the live tests — Expected: 9 passed; then the full suite — Expected: only the known `test_add_vector_layer_returns_layer_id` lock failure.
- [ ] **Step 5: Commit** `feat(plugin): compose_panels renders a grid of linked-scale map panels (TASK-22)`; PR A (Tasks 1–2) with reviews.

### Task 3: MCP tool `qgis_compose_panels`

**Files:**
- Modify: `src/qgis_mcp_workflows/server.py` (tool + `PanelsResult`)
- Test: `tests/test_compose_panels.py` (FakeExecutor)

**Interfaces:**
- Consumes: Task 1 `panel_group`, `group_scales`, `ticks`; Task 2 dispatch contract.
- Produces:
  - `class PanelGroup(BaseModel): palette: str; vmin: float; vmax: float; data_max: float; capped: bool; panels: list[int]`
  - `class PanelsResult(ComposeLayoutResult): n_panels: int; groups: list[PanelGroup]; fonts_missing: list[str]; n_matched: int; n_unmatched: int; panel_boxes_mm: list[list[float]]; colorbar_boxes_mm: list[list[float]]` (subclassing `ComposeLayoutResult` gives provenance its written-file rule).
  - `qgis_compose_panels(zones_path, value_csv, value_fields, rows, cols, output_path, join_field="zone_id", column_titles=None, row_labels=None, share_scale="row", palettes=None, vmin=None, cap_percentile=100.0, colorbar=True, colorbar_labels=None, tick_format=".3g", n_ticks=5, unmatched="hide", extent=None, width_mm=160.0, height_mm=140.0, font_family=None, title_size_pt=9.0, label_size_pt=9.0, tick_size_pt=7.0, outline_width_mm=0.05, outline_color="#cccccc", dpi=300) -> PanelsResult`, registered with `@_maybe_tool` (`destructiveHint=False`, `idempotentHint=True`), `palettes` default `["YlOrRd"]` and a single palette applied to every group; `colorbar_position` sent as `bottom` for `column`, else `right`; CSV read like `qgis_render_choropleth` (non-numeric cells skipped per column).

- [ ] **Step 1: Write the failing tests:**
  - `test_dispatches_one_panel_per_column_with_row_scales`: 2 × 2 CSV → dispatch params: `panels[i]["values"]` from `value_fields[i]`, `panels[i]["group"] == [0, 0, 1, 1][i]`, `groups[g]["ticks"] == [list(t) for t in ticks(...)]`, `colorbar_position == "right"`.
  - `test_argument_errors_are_refused` (parametrised): `len(value_fields) != rows*cols`, `len(row_labels) != rows`, `len(column_titles) != cols`, `len(palettes) not in (1, n_groups)`, `tick_format="q"`, `tick_format="{0}"`, `cap_percentile=0`, `output_path="x.jpg"` → `InvalidArgumentError` with `Next:`.
  - `test_a_missing_csv_column_is_named`: → `FieldNotFoundError`.
  - `test_blank_and_nan_cells_stay_out_of_the_scale`: cells `""`, `"nan"`, `"x"` → absent from `values`; scale over the rest.
  - `test_a_single_panel_works`: 1 × 1 → one group, one panel.
  - `test_the_result_reports_scales_and_is_a_written_file`: `result.groups[0].capped`, and `provenance.written_files(result) == [output_path]`.
- [ ] **Step 2: Run** `uv run --no-sync pytest tests/test_compose_panels.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `PanelGroup`, `PanelsResult`, `qgis_compose_panels` in `server.py`.
- [ ] **Step 4: Run** the file — Expected: all pass; ruff clean.
- [ ] **Step 5: Commit** `feat: qgis_compose_panels tool (TASK-22)`

### Task 4: End to end, docs and release

**Files:**
- Modify: `docs/DESIGN.md` (§4 entry for `qgis_compose_panels`, status line 1.16.0), `CLAUDE.md` (tool table row, counts 27 → 28, version), `README.md` (`## Tools (28 standalone — 27 workflow + …`), `CHANGELOG.md` (v1.16.0), `pyproject.toml`, `qgis_mcp_workflows_plugin/metadata.txt`, `uv.lock` (1.16.0), local `AGENTS.md` (counts; untracked)
- Test: `tests/test_panels_live.py` (one more test)

- [ ] **Step 1: Write the failing end-to-end test** `test_fig_spatial_shape_through_the_mcp_tool`: `tests/fixtures/tiny_zones.geojson` + a CSV with `origin_sim_s, origin_obs_s, dest_sim_s, dest_obs_s` → `await server.mcp.call_tool("qgis_compose_panels", {... rows 2, cols 2, share_scale "row", palettes ["Reds", "Blues"], cap_percentile 98, row_labels ["Origins\nR² = 0.79", "Destinations\nR² = 0.69"], column_titles ["Simulated", "Observed (PT)"], tick_format ".1%", width_mm 152.4, height_mm 137.2, output .pdf})` → PDF written and its `.provenance.json` sidecar lists `zones_path` and `value_csv` inputs.
- [ ] **Step 2: Run it** — Expected: PASS once Tasks 1–3 are in (it fails before Task 3 lands).
- [ ] **Step 3: Docs and version** as listed; `tests/test_docs_consistency.py` passes with 28.
- [ ] **Step 4: Full checklist:** `uv run --no-sync pytest -q`, `uv tool run ruff check src/ tests/`, the 3.9 parse — Expected: only the known lock failure.
- [ ] **Step 5: Commit** `docs: qgis_compose_panels, release 1.16.0 (TASK-22)`; PR B with reviews.

**Acceptance with North (after merge, PFLOW side, separate task in PFLOW):** `make_fig_spatial.py` writes the four share columns per szone to a CSV (it already computes them) and the `R²` values; Claude renders `fig_spatial.pdf` through `qgis_compose_panels` with North's font and line weights; North compares it with the current matplotlib figure.

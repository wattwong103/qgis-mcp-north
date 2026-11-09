# Panel grid figures — design

Status: proposed 2026-10-30 (TASK-22). North asked for it; the design choices below are Claude's calls unless marked as North's.

## Why

A recon of GUFM and PFLOW (2026-10-30) found that every GUFM map since the Sep 21 defence, and PFLOW's paper figures, are hand-written matplotlib, mostly because the tool draws one map per image. The first target, North's choice, is the BigData 2026 camera-ready `fig_spatial` (`PFLOW/20_taxi/papers/acmsig2026/figures/make_fig_spatial.py`): a 2 × 2 grid of the 23-ward PT szones, rows origins (Reds) and destinations (Blues), columns simulated and observed, shares on one colour scale per row capped at the 98th percentile, `R²` beside each row. For the camera-ready North wants (North's words): **a colour bar, made with the tool, with custom fonts and line weights.**

## What

One new tool, `qgis_compose_panels`: a print layout with `rows × cols` map panels of one polygon layer, each panel coloured by one CSV column, exported to PDF / SVG / PNG at an exact size.

- **Data:** `zones_path` + `value_csv` joined on `join_field` (as `qgis_render_choropleth`). The caller computes the values (PFLOW keeps computing shares and `R²` through its own `pt_val` join, so figure and metrics stay one join). `value_fields` lists the CSV columns row-major.
- **Shared scales:** `share_scale` = `row` (paper default), `column` or `all`. A group's scale runs from `vmin` (default `min(0, data min)`) to the `cap_percentile` percentile of the group's values (numpy's linear interpolation; 100 = the maximum). Values above it take the top colour; the top tick then reads `≥ x`.
- **Colour:** continuous, like matplotlib's `Normalize`: each zone's colour is the group palette at `(v - vmin) / (vmax - vmin)` clipped to [0, 1]. Palettes resolve through the plugin's existing `_resolve_color_ramp` (ColorBrewer, vendored scientific maps), one per group.
- **Colour bar:** one per group, drawn from the same ramp: vertical at the right of each row (`row`, `all`), horizontal under each column (`column`); `n_ticks` evenly spaced labels formatted with a Python format spec (`tick_format`, e.g. `.1%`); optional `colorbar_labels`.
- **Text:** `column_titles` over the top row, `row_labels` rotated at the left (multi-line, e.g. `"Origins\nR² = 0.79"`).
- **Typography and weights:** one `font_family` (missing fonts are reported, never silently swapped), `title_size_pt`, `label_size_pt`, `tick_size_pt`; zone `outline_width_mm` and `outline_color`.
- **Size:** `width_mm` × `height_mm` page; PNG at `dpi`.
- **Unmatched zones:** `hide` (default; the paper draws only the 23-ward szones) or `nodata` (light grey, outside the scale).
- **Provenance:** a sidecar like every other file-writing tool, so the figure has a replay script.

## Non-goals

Mesh-grid polygons (the next task), computing values from points, difference maps, insets / overview + zoom panels, per-panel extents, legends of classes, interactive output.

## Anti-goals

A figure that silently differs from the numbers: no hidden resampling or reclassification, scales reported in the result (vmin, vmax, data max, capped), fonts that were not found are listed.

## Constraints

Plugin stays Python 3.9 / PyQt6 compatible; every error ends with a `Next:` hint; all panels share one extent so simulated and observed frame identically; `tick_format` is a format *spec* applied with `format(value, spec)`, never a template.

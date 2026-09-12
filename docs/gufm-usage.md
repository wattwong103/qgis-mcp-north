# Using qgis-mcp-workflows for GUFM figures

Primary recipes now. PFLOW paths remain in [`pflow-usage.md`](pflow-usage.md) for
historical W17 decks.

Zone id on the 23-ward layer is **JIS municipality code** `N03_007` (e.g.
`13101` = Chiyoda), exposed as `zone_id` in `assets/zones_tokyo23.gpkg`. That
is the same field as GUFM `home_zone` on persona CSVs. Mesh tokens are JIS L3
(8-digit); `assets/zones_mesh_l3.gpkg` dissolves the L4 `Tokyo23_mesh.shp`
(`KEY_CODE[:8]`).

Rebuild zone assets after a UKG shapefile update:

```bash
uv run --no-sync --extra drm scripts/build_gufm_zones.py
uv run --no-sync --extra pptx scripts/make_sekilab_blank.py
```

## 1. 23-ward choropleth

```python
qgis_render_choropleth(
    zones_path="<repo>/assets/zones_tokyo23.gpkg",
    value_csv="<repo>/assets/tokyo23_home_counts.csv",
    value_field="n_persons",
    join_field="zone_id",
    output_png="/tmp/gufm_wards.png",
    palette="gufm",
    label_field="name",
    title="GUFM home-zone persons (23 wards)",
    legend=True,
    scale_bar=True,
    north_arrow=True,
)
```

Join field is `zone_id` (= `N03_007`). A zero-match `JoinError` almost always
means the CSV still uses a different zone system (PT s-zone, L4 mesh).

## 2. Routed trajectories

GUFM `dump_trajectories_for_qgis.py` writes `trip_id,seq,lon,lat,mode,datetime`.

```python
qgis_render_trajectory(
    input_path="~/Dropbox/gufm/21_decks/2026-06-12_lab_meeting/_mapdata/gt_routed.csv",
    output_png="/tmp/gufm_traj.png",
    mode_col="mode",
    render_mode="lines",
    basemap="light",
    scale_bar=True,
    north_arrow=True,
)
```

Offline underlays (not tiles): pass `basemap_paths` with
`~/Dropbox/gufm/10_data/urban_kg/urban_data/polygon/Tokyo23.shp` and
`.../road/Tokyo_railway.shp`. Tokyo23 has no `.prj`; inspect reports unknown CRS
until `qgis_load_layer(..., crs="EPSG:4326")`.

## 3. JAXA LULC underlay / zonal stats

The 100 m JAXA raster still lives next to PFLOW
(`~/Dropbox/PFLOW/Pseudo-PFLOW/src/truck/2024JPN_v25.04_100m/2024JPN_v25.04_100m.tif`).
It is optional — GUFM maps usually use ward + railway underlays.

```python
qgis_render_choropleth(
    zones_path="<repo>/assets/zones_tokyo23.gpkg",
    value_field="n_persons",
    value_csv="<repo>/assets/tokyo23_home_counts.csv",
    join_field="zone_id",
    output_png="/tmp/gufm_lulc.png",
    basemap_paths=["~/Dropbox/PFLOW/Pseudo-PFLOW/src/truck/2024JPN_v25.04_100m/2024JPN_v25.04_100m.tif"],
)
qgis_zonal_stats(
    zones_path="<repo>/assets/zones_tokyo23.gpkg",
    raster_path=".../2024JPN_v25.04_100m.tif",
    output_path="/tmp/ward_lulc.csv",
    stats=["count", "mean"],
    prefix="lulc_",
)
```

`basemap_paths` now accepts rasters. A filename matching `lulc` / `jaxa` /
`2024jpn` gets the published 15-class palette (built-up = red, water = dark blue).

## 4. DRM routing

GUFM's matplotlib router is `gufm/scripts/figures/routing.py`. The MCP equivalent
does not copy the 57 MB cache; it reads it in place.

```python
qgis_route_on_network(
    input_csv="~/Dropbox/gufm/21_decks/2026-06-12_lab_meeting/_mapdata/hero_stops.csv",
    network_path="~/Dropbox/gufm/10_data/network_cache/drm_inner_tokyo.tsv",
    rail_network_path="~/Dropbox/gufm/10_data/network_cache/rail_inner_tokyo.tsv",
    output_csv="/tmp/gufm_hero_routed.csv",
    seq_col="seq",
    lon_col="lon",
    lat_col="lat",
    time_col="clock",   # hero_stops has clock, not datetime
    mode_col="mode",    # absent on hero_stops → default CAR
)
qgis_render_trajectory(
    input_path="/tmp/gufm_hero_routed.csv",
    output_png="/tmp/gufm_hero_routed.png",
    mode_col="mode",
    render_mode="lines",
    scale_bar=True,
    north_arrow=True,
    basemap_paths=[
        "~/Dropbox/gufm/10_data/urban_kg/urban_data/polygon/Tokyo23.shp",
        "~/Dropbox/gufm/10_data/urban_kg/urban_data/road/Tokyo_railway.shp",
    ],
)
```

Needs `uv sync --extra network` once. First call loads ~289k DRM links (~a few
seconds) and caches them for the rest of the process.

`qgis_render_link_density` is still the PFLOW national-DRM tool (needs
`scripts/build_drm_network.py`). Routing here is **stop → polyline**, not
link-density choropleths.

## 5. Lab deck

`qgis_figures_to_pptx` defaults to `assets/sekilab_blank.pptx` (Sekimoto-lab
widescreen, empty). Pass `template_pptx=` only to append to an existing deck —
do not point it at `Seki_Lab_Template_latest.pptx` (that file is 17 content
slides, one layout named DEFAULT).

## KSJ / DRM on disk (not copied into this repo)

| Path | Use |
|---|---|
| `gufm/10_data/urban_kg/urban_data/polygon/Tokyo23.shp` | 23 wards, `N03_007` |
| `gufm/10_data/urban_kg/urban_data/polygon/Tokyo23_mesh.shp` | L4 mesh, dissolve to L3 |
| `gufm/10_data/urban_kg/urban_data/road/Tokyo_railway.shp` | rail underlay |
| `gufm/10_data/urban_kg/urban_data/road/DRM_Tokyo.shp` | Tokyo DRM (large) |
| `gufm/10_data/ksj/extracted/N02-22_RailroadSection.geojson` | national rail sections |
| `gufm/10_data/ksj/UTF-8/N02-22_Station.geojson` | stations |

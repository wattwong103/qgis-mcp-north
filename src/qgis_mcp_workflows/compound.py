"""Compound-mode tools — grouped wrappers over the standalone workflow tools.

Activated by ``QGIS_MCP_WORKFLOWS_TOOL_MODE=compound``. FastMCP then exposes
``qgis_inspect`` / ``qgis_style`` / ``qgis_render`` / ``qgis_export`` instead of
the fine-grained tools. ``qgis_eval``, ``qgis_ping``, and ``qgis_diagnose``
register in both modes.

The compound wrappers do NOT reimplement logic — they import the standalone tool
functions from ``server`` and dispatch on a discriminator arg.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from qgis_mcp_workflows.server import (
    AtlasExportResult,
    BasemapCatalogResult,
    BatchRenderResult,
    CatchmentResult,
    ChoroplethResult,
    ComposeLayoutResult,
    DiagramMapResult,
    DuckDbRenderResult,
    ExportResult,
    GraduatedStyleResult,
    LayerInfo,
    LinkDensityResult,
    LoadedLayer,
    ODFlowResult,
    PptxResult,
    ProjectInfo,
    RenderResult,
    StyleResult,
    TrajectoryResult,
    _maybe_compound_tool,
    qgis_batch_render,
    qgis_compose_layout,
    qgis_export_atlas,
    qgis_export_layout,
    qgis_figures_to_pptx,
    qgis_layer_inspect,
    qgis_list_basemaps,
    qgis_load_layer,
    qgis_project_load,
    qgis_render_catchment,
    qgis_render_choropleth,
    qgis_render_diagram_map,
    qgis_render_from_duckdb,
    qgis_render_link_density,
    qgis_render_map,
    qgis_render_od_flows,
    qgis_render_trajectory,
    qgis_style_categorized,
    qgis_style_graduated,
)

# ---------------------------------------------------------------------------
# qgis_inspect — replaces qgis_layer_inspect / qgis_load_layer / qgis_project_load
# ---------------------------------------------------------------------------


@_maybe_compound_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=False, destructiveHint=False, openWorldHint=True
    )
)
def qgis_inspect(
    kind: Annotated[Literal["layer", "project", "basemaps"], Field(description='"layer" for a vector/raster file, "project" for a .qgz/.qgs, "basemaps" lists tile sources.')],
    path: Annotated[str, Field(description="Absolute path to the file. Unused when kind='basemaps'.")] = "",
    register: Annotated[bool, Field(description="kind='layer' only: True keeps the layer loaded (mutates project) and returns layer_id; False loads transiently for metadata only.")] = False,
    name: Annotated[str | None, Field(description="kind='layer' + register=True only: optional display name.")] = None,
    crs: Annotated[str | None, Field(description='kind=\'layer\' + register=True only: override CRS, e.g. "EPSG:4326".')] = None,
) -> LayerInfo | LoadedLayer | ProjectInfo | BasemapCatalogResult:
    """Inspect or load a layer/project — compound replacement for the 3 inspection tools.

    Dispatch:
    - kind="layer" + register=False → metadata-only inspect (no project mutation)
    - kind="layer" + register=True  → register layer + return layer_id
    - kind="project"                → load .qgz/.qgs + return layers + layouts

    When to use: every read-only or layer/project-loading step in a pipeline.
    """
    if kind == "layer":
        if not path:
            raise ValueError('qgis_inspect(kind="layer") requires path.')
        if register:
            return qgis_load_layer(path=path, name=name, crs=crs)
        return qgis_layer_inspect(path=path)
    if kind == "project":
        if not path:
            raise ValueError('qgis_inspect(kind="project") requires path.')
        return qgis_project_load(qgz_path=path)
    if kind == "basemaps":
        return qgis_list_basemaps()
    raise ValueError(f"Unknown kind: {kind!r}")


# ---------------------------------------------------------------------------
# qgis_style — replaces qgis_style_categorized / qgis_style_graduated
# ---------------------------------------------------------------------------


@_maybe_compound_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=False
    )
)
def qgis_style(
    type: Annotated[Literal["categorized", "graduated"], Field(description='"categorized" for one-color-per-value, "graduated" for value-binned ramp.')],
    layer_id: Annotated[str, Field(description="layer_id from qgis_inspect(register=True) or qgis_inspect(kind='project').")],
    field: Annotated[str, Field(description="Field name to style on.")],
    palette: Annotated[str, Field(description='ColorBrewer palette name, e.g. "Set2" for categorized, "YlOrRd" for graduated.')] = "Spectral",
    n_classes: Annotated[int, Field(description="graduated only: number of bins.", ge=2, le=15)] = 5,
    mode: Annotated[Literal["quantile", "equal_interval", "natural_breaks", "pretty"], Field(description="graduated only: binning strategy.")] = "quantile",
    classes: Annotated[list[str] | None, Field(description="categorized only: subset/order of category values.")] = None,
    diverging: Annotated[bool, Field(description="graduated only: symmetric breaks around center.")] = False,
    center: Annotated[float, Field(description="graduated only: neutral midpoint when diverging.")] = 0.0,
) -> StyleResult | GraduatedStyleResult:
    """Apply categorical or graduated symbology — compound replacement for the 2 styling tools.

    Dispatch:
    - type="categorized" → qgis_style_categorized (palette, classes)
    - type="graduated"   → qgis_style_graduated (palette, n_classes, mode)
    """
    if type == "categorized":
        return qgis_style_categorized(
            layer_id=layer_id, field=field, palette=palette, classes=classes
        )
    if type == "graduated":
        return qgis_style_graduated(
            layer_id=layer_id, field=field, n_classes=n_classes, mode=mode, palette=palette,
            diverging=diverging, center=center,
        )
    raise ValueError(f"Unknown type: {type!r}")


# ---------------------------------------------------------------------------
# qgis_render — replaces the 4 render tools
# ---------------------------------------------------------------------------


@_maybe_compound_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render(
    mode: Annotated[Literal["map", "choropleth", "trajectory", "od_flows", "link_density", "diagram_map", "catchment", "duckdb"], Field(description="Render mode.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    layer_ids: Annotated[list[str] | None, Field(description='mode="map" only: layer_ids to render bottom-to-top.')] = None,
    extent: Annotated[list[float] | None, Field(description="[xmin, ymin, xmax, ymax] where the mode accepts an extent.")] = None,
    background: Annotated[str, Field(description="map: background color.")] = "white",
    zones_path: Annotated[str | None, Field(description="choropleth/od_flows: polygon zones file.")] = None,
    value_field: Annotated[str | None, Field(description="choropleth/duckdb: numeric column to render.")] = None,
    value_csv: Annotated[str | None, Field(description="choropleth: optional CSV joined to zones_path.")] = None,
    join_field: Annotated[str, Field(description="choropleth: join column on both sides.")] = "zone_id",
    n_classes: Annotated[int, Field(description="graduated bins.", ge=2, le=15)] = 5,
    classification_mode: Annotated[Literal["quantile", "equal_interval", "natural_breaks", "pretty"], Field(description="Binning strategy.")] = "quantile",
    palette: Annotated[str, Field(description="Color ramp.")] = "YlOrRd",
    title: Annotated[str | None, Field(description="choropleth title.")] = None,
    legend: Annotated[bool, Field(description="choropleth legend.")] = True,
    diverging: Annotated[bool, Field(description="choropleth/duckdb: symmetric breaks around center.")] = False,
    center: Annotated[float, Field(description="Neutral midpoint when diverging.")] = 0.0,
    label_field: Annotated[str | None, Field(description="choropleth: haloed polygon label field.")] = None,
    input_path: Annotated[str | None, Field(description="trajectory: CSV or GPX path.")] = None,
    lon_col: Annotated[str, Field(description="trajectory/duckdb lon col.")] = "lon",
    lat_col: Annotated[str, Field(description="trajectory/duckdb lat col.")] = "lat",
    time_col: Annotated[str, Field(description="trajectory time col.")] = "datetime",
    id_col: Annotated[str, Field(description="trajectory grouping col.")] = "trip_id",
    mode_col: Annotated[str | None, Field(description="trajectory categorical color column.")] = None,
    render_mode: Annotated[Literal["lines", "points", "heatmap"], Field(description="trajectory visualization style.")] = "lines",
    sample_rate: Annotated[float, Field(description="trajectory sample_rate.", gt=0.0, le=1.0)] = 1.0,
    max_points: Annotated[int, Field(description="trajectory hard cap.", ge=1000)] = 500_000,
    od_csv: Annotated[str | None, Field(description="od_flows: OD CSV.")] = None,
    origin_col: Annotated[str, Field(description="od_flows origin col.")] = "origin",
    dest_col: Annotated[str, Field(description="od_flows destination col.")] = "destination",
    value_col: Annotated[str, Field(description="od_flows/link_density magnitude col.")] = "trip_count",
    zone_id_field: Annotated[str, Field(description="od_flows zone-id field on zones layer.")] = "zone_id",
    top_n: Annotated[int | None, Field(description="od_flows/link_density top-N filter.")] = None,
    arc_style: Annotated[Literal["line", "arrow", "curved"], Field(description="od_flows arc rendering.")] = "line",
    trajectory_csvs: Annotated[list[str] | None, Field(description="link_density: trajectory CSV paths.")] = None,
    load_csv: Annotated[str | None, Field(description="link_density: pre-aggregated link_id,volume CSV from qgis_assign_section_load.")] = None,
    drm_network_path: Annotated[str | None, Field(description="link_density: DRM GeoPackage path.")] = None,
    link_id_col: Annotated[str, Field(description="link_density join column.")] = "link_id",
    aggregation: Annotated[Literal["count", "sum"], Field(description="link_density aggregation.")] = "count",
    min_density: Annotated[float, Field(description="link_density floor.", ge=0.0)] = 1.0,
    layer_path: Annotated[str | None, Field(description="diagram_map: vector layer path.")] = None,
    value_fields: Annotated[list[str] | None, Field(description="diagram_map: numeric fields to chart.")] = None,
    diagram_type: Annotated[Literal["pie", "bar"], Field(description="diagram_map chart type.")] = "pie",
    size: Annotated[float, Field(description="diagram_map size in mm.", gt=0.0, le=80.0)] = 10.0,
    points_path: Annotated[str | None, Field(description="catchment: point layer path.")] = None,
    method: Annotated[Literal["voronoi"], Field(description="catchment method.")] = "voronoi",
    db_path: Annotated[str | None, Field(description="duckdb: database file.")] = None,
    query: Annotated[str | None, Field(description="duckdb: SELECT returning one row per feature.")] = None,
    geometry_column: Annotated[str | None, Field(description="duckdb: WKT geometry column.")] = None,
    lon_column: Annotated[str | None, Field(description="duckdb: longitude column.")] = None,
    lat_column: Annotated[str | None, Field(description="duckdb: latitude column.")] = None,
    max_features: Annotated[int, Field(description="duckdb row ceiling.", ge=1, le=500000)] = 50000,
    crs: Annotated[str, Field(description="duckdb result CRS.")] = "EPSG:4326",
    basemap_paths: Annotated[list[str] | None, Field(description="Optional vector basemap layers.")] = None,
    basemap: Annotated[str, Field(description='Tile basemap: "none", a preset, or "qms:<id>".')] = "none",
    basemap_opacity: Annotated[float, Field(description="Tile basemap opacity.", ge=0.0, le=1.0)] = 1.0,
    width: Annotated[int, Field(description="Image width.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
    scale_bar: Annotated[bool, Field(description="Draw a metric scale bar on the PNG.")] = False,
    north_arrow: Annotated[bool, Field(description="Draw a north arrow on the PNG.")] = False,
) -> RenderResult | ChoroplethResult | TrajectoryResult | ODFlowResult | LinkDensityResult | DiagramMapResult | CatchmentResult | DuckDbRenderResult:
    """Render any figure type — compound replacement for the standalone render tools."""
    if mode == "map":
        if not layer_ids:
            raise ValueError('qgis_render(mode="map") requires layer_ids.')
        return qgis_render_map(
            layer_ids=layer_ids, output_png=output_png, width=width, height=height,
            dpi=dpi, extent=extent, background=background,
            scale_bar=scale_bar, north_arrow=north_arrow,
        )
    if mode == "choropleth":
        if not zones_path or not value_field:
            raise ValueError('qgis_render(mode="choropleth") requires zones_path and value_field.')
        return qgis_render_choropleth(
            zones_path=zones_path, value_field=value_field, output_png=output_png,
            value_csv=value_csv, join_field=join_field, n_classes=n_classes,
            mode=classification_mode, palette=palette, title=title, legend=legend,
            diverging=diverging, center=center, label_field=label_field,
            basemap_paths=basemap_paths, basemap=basemap, basemap_opacity=basemap_opacity,
            width=width, height=height, dpi=dpi,
            scale_bar=scale_bar, north_arrow=north_arrow,
        )
    if mode == "trajectory":
        if not input_path:
            raise ValueError('qgis_render(mode="trajectory") requires input_path.')
        return qgis_render_trajectory(
            input_path=input_path, output_png=output_png, lon_col=lon_col,
            lat_col=lat_col, time_col=time_col, id_col=id_col, mode_col=mode_col,
            render_mode=render_mode, sample_rate=sample_rate, max_points=max_points,
            basemap_paths=basemap_paths, basemap=basemap, basemap_opacity=basemap_opacity,
            extent=extent, width=width, height=height, dpi=dpi,
            scale_bar=scale_bar, north_arrow=north_arrow,
        )
    if mode == "od_flows":
        if not od_csv or not zones_path:
            raise ValueError('qgis_render(mode="od_flows") requires od_csv and zones_path.')
        return qgis_render_od_flows(
            od_csv=od_csv, zones_layer_path=zones_path, output_png=output_png,
            origin_col=origin_col, dest_col=dest_col, value_col=value_col,
            zone_id_field=zone_id_field, top_n=top_n, arc_style=arc_style,
            basemap_paths=basemap_paths, basemap=basemap, basemap_opacity=basemap_opacity,
            label_field=label_field,
            width=width, height=height, dpi=dpi,
            scale_bar=scale_bar, north_arrow=north_arrow,
        )
    if mode == "link_density":
        if not drm_network_path or not (trajectory_csvs or load_csv):
            raise ValueError(
                'qgis_render(mode="link_density") requires drm_network_path and '
                "trajectory_csvs or load_csv."
            )
        return qgis_render_link_density(
            trajectory_csvs=trajectory_csvs, load_csv=load_csv,
            drm_network_path=drm_network_path,
            output_png=output_png, link_id_col=link_id_col, aggregation=aggregation,
            value_col=value_col if aggregation == "sum" else None,
            n_classes=n_classes, mode=classification_mode, palette=palette,
            min_density=min_density, top_n=top_n, extent=extent,
            basemap_paths=basemap_paths, basemap=basemap, basemap_opacity=basemap_opacity,
            label_field=label_field,
            width=width, height=height, dpi=dpi,
            scale_bar=scale_bar, north_arrow=north_arrow,
        )
    if mode == "diagram_map":
        if not layer_path or not value_fields:
            raise ValueError('qgis_render(mode="diagram_map") requires layer_path and value_fields.')
        return qgis_render_diagram_map(
            layer_path=layer_path, value_fields=value_fields, output_png=output_png,
            diagram_type=diagram_type, size=size, palette=palette, extent=extent,
            basemap=basemap, basemap_opacity=basemap_opacity,
            width=width, height=height, dpi=dpi,
        )
    if mode == "catchment":
        if not points_path:
            raise ValueError('qgis_render(mode="catchment") requires points_path.')
        return qgis_render_catchment(
            points_path=points_path, output_png=output_png, method=method, extent=extent,
            basemap=basemap, basemap_opacity=basemap_opacity,
            width=width, height=height, dpi=dpi,
        )
    if mode == "duckdb":
        if not db_path or not query:
            raise ValueError('qgis_render(mode="duckdb") requires db_path and query.')
        return qgis_render_from_duckdb(
            db_path=db_path, query=query, output_png=output_png,
            geometry_column=geometry_column, lon_column=lon_column, lat_column=lat_column,
            value_field=value_field, crs=crs, n_classes=n_classes, mode=classification_mode,
            palette=palette, diverging=diverging, center=center, max_features=max_features,
            basemap=basemap, basemap_opacity=basemap_opacity,
            width=width, height=height, dpi=dpi,
        )
    raise ValueError(f"Unknown mode: {mode!r}")


# ---------------------------------------------------------------------------
# qgis_export — replaces qgis_export_layout / qgis_batch_render / qgis_figures_to_pptx
# ---------------------------------------------------------------------------


@_maybe_compound_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_export(
    kind: Annotated[Literal["layout", "batch", "pptx", "compose_layout", "atlas"], Field(description='"layout" one print composer page, "atlas" every atlas page, "batch" fans out per attribute, "pptx" assembles slides, "compose_layout" builds a print layout from layers.')],
    output_path: Annotated[str | None, Field(description='Output file path for kind in {"layout","pptx","compose_layout"}.')] = None,
    qgz_path: Annotated[str | None, Field(description='kind="layout": project file.')] = None,
    layout_name: Annotated[str | None, Field(description='kind in {"layout","batch"}: print-composer layout name.')] = None,
    format: Annotated[Literal["png", "pdf", "svg"], Field(description='kind="layout" only: output format.')] = "png",
    dpi: Annotated[int, Field(description="Export DPI.", ge=72, le=600)] = 300,
    template_qgz: Annotated[str | None, Field(description='kind="batch": template project.')] = None,
    attribute: Annotated[str | None, Field(description='kind="batch": field on the active layer to filter by.')] = None,
    values: Annotated[list[str] | None, Field(description='kind="batch": filter values to iterate.')] = None,
    output_dir: Annotated[str | None, Field(description='kind="batch": output directory.')] = None,
    filename_template: Annotated[str, Field(description='kind="batch" filename template.')] = "{value}.png",
    figure_paths: Annotated[list[str] | None, Field(description='kind="pptx": PNG/JPG paths to add as slides.')] = None,
    layout: Annotated[Literal["title_and_image", "image_only", "two_column", "title_image_caption"], Field(description='kind="pptx": per-slide layout.')] = "title_and_image",
    captions: Annotated[list[str] | None, Field(description='kind="pptx": per-slide captions.')] = None,
    template_pptx: Annotated[str | None, Field(description='kind="pptx": template deck to append to.')] = None,
    layer_paths: Annotated[list[str] | None, Field(description='kind="compose_layout": layers bottom-to-top.')] = None,
    title: Annotated[str | None, Field(description='kind="compose_layout": page title.')] = None,
    extent: Annotated[list[float] | None, Field(description='kind="compose_layout": map extent.')] = None,
    page: Annotated[Literal["a4_landscape", "a4_portrait", "a3_landscape", "square"], Field(description='kind="compose_layout": page size.')] = "a4_landscape",
    legend: Annotated[bool, Field(description='kind="compose_layout": add a legend.')] = True,
    scale_bar: Annotated[bool, Field(description='kind="compose_layout": add a scale bar.')] = True,
    north_arrow: Annotated[bool, Field(description='kind="compose_layout": add a north arrow.')] = True,
) -> ExportResult | BatchRenderResult | PptxResult | ComposeLayoutResult | AtlasExportResult:
    """Export / batch-render / compose / deliver-as-pptx."""
    if kind == "layout":
        if not qgz_path or not layout_name or not output_path:
            raise ValueError('qgis_export(kind="layout") requires qgz_path, layout_name, output_path.')
        return qgis_export_layout(
            qgz_path=qgz_path, layout_name=layout_name, output_path=output_path,
            format=format, dpi=dpi,
        )
    if kind == "batch":
        if not template_qgz or not attribute or values is None or not output_dir:
            raise ValueError('qgis_export(kind="batch") requires template_qgz, attribute, values, output_dir.')
        return qgis_batch_render(
            template_qgz=template_qgz, attribute=attribute, values=values,
            output_dir=output_dir, layout_name=layout_name,
            filename_template=filename_template,
        )
    if kind == "pptx":
        if not figure_paths or not output_path:
            raise ValueError('qgis_export(kind="pptx") requires figure_paths and output_path.')
        return qgis_figures_to_pptx(
            figure_paths=figure_paths, pptx_path=output_path, layout=layout,
            captions=captions, template_pptx=template_pptx,
        )
    if kind == "compose_layout":
        if not layer_paths or not output_path:
            raise ValueError(
                'qgis_export(kind="compose_layout") requires layer_paths and output_path.'
            )
        return qgis_compose_layout(
            layer_paths=layer_paths, output_path=output_path, title=title, extent=extent,
            page=page, legend=legend, scale_bar=scale_bar, north_arrow=north_arrow, dpi=dpi,
        )
    if kind == "atlas":
        if not qgz_path or not layout_name or not output_dir:
            raise ValueError(
                'qgis_export(kind="atlas") requires qgz_path, layout_name, output_dir.'
            )
        return qgis_export_atlas(
            qgz_path=qgz_path, layout_name=layout_name, output_dir=output_dir,
            format=format if format in ("png", "pdf", "jpg") else "png", dpi=dpi,
        )
    raise ValueError(f"Unknown kind: {kind!r}")

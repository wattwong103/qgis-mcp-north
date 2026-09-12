#!/usr/bin/env python3
"""qgis-mcp-workflows — focused QGIS MCP server for transportation research figures.

Forked from nkarasiak/qgis-mcp. Workflow tools plus ``qgis_eval`` (not a
PyQGIS-primitive dump). See ``docs/DESIGN.md`` for the tool surface, response
shapes, error taxonomy, and roadmap. The tool count is guarded by
``tests/test_docs_consistency.py`` — do not restate it here.

Default socket port: 9877 (vs upstream nkarasiak/qgis-mcp on 9876). Both servers
can run side-by-side; the LLM picks per request based on tool descriptions.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Annotated, Literal

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # mcp >= 2.0 renamed fastmcp -> mcpserver
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except Exception as _mcp_exc:
        sys.stderr.write(
            "qgis-mcp-workflows: cannot import the MCP SDK "
            f"({type(_mcp_exc).__name__}: {_mcp_exc}). "
            "If this repo lives in Dropbox, .venv is often incomplete — run: uv sync\n"
        )
        raise
except Exception as _mcp_exc:
    sys.stderr.write(
        "qgis-mcp-workflows: cannot import the MCP SDK "
        f"({type(_mcp_exc).__name__}: {_mcp_exc}). "
        "If this repo lives in Dropbox, .venv is often incomplete — run: uv sync\n"
    )
    raise

from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from qgis_mcp_workflows.helpers import with_png_preview

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _setup_logging() -> logging.Logger:
    """stderr (WARNING+) plus optional rotating file handler."""
    log = logging.getLogger("QgisMcpWorkflowsServer")
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(fmt)
    log.addHandler(stderr_handler)

    default_log_file = os.path.join("~", ".local", "share", "qgis-mcp-workflows", "server.log")
    log_file_raw = os.environ.get("QGIS_MCP_WORKFLOWS_LOG_FILE", default_log_file)
    log_level_name = os.environ.get("QGIS_MCP_WORKFLOWS_LOG_LEVEL", "INFO").upper()
    file_level = getattr(logging, log_level_name, logging.INFO)

    if log_file_raw:
        log_file = os.path.expanduser(log_file_raw)
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        file_handler.setLevel(file_level)
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
        log.setLevel(min(logging.WARNING, file_level))
    else:
        log.setLevel(logging.WARNING)
    return log


logger = _setup_logging()


# ---------------------------------------------------------------------------
# Optional movingpandas — enables speed-binned trajectory rendering when
# installed via `uv sync --extra trajectory`. Detected once at module load;
# tests patch this attribute to exercise both code paths.
# ---------------------------------------------------------------------------

try:
    import movingpandas as _mp  # noqa: F401

    _HAS_MP = True
except Exception:  # ImportError or any movingpandas init failure
    _HAS_MP = False


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

SERVER_INSTRUCTIONS = """\
qgis-mcp-workflows — opinionated QGIS MCP for transportation research figure
pipelines (PFLOW, GUFM). First call qgis_ping / qgis_diagnose. Then:

- qgis_layer_inspect before any join/render so field names are real.
- qgis_spatial_join to copy attributes by location (points in zones);
  qgis_zonal_stats for raster-per-polygon (JAXA LULC onto zones).
- qgis_render_choropleth for zone polygons + value CSV (pass scale_bar=True
  and north_arrow=True for a publication PNG). GUFM 23-ward polygons:
  assets/zones_tokyo23.gpkg, join_field=zone_id (JIS N03_007).
- qgis_render_trajectory for GUFM routed CSV (trip_id, seq, lon, lat, mode,
  datetime) or PFLOW CSV; use sample_rate on large files; basemap="light".
- qgis_route_on_network to snap GUFM stops onto DRM/rail (Tokyo TSV cache),
  then qgis_render_trajectory on the routed CSV.
- qgis_render_od_flows for OD CSV + zones; qgis_assign_section_load then
  qgis_render_link_density(load_csv=...) for network section loads.
- qgis_compose_layout when you need a legend/scale bar as a print layout;
  qgis_figures_to_pptx to drop PNGs into a weekly deck.
- qgis_eval only as an escape hatch.

If you need feature editing, Processing algorithms, or layer-tree groups,
use upstream nkarasiak/qgis-mcp side-by-side (port 9876).
"""

mcp = FastMCP("qgis-mcp-workflows", instructions=SERVER_INSTRUCTIONS)


@mcp.prompt(title="Choropleth from CSV", description="Zone polygons + value CSV → publication PNG.")
def prompt_choropleth() -> str:
    return (
        "Call qgis_layer_inspect on the polygon layer first. Then "
        "qgis_render_choropleth(zones_path, value_field, output_png, value_csv=..., "
        "join_field=..., palette='YlOrRd', scale_bar=True, north_arrow=True). "
        "If n_matched is 0, the join field is wrong — inspect both sides and retry."
    )


@mcp.prompt(title="W17 weekly figures", description="Choropleth + trajectory + OD → pptx.")
def prompt_w17() -> str:
    return (
        "Produce a 3-slide deck: (1) qgis_render_choropleth of zone totals, "
        "(2) qgis_render_trajectory heatmap with sample_rate if the CSV is large, "
        "(3) qgis_render_od_flows of the strongest flows (top_n=100). "
        "Pass scale_bar=True, north_arrow=True on each render. "
        "Finish with qgis_figures_to_pptx(layout='title_image_caption')."
    )


@mcp.prompt(title="Section load map", description="OD matrix → network volumes → graduated links.")
def prompt_section_load() -> str:
    return (
        "qgis_assign_section_load(od_csv, network_path, output_csv, zones_path=...) "
        "then qgis_render_link_density(drm_network_path=network_path, load_csv=output_csv, "
        "output_png=..., scale_bar=True, north_arrow=True). "
        "Needs `uv sync --extra network` once."
    )


@mcp.prompt(title="Atlas export", description="Export every atlas page from a print layout.")
def prompt_atlas() -> str:
    return (
        "qgis_project_load(qgz_path) lists layouts. If a layout has atlas_enabled, "
        "call qgis_export_atlas(qgz_path, layout_name, output_dir, format='png'). "
        "If atlas is off, use qgis_export_layout or qgis_batch_render instead."
    )


@mcp.prompt(title="Spatial join", description="Copy attributes by location into a GeoPackage.")
def prompt_spatial_join() -> str:
    return (
        "qgis_layer_inspect both layers first (crs + extent must overlap). Then "
        "qgis_spatial_join(target_path, join_path, output_path, predicate='intersects', "
        "method='one_to_one'). Use method='one_to_many' when one target should keep "
        "every matching join feature. Then qgis_render_choropleth or qgis_style_categorized "
        "on the written GeoPackage."
    )


@mcp.prompt(title="GUFM ward choropleth", description="Tokyo 23-ward polygons + home_zone counts.")
def prompt_gufm_wards() -> str:
    return (
        "qgis_layer_inspect assets/zones_tokyo23.gpkg (join_field is zone_id = JIS N03_007). "
        "Then qgis_render_choropleth(zones_path=..., value_csv=..., value_field='n_persons', "
        "join_field='zone_id', palette='gufm', scale_bar=True, north_arrow=True). "
        "Trajectory CSVs from dump_trajectories_for_qgis.py use mode_col='mode'."
    )


@mcp.prompt(title="GUFM DRM routing", description="Snap stop sequences onto Tokyo DRM / rail.")
def prompt_gufm_route() -> str:
    return (
        "qgis_route_on_network(input_csv=hero_stops.csv, "
        "network_path=~/Dropbox/gufm/10_data/network_cache/drm_inner_tokyo.tsv, "
        "output_csv=/tmp/routed.csv, lon_col='lon', lat_col='lat', seq_col='seq', "
        "rail_network_path=~/Dropbox/gufm/10_data/network_cache/rail_inner_tokyo.tsv). "
        "Then qgis_render_trajectory(input_path=output_csv, mode_col='mode', "
        "scale_bar=True, north_arrow=True). Needs `uv sync --extra network`."
    )


@mcp.prompt(title="Zonal stats", description="Raster statistics per polygon (JAXA LULC, DEM).")
def prompt_zonal_stats() -> str:
    return (
        "qgis_zonal_stats(zones_path, raster_path, output_path, "
        "stats=['count','mean','sum'], prefix=''). output_path may be .gpkg or .csv. "
        "Then qgis_render_choropleth(zones_path=output_path, value_field=<prefix>mean)."
    )


@mcp.resource(
    "qgis://status",
    title="QGIS stack status",
    description="Plugin/server health, versions, project layer count.",
    mime_type="application/json",
)
def resource_status() -> str:
    import json

    try:
        return qgis_diagnose().model_dump_json()
    except Exception as err:
        return json.dumps({"status": "error", "error": str(err)})


@mcp.resource(
    "qgis://project",
    title="Loaded QGIS project",
    description="Filename, CRS, and a short layer list from the active project.",
    mime_type="application/json",
)
def resource_project() -> str:
    import json

    from qgis_mcp_workflows.executors import get_executor

    try:
        return json.dumps(get_executor().dispatch("get_project_info", {}))
    except Exception as err:
        return json.dumps({"status": "error", "error": str(err)})


@mcp.resource(
    "qgis://basemaps",
    title="Available tile basemaps",
    description="Presets and QuickMapServices ids for basemap=.",
    mime_type="application/json",
)
def resource_basemaps() -> str:
    import json

    try:
        return qgis_list_basemaps().model_dump_json()
    except Exception as err:
        return json.dumps({"status": "error", "error": str(err)})


# ---------------------------------------------------------------------------
# Tool registration mode — full vs compound (5 grouped tools).
#
# Read at module load. Tests patch this attribute to verify both surfaces.
# Compound mode collapses the surface to qgis_inspect / qgis_style / qgis_render /
# qgis_export / qgis_eval for token-constrained LLMs (Haiku, small open-weights).
# ---------------------------------------------------------------------------

TOOL_MODE = os.environ.get("QGIS_MCP_WORKFLOWS_TOOL_MODE", "full").lower()
if TOOL_MODE not in ("full", "compound"):
    logger.warning("Unknown QGIS_MCP_WORKFLOWS_TOOL_MODE=%r; defaulting to 'full'", TOOL_MODE)
    TOOL_MODE = "full"


def _maybe_tool(*args, **kwargs):
    """Register with FastMCP in full mode; keep the original callable on the module.

    PNG preview is applied only to the registered copy so Python callers
    (tests, ``scripts/demo_w17.py``) still get the Pydantic model.
    """

    def deco(f):
        if TOOL_MODE == "full":
            mcp.tool(*args, **kwargs)(with_png_preview(f))
        return f

    return deco


def _maybe_compound_tool(*args, **kwargs):
    """Register compound tools with FastMCP; keep the original callable on the module."""

    def deco(f):
        if TOOL_MODE == "compound":
            mcp.tool(*args, **kwargs)(with_png_preview(f))
        return f

    return deco


def _register_compound_tools_if_enabled() -> None:
    """Import compound.py at module-load tail to trigger _maybe_compound_tool decorators.

    Imports are conditional: only fire when TOOL_MODE='compound' to avoid pulling
    in compound.py's dependencies (which import every standalone tool) on full-mode
    cold-starts. Tests patch TOOL_MODE and reimport for both surfaces.
    """
    if TOOL_MODE == "compound":
        from qgis_mcp_workflows import compound  # noqa: F401  — import for side effects


def _executor_transport_name() -> str:
    """plugin / headless / fake — derived from the active executor class."""
    from qgis_mcp_workflows.executors import get_executor

    name = type(get_executor()).__name__
    mapping = {"PluginExecutor": "plugin", "HeadlessExecutor": "headless"}
    if name in mapping:
        return mapping[name]
    stem = name[:-8] if name.endswith("Executor") else name
    return stem.lower() or "unknown"


# ---------------------------------------------------------------------------
# Pydantic response models — one per tool; see DESIGN.md §4 for field rationale
# ---------------------------------------------------------------------------


class FieldInfo(BaseModel):
    name: str
    type: str
    n_unique: int | None = None


class LayerInfo(BaseModel):
    """Read-only metadata about a vector or raster file on disk."""

    path: str
    geometry_type: Literal["point", "line", "polygon", "raster", "no_geom"]
    crs: str
    n_features: int
    extent: list[float] = Field(..., description="[xmin, ymin, xmax, ymax]")
    fields: list[FieldInfo]


class LoadedLayer(LayerInfo):
    """Layer that has been added to the active QGIS project."""

    layer_id: str


class LayerSummary(BaseModel):
    """Compact summary of a layer registered in a project."""

    layer_id: str
    name: str
    geometry_type: str
    visible: bool


class LayoutSummary(BaseModel):
    name: str


class ProjectInfo(BaseModel):
    project_path: str
    crs: str
    extent: list[float]
    layers: list[LayerSummary]
    layouts: list[LayoutSummary]


class ClassEntry(BaseModel):
    value: str
    color: str
    n_features: int


class StyleResult(BaseModel):
    layer_id: str
    n_classes: int
    classes: list[ClassEntry]


class GraduatedStyleResult(StyleResult):
    breaks: list[float]
    mode: str
    diverging: bool = False
    center: float = 0.0
    diverging_one_sided: bool = False


# ---------------------------------------------------------------------------
# Basemap tile presets — no-API-key XYZ providers drawn UNDER the data.
# Each entry: (url_template, attribution, zmax). Esri REST tiles use {z}/{y}/{x}
# order; that ordering is encoded in the template and passed to the plugin verbatim.
#
# Presets are named for the ROLE they play in a figure (light / dark / streets /
# imagery), not for the vendor that currently serves them. That is deliberate:
# these used to be named after CARTO products, and when CARTO put its raster CDN
# behind an API key the names were left pointing at something they no longer
# described. Roles stay true across a provider swap; product names don't. The old
# names remain accepted as aliases so existing calls keep working.
#
# Attribution strings are copied verbatim from each service's own metadata
# (the ArcGIS REST `copyrightText` field, or the OSM tile policy) — never
# composed by hand, because an invented credit is worse than none.
# ---------------------------------------------------------------------------

BasemapName = Literal[
    "none",
    # canonical, role-based
    "light", "dark", "streets", "imagery",
    # deprecated aliases, kept so existing calls don't break
    "positron", "dark_matter", "voyager", "osm", "esri_imagery",
]

_ESRI_CANVAS_ATTR = "Esri, HERE, Garmin, (c) OpenStreetMap contributors, and the GIS user community"

_BASEMAP_PRESETS: dict[str, tuple[str, str, int]] = {
    "light": (
        "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        _ESRI_CANVAS_ATTR,
        20,
    ),
    "dark": (
        "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        _ESRI_CANVAS_ATTR,
        20,
    ),
    "streets": (
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "© OpenStreetMap contributors",
        19,
    ),
    "imagery": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community",
        19,
    ),
}

# Old preset name → canonical role. "voyager" has no keyless like-for-like
# replacement (it was a CARTO-specific style), so it resolves to the nearest
# honest equivalent rather than to an Esri service pretending to be it.
_BASEMAP_ALIASES: dict[str, str] = {
    "positron": "light",
    "dark_matter": "dark",
    "voyager": "streets",
    "osm": "streets",
    "esri_imagery": "imagery",
}


QMS_PREFIX = "qms:"


def _resolve_basemap(basemap: str, opacity: float) -> dict | None:
    """Resolve a basemap argument into the ``basemap_spec`` sent to the plugin.

    ``"none"`` returns ``None`` (legacy white-background behavior, unchanged).
    A known preset returns a live-XYZ spec the plugin loads via
    ``QgsRasterLayer(type=xyz, provider="wms")``. Deprecated aliases resolve to
    their canonical role and report the canonical name in ``name``, so the
    response says what was actually drawn.

    ``"qms:<id>"`` is passed through unresolved, as ``{"kind": "qms", ...}``. The
    QuickMapServices catalog lives in the QGIS user profile, which exists
    wherever QGIS runs and not necessarily on this machine — with
    ``--transport=plugin`` against another host, resolving here would read the
    wrong profile. The plugin resolves it and reports back what it drew.
    """
    from qgis_mcp_workflows.errors import BasemapNotFoundError

    if basemap == "none":
        return None

    if basemap.startswith(QMS_PREFIX):
        source_id = basemap[len(QMS_PREFIX):].strip()
        if not source_id:
            raise BasemapNotFoundError(basemap, sorted(_BASEMAP_PRESETS))
        return {"kind": "qms", "id": source_id, "opacity": float(opacity)}

    canonical = _BASEMAP_ALIASES.get(basemap, basemap)
    if canonical not in _BASEMAP_PRESETS:
        raise BasemapNotFoundError(basemap, sorted(_BASEMAP_PRESETS))
    url, attribution, zmax = _BASEMAP_PRESETS[canonical]
    return {
        "kind": "xyz",
        "name": canonical,
        "url": url,
        "zmin": 0,
        "zmax": zmax,
        "attribution": attribution,
        "opacity": float(opacity),
    }


class RenderResult(BaseModel):
    output_path: str
    width: int
    height: int
    dpi: int
    extent: list[float]
    crs: str
    n_layers: int
    basemap_attribution: str | None = None
    basemap_source: str | None = None


class JoinResult(BaseModel):
    csv: str
    field: str
    n_matched: int
    n_unmatched: int


class ChoroplethResult(RenderResult):
    field: str
    n_classes: int
    breaks: list[float]
    mode: str
    min_value: float
    max_value: float
    n_features: int
    join: JoinResult | None = None
    diverging: bool = False
    center: float = 0.0
    diverging_one_sided: bool = False


class TrajectoryResult(RenderResult):
    n_trajectories: int
    n_points_total: int
    n_points_rendered: int
    downsampled: bool
    time_range: list[str] | None = None
    modes: list[str] | None = None
    used_movingpandas: bool


class ODFlowResult(RenderResult):
    n_flows: int
    n_flows_rendered: int
    n_zones: int
    max_flow: float
    min_flow_rendered: float
    n_unmatched_origins: int
    n_unmatched_destinations: int


class LinkDensityResult(RenderResult):
    n_trajectory_rows_total: int
    n_points_total: int
    n_links_with_traffic: int
    n_links_rendered: int
    n_unmatched_link_ids: int
    density_field: str
    breaks: list[float]
    mode: str
    min_density: float
    max_density: float
    aggregation: str


class ExportResult(BaseModel):
    output_path: str
    format: str
    n_pages: int
    layout_name: str


class ComposeLayoutResult(BaseModel):
    output_path: str
    format: str
    n_layers: int
    items: list[str]
    page_size_mm: list[float]


class DiagramMapResult(RenderResult):
    diagram_type: str
    value_fields: list[str]
    n_features: int


class CatchmentResult(RenderResult):
    method: str
    n_points: int
    n_catchments: int


class BatchManifestEntry(BaseModel):
    value: str
    output_path: str
    extent: list[float]


class BatchError(BaseModel):
    value: str
    error: str


class BatchRenderResult(BaseModel):
    output_dir: str
    n_rendered: int
    manifest: list[BatchManifestEntry]
    errors: list[BatchError]


class PptxResult(BaseModel):
    pptx_path: str
    n_slides_added: int
    n_slides_total: int
    slide_titles: list[str | None]


class EvalResult(BaseModel):
    stdout: str
    stderr: str
    return_values: dict | None = None
    exception: str | None = None


# ---------------------------------------------------------------------------
# Stub helper
# ---------------------------------------------------------------------------


def _stub(tool_name: str, design_section: str) -> None:
    """Raise a clear NotImplementedError pointing at the design doc."""
    raise NotImplementedError(
        f"{tool_name} is not implemented — see docs/DESIGN.md §{design_section}."
    )


_RASTER_EXTENSIONS = (".tif", ".tiff", ".geotiff", ".vrt", ".asc", ".img", ".jp2")


def _is_raster_path(path: str) -> bool:
    return path.lower().endswith(_RASTER_EXTENSIONS)


def _translate_geometry_type(plugin_type: str) -> str:
    """Plugin's ``vector_{0,1,2}`` / ``raster`` → DESIGN.md geometry_type enum."""
    if plugin_type == "raster":
        return "raster"
    if plugin_type.startswith("vector_"):
        idx = plugin_type.split("_", 1)[1]
        return {"0": "point", "1": "line", "2": "polygon"}.get(idx, "no_geom")
    return "no_geom"


def _load_and_get_info(executor, abs_path: str, name: str | None = None):
    """Load a layer + fetch metadata. Removes the layer if get_layer_info fails.

    Returns ``(layer_id, info_dict, is_raster)``. Layer stays loaded on success
    so the caller can decide whether to remove it (transient inspect) or keep it
    (persistent load).
    """
    is_raster = _is_raster_path(abs_path)
    load_cmd = "add_raster_layer" if is_raster else "add_vector_layer"
    params = {"path": abs_path}
    if name:
        params["name"] = name
    load_result = executor.dispatch(load_cmd, params)
    layer_id = load_result["id"]
    try:
        info = executor.dispatch("get_layer_info", {"layer_id": layer_id})
    except Exception:
        try:
            executor.dispatch("remove_layer", {"layer_id": layer_id})
        except Exception:
            logger.warning("post-error cleanup failed for %s", layer_id, exc_info=True)
        raise
    return layer_id, info, is_raster


def _layer_info_kwargs(abs_path: str, info: dict, is_raster: bool) -> dict:
    """Translate plugin's get_layer_info response into LayerInfo constructor kwargs."""
    extent_dict = info["extent"]
    fields = [
        FieldInfo(name=f["name"], type=f["type"], n_unique=f.get("n_unique"))
        for f in info.get("fields", [])
    ]
    return {
        "path": abs_path,
        "geometry_type": _translate_geometry_type(info["type"]),
        "crs": info["crs"],
        "n_features": 0 if is_raster else info.get("feature_count", 0),
        "extent": [
            extent_dict["xmin"], extent_dict["ymin"],
            extent_dict["xmax"], extent_dict["ymax"],
        ],
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Tools — Connectivity (always registered, both tool modes)
# ---------------------------------------------------------------------------


class PingResult(BaseModel):
    pong: bool
    transport: str


class DiagnoseResult(BaseModel):
    status: str
    transport: str
    checks: list[dict]


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_ping() -> PingResult:
    """Check connectivity to the QGIS backend (plugin socket or headless subprocess).

    When to use: first call after a client connects, or when a later tool fails
    with a transport error. Returns ``pong=true`` and the active transport name.
    """
    from qgis_mcp_workflows.executors import get_executor

    result = get_executor().dispatch("ping", {})
    pong = bool(result.get("pong", False))
    return PingResult(pong=pong, transport=_executor_transport_name())


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_diagnose() -> DiagnoseResult:
    """Health-check the MCP ↔ QGIS stack: versions, project, processing providers.

    When to use: after an update, or when a tool looks missing. Compares plugin
    ``metadata.txt`` against the MCP server package version so drift is loud.
    """
    from qgis_mcp_workflows.executors import get_executor
    from qgis_mcp_workflows.helpers import enrich_diagnose

    raw = get_executor().dispatch("diagnose", {})
    enriched = enrich_diagnose(raw if isinstance(raw, dict) else {"status": "error", "checks": []})
    return DiagnoseResult(
        status=enriched.get("status", "error"),
        transport=_executor_transport_name(),
        checks=list(enriched.get("checks", [])),
    )


# ---------------------------------------------------------------------------
# Tools — Inspection & loading (3)
# ---------------------------------------------------------------------------


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_layer_inspect(
    path: Annotated[str, Field(description="Absolute path to a shapefile (.shp), GeoPackage (.gpkg), GeoJSON (.geojson), or raster (.tif).")],
) -> LayerInfo:
    """Read metadata for a layer file *without* loading it.

    When to use: before any render_* call, to confirm field names, geometry
    type, CRS, and feature count. Cheap (no project mutation, no QGIS render).

    Inputs: absolute path to a vector or raster file on disk.

    Returns: ``LayerInfo`` with path, geometry_type ∈ {point, line, polygon,
    raster, no_geom}, CRS string (EPSG:#### or proj string), n_features,
    extent as ``[xmin, ymin, xmax, ymax]``, and the list of fields with their
    types and unique-value counts.

    Chains into: ``qgis_load_layer`` (if you need to render),
    ``qgis_render_choropleth`` (pass ``zones_path=path``),
    ``qgis_render_trajectory`` (pass ``input_path=path``).
    """
    from qgis_mcp_workflows.executors import get_executor

    abs_path = os.path.abspath(path)
    executor = get_executor()
    layer_id, info, is_raster = _load_and_get_info(executor, abs_path)
    try:
        return LayerInfo(**_layer_info_kwargs(abs_path, info, is_raster))
    finally:
        try:
            executor.dispatch("remove_layer", {"layer_id": layer_id})
        except Exception:
            logger.warning("transient cleanup failed for %s", layer_id, exc_info=True)


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=False, destructiveHint=False, openWorldHint=True
    )
)
def qgis_load_layer(
    path: Annotated[str, Field(description="Absolute path to a vector or raster file.")],
    name: Annotated[str | None, Field(description="Display name for the layer in the project. Defaults to the file's stem.")] = None,
    crs: Annotated[str | None, Field(description='Override CRS, e.g. "EPSG:4326". Use when the file is missing a .prj sidecar.')] = None,
) -> LoadedLayer:
    """Add a layer to the active project and return its metadata + layer_id.

    When to use: when downstream tools need to operate on a registered layer
    (styling, rendering with multiple layers, batch operations). For one-shot
    figure renders on a single file, ``qgis_render_choropleth`` and friends
    accept a path directly and don't require this step.

    Returns: ``LoadedLayer`` — same fields as ``LayerInfo`` plus ``layer_id``
    used by ``qgis_style_*`` and ``qgis_render_map``.

    Chains into: ``qgis_style_categorized``, ``qgis_style_graduated``,
    ``qgis_render_map``.
    """
    from qgis_mcp_workflows.errors import CrsMismatchError, ExecutorError
    from qgis_mcp_workflows.executors import get_executor

    abs_path = os.path.abspath(path)
    executor = get_executor()
    layer_id, info, is_raster = _load_and_get_info(executor, abs_path, name=name)
    kwargs = _layer_info_kwargs(abs_path, info, is_raster)

    if crs is not None:
        try:
            crs_result = executor.dispatch("set_layer_crs", {"layer_id": layer_id, "crs": crs})
        except ExecutorError as err:
            try:
                executor.dispatch("remove_layer", {"layer_id": layer_id})
            except Exception:
                logger.warning("post-error cleanup failed for %s", layer_id, exc_info=True)
            raise CrsMismatchError(crs, err.message) from err
        kwargs["crs"] = crs_result.get("crs", crs)

    return LoadedLayer(layer_id=layer_id, **kwargs)


class SpatialJoinResult(BaseModel):
    output_path: str
    n_target: int
    n_join: int
    n_matched: int
    n_unmatched: int
    n_output_features: int
    predicate: str
    method: str
    joined_fields: list[str]


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_spatial_join(
    target_path: Annotated[str, Field(description="Features that keep their geometry (points, lines, or polygons).")],
    join_path: Annotated[str, Field(description="Layer whose attributes are copied onto matching target features.")],
    output_path: Annotated[str, Field(description="Absolute output path. .gpkg / .geojson / .shp.")],
    predicate: Annotated[
        Literal["intersects", "contains", "within", "touches", "overlaps", "crosses", "equals"],
        Field(description="Spatial predicate. Default intersects (point-in-polygon, overlapping polygons)."),
    ] = "intersects",
    method: Annotated[
        Literal["one_to_one", "one_to_many"],
        Field(description="one_to_one keeps the first match per target; one_to_many duplicates the target once per match."),
    ] = "one_to_one",
    join_fields: Annotated[
        list[str] | None,
        Field(description="Join-layer fields to copy. Default: all. Colliding names get a _j suffix."),
    ] = None,
    prefix: Annotated[str, Field(description="Optional prefix for copied field names.")] = "",
    keep_unmatched: Annotated[bool, Field(description="Keep target features with no match (join fields NULL).")] = True,
) -> SpatialJoinResult:
    """Join attributes by location and write a new layer.

    When to use: PFLOW pickups → zone polygons, stations → catchments, or any
    two overlapping layers. Atomic (no leftover project layers). Does **not**
    use the Processing toolbox — ``QgsSpatialIndex`` + geometry predicates.

    Returns: ``SpatialJoinResult`` with match counts and the written path.

    Chains into: ``qgis_render_choropleth``, ``qgis_style_categorized``,
    ``qgis_layer_inspect`` on ``output_path``.
    """
    from qgis_mcp_workflows.errors import (
        ExecutorError,
        FieldNotFoundError,
        LayerNotFoundError,
        SpatialJoinEmptyError,
    )
    from qgis_mcp_workflows.executors import get_executor

    abs_target = os.path.abspath(target_path)
    abs_join = os.path.abspath(join_path)
    abs_out = os.path.abspath(output_path)
    params: dict = {
        "target_path": abs_target,
        "join_path": abs_join,
        "output_path": abs_out,
        "predicate": predicate,
        "method": method,
        "prefix": prefix,
        "keep_unmatched": keep_unmatched,
    }
    if join_fields is not None:
        params["join_fields"] = list(join_fields)
    try:
        result = get_executor().dispatch("spatial_join", params, timeout=300)
    except ExecutorError as err:
        if "SPATIAL_JOIN_EMPTY" in err.message:
            raise SpatialJoinEmptyError(err.message) from err
        if "LAYER_NOT_FOUND" in err.message:
            path = abs_target if abs_target in err.message else abs_join
            raise LayerNotFoundError(path) from err
        if "FIELD_NOT_FOUND" in err.message:
            raise FieldNotFoundError(join_fields[0] if join_fields else "?", []) from err
        raise
    return SpatialJoinResult(
        output_path=os.path.abspath(result["output_path"]),
        n_target=int(result.get("n_target") or 0),
        n_join=int(result.get("n_join") or 0),
        n_matched=int(result.get("n_matched") or 0),
        n_unmatched=int(result.get("n_unmatched") or 0),
        n_output_features=int(result.get("n_output_features") or 0),
        predicate=result.get("predicate", predicate),
        method=result.get("method", method),
        joined_fields=list(result.get("joined_fields") or []),
    )


class ZonalStatsResult(BaseModel):
    output_path: str
    n_zones: int
    n_with_value: int
    stats: list[str]
    fields_added: list[str]
    raster_band: int
    prefix: str


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_zonal_stats(
    zones_path: Annotated[str, Field(description="Polygon layer (zones, prefectures, catchments).")],
    raster_path: Annotated[str, Field(description="Raster to summarize (JAXA LULC, DEM, density).")],
    output_path: Annotated[str, Field(description="Absolute output path. .gpkg keeps geometry; .csv is attributes only.")],
    stats: Annotated[
        list[Literal["count", "sum", "mean", "median", "stdev", "min", "max"]] | None,
        Field(description="Statistics to compute. Default count+sum+mean."),
    ] = None,
    prefix: Annotated[str, Field(description="Prefix for new fields (e.g. 'lulc_' → lulc_mean).")] = "",
    raster_band: Annotated[int, Field(description="Raster band (1-based).", ge=1)] = 1,
) -> ZonalStatsResult:
    """Raster statistics per polygon, written to GeoPackage or CSV.

    When to use: JAXA 100m LULC (or any raster) summarized onto zone polygons
    before a choropleth. Uses ``QgsZonalStatistics`` (qgis.analysis) — no
    Processing toolbox, works headless.

    Returns: ``ZonalStatsResult`` with ``fields_added`` (the new stat columns)
    and ``n_with_value``.

    Chains into: ``qgis_render_choropleth(zones_path=output_path, value_field=...)``.
    """
    from qgis_mcp_workflows.errors import ExecutorError, LayerNotFoundError, ZonalStatsError
    from qgis_mcp_workflows.executors import get_executor

    abs_zones = os.path.abspath(zones_path)
    abs_raster = os.path.abspath(raster_path)
    abs_out = os.path.abspath(output_path)
    stat_list = list(stats) if stats else ["count", "sum", "mean"]
    params = {
        "zones_path": abs_zones,
        "raster_path": abs_raster,
        "output_path": abs_out,
        "stats": stat_list,
        "prefix": prefix,
        "raster_band": raster_band,
    }
    try:
        result = get_executor().dispatch("zonal_stats", params, timeout=300)
    except ExecutorError as err:
        if "LAYER_NOT_FOUND" in err.message:
            path = abs_zones if abs_zones in err.message else abs_raster
            raise LayerNotFoundError(path) from err
        if "ZONAL_FAILED" in err.message or "WRITE_FAILED" in err.message:
            raise ZonalStatsError(err.message) from err
        raise
    return ZonalStatsResult(
        output_path=os.path.abspath(result["output_path"]),
        n_zones=int(result.get("n_zones") or 0),
        n_with_value=int(result.get("n_with_value") or 0),
        stats=list(result.get("stats") or stat_list),
        fields_added=list(result.get("fields_added") or []),
        raster_band=int(result.get("raster_band") or raster_band),
        prefix=result.get("prefix", prefix) or "",
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=False, destructiveHint=False, openWorldHint=True
    )
)
def qgis_project_load(
    qgz_path: Annotated[str, Field(description="Absolute path to a .qgz or .qgs project file.")],
) -> ProjectInfo:
    """Load a saved QGIS project (``.qgz`` / ``.qgs``).

    When to use: when the user has already designed a map in QGIS — layers
    loaded, styles applied, layouts configured — and you want to export
    figures from it. The W17 weekly-deck pattern uses this: load project,
    then ``qgis_export_layout`` or ``qgis_render_map``.

    Returns: ``ProjectInfo`` listing all layers (with layer_ids) and all
    print-composer layouts available for export.

    Chains into: ``qgis_export_layout``, ``qgis_batch_render``,
    ``qgis_render_map``.
    """
    from qgis_mcp_workflows.errors import ExecutorError, ProjectLoadError
    from qgis_mcp_workflows.executors import get_executor

    abs_qgz = os.path.abspath(qgz_path)
    try:
        result = get_executor().dispatch("project_load", {"qgz_path": abs_qgz}, timeout=30)
    except ExecutorError as err:
        raise ProjectLoadError(abs_qgz, err.message) from err

    return ProjectInfo(
        project_path=result["project_path"],
        crs=result["crs"],
        extent=result["extent"],
        layers=[
            LayerSummary(
                layer_id=la["layer_id"],
                name=la["name"],
                geometry_type=la["geometry_type"],
                visible=la["visible"],
            )
            for la in result.get("layers", [])
        ],
        layouts=[LayoutSummary(name=lo["name"]) for lo in result.get("layouts", [])],
    )


# ---------------------------------------------------------------------------
# Tools — Styling (2)
# ---------------------------------------------------------------------------


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=False
    )
)
def qgis_style_categorized(
    layer_id: Annotated[str, Field(description="layer_id from qgis_load_layer or qgis_project_load.")],
    field: Annotated[str, Field(description="Field name to categorize on (string-typed).")],
    palette: Annotated[str, Field(description='ColorBrewer palette name, e.g. "Set2", "Paired", "Dark2".')] = "Set2",
    classes: Annotated[list[str] | None, Field(description="Optional subset/order of category values to render; others get a default 'no data' style.")] = None,
) -> StyleResult:
    """Apply categorical (one-color-per-value) symbology to a vector layer.

    When to use: when ``field`` holds a small set of categorical values
    (e.g., ``transport_mode``, ``taxi_type``, ``zone_type``) and you want
    each category in a distinct color.

    Returns: ``StyleResult`` with the resolved class list and per-class
    feature counts.
    """
    from qgis_mcp_workflows.errors import ExecutorError, FieldNotFoundError, LayerNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    params = {
        "layer_id": layer_id,
        "style_type": "categorized",
        "field": field,
        "color_ramp": palette,
    }
    if classes is not None:
        params["classes_subset"] = list(classes)

    try:
        result = get_executor().dispatch("set_layer_style", params, timeout=30)
    except ExecutorError as err:
        if "Field not found" in err.message:
            raise FieldNotFoundError(field, []) from err
        if "Layer not found" in err.message:
            raise LayerNotFoundError(layer_id) from err
        raise

    return StyleResult(
        layer_id=layer_id,
        n_classes=result["n_classes"],
        classes=[
            ClassEntry(value=c["value"], color=c["color"], n_features=c["n_features"])
            for c in result.get("classes", [])
        ],
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=False
    )
)
def qgis_style_graduated(
    layer_id: Annotated[str, Field(description="layer_id from qgis_load_layer or qgis_project_load.")],
    field: Annotated[str, Field(description="Numeric field to bin on (e.g., total_trips, trip_count, vkt).")],
    n_classes: Annotated[int, Field(description="Number of bins.", ge=2, le=15)] = 5,
    mode: Annotated[Literal["quantile", "equal_interval", "natural_breaks", "pretty"], Field(description="Binning strategy.")] = "quantile",
    palette: Annotated[str, Field(description='Sequential colorbrewer palette, e.g. "YlOrRd", "Blues", "Viridis".')] = "YlOrRd",
    diverging: Annotated[bool, Field(description="Diverging color scheme with a fixed neutral midpoint, for signed data (e.g. net flux). Replaces mode-based boundaries with symmetric breaks around center; pair with a diverging palette (vik/RdBu/balance).")] = False,
    center: Annotated[float, Field(description="Neutral midpoint for diverging mode (e.g. 0). Ignored when diverging is False.")] = 0.0,
) -> GraduatedStyleResult:
    """Apply graduated (value-based color ramp) symbology — the choropleth primitive.

    When to use: as a low-level building block. For zone-level choropleths,
    prefer ``qgis_render_choropleth`` (one call instead of three).

    Returns: ``GraduatedStyleResult`` — class list + per-class feature counts +
    the resolved ``breaks`` array + the ``mode`` used.
    """
    from qgis_mcp_workflows.errors import ExecutorError, FieldNotFoundError, LayerNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    params = {
        "layer_id": layer_id,
        "style_type": "graduated",
        "field": field,
        "classes": n_classes,
        "mode": mode,
        "color_ramp": palette,
        "diverging": diverging,
        "center": center,
    }

    try:
        result = get_executor().dispatch("set_layer_style", params, timeout=30)
    except ExecutorError as err:
        if "Field not found" in err.message:
            raise FieldNotFoundError(field, []) from err
        if "Layer not found" in err.message:
            raise LayerNotFoundError(layer_id) from err
        raise

    return GraduatedStyleResult(
        layer_id=layer_id,
        n_classes=result["n_classes"],
        classes=[
            ClassEntry(value=c["value"], color=c["color"], n_features=c["n_features"])
            for c in result.get("classes", [])
        ],
        breaks=result.get("breaks", []),
        mode=result.get("mode", mode),
        diverging=result.get("diverging", diverging),
        center=result.get("center", center),
        diverging_one_sided=result.get("diverging_one_sided", False),
    )


# ---------------------------------------------------------------------------
# Tools — Rendering (4)
# ---------------------------------------------------------------------------


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_map(
    layer_ids: Annotated[list[str], Field(description="Layers to render, drawn in order bottom→top.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI (affects font size).", ge=72, le=600)] = 150,
    extent: Annotated[list[float] | None, Field(description="Render extent as [xmin, ymin, xmax, ymax]. If omitted, uses the union of layer extents with 5% padding.")] = None,
    background: Annotated[str, Field(description='Map background, named or hex (e.g. "white", "#fafafa", "transparent").')] = "white",
    scale_bar: Annotated[bool, Field(description="Draw a metric scale bar in the lower-left of the PNG.")] = False,
    north_arrow: Annotated[bool, Field(description="Draw a north arrow in the upper-right of the PNG.")] = False,
) -> RenderResult:
    """Render a list of already-loaded layers to PNG.

    When to use: generic render. For domain-specific cases prefer the
    workflow tools (``qgis_render_choropleth``, ``qgis_render_trajectory``,
    ``qgis_render_od_flows``) — they handle data loading, styling, and
    extent inference for you.

    Returns: ``RenderResult`` with the absolute output_path, image dims,
    final extent, CRS, and number of layers rendered.

    Chains into: ``qgis_figures_to_pptx``, ``qgis_batch_render``.
    """
    from qgis_mcp_workflows.executors import get_executor

    abs_output = os.path.abspath(output_png)
    params: dict = {
        "layer_ids": list(layer_ids),
        "output_png": abs_output,
        "width": width,
        "height": height,
        "dpi": dpi,
        "background": background,
        "scale_bar": scale_bar,
        "north_arrow": north_arrow,
    }
    if extent is not None:
        params["extent"] = list(extent)

    result = get_executor().dispatch("render_layers_to_path", params, timeout=60)
    return RenderResult(
        output_path=result["output_path"],
        width=result["width"],
        height=result["height"],
        dpi=result["dpi"],
        extent=result["extent"],
        crs=result["crs"],
        n_layers=result["n_layers"],
    )


class BasemapCatalogResult(BaseModel):
    """What `basemap=` will accept in this QGIS profile."""

    presets: list[str]
    qms: list[dict]
    n_qms: int = 0
    qms_rejected: list[dict] = []
    qms_error: str | None = None


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, destructiveHint=False, openWorldHint=False
    )
)
def qgis_list_basemaps(
    group: Annotated[str | None, Field(description='Restrict to one QuickMapServices group, e.g. "esri", "openstreetmap", "versatiles".')] = None,
    keyless_only: Annotated[bool, Field(description="Drop sources on hosts known to require an API key. A heuristic on the URL host, not a guarantee — a provider can start requiring a key without changing its URL.")] = False,
) -> BasemapCatalogResult:
    """List every basemap `basemap=` accepts here: built-in presets + QuickMapServices.

    Call this before passing `basemap="qms:<id>"` — those ids come from directory
    names inside the QGIS profile, so they cannot be guessed from a tool schema.

    `qms_rejected` explains what was filtered and why: sources declaring a CRS
    other than EPSG:3857 (our XYZ provider assumes 3857, so they would draw
    misregistered) and providers whose terms restrict tile access to their own
    apps. `qms_error` is set instead when QuickMapServices isn't installed —
    the built-in presets still work in that case.
    """
    from qgis_mcp_workflows.executors import get_executor

    result = get_executor().dispatch(
        "list_basemaps", {"group": group, "keyless_only": bool(keyless_only)}
    )
    return BasemapCatalogResult(
        presets=result.get("presets", []),
        qms=result.get("qms", []),
        n_qms=result.get("n_qms", 0),
        qms_rejected=result.get("qms_rejected", []),
        qms_error=result.get("qms_error"),
    )


class DuckDbRenderResult(RenderResult):
    """Render plus what the query actually produced."""

    geometry_type: str
    n_features: int
    n_skipped: int = 0
    fields: list[str] = []
    field: str | None = None
    breaks: list[float] | None = None
    row_limit_hit: bool = False


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_from_duckdb(
    db_path: Annotated[str, Field(description="Absolute path to a DuckDB database file, e.g. ~/Dropbox/PFLOW/output/viz/kichijoji.duckdb.")],
    query: Annotated[str, Field(description="SELECT returning one row per feature. Must include the geometry columns named below. The connection is opened READ-ONLY, so the query cannot modify the database.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    geometry_column: Annotated[str | None, Field(description='Column holding WKT geometry text, e.g. "geom". For a DuckDB spatial GEOMETRY column, wrap it in the query: SELECT ST_AsText(geom) AS geom. Mutually exclusive with lon_column/lat_column.')] = None,
    lon_column: Annotated[str | None, Field(description="Longitude column, for point data with no geometry column. Use with lat_column.")] = None,
    lat_column: Annotated[str | None, Field(description="Latitude column, for point data with no geometry column. Use with lon_column.")] = None,
    value_field: Annotated[str | None, Field(description="Numeric column to style graduated. Omit for a single flat symbol.")] = None,
    crs: Annotated[str, Field(description="CRS of the coordinates in the query result. PFLOW trajectories are EPSG:4326.")] = "EPSG:4326",
    n_classes: Annotated[int, Field(description="Number of graduated bins, when value_field is set.", ge=2, le=15)] = 5,
    mode: Annotated[Literal["quantile", "equal_interval", "natural_breaks", "pretty"], Field(description="Binning strategy.")] = "quantile",
    palette: Annotated[str, Field(description='Color ramp, e.g. "YlOrRd", "Blues", "viridis".')] = "YlOrRd",
    diverging: Annotated[bool, Field(description="Symmetric breaks around center, for signed data.")] = False,
    center: Annotated[float, Field(description="Neutral midpoint when diverging.")] = 0.0,
    max_features: Annotated[int, Field(description="Row ceiling. The query is wrapped in a LIMIT so a mistaken SELECT * against a multi-GB table cannot pull the whole thing into memory.", ge=1, le=500000)] = 50000,
    basemap: Annotated[str, Field(description='Tile basemap drawn under the result. Presets "light"/"dark"/"streets"/"imagery", a "qms:<id>" QuickMapServices source, or "none".')] = "none",
    basemap_opacity: Annotated[float, Field(description="Opacity of the tile basemap, 0.0-1.0.", ge=0.0, le=1.0)] = 1.0,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
) -> DuckDbRenderResult:
    """Render the result of a DuckDB query directly, with no CSV in between.

    For PFLOW's DuckDB stores — `output/viz/kichijoji.duckdb` is 1.1 GB with
    ~10M waypoints — querying beats exporting a CSV and loading that, both in
    time and in not materialising an intermediate file.

    Geometry has to be named explicitly because a DuckDB table has no convention
    for where it lives — either `geometry_column` (WKT text) or the
    `lon_column`/`lat_column` pair for points.

    The connection is opened read-only, so a query cannot alter the database, and
    the query is wrapped in a LIMIT so a mistaken `SELECT *` against a multi-GB
    table cannot pull it all into memory.
    """
    import os

    from qgis_mcp_workflows.errors import QgisMcpWorkflowsError
    from qgis_mcp_workflows.executors import get_executor

    try:
        import duckdb
    except ImportError as exc:
        raise QgisMcpWorkflowsError(
            "The duckdb extra is not installed. "
            "Next: run `uv sync --extra duckdb`, then retry."
        ) from exc

    abs_db = os.path.abspath(db_path)
    if not os.path.exists(abs_db):
        raise QgisMcpWorkflowsError(
            f"DuckDB database not found: {abs_db}. Next: check the path and retry."
        )

    use_lonlat = bool(lon_column and lat_column)
    if bool(geometry_column) == use_lonlat:
        raise QgisMcpWorkflowsError(
            "Specify exactly one geometry source: geometry_column (WKT text), or "
            "lon_column plus lat_column. "
            "Next: retry with geometry_column='geom', or lon_column='lon', lat_column='lat'."
        )

    # read_only protects the caller's database from anything the query does.
    try:
        conn = duckdb.connect(abs_db, read_only=True)
    except Exception as exc:
        raise QgisMcpWorkflowsError(
            f"Could not open {abs_db} read-only: {exc}. "
            "Next: check the file is a DuckDB database and not locked by another process."
        ) from exc

    try:
        wrapped = f"SELECT * FROM ({query.rstrip().rstrip(';')}) AS _q LIMIT {int(max_features) + 1}"
        try:
            cursor = conn.execute(wrapped)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        except Exception as exc:
            raise QgisMcpWorkflowsError(
                f"DuckDB query failed: {exc}. "
                "Next: check the query against the table schema — "
                "`qgis_eval` can run `DESCRIBE <table>` if you need it."
            ) from exc
    finally:
        conn.close()

    row_limit_hit = len(rows) > max_features
    rows = rows[:max_features]
    if not rows:
        raise QgisMcpWorkflowsError(
            "The query returned no rows, so there is nothing to render. "
            "Next: run the query with a wider filter and retry."
        )

    missing = [
        c for c in ([geometry_column] if geometry_column else [lon_column, lat_column])
        if c not in columns
    ]
    if missing:
        raise QgisMcpWorkflowsError(
            f"Column(s) {missing} not in the query result. Available: {columns}. "
            "Next: add them to the SELECT list and retry."
        )

    idx = {c: i for i, c in enumerate(columns)}
    geom_cols = {geometry_column} if geometry_column else {lon_column, lat_column}
    attr_cols = [c for c in columns if c not in geom_cols]

    features = []
    n_bad_geom = 0
    for row in rows:
        if geometry_column:
            wkt = row[idx[geometry_column]]
            if not isinstance(wkt, str) or not wkt.strip():
                n_bad_geom += 1
                continue
        else:
            lon, lat = row[idx[lon_column]], row[idx[lat_column]]
            if lon is None or lat is None:
                n_bad_geom += 1
                continue
            wkt = f"POINT ({float(lon)} {float(lat)})"
        feat = {"wkt": wkt}
        for c in attr_cols:
            feat[c] = row[idx[c]]
        features.append(feat)

    if not features:
        raise QgisMcpWorkflowsError(
            f"All {len(rows)} rows lacked usable geometry. "
            + (f"Column {geometry_column!r} should hold WKT text — for a DuckDB "
               "spatial GEOMETRY column use ST_AsText(...) in the SELECT. "
               if geometry_column else
               f"Columns {lon_column!r}/{lat_column!r} should hold numeric coordinates. ")
            + "Next: adjust the query and retry."
        )

    result = get_executor().dispatch("render_wkt_features", {
        "features": features,
        "output_png": os.path.abspath(output_png),
        "crs": crs,
        "value_field": value_field,
        "n_classes": n_classes,
        "mode": mode,
        "palette": palette,
        "diverging": diverging,
        "center": center,
        "basemap_spec": _resolve_basemap(basemap, basemap_opacity),
        "width": width,
        "height": height,
        "dpi": dpi,
    })

    return DuckDbRenderResult(
        output_path=result["output_path"],
        width=result["width"], height=result["height"], dpi=result["dpi"],
        extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
        geometry_type=result["geometry_type"],
        n_features=result["n_features"],
        n_skipped=result.get("n_skipped", 0) + n_bad_geom,
        fields=result.get("fields", []),
        field=result.get("field"),
        breaks=result.get("breaks"),
        row_limit_hit=row_limit_hit,
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_choropleth(
    zones_path: Annotated[str, Field(description="Absolute path to a polygon layer (shp, gpkg, geojson). For PFLOW prefecture choropleth, use polbnda_jpn_new.shp.")],
    value_field: Annotated[str, Field(description="Field name to render. If value_csv is given, this is the column in that CSV; else this is an attribute on zones_path.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    value_csv: Annotated[str | None, Field(description="Optional CSV path joined to zones_path on join_field. Use this for the PFLOW pattern: zone_trips.csv joined to a zones polygon.")] = None,
    join_field: Annotated[str, Field(description='Common key between zones_path and value_csv. PFLOW uses "zone_id" (MFS-coded), "PRF_CODE" (prefecture), or similar.')] = "zone_id",
    n_classes: Annotated[int, Field(description="Number of choropleth bins.", ge=2, le=15)] = 5,
    mode: Annotated[Literal["quantile", "equal_interval", "natural_breaks", "pretty"], Field(description="Binning strategy.")] = "quantile",
    palette: Annotated[str, Field(description='Sequential colorbrewer palette, e.g. "YlOrRd", "Blues", "Viridis".')] = "YlOrRd",
    diverging: Annotated[bool, Field(description="Diverging color scheme pinned at a neutral midpoint, for signed data (net flux = arrivals minus departures). Symmetric class breaks around center; pair with a diverging palette (vik/RdBu/balance).")] = False,
    center: Annotated[float, Field(description="Neutral midpoint for diverging mode (e.g. 0). Ignored when diverging is False.")] = 0.0,
    label_field: Annotated[str | None, Field(description="Optional zones attribute to label each polygon with (e.g. a ward/prefecture name), drawn with a white halo for legibility.")] = None,
    title: Annotated[str | None, Field(description="Optional title rendered at the top of the figure.")] = None,
    legend: Annotated[bool, Field(description="Render a legend with class breaks.")] = True,
    basemap_paths: Annotated[list[str] | None, Field(description="Optional vector basemap layers drawn under the choropleth (e.g., coastline, rivers, prefecture borders).")] = None,
    basemap: Annotated[str, Field(description='Tile basemap drawn under the data for real-world context. Presets: "light" (neutral grey, best under choropleths), "dark", "streets" (OpenStreetMap), "imagery" (satellite); "none" for a plain white background. Any QuickMapServices source installed in the QGIS profile can be used as "qms:<id>" (e.g. "qms:opentopomap") — call qgis_list_basemaps for the ids. The old CARTO names (positron/dark_matter/voyager) still work as aliases. No API key needed.')] = "none",
    basemap_opacity: Annotated[float, Field(description="Opacity of the tile basemap, 0.0-1.0. Use 0.5-0.8 to mute it so the choropleth colors read on top.", ge=0.0, le=1.0)] = 1.0,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
    scale_bar: Annotated[bool, Field(description="Draw a metric scale bar in the lower-left of the PNG.")] = False,
    north_arrow: Annotated[bool, Field(description="Draw a north arrow in the upper-right of the PNG.")] = False,
) -> ChoroplethResult:
    """Render a zone-level choropleth in one call. PFLOW workflow tool.

    When to use: any zone-aggregated visualization where each polygon gets
    a color from a numeric value. Replaces ``qgis_load_layer`` +
    ``qgis_style_graduated`` + ``qgis_render_map`` chain.

    Two data shapes supported:
    1. ``value_field`` is already an attribute on ``zones_path`` → render directly.
    2. ``value_csv`` is provided → left-join ``value_csv[join_field]`` to
       ``zones_path[join_field]``, then render. Mismatches surface in the
       response as ``join.n_unmatched``, never as silent zero values.

    PFLOW example: ``zones_path=polbnda_jpn_new.shp``,
    ``value_csv=zone_trips.csv``, ``value_field=total_trips``,
    ``join_field=zone_id``.

    Returns: ``ChoroplethResult`` with output path, breaks, min/max, and a
    ``join`` block (if value_csv was used) reporting matched vs unmatched.

    Chains into: ``qgis_figures_to_pptx``, ``qgis_batch_render``.
    """
    import csv as _csv

    from qgis_mcp_workflows.errors import ExecutorError, FieldNotFoundError, JoinError
    from qgis_mcp_workflows.executors import get_executor

    abs_zones = os.path.abspath(zones_path)
    abs_output = os.path.abspath(output_png)
    abs_basemaps = [os.path.abspath(p) for p in (basemap_paths or [])]
    abs_csv = os.path.abspath(value_csv) if value_csv else None

    value_dict: dict[str, float] | None = None
    if abs_csv is not None:
        with open(abs_csv, encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f)
            csv_columns = reader.fieldnames or []
            if value_field not in csv_columns:
                raise FieldNotFoundError(value_field, csv_columns)
            if join_field not in csv_columns:
                raise FieldNotFoundError(join_field, csv_columns)
            value_dict = {}
            for row in reader:
                key = row[join_field]
                raw = row[value_field]
                try:
                    value_dict[str(key)] = float(raw)
                except (TypeError, ValueError):
                    logger.warning(
                        "skipping non-numeric value in %s: %s=%r → %s=%r",
                        abs_csv, join_field, key, value_field, raw,
                    )

    params: dict = {
        "zones_path": abs_zones,
        "value_field": value_field,
        "output_png": abs_output,
        "value_dict": value_dict,
        "join_field": join_field,
        "n_classes": n_classes,
        "mode": mode,
        "palette": palette,
        "diverging": diverging,
        "center": center,
        "label_field": label_field,
        "title": title,
        "legend": legend,
        "basemap_paths": abs_basemaps,
        "basemap_spec": _resolve_basemap(basemap, basemap_opacity),
        "width": width,
        "height": height,
        "dpi": dpi,
        "scale_bar": scale_bar,
        "north_arrow": north_arrow,
    }

    try:
        result = get_executor().dispatch("render_choropleth", params, timeout=60)
    except ExecutorError as err:
        if "JOIN_NO_MATCH" in str(err):
            raise JoinError(err.message) from err
        raise

    join_block = None
    if value_dict is not None:
        join_block = JoinResult(
            csv=abs_csv or "",
            field=join_field,
            n_matched=result["n_matched"],
            n_unmatched=result["n_unmatched"],
        )
    return ChoroplethResult(
        output_path=result["output_path"],
        width=result["width"],
        height=result["height"],
        dpi=result["dpi"],
        extent=result["extent"],
        crs=result["crs"],
        n_layers=result["n_layers"],
        field=result["field"],
        n_classes=result["n_classes"],
        breaks=result["breaks"],
        mode=result["mode"],
        min_value=result["min_value"],
        max_value=result["max_value"],
        n_features=result["n_features"],
        join=join_block,
        diverging=result.get("diverging", diverging),
        center=result.get("center", center),
        diverging_one_sided=result.get("diverging_one_sided", False),
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_trajectory(
    input_path: Annotated[str, Field(description="Absolute path to a CSV (with lon, lat, datetime, trip_id columns by default — matches PFLOW trajectory schema) or GPX file.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    lon_col: Annotated[str, Field(description="Longitude column name in the CSV.")] = "lon",
    lat_col: Annotated[str, Field(description="Latitude column name in the CSV.")] = "lat",
    time_col: Annotated[str, Field(description="Datetime column name (ISO 8601 or PFLOW 'YYYY-MM-DD HH:MM:SS' string).")] = "datetime",
    id_col: Annotated[str, Field(description="Trajectory grouping column. Each unique value is one trajectory.")] = "trip_id",
    mode_col: Annotated[str | None, Field(description='Optional categorical column to color trajectories by (e.g., "transport_mode").')] = None,
    render_mode: Annotated[Literal["lines", "points", "heatmap"], Field(description="Visualization style.")] = "lines",
    sample_rate: Annotated[float, Field(description="Fraction of points to keep (1.0 = all, 0.01 = every 100th).", gt=0.0, le=1.0)] = 1.0,
    max_points: Annotated[int, Field(description="Hard cap on rendered points; exceeded → automatic downsample with response flag.", ge=1000)] = 500_000,
    basemap_paths: Annotated[list[str] | None, Field(description="Optional vector basemap layers drawn under trajectories.")] = None,
    basemap: Annotated[str, Field(description='Tile basemap drawn under the trajectories. Presets: "light", "dark", "streets", "imagery"; "none" for a plain white background. QuickMapServices as "qms:<id>".')] = "none",
    basemap_opacity: Annotated[float, Field(description="Opacity of the tile basemap, 0.0-1.0.", ge=0.0, le=1.0)] = 1.0,
    extent: Annotated[list[float] | None, Field(description="[lon_min, lat_min, lon_max, lat_max] in EPSG:4326. Clips before rendering.")] = None,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
    scale_bar: Annotated[bool, Field(description="Draw a metric scale bar in the lower-left of the PNG.")] = False,
    north_arrow: Annotated[bool, Field(description="Draw a north arrow in the upper-right of the PNG.")] = False,
) -> TrajectoryResult:
    """Render trajectory data from CSV/GPX. PFLOW/GUFM workflow tool.

    When to use: visualizing GPS-style point sequences. Defaults match
    PFLOW trajectory CSV schema (``lon, lat, datetime, trip_id,
    transport_mode``). PFLOW files are large (3M+ rows, ~1 GB each); use
    ``sample_rate`` and ``extent`` to keep renders fast.

    If ``movingpandas`` and the QGIS Trajectools plugin are installed,
    auto-uses them for richer rendering (speed bins, stop detection); else
    falls back to plain line/point rendering. Reports which path was taken
    via ``used_movingpandas``.

    Returns: ``TrajectoryResult`` with totals, rendered count, downsample
    flag, observed time range, modes seen.

    Chains into: ``qgis_figures_to_pptx``.
    """
    import csv as _csv

    from qgis_mcp_workflows.errors import EmptyAfterFilterError, FieldNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_png)
    abs_basemaps = [os.path.abspath(p) for p in (basemap_paths or [])]

    # GPX path: skip CSV parse, hand path through to plugin's OGR loader.
    if abs_input.lower().endswith(".gpx"):
        params: dict = {
            "input_path": abs_input,
            "output_png": abs_output,
            "render_mode": render_mode,
            "basemap_paths": abs_basemaps,
            "basemap_spec": _resolve_basemap(basemap, basemap_opacity),
            "extent": list(extent) if extent is not None else None,
            "width": width,
            "height": height,
            "dpi": dpi,
            "features": None,
            "mode_col": mode_col,
            "used_movingpandas": False,
            "speed_field": None,
            "scale_bar": scale_bar,
            "north_arrow": north_arrow,
        }
        result = get_executor().dispatch("render_trajectory", params, timeout=120)
        return TrajectoryResult(
            output_path=result["output_path"],
            width=result["width"], height=result["height"], dpi=result["dpi"],
            extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
            n_trajectories=result["n_trajectories"],
            n_points_total=result["n_points_total"],
            n_points_rendered=result["n_points_rendered"],
            downsampled=result["downsampled"],
            time_range=result.get("time_range"),
            modes=result.get("modes"),
            used_movingpandas=result.get("used_movingpandas", False),
            basemap_attribution=result.get("basemap_attribution"),
            basemap_source=result.get("basemap_source"),
        )

    # CSV path: parse + validate columns MCP-side.
    with open(abs_input, encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        columns = reader.fieldnames or []
        for required in (lon_col, lat_col, time_col, id_col):
            if required not in columns:
                raise FieldNotFoundError(required, columns)
        if mode_col is not None and mode_col not in columns:
            raise FieldNotFoundError(mode_col, columns)
        rows = list(reader)

    n_points_total = len(rows)

    # Apply extent clip first (caller's filter), then sampling.
    if extent is not None:
        xmin, ymin, xmax, ymax = extent
        kept = []
        for row in rows:
            try:
                lon = float(row[lon_col])
                lat = float(row[lat_col])
            except (TypeError, ValueError):
                continue
            if xmin <= lon <= xmax and ymin <= lat <= ymax:
                kept.append(row)
        rows = kept
        if not rows:
            raise EmptyAfterFilterError(
                f"0 rows after extent clip [{xmin}, {ymin}, {xmax}, {ymax}]"
            )

    # Sampling: stride by 1/sample_rate, then cap by max_points.
    downsampled = False
    if sample_rate < 1.0:
        stride = max(1, round(1.0 / sample_rate))
        rows = rows[::stride]
        if not rows:
            raise EmptyAfterFilterError(
                f"0 rows after sample_rate={sample_rate} (stride={stride})"
            )
    if len(rows) > max_points:
        stride2 = -(-len(rows) // max_points)  # ceil division
        rows = rows[::stride2]
        downsampled = True

    # Build feature list — small dicts, JSON-safe over the socket.
    features: list[dict] = []
    modes_seen: set[str] = set()
    time_min: str | None = None
    time_max: str | None = None
    for row in rows:
        try:
            lon = float(row[lon_col])
            lat = float(row[lat_col])
        except (TypeError, ValueError):
            continue
        ts = row.get(time_col, "")
        if time_min is None or (ts and ts < time_min):
            time_min = ts
        if time_max is None or (ts and ts > time_max):
            time_max = ts
        feat: dict = {
            "trip_id": str(row[id_col]),
            "lon": lon,
            "lat": lat,
            "datetime": ts,
        }
        if mode_col is not None:
            mode_val = row.get(mode_col, "")
            feat["mode"] = mode_val
            if mode_val:
                modes_seen.add(mode_val)
        features.append(feat)

    if not features:
        raise EmptyAfterFilterError("0 valid rows after numeric coercion")

    # movingpandas integration: speed-binned line rendering only.
    used_mp = False
    speed_field: str | None = None
    if _HAS_MP and render_mode == "lines" and mode_col is None:
        try:
            import movingpandas as mp  # uses sys.modules; tests patch this in
            speeds = _compute_movingpandas_speeds(mp, features)
            if speeds is not None and len(speeds) == len(features):
                for feat, sp in zip(features, speeds, strict=False):
                    feat["speed_kmh"] = sp
                used_mp = True
                speed_field = "speed_kmh"
        except Exception:
            logger.warning("movingpandas integration failed, falling back", exc_info=True)

    n_trajectories = len({f["trip_id"] for f in features})
    time_range = [time_min, time_max] if (time_min and time_max) else None
    modes_list = sorted(modes_seen) if modes_seen else None

    params = {
        "input_path": abs_input,
        "output_png": abs_output,
        "render_mode": render_mode,
        "basemap_paths": abs_basemaps,
        "basemap_spec": _resolve_basemap(basemap, basemap_opacity),
        "extent": list(extent) if extent is not None else None,
        "width": width,
        "height": height,
        "dpi": dpi,
        "features": features,
        "mode_col": mode_col,
        "used_movingpandas": used_mp,
        "speed_field": speed_field,
        "scale_bar": scale_bar,
        "north_arrow": north_arrow,
    }
    result = get_executor().dispatch("render_trajectory", params, timeout=120)
    return TrajectoryResult(
        output_path=result["output_path"],
        width=result["width"], height=result["height"], dpi=result["dpi"],
        extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
        n_trajectories=result.get("n_trajectories", n_trajectories),
        n_points_total=result.get("n_points_total", n_points_total),
        n_points_rendered=result.get("n_points_rendered", len(features)),
        downsampled=result.get("downsampled", downsampled),
        time_range=result.get("time_range", time_range),
        modes=result.get("modes", modes_list),
        used_movingpandas=result.get("used_movingpandas", used_mp),
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
    )


def _compute_movingpandas_speeds(mp, features: list[dict]) -> list[float] | None:
    """Build a TrajectoryCollection and return per-point speeds in km/h.

    Returns None if movingpandas can't produce a length-matched speed series.
    """
    try:
        import pandas as pd

        df = pd.DataFrame(features)
        df["datetime"] = pd.to_datetime(df["datetime"])
        try:
            import geopandas as gpd
            from shapely.geometry import Point

            gdf = gpd.GeoDataFrame(
                df,
                geometry=[Point(lon, lat) for lon, lat in zip(df["lon"], df["lat"], strict=False)],
                crs="EPSG:4326",
            )
            tc = mp.TrajectoryCollection(gdf, traj_id_col="trip_id", t="datetime")
        except Exception:
            # Tests patch in a fake mp.TrajectoryCollection that accepts anything;
            # real movingpandas requires geopandas, which is bundled with the
            # [trajectory] extra. Fall through to a permissive constructor for the
            # test path.
            tc = mp.TrajectoryCollection(df, traj_id_col="trip_id", t="datetime")
        try:
            tc.add_speed(overwrite=True, units=("km", "h"), name="speed_kmh")
        except TypeError:
            tc.add_speed()  # fakes may accept no kwargs
        point_gdf = tc.to_point_gdf()
        col = "speed_kmh" if "speed_kmh" in point_gdf.columns else "speed"
        if col not in point_gdf.columns:
            return None
        speeds = [float(v) if v == v else 0.0 for v in point_gdf[col].tolist()]
        return speeds
    except Exception:
        logger.warning("movingpandas speed computation failed", exc_info=True)
        return None


def _aggregate_link_density(
    csv_paths: list,
    link_id_col: str,
    aggregation: str,
    value_col: str | None,
) -> tuple[dict[str, float], int]:
    """Stream-aggregate trajectory CSVs into a {link_id → density} dict.

    Returns (density_dict, n_rows_read). Streaming: never holds more than one
    row in memory beyond the accumulator. Non-numeric values in value_col are
    skipped silently (logged at WARNING) so a few bad rows don't kill the run.

    Raises:
        FieldNotFoundError: if link_id_col or value_col is missing from any CSV.
        ValueError: if aggregation='sum' but value_col is None.
    """
    import csv as _csv

    from qgis_mcp_workflows.errors import FieldNotFoundError

    if aggregation == "sum" and value_col is None:
        raise ValueError("aggregation='sum' requires value_col to be set.")
    if aggregation not in ("count", "sum"):
        raise ValueError(f"Unknown aggregation: {aggregation!r}. Use 'count' or 'sum'.")

    density: dict[str, float] = {}
    n_rows = 0

    for path in csv_paths:
        with open(path, encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f)
            columns = reader.fieldnames or []
            if link_id_col not in columns:
                raise FieldNotFoundError(link_id_col, columns)
            if value_col is not None and value_col not in columns:
                raise FieldNotFoundError(value_col, columns)

            for row in reader:
                n_rows += 1
                link_id = row[link_id_col]
                if not link_id:
                    continue
                if aggregation == "count":
                    density[link_id] = density.get(link_id, 0.0) + 1.0
                else:  # sum
                    raw = row[value_col]  # type: ignore[index]
                    try:
                        val = float(raw)
                    except (TypeError, ValueError):
                        continue
                    if val != val:  # NaN check
                        continue
                    density[link_id] = density.get(link_id, 0.0) + val

    return density, n_rows


def _read_load_csv(path: str, link_id_col: str, volume_col: str) -> tuple[dict[str, float], int]:
    """Read a pre-aggregated link-volume CSV (from qgis_assign_section_load)."""
    import csv as _csv

    from qgis_mcp_workflows.errors import FieldNotFoundError

    density: dict[str, float] = {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        columns = reader.fieldnames or []
        if link_id_col not in columns:
            raise FieldNotFoundError(link_id_col, columns)
        if volume_col not in columns:
            raise FieldNotFoundError(volume_col, columns)
        n_rows = 0
        for row in reader:
            n_rows += 1
            key = row[link_id_col]
            if not key:
                continue
            try:
                density[str(key)] = density.get(str(key), 0.0) + float(row[volume_col])
            except (TypeError, ValueError):
                continue
    return density, n_rows


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_od_flows(
    od_csv: Annotated[str, Field(description="Absolute path to a long-format OD CSV. PFLOW schema: origin, destination, trip_count, avg_distance_km.")],
    zones_layer_path: Annotated[str, Field(description="Absolute path to a polygon layer with one feature per zone, keyed by zone_id_field.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    origin_col: Annotated[str, Field(description="Origin zone column in od_csv.")] = "origin",
    dest_col: Annotated[str, Field(description="Destination zone column in od_csv.")] = "destination",
    value_col: Annotated[str, Field(description="Flow magnitude column. Arc widths scale linearly with this.")] = "trip_count",
    zone_id_field: Annotated[str, Field(description="Zone identifier field on zones_layer_path. Must match origin_col / dest_col values.")] = "zone_id",
    top_n: Annotated[int | None, Field(description="Render only the top-N flows by value. None renders all matched flows.")] = None,
    arc_style: Annotated[Literal["line", "arrow", "curved"], Field(description='Arc rendering: "line" (straight, default), "arrow" (directional), or "curved" (directional bezier). Arrows/curves scale width + head with flow.')] = "line",
    basemap_paths: Annotated[list[str] | None, Field(description="Optional vector basemap layers drawn under arcs.")] = None,
    basemap: Annotated[str, Field(description='Tile basemap drawn under the arcs for real-world context. Presets: "light" (neutral grey, best under choropleths), "dark", "streets" (OpenStreetMap), "imagery" (satellite); "none" for a plain white background. Any QuickMapServices source installed in the QGIS profile can be used as "qms:<id>" (e.g. "qms:opentopomap") — call qgis_list_basemaps for the ids. The old CARTO names (positron/dark_matter/voyager) still work as aliases. No API key needed.')] = "none",
    basemap_opacity: Annotated[float, Field(description="Opacity of the tile basemap, 0.0-1.0.", ge=0.0, le=1.0)] = 1.0,
    label_field: Annotated[str | None, Field(description='Haloed line label. OD memory-layer fields: "origin", "destination", "trip_count".')] = None,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
    scale_bar: Annotated[bool, Field(description="Draw a metric scale bar in the lower-left of the PNG.")] = False,
    north_arrow: Annotated[bool, Field(description="Draw a north arrow in the upper-right of the PNG.")] = False,
) -> ODFlowResult:
    """Render origin-destination arcs over a zones layer. PFLOW workflow tool.

    When to use: any "flows between regions" visualization. PFLOW example:
    ``od_csv=truck/run_*/od_flows.csv``,
    ``zones_layer_path=polbnda_jpn_new.shp``, ``zone_id_field=PRF_CODE``.

    PFLOW reality: multiple zone-id systems coexist (MFS##, PRF##, Z##) in
    different files. The caller must align ``od_csv[origin_col / dest_col]``
    with ``zones_layer[zone_id_field]``. Mismatches surface as
    ``n_unmatched_origins`` / ``n_unmatched_destinations`` — loud, not
    silent zero-flow renders.

    Returns: ``ODFlowResult`` with rendered count, max flow, and unmatched
    counts on each side.

    Chains into: ``qgis_figures_to_pptx``.
    """
    import csv as _csv

    from qgis_mcp_workflows.errors import FieldNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    abs_od = os.path.abspath(od_csv)
    abs_zones = os.path.abspath(zones_layer_path)
    abs_output = os.path.abspath(output_png)
    abs_basemaps = [os.path.abspath(p) for p in (basemap_paths or [])]
    basemap_spec = _resolve_basemap(basemap, basemap_opacity)

    with open(abs_od, encoding="utf-8", newline="") as f:
        reader = _csv.DictReader(f)
        columns = reader.fieldnames or []
        for required in (origin_col, dest_col, value_col):
            if required not in columns:
                raise FieldNotFoundError(required, columns)
        flows: list[dict] = []
        for row in reader:
            try:
                value = float(row[value_col])
            except (TypeError, ValueError):
                logger.warning("skipping non-numeric flow value: %s", row)
                continue
            flows.append({
                "origin": str(row[origin_col]),
                "destination": str(row[dest_col]),
                "value": value,
            })

    # Sort descending by value so plugin can compute max_flow up front and so
    # top_n picks the largest. Stable sort keeps tie-breaks deterministic.
    flows.sort(key=lambda f: f["value"], reverse=True)
    if top_n is not None and top_n > 0:
        flows = flows[:top_n]

    params = {
        "od_csv": abs_od,
        "zones_path": abs_zones,
        "output_png": abs_output,
        "flows": flows,
        "zone_id_field": zone_id_field,
        "arc_style": arc_style,
        "basemap_paths": abs_basemaps,
        "basemap_spec": basemap_spec,
        "label_field": label_field,
        "width": width,
        "height": height,
        "dpi": dpi,
        "scale_bar": scale_bar,
        "north_arrow": north_arrow,
    }
    result = get_executor().dispatch("render_od_flows", params, timeout=60)
    return ODFlowResult(
        output_path=result["output_path"],
        width=result["width"], height=result["height"], dpi=result["dpi"],
        extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
        n_flows=result["n_flows"],
        n_flows_rendered=result["n_flows_rendered"],
        n_zones=result["n_zones"],
        max_flow=result["max_flow"],
        min_flow_rendered=result["min_flow_rendered"],
        n_unmatched_origins=result["n_unmatched_origins"],
        n_unmatched_destinations=result["n_unmatched_destinations"],
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_link_density(
    drm_network_path: Annotated[str, Field(description="Absolute path to the pre-built DRM network GeoPackage. Build once via scripts/build_drm_network.py.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    trajectory_csvs: Annotated[list[str] | None, Field(description="One or more PFLOW trajectory CSV paths. Each must contain link_id_col. Streamed (not loaded fully); safe for multi-GB inputs. Mutually exclusive with load_csv.")] = None,
    load_csv: Annotated[str | None, Field(description="Pre-aggregated link volumes from qgis_assign_section_load. Columns: link_id_col + volume_col. Mutually exclusive with trajectory_csvs.")] = None,
    link_id_col: Annotated[str, Field(description="Join column on the trajectory/load CSV and the DRM layer.")] = "link_id",
    aggregation: Annotated[Literal["count", "sum"], Field(description="Per-link aggregation for trajectory_csvs. Ignored when load_csv is set.")] = "count",
    value_col: Annotated[str | None, Field(description="Numeric column to sum (trajectory_csvs + aggregation='sum'), or the volume column on load_csv (default 'volume').")] = None,
    n_classes: Annotated[int, Field(description="Number of graduated bins for symbology.", ge=2, le=15)] = 7,
    mode: Annotated[Literal["quantile", "equal_interval", "natural_breaks", "pretty"], Field(description="Binning strategy for graduated styling.")] = "quantile",
    palette: Annotated[str, Field(description='Sequential colorbrewer palette, e.g. "YlOrRd", "Blues", "Viridis".')] = "YlOrRd",
    min_density: Annotated[float, Field(description="Drop links with density below this. Use to denoise rare-traffic links.", ge=0.0)] = 1.0,
    top_n: Annotated[int | None, Field(description="Render only the top-N densest links. None = all matched links.")] = None,
    extent: Annotated[list[float] | None, Field(description="Render extent [xmin, ymin, xmax, ymax] in EPSG:4326. If omitted, uses DRM layer extent.")] = None,
    basemap_paths: Annotated[list[str] | None, Field(description="Optional vector basemap layers drawn under links.")] = None,
    basemap: Annotated[str, Field(description='Tile basemap drawn under the links for real-world context. Presets: "light" (neutral grey, best under choropleths), "dark", "streets" (OpenStreetMap), "imagery" (satellite); "none" for a plain white background. Any QuickMapServices source installed in the QGIS profile can be used as "qms:<id>" (e.g. "qms:opentopomap") — call qgis_list_basemaps for the ids. The old CARTO names (positron/dark_matter/voyager) still work as aliases. No API key needed.')] = "none",
    basemap_opacity: Annotated[float, Field(description="Opacity of the tile basemap, 0.0-1.0.", ge=0.0, le=1.0)] = 1.0,
    label_field: Annotated[str | None, Field(description="Haloed line label. Pass the density field name (or 'density') to label each link with its volume.")] = None,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
    scale_bar: Annotated[bool, Field(description="Draw a metric scale bar in the lower-left of the PNG.")] = False,
    north_arrow: Annotated[bool, Field(description="Draw a north arrow in the upper-right of the PNG.")] = False,
) -> LinkDensityResult:
    """Render a DRM-link traffic-density choropleth from PFLOW trajectories. v2 workflow tool.

    When to use: visualizing where in the road network traffic concentrates,
    aggregated over potentially many GB of trajectory CSVs. Major upgrade
    over `qgis_render_trajectory`'s raw GPS scatter when you care about
    network-level density rather than individual paths.

    Prerequisite: `assets/drm_network.gpkg` must exist (one-time build via
    `scripts/build_drm_network.py`). The tool raises ``DRMNetworkNotFoundError``
    with the exact build command if missing.

    Big-data discipline: trajectory CSVs are streamed row-by-row, never
    loaded fully. Aggregation happens MCP-side; only the per-link totals
    (typically <100k entries) are sent to the plugin.

    Returns: ``LinkDensityResult`` — totals, matched/unmatched counts,
    resolved breaks, min/max density.

    Chains into: ``qgis_figures_to_pptx``.
    """
    from qgis_mcp_workflows.errors import (
        DRMNetworkNotFoundError,
        EmptyAfterFilterError,
    )
    from qgis_mcp_workflows.executors import get_executor

    abs_drm = os.path.abspath(drm_network_path)
    abs_output = os.path.abspath(output_png)
    abs_basemaps = [os.path.abspath(p) for p in (basemap_paths or [])]
    basemap_spec = _resolve_basemap(basemap, basemap_opacity)

    if not os.path.exists(abs_drm):
        raise DRMNetworkNotFoundError(abs_drm)

    if load_csv and trajectory_csvs:
        raise ValueError("Pass trajectory_csvs or load_csv, not both.")
    if load_csv:
        density, n_rows_total = _read_load_csv(
            os.path.abspath(load_csv),
            link_id_col=link_id_col,
            volume_col=value_col or "volume",
        )
        aggregation = "sum"
        value_col = value_col or "volume"
    elif trajectory_csvs:
        abs_csvs = [os.path.abspath(p) for p in trajectory_csvs]
        density, n_rows_total = _aggregate_link_density(
            csv_paths=abs_csvs,
            link_id_col=link_id_col,
            aggregation=aggregation,
            value_col=value_col,
        )
    else:
        raise ValueError("qgis_render_link_density requires trajectory_csvs or load_csv.")

    n_points_total = int(sum(density.values())) if aggregation == "count" else n_rows_total

    if min_density > 0.0:
        density = {k: v for k, v in density.items() if v >= min_density}

    if top_n is not None and top_n > 0:
        sorted_items = sorted(density.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        density = dict(sorted_items)

    if not density:
        raise EmptyAfterFilterError(
            f"0 links left after min_density={min_density}, top_n={top_n}. "
            f"Aggregated {len(density)} links from {n_rows_total} rows."
        )

    params: dict = {
        "density": density,
        "drm_network_path": abs_drm,
        "output_png": abs_output,
        "link_id_col": link_id_col,
        "aggregation": aggregation,
        "value_col": value_col,
        "n_classes": n_classes,
        "mode": mode,
        "palette": palette,
        "extent": list(extent) if extent is not None else None,
        "basemap_paths": abs_basemaps,
        "basemap_spec": basemap_spec,
        "label_field": label_field,
        "width": width,
        "height": height,
        "dpi": dpi,
        "scale_bar": scale_bar,
        "north_arrow": north_arrow,
    }
    result = get_executor().dispatch("render_link_density", params, timeout=120)

    return LinkDensityResult(
        output_path=result["output_path"],
        width=result["width"], height=result["height"], dpi=result["dpi"],
        extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
        n_trajectory_rows_total=n_rows_total,
        n_points_total=n_points_total,
        n_links_with_traffic=result["n_links_with_traffic"],
        n_links_rendered=result["n_links_rendered"],
        n_unmatched_link_ids=result["n_unmatched_link_ids"],
        density_field=result["density_field"],
        breaks=result["breaks"],
        mode=result["mode"],
        min_density=result["min_density"],
        max_density=result["max_density"],
        aggregation=aggregation,
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
    )


class SectionLoadResult(BaseModel):
    output_csv: str
    n_od: int
    n_assigned: int
    n_unassigned: int
    n_unmatched_origins: int
    n_unmatched_destinations: int
    n_links_with_load: int
    n_nodes: int
    n_edges: int


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_assign_section_load(
    od_csv: Annotated[str, Field(description="Long-format OD CSV (origin, destination, value).")],
    network_path: Annotated[str, Field(description="Line network as GeoJSON or GeoPackage (DRM). Endpoints become graph nodes unless from_node/to_node fields are set.")],
    output_csv: Annotated[str, Field(description="Absolute path for the link_id,volume CSV. Feed this to qgis_render_link_density(load_csv=...).")],
    zones_path: Annotated[str | None, Field(description="Polygon/point layer used to snap OD ids to nearest network nodes via centroids. Omit if origin/dest already match graph node ids.")] = None,
    origin_col: Annotated[str, Field(description="Origin column in od_csv.")] = "origin",
    dest_col: Annotated[str, Field(description="Destination column in od_csv.")] = "destination",
    value_col: Annotated[str, Field(description="OD volume column.")] = "trip_count",
    zone_id_field: Annotated[str, Field(description="Zone id field on zones_path.")] = "zone_id",
    link_id_field: Annotated[str, Field(description="Link id field on the network.")] = "link_id",
    from_node_field: Annotated[str | None, Field(description="Optional from-node field; if omitted, nodes are rounded line endpoints.")] = None,
    to_node_field: Annotated[str | None, Field(description="Optional to-node field.")] = None,
) -> SectionLoadResult:
    """All-or-nothing assignment of OD volumes onto a road network.

    When to use: turn an OD matrix into section loads (link volumes) for
    ``qgis_render_link_density``. Shortest-path assignment via networkx
    (``uv sync --extra network``). Zones snap to the nearest network node.

    Returns: ``SectionLoadResult`` with the volume CSV path and assignment
    counts. Unroutable OD pairs increment ``n_unassigned`` instead of failing.

    Chains into: ``qgis_render_link_density(load_csv=output_csv)``.
    """
    from qgis_mcp_workflows.errors import FieldNotFoundError
    from qgis_mcp_workflows.section_load import (
        assign_aon,
        load_network,
        load_zone_centroids,
        read_od_csv,
        snap_to_nodes,
        write_volume_csv,
    )

    abs_od = os.path.abspath(od_csv)
    abs_net = os.path.abspath(network_path)
    abs_out = os.path.abspath(output_csv)
    rows, columns = read_od_csv(abs_od)
    for required in (origin_col, dest_col, value_col):
        if required not in columns:
            raise FieldNotFoundError(required, columns)

    graph, node_xy = load_network(
        abs_net,
        link_id_field=link_id_field,
        from_node_field=from_node_field,
        to_node_field=to_node_field,
    )
    if zones_path:
        centroids = load_zone_centroids(os.path.abspath(zones_path), zone_id_field=zone_id_field)
        snapped = snap_to_nodes(centroids, node_xy)
        origin_nodes = dest_nodes = snapped
    else:
        # OD ids are already graph node keys.
        identity = {str(row[origin_col]): str(row[origin_col]) for row in rows}
        identity.update({str(row[dest_col]): str(row[dest_col]) for row in rows})
        origin_nodes = dest_nodes = identity

    volumes, stats = assign_aon(
        graph,
        rows,
        origin_nodes=origin_nodes,
        dest_nodes=dest_nodes,
        origin_col=origin_col,
        dest_col=dest_col,
        value_col=value_col,
    )
    write_volume_csv(abs_out, volumes, link_id_col=link_id_field)
    return SectionLoadResult(output_csv=abs_out, **stats)


class RouteResult(BaseModel):
    output_csv: str
    n_trips: int
    n_legs: int
    n_routed: int
    n_straight: int
    n_points: int
    network_path: str


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_route_on_network(
    input_csv: Annotated[str, Field(description="Stop/point CSV. Consecutive rows per trip_id (ordered by seq) become legs.")],
    network_path: Annotated[str, Field(description="Road network: GUFM drm_inner_tokyo.tsv, or a line GeoJSON/GeoPackage with link_id.")],
    output_csv: Annotated[str, Field(description="Routed point CSV (trip_id,seq,lon,lat,mode,datetime,link_id) for qgis_render_trajectory.")],
    rail_network_path: Annotated[str | None, Field(description="Optional rail TSV/GeoJSON. Legs whose mode is RAIL/TRAIN use this instead of the road network.")] = None,
    id_col: Annotated[str, Field(description="Trip grouping column. If missing, all rows are one trip.")] = "trip_id",
    seq_col: Annotated[str, Field(description="Order column within a trip.")] = "seq",
    lon_col: Annotated[str, Field(description="Longitude column.")] = "lon",
    lat_col: Annotated[str, Field(description="Latitude column.")] = "lat",
    mode_col: Annotated[str | None, Field(description="Mode column (destination of each leg). Default CAR when absent.")] = "mode",
    time_col: Annotated[str | None, Field(description="Optional clock/datetime column (e.g. GUFM hero_stops 'clock').")] = None,
    max_snap_km: Annotated[float, Field(description="If either end is farther than this from the network, the leg stays a straight line.", gt=0.0, le=50.0)] = 3.0,
    link_id_field: Annotated[str, Field(description="link_id field when network_path is GeoJSON/GPKG.")] = "link_id",
    from_node_field: Annotated[str | None, Field(description="Optional from-node field on a GeoJSON/GPKG network.")] = None,
    to_node_field: Annotated[str | None, Field(description="Optional to-node field on a GeoJSON/GPKG network.")] = None,
) -> RouteResult:
    """Snap consecutive stops onto DRM/rail and write a routed trajectory CSV.

    When to use: GUFM stop sequences (hero_stops.csv, decoded ACT locations)
    before ``qgis_render_trajectory``. Same idea as
    ``gufm/scripts/figures/routing.py``, as an MCP workflow: shortest path on
    ``drm_inner_tokyo.tsv`` (road) / ``rail_inner_tokyo.tsv`` (RAIL legs),
    straight-line fallback when snap fails or the graph is disconnected.

    Needs ``uv sync --extra network``. The 57 MB Tokyo TSV is read from disk
    (not copied into this repo) and cached for the rest of the process.

    Chains into: ``qgis_render_trajectory(input_path=output_csv, mode_col='mode')``.
    """
    from qgis_mcp_workflows.errors import FieldNotFoundError, LayerNotFoundError
    from qgis_mcp_workflows.route import (
        load_route_network,
        read_stop_csv,
        route_sequences,
        write_routed_csv,
    )

    abs_in = os.path.abspath(os.path.expanduser(input_csv))
    abs_net = os.path.abspath(os.path.expanduser(network_path))
    abs_out = os.path.abspath(os.path.expanduser(output_csv))
    if not os.path.isfile(abs_in):
        raise LayerNotFoundError(abs_in)
    if not os.path.isfile(abs_net):
        raise LayerNotFoundError(abs_net)
    rows, columns = read_stop_csv(abs_in)
    for required in (lon_col, lat_col):
        if required not in columns:
            raise FieldNotFoundError(required, columns)
    id_use = id_col if id_col in columns else "trip_id"
    seq_use = seq_col if seq_col in columns else "seq"
    mode_use = mode_col if mode_col and mode_col in columns else None
    time_use = time_col if time_col and time_col in columns else None
    road = load_route_network(
        abs_net,
        link_id_field=link_id_field,
        from_node_field=from_node_field,
        to_node_field=to_node_field,
    )
    rail = None
    if rail_network_path:
        abs_rail = os.path.abspath(os.path.expanduser(rail_network_path))
        if not os.path.isfile(abs_rail):
            raise LayerNotFoundError(abs_rail)
        rail = load_route_network(abs_rail)
    routed, stats = route_sequences(
        rows,
        road,
        rail=rail,
        id_col=id_use,
        seq_col=seq_use,
        lon_col=lon_col,
        lat_col=lat_col,
        mode_col=mode_use,
        time_col=time_use,
        max_snap_km=max_snap_km,
    )
    write_routed_csv(abs_out, routed)
    return RouteResult(output_csv=abs_out, network_path=abs_net, **stats)


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_diagram_map(
    layer_path: Annotated[str, Field(description="Absolute path to a vector layer (polygons or points).")],
    value_fields: Annotated[list[str], Field(description="Numeric fields to chart per feature (>=1). Each becomes a pie slice / bar.")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    diagram_type: Annotated[Literal["pie", "bar"], Field(description="Chart drawn on each feature.")] = "pie",
    size: Annotated[float, Field(description="Diagram size in mm.", gt=0.0, le=80.0)] = 10.0,
    palette: Annotated[str, Field(description='Palette for the series colors (e.g. "Set2", "Dark2", "viridis").')] = "Set2",
    extent: Annotated[list[float] | None, Field(description="Render extent [xmin, ymin, xmax, ymax] in the layer's CRS. Omit for full extent + 5%.")] = None,
    basemap: Annotated[BasemapName, Field(description='Tile basemap under the diagrams. "none" = white background.')] = "none",
    basemap_opacity: Annotated[float, Field(description="Tile basemap opacity 0.0-1.0.", ge=0.0, le=1.0)] = 1.0,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
) -> DiagramMapResult:
    """Render pie/bar charts on each map feature — "chart in map". v2 workflow tool.

    When to use: show a small multivariate breakdown (e.g., arrivals vs departures,
    mode split) per zone/station directly on the map, instead of a single
    choropleth color. Each value_field becomes a pie slice or bar.

    Returns: ``DiagramMapResult`` with the diagram type, charted fields, and
    feature count.

    Chains into: ``qgis_compose_layout``, ``qgis_figures_to_pptx``.
    """
    from qgis_mcp_workflows.executors import get_executor

    abs_layer = os.path.abspath(layer_path)
    abs_output = os.path.abspath(output_png)
    params = {
        "layer_path": abs_layer,
        "value_fields": list(value_fields),
        "output_png": abs_output,
        "diagram_type": diagram_type,
        "size": size,
        "palette": palette,
        "extent": list(extent) if extent is not None else None,
        "basemap_spec": _resolve_basemap(basemap, basemap_opacity),
        "width": width,
        "height": height,
        "dpi": dpi,
    }
    result = get_executor().dispatch("render_diagram_map", params, timeout=120)
    return DiagramMapResult(
        output_path=result["output_path"],
        width=result["width"], height=result["height"], dpi=result["dpi"],
        extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
        diagram_type=result["diagram_type"],
        value_fields=result["value_fields"],
        n_features=result["n_features"],
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_render_catchment(
    points_path: Annotated[str, Field(description="Absolute path to a point layer (e.g. stations).")],
    output_png: Annotated[str, Field(description="Absolute path for the output PNG.")],
    method: Annotated[Literal["voronoi"], Field(description="Catchment method. 'voronoi' = Thiessen service areas (one cell per point, nearest-point tessellation). Buffer rings / network isochrones are future methods.")] = "voronoi",
    extent: Annotated[list[float] | None, Field(description="Render extent [xmin, ymin, xmax, ymax] in the layer's CRS. Omit for full extent + 5%.")] = None,
    basemap: Annotated[BasemapName, Field(description='Tile basemap under the catchments. "none" = white background.')] = "none",
    basemap_opacity: Annotated[float, Field(description="Tile basemap opacity 0.0-1.0.", ge=0.0, le=1.0)] = 1.0,
    width: Annotated[int, Field(description="Image width in pixels.", ge=200, le=8000)] = 1600,
    height: Annotated[int, Field(description="Image height in pixels.", ge=200, le=8000)] = 1200,
    dpi: Annotated[int, Field(description="Image DPI.", ge=72, le=600)] = 150,
) -> CatchmentResult:
    """Render Voronoi service-area catchments around points. v2 workflow tool.

    When to use: approximate each station/facility's service area as its Thiessen
    cell (nearest-point tessellation) — the catchment method from the TransInfor
    fig05. Uses a pure QgsGeometry Voronoi op (no Processing framework).

    Returns: ``CatchmentResult`` with the method, point count, and catchment count.

    Chains into: ``qgis_compose_layout``, ``qgis_figures_to_pptx``.
    """
    from qgis_mcp_workflows.executors import get_executor

    abs_points = os.path.abspath(points_path)
    abs_output = os.path.abspath(output_png)
    params = {
        "points_path": abs_points,
        "output_png": abs_output,
        "method": method,
        "extent": list(extent) if extent is not None else None,
        "basemap_spec": _resolve_basemap(basemap, basemap_opacity),
        "width": width,
        "height": height,
        "dpi": dpi,
    }
    result = get_executor().dispatch("render_catchment", params, timeout=180)
    return CatchmentResult(
        output_path=result["output_path"],
        width=result["width"], height=result["height"], dpi=result["dpi"],
        extent=result["extent"], crs=result["crs"], n_layers=result["n_layers"],
        method=result["method"],
        n_points=result["n_points"],
        n_catchments=result["n_catchments"],
        basemap_attribution=result.get("basemap_attribution"),
        basemap_source=result.get("basemap_source"),
    )


# ---------------------------------------------------------------------------
# Tools — Export & Batch & Delivery (3)
# ---------------------------------------------------------------------------


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_export_layout(
    qgz_path: Annotated[str, Field(description="Absolute path to a .qgz or .qgs project containing the layout.")],
    layout_name: Annotated[str, Field(description="Print-composer layout name. List available layouts via qgis_project_load.")],
    output_path: Annotated[str, Field(description="Absolute path for the output file. Extension can be .png, .pdf, or .svg.")],
    format: Annotated[Literal["png", "pdf", "svg"], Field(description="Output format. Should match output_path extension.")] = "png",
    dpi: Annotated[int, Field(description="Export DPI.", ge=72, le=600)] = 300,
) -> ExportResult:
    """Export a saved print-composer layout to file.

    When to use: when the user has designed a polished figure layout in
    QGIS (titles, legends, scale bars, multi-panel) and wants to export it
    rather than re-render programmatically.

    Returns: ``ExportResult`` with format, page count, and resolved layout name.

    Chains into: ``qgis_figures_to_pptx``, ``qgis_batch_render``.
    """
    from qgis_mcp_workflows.errors import ExecutorError, LayoutNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    abs_qgz = os.path.abspath(qgz_path)
    abs_output = os.path.abspath(output_path)
    params = {
        "qgz_path": abs_qgz,
        "layout_name": layout_name,
        "output_path": abs_output,
        "format": format,
        "dpi": dpi,
    }
    try:
        result = get_executor().dispatch("export_layout", params, timeout=60)
    except ExecutorError as err:
        if "LAYOUT_NOT_FOUND" in err.message:
            import re

            avail_match = re.search(r"Available:\s*\[([^\]]*)\]", err.message)
            avail = []
            if avail_match:
                avail = [
                    s.strip().strip("'\"")
                    for s in avail_match.group(1).split(",")
                    if s.strip()
                ]
            raise LayoutNotFoundError(layout_name, avail) from err
        raise

    return ExportResult(
        output_path=result["output_path"],
        format=result["format"],
        n_pages=result["n_pages"],
        layout_name=result["layout_name"],
    )


class AtlasExportResult(BaseModel):
    output_dir: str
    output_path: str
    format: str
    n_pages: int
    layout_name: str
    files: list[str]


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_export_atlas(
    qgz_path: Annotated[str, Field(description="Absolute path to a .qgz/.qgs whose layout has an atlas coverage layer.")],
    layout_name: Annotated[str, Field(description="Print-composer layout name with atlas enabled.")],
    output_dir: Annotated[str, Field(description="Directory for atlas pages (created if missing). PNG: one file per feature. PDF: atlas.pdf.")],
    format: Annotated[Literal["png", "pdf", "jpg"], Field(description="png/jpg = one image per coverage feature; pdf = one multi-page file.")] = "png",
    dpi: Annotated[int, Field(description="Export DPI.", ge=72, le=600)] = 300,
) -> AtlasExportResult:
    """Export every page of a print-layout atlas.

    When to use: a .qgz already has an atlas (coverage layer + filename
    expression) — e.g. one PNG per prefecture. Complements ``qgis_batch_render``
    (attribute fan-out without a pre-authored atlas) and ``qgis_export_layout``
    (single page).

    Returns: ``AtlasExportResult`` with ``files`` (absolute paths) and ``n_pages``.
    """
    from qgis_mcp_workflows.errors import AtlasDisabledError, ExecutorError, LayoutNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    abs_qgz = os.path.abspath(qgz_path)
    abs_dir = os.path.abspath(output_dir)
    os.makedirs(abs_dir, exist_ok=True)
    params = {
        "qgz_path": abs_qgz,
        "layout_name": layout_name,
        "output_dir": abs_dir,
        "format": format,
        "dpi": dpi,
    }
    try:
        result = get_executor().dispatch("export_atlas", params, timeout=300)
    except ExecutorError as err:
        if "LAYOUT_NOT_FOUND" in err.message:
            raise LayoutNotFoundError(layout_name, []) from err
        if "ATLAS_DISABLED" in err.message:
            raise AtlasDisabledError(layout_name) from err
        raise
    files = [os.path.abspath(p) for p in result.get("files") or []]
    return AtlasExportResult(
        output_dir=result.get("output_dir", abs_dir),
        output_path=result.get("output_path") or (files[0] if files else abs_dir),
        format=result.get("format", format),
        n_pages=int(result.get("n_pages") or len(files)),
        layout_name=result.get("layout_name", layout_name),
        files=files,
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_compose_layout(
    layer_paths: Annotated[list[str], Field(description="Absolute paths to layers in bottom->top draw order (vector or raster). Styling comes from each layer's own saved style; default symbology otherwise.")],
    output_path: Annotated[str, Field(description="Absolute output path; format inferred from extension (.png / .pdf / .svg).")],
    title: Annotated[str | None, Field(description="Optional title across the top of the page.")] = None,
    extent: Annotated[list[float] | None, Field(description="Map extent [xmin, ymin, xmax, ymax] in the layers' CRS. If omitted, uses the union of layer extents + 5% padding.")] = None,
    page: Annotated[Literal["a4_landscape", "a4_portrait", "a3_landscape", "square"], Field(description="Page size preset.")] = "a4_landscape",
    legend: Annotated[bool, Field(description="Add a legend linked to the map.")] = True,
    scale_bar: Annotated[bool, Field(description="Add a scale bar linked to the map.")] = True,
    north_arrow: Annotated[bool, Field(description="Add a north arrow from QGIS's bundled SVGs.")] = True,
    dpi: Annotated[int, Field(description="Export DPI.", ge=72, le=600)] = 300,
) -> ComposeLayoutResult:
    """Compose a deck-ready print layout from layers and export it. v2 workflow tool.

    When to use: turn one or more data/rendered layers into a publication figure
    with a titled map panel plus a linked legend, scale bar and north arrow — the
    gap that ``qgis_export_layout`` (which only exports pre-authored .qgz layouts)
    leaves open. Single panel for now; multi-panel / inset is a future extension.

    Returns: ``ComposeLayoutResult`` with output path, format, layer count, the
    furniture items added, and page size in mm.

    Chains into: ``qgis_figures_to_pptx``.
    """
    from qgis_mcp_workflows.executors import get_executor

    abs_layers = [os.path.abspath(p) for p in layer_paths]
    abs_output = os.path.abspath(output_path)
    params = {
        "layer_paths": abs_layers,
        "output_path": abs_output,
        "title": title,
        "extent": list(extent) if extent is not None else None,
        "page": page,
        "legend": legend,
        "scale_bar": scale_bar,
        "north_arrow": north_arrow,
        "dpi": dpi,
    }
    result = get_executor().dispatch("compose_layout", params, timeout=120)
    return ComposeLayoutResult(
        output_path=result["output_path"],
        format=result["format"],
        n_layers=result["n_layers"],
        items=result["items"],
        page_size_mm=result["page_size_mm"],
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_batch_render(
    template_qgz: Annotated[str, Field(description="Absolute path to a template project. Must contain a single 'active' layer to filter and (optionally) a layout to export.")],
    attribute: Annotated[str, Field(description="Field on the active layer to filter by.")],
    values: Annotated[list[str], Field(description="Filter values to iterate. One render per value.")],
    output_dir: Annotated[str, Field(description="Absolute path to a directory where renders are written.")],
    layout_name: Annotated[str | None, Field(description="If given, exports the layout with this name; otherwise renders the map canvas.")] = None,
    filename_template: Annotated[str, Field(description="Filename template, e.g. '{value}.png' or 'choropleth_{value}.png'.")] = "{value}.png",
) -> BatchRenderResult:
    """Fan-out: render the same template per filter value. Workflow tool.

    When to use: 'render the OD map for each scenario', 'render the
    choropleth for each timestep', 'one figure per prefecture'. Emits a
    manifest you can feed straight into ``qgis_figures_to_pptx``.

    Returns: ``BatchRenderResult`` with output_dir, n_rendered, manifest
    of (value → output_path → extent), and a separate errors list for
    values that failed (e.g., zero matching features).

    Chains into: ``qgis_figures_to_pptx``.
    """
    from qgis_mcp_workflows.errors import ExecutorError, FieldNotFoundError
    from qgis_mcp_workflows.executors import get_executor

    abs_template = os.path.abspath(template_qgz)
    abs_output_dir = os.path.abspath(output_dir)

    if not values:
        return BatchRenderResult(
            output_dir=abs_output_dir,
            n_rendered=0,
            manifest=[],
            errors=[],
        )

    params = {
        "template_qgz": abs_template,
        "attribute": attribute,
        "values": list(values),
        "output_dir": abs_output_dir,
        "layout_name": layout_name,
        "filename_template": filename_template,
    }
    try:
        result = get_executor().dispatch("batch_render", params, timeout=300)
    except ExecutorError as err:
        if "FIELD_NOT_FOUND" in err.message:
            import re

            field_match = re.search(r"FIELD_NOT_FOUND:\s*['\"]([^'\"]+)['\"]", err.message)
            avail_match = re.search(r"Available:\s*\[([^\]]*)\]", err.message)
            field = field_match.group(1) if field_match else attribute
            avail = []
            if avail_match:
                avail = [
                    s.strip().strip("'\"")
                    for s in avail_match.group(1).split(",")
                    if s.strip()
                ]
            raise FieldNotFoundError(field, avail) from err
        raise

    return BatchRenderResult(
        output_dir=result["output_dir"],
        n_rendered=result["n_rendered"],
        manifest=[
            BatchManifestEntry(
                value=m["value"], output_path=m["output_path"], extent=m["extent"]
            )
            for m in result.get("manifest", [])
        ],
        errors=[
            BatchError(value=e["value"], error=e["error"])
            for e in result.get("errors", [])
        ],
    )


@_maybe_tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=True, destructiveHint=False, openWorldHint=True
    )
)
def qgis_figures_to_pptx(
    figure_paths: Annotated[list[str], Field(description="Absolute paths to PNG/JPG figure files. One per slide.")],
    pptx_path: Annotated[str, Field(description="Absolute path for the output .pptx.")],
    layout: Annotated[Literal["title_and_image", "image_only", "two_column", "title_image_caption"], Field(description='Slide layout. "two_column" pairs consecutive figures. "title_image_caption" uses a newline in captions[i] as title vs body under the figure.')] = "title_and_image",
    captions: Annotated[list[str] | None, Field(description="Optional per-slide captions. Must match length of figure_paths if given.")] = None,
    template_pptx: Annotated[str | None, Field(description="If given, slides are appended to this template. If omitted, the bundled Sekimoto-lab blank (assets/sekilab_blank.pptx) is used when present.")] = None,
) -> PptxResult:
    """Drop figures into a PowerPoint deck. Delivery tool — closes the W17 loop.

    When to use: as the final step of a figure pipeline. After rendering
    one or more PNGs (via ``qgis_render_*`` or ``qgis_batch_render``), call
    this once to assemble them into a deck.

    Returns: ``PptxResult`` with pptx_path, slides added/total, and
    per-slide titles (None for slides without titles).
    """
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    if captions is not None and len(captions) != len(figure_paths):
        raise ValueError(
            f"captions length ({len(captions)}) must match figure_paths length "
            f"({len(figure_paths)}). Pass captions=None to skip titles entirely."
        )

    from qgis_mcp_workflows.helpers import (
        SEKILAB_SLIDE_HEIGHT,
        SEKILAB_SLIDE_WIDTH,
        bundled_asset,
    )

    abs_figs = [os.path.abspath(fig) for fig in figure_paths]
    abs_pptx = os.path.abspath(pptx_path)
    if template_pptx:
        prs = Presentation(os.path.abspath(template_pptx))
    else:
        bundled = bundled_asset("assets", "sekilab_blank.pptx")
        if bundled:
            prs = Presentation(bundled)
        else:
            prs = Presentation()
            prs.slide_width = SEKILAB_SLIDE_WIDTH
            prs.slide_height = SEKILAB_SLIDE_HEIGHT
    n_layouts = len(prs.slide_layouts)
    blank = prs.slide_layouts[6] if n_layouts > 6 else prs.slide_layouts[n_layouts - 1]
    title_only = prs.slide_layouts[5] if n_layouts > 5 else prs.slide_layouts[0]
    slide_w = int(prs.slide_width)

    def _caption_at(i: int) -> str | None:
        if captions is None:
            return None
        return captions[i]

    def _split_title_body(text: str | None) -> tuple[str | None, str | None]:
        if not text:
            return None, None
        if "\n" in text:
            head, tail = text.split("\n", 1)
            return (head.strip() or None), (tail.strip() or None)
        return text, None

    def _add_textbox(slide, left, top, width, height, text: str, *, size=12, bold=False):
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        p.font.size = Pt(size)
        p.font.bold = bold
        p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
        p.alignment = PP_ALIGN.LEFT
        return box

    slide_titles: list[str | None] = []
    slides_added = 0

    if layout == "two_column":
        # Pair consecutive figures on one slide: left / right, caption under each.
        for i in range(0, len(abs_figs), 2):
            slide = prs.slides.add_slide(blank)
            slides_added += 1
            gutter = Inches(0.35)
            col_w = (slide_w - gutter * 3) // 2
            top = Inches(0.4)
            img_h = Inches(5.4)
            cap_h = Inches(1.0)
            pair_titles: list[str] = []
            for col, idx in enumerate((i, i + 1)):
                if idx >= len(abs_figs):
                    break
                left = gutter + col * (col_w + gutter)
                slide.shapes.add_picture(abs_figs[idx], left, top, width=col_w)
                cap = _caption_at(idx)
                if cap:
                    _add_textbox(slide, left, top + img_h + Inches(0.1), col_w, cap_h, cap, size=12)
                    pair_titles.append(cap.split("\n", 1)[0])
            slide_titles.append(" | ".join(pair_titles) if pair_titles else None)
    elif layout == "title_image_caption":
        # Title on top; image; leftover caption lines under the figure.
        # A newline in captions[i] splits title vs body; a single line is the title.
        for i, fig in enumerate(abs_figs):
            slide = prs.slides.add_slide(title_only)
            slides_added += 1
            title_text, body = _split_title_body(_caption_at(i))
            if title_text and slide.shapes.title is not None:
                slide.shapes.title.text = title_text
            slide_titles.append(title_text)
            img_top = Inches(1.35)
            img_left = Inches(0.6)
            img_width = slide_w - Inches(1.2)
            img_height = Inches(4.7) if body else Inches(5.5)
            slide.shapes.add_picture(fig, img_left, img_top, width=img_width, height=img_height)
            if body:
                _add_textbox(
                    slide,
                    img_left,
                    img_top + img_height + Inches(0.08),
                    img_width,
                    Inches(0.9),
                    body,
                    size=13,
                )
    else:
        # title_and_image (layout 5) / image_only (layout 6) — unchanged.
        layout_idx = 6 if layout == "image_only" else 5
        if layout_idx >= n_layouts:
            layout_idx = 0 if layout != "image_only" else n_layouts - 1
        chosen = prs.slide_layouts[layout_idx]
        for i, fig in enumerate(abs_figs):
            slide = prs.slides.add_slide(chosen)
            slides_added += 1
            title_text = None
            if layout != "image_only" and _caption_at(i) and slide.shapes.title is not None:
                title_text = _caption_at(i)
                slide.shapes.title.text = title_text
            slide_titles.append(title_text)
            slide.shapes.add_picture(fig, Inches(0.5), Inches(1.5), height=Inches(5.5))

    prs.save(abs_pptx)
    return PptxResult(
        pptx_path=abs_pptx,
        n_slides_added=slides_added,
        n_slides_total=len(prs.slides),
        slide_titles=slide_titles,
    )


# ---------------------------------------------------------------------------
# Tools — Escape hatch (1)
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, idempotentHint=False, destructiveHint=True, openWorldHint=True
    )
)
def qgis_eval(
    code: Annotated[str, Field(description="PyQGIS source to execute.")],
    return_vars: Annotated[list[str] | None, Field(description="Local variable names to capture from the executed scope and return JSON-serialized.")] = None,
) -> EvalResult:
    """Execute arbitrary PyQGIS. Escape hatch — prefer the workflow tools.

    When to use: only when no workflow tool fits — e.g., an unusual
    processing algorithm, a custom symbology that ``qgis_style_*`` can't
    express, or interactive debugging. For routine work (load, style,
    render, export, batch, deck) use the dedicated tools.

    In plugin transport, executes inside the running QGIS process. In
    headless transport, executes in the standalone PyQGIS subprocess.
    Either way, this can mutate state — annotated as destructive.

    Returns: ``EvalResult`` with stdout, stderr, captured ``return_values``
    (if ``return_vars`` was given), and exception traceback (if any).
    """
    from qgis_mcp_workflows.executors import get_executor

    params: dict = {"code": code}
    if return_vars is not None:
        params["return_vars"] = list(return_vars)

    result = get_executor().dispatch("execute_code", params, timeout=300)

    exception_text: str | None = None
    if not result.get("executed", True):
        exception_text = result.get("traceback") or result.get("error")

    return EvalResult(
        stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""),
        return_values=result.get("return_values") if return_vars is not None else None,
        exception=exception_text,
    )


# Trigger compound-mode tool registration if env var requested it.
# This must come AFTER all standalone tool functions are defined (compound.py imports them).
_register_compound_tools_if_enabled()


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------


def _plugin_reachable(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """Probe ``host:port`` with a short connect timeout. Used by ``auto`` mode."""
    import socket as _socket

    try:
        with _socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _build_executor(transport: str):
    """Construct the executor for the chosen transport.

    ``auto`` first probes the plugin port; falls back to headless if the plugin
    is not reachable. Errors from headless construction propagate so the user
    sees a single clear ``HeadlessUnavailableError`` rather than a silent fall.
    """
    from qgis_mcp_workflows.executors.plugin import PluginExecutor
    from qgis_mcp_workflows.helpers import DEFAULT_HOST, DEFAULT_PORT

    host = os.environ.get("QGIS_MCP_WORKFLOWS_HOST", DEFAULT_HOST)
    port = int(os.environ.get("QGIS_MCP_WORKFLOWS_PORT", str(DEFAULT_PORT)))

    if transport == "plugin":
        return PluginExecutor(host=host, port=port), "plugin"
    if transport == "headless":
        from qgis_mcp_workflows.executors.headless import HeadlessExecutor

        return HeadlessExecutor(), "headless"
    if transport == "auto":
        if _plugin_reachable(host, port):
            return PluginExecutor(host=host, port=port), "plugin"
        from qgis_mcp_workflows.executors.headless import HeadlessExecutor

        return HeadlessExecutor(), "headless"
    raise ValueError(f"Unknown transport: {transport!r}. Use plugin / headless / auto.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _version() -> str:
    """Package version, read from installed metadata.

    Previously hardcoded, which silently drifted (server logged 1.3.0 while
    pyproject.toml said 1.4.0). Reading it keeps the banner honest.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("qgis-mcp-workflows")
    except PackageNotFoundError:  # running from a source tree without an install
        return "unknown"


def main() -> None:
    """Run the MCP server. CLI flag ``--transport`` overrides the env default."""
    import argparse

    parser = argparse.ArgumentParser(prog="qgis-mcp-workflows-server")
    parser.add_argument(
        "--transport",
        choices=("plugin", "headless", "auto"),
        default=os.environ.get("QGIS_MCP_WORKFLOWS_TRANSPORT", "auto"),
        help="QGIS backend: plugin (TCP socket to running QGIS Desktop), "
        "headless (PyQGIS subprocess), auto (probe plugin port, fall back to "
        "headless). Default: auto. Env: QGIS_MCP_WORKFLOWS_TRANSPORT.",
    )
    args = parser.parse_args()

    from qgis_mcp_workflows.executors import set_executor

    executor, chosen = _build_executor(args.transport)
    set_executor(executor)
    logger.info("qgis-mcp-workflows server starting (v%s, transport=%s)", _version(), chosen)
    mcp.run()


if __name__ == "__main__":
    main()

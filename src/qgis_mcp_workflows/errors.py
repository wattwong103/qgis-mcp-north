"""Typed exceptions for qgis-mcp-workflows tools.

Every exception message ends with one suggested next tool call, per
``docs/DESIGN.md`` §5 ("actionable error messages"). Tools raise these;
FastMCP turns them into MCP error responses.
"""

from __future__ import annotations

try:  # mcp >= 2.0 renamed fastmcp -> mcpserver (same shim as server.py)
    from mcp.server.mcpserver.exceptions import ToolError
except ModuleNotFoundError:
    from mcp.server.fastmcp.exceptions import ToolError


class QgisMcpWorkflowsError(ToolError):
    """Base class for all qgis-mcp-workflows tool errors.

    Subclasses the SDK's ``ToolError`` because mcp >= 2.1 hides the message of
    any other exception ("Error executing tool <name>"), which would drop every
    ``Next:`` hint below.
    """


class InvalidArgumentError(QgisMcpWorkflowsError, ValueError):
    """The call is missing an argument or combines arguments that conflict.

    Also a ``ValueError``, which these checks raised before they were typed.
    """

    def __init__(
        self, message: str, next_step: str = "retry the same call with the arguments named above"
    ) -> None:
        super().__init__(f"{message} Next: {next_step}.")


class InputFileNotFoundError(QgisMcpWorkflowsError, FileNotFoundError):
    """An input file argument (CSV, figure, template) does not exist.

    Also a ``FileNotFoundError``, which ``open()`` raised before this was typed.
    """

    def __init__(self, argument: str, path: str) -> None:
        super().__init__(
            f"{argument} not found: {path!r}. "
            f"Next: pass an absolute path that exists on this machine, then retry."
        )
        self.argument = argument
        self.path = path


class PluginUnavailableError(QgisMcpWorkflowsError):
    """The QGIS plugin socket isn't reachable."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(
            f"Cannot reach QGIS plugin at {host}:{port}. "
            f"Open QGIS, enable the 'QGIS MCP Workflows' plugin, and click Run MCP "
            f"on the toolbar (Plugins menu: QGIS MCP). "
            f"That button's menu has Auto-start on startup, which stays off until you tick it. "
            f"Next: click Run MCP, then retry the same tool call."
        )
        self.host = host
        self.port = port


class ExecutorError(QgisMcpWorkflowsError):
    """Generic plugin-side error (unmapped)."""

    def __init__(self, command: str, message: str) -> None:
        # A plugin message that already says what to do keeps that one hint.
        hint = "" if "Next:" in message else " Next: inspect inputs, then retry. If persistent, escape via qgis_eval."
        super().__init__(f"Plugin command '{command}' failed: {message}.{hint}")
        self.command = command
        self.message = message


class LayerNotFoundError(QgisMcpWorkflowsError):
    """A layer file or layer_id couldn't be resolved."""

    def __init__(self, path_or_id: str) -> None:
        super().__init__(
            f"Layer not found: {path_or_id!r}. "
            f"Next: call qgis_layer_inspect with an absolute path to verify the file exists."
        )
        self.path_or_id = path_or_id


class FieldNotFoundError(QgisMcpWorkflowsError):
    """A field name isn't present on the layer / CSV."""

    def __init__(self, field: str, available: list[str]) -> None:
        avail = ", ".join(available) if available else "(none)"
        super().__init__(
            f"Field {field!r} not found. Available: {avail}. "
            f"Next: call qgis_layer_inspect to see actual field names, then retry with the correct value_field."
        )
        self.field = field
        self.available = available


class BasemapNotFoundError(QgisMcpWorkflowsError):
    """An unknown basemap name that is neither a preset nor a ``qms:`` reference."""

    def __init__(self, basemap: str, available: list[str]) -> None:
        avail = ", ".join(available) if available else "(none)"
        super().__init__(
            f"Unknown basemap {basemap!r}. Built-in presets: {avail}, or 'none'. "
            f"For a QuickMapServices source use the 'qms:<id>' form. "
            f"Next: call qgis_list_basemaps to see every id available in this QGIS profile."
        )
        self.basemap = basemap
        self.available = available


class JoinError(QgisMcpWorkflowsError):
    """A CSV → polygon join produced zero matches (clearly wrong join key).

    Constructor takes a free-form description; the plugin already embeds the
    join field name and sample CSV / layer values in its error message.
    """

    def __init__(self, description: str) -> None:
        super().__init__(
            f"{description} "
            f"Likely cause: zone-id systems differ (PFLOW uses MFS##, PRF##, Z## in different files). "
            f"Next: call qgis_layer_inspect on the polygon layer to see the actual join field's values."
        )
        self.description = description


class PluginOutdatedError(QgisMcpWorkflowsError):
    """The running QGIS side ignored figure controls it does not know.

    ``set_layer_style`` and ``render_choropleth`` accept ``**kwargs``, so an
    older plugin process returns success and draws the old figure. The server
    asks for ``style_applied`` whenever the caller set a control.
    """

    def __init__(self, transport: str) -> None:
        if transport == "headless":
            hint = "restart the MCP server so the headless runner loads this checkout's plugin"
        elif transport == "plugin":
            hint = "restart QGIS so the workflows plugin reloads"
        else:
            hint = "restart QGIS (plugin transport) or the MCP server (headless) so the figure controls load"
        super().__init__(
            "The QGIS plugin did not apply the figure controls, so it is older than this server. "
            f"Next: {hint}."
        )
        self.transport = transport


class PluginNotStartedError(QgisMcpWorkflowsError):
    """QGIS Desktop is open, but the toolbar control that listens on the socket was not clicked.

    ``transport=auto`` raises this instead of drawing in an empty headless project.
    """

    def __init__(self, host: str, port: int) -> None:
        super().__init__(
            f"QGIS Desktop is open, but Run MCP was not started, so nothing is listening "
            f"at {host}:{port}. "
            f"Click Run MCP on the QGIS toolbar (Plugins menu: QGIS MCP). "
            f"That button's menu has Auto-start on startup, which stays off until you tick it. "
            f"Next: click Run MCP, then qgis_ping()."
        )
        self.host = host
        self.port = port


class HeadlessUnavailableError(QgisMcpWorkflowsError):
    """The headless transport's QGIS Python launcher could not be found or started.

    Raised when the caller asked for headless (``--transport=headless`` or the
    config/env equivalent). The hint stays on that transport.
    """

    def __init__(self, detail: str) -> None:
        # One Next: only. A launcher message that already names a recovery is
        # dropped so it cannot point at plugin while the caller asked for headless.
        self.detail = detail.split(" Next:")[0].rstrip(". ")
        super().__init__(
            f"Headless transport unavailable: {self.detail}. "
            f"Install OSGeo4W (Windows) or the QGIS app (macOS) so PyQGIS is available, then set "
            f"QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER to python-qgis(-ltr).bat or the python inside QGIS.app. "
            f"Next: fix that launcher path, then restart the server with --transport=headless."
        )


class TransportUnavailableError(QgisMcpWorkflowsError):
    """``transport=auto`` found neither the plugin socket nor a headless launcher.

    Raised per tool call (the server starts degraded rather than exiting), so
    the client sees this message instead of a closed MCP connection.
    """

    def __init__(self, host: str, port: int, headless_detail: str) -> None:
        super().__init__(
            f"No QGIS backend: the plugin is not listening at {host}:{port}, and "
            f"headless is unavailable ({headless_detail}). "
            f"Open QGIS, enable 'QGIS MCP Workflows', and click Run MCP "
            f"on the toolbar (Plugins menu: QGIS MCP), or set "
            f"QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER and restart with --transport=headless. "
            f"Next: qgis_ping() once QGIS is open."
        )
        self.host = host
        self.port = port
        self.headless_detail = headless_detail


class CrsMismatchError(QgisMcpWorkflowsError):
    """A requested CRS override could not be applied to the loaded layer."""

    def __init__(self, requested_crs: str, detail: str) -> None:
        super().__init__(
            f"Could not apply CRS {requested_crs!r}: {detail}. "
            f"Next: call qgis_layer_inspect to confirm the file's native CRS, "
            f"then retry qgis_load_layer with a known-valid EPSG code (e.g. EPSG:4326)."
        )
        self.requested_crs = requested_crs
        self.detail = detail


class EmptyAfterFilterError(QgisMcpWorkflowsError):
    """An extent / sampling / filter step left zero features to render."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"No rows survive the filter: {detail}. "
            f"Next: widen extent, raise sample_rate, or call qgis_layer_inspect on the source to confirm coverage."
        )
        self.detail = detail


class ProjectLoadError(QgisMcpWorkflowsError):
    """A QGIS project (.qgz / .qgs) failed to load."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Failed to load project {path!r}: {reason}. "
            f"Next: open the file in QGIS Desktop to confirm it loads cleanly, then retry."
        )
        self.path = path
        self.reason = reason


class LayoutNotFoundError(QgisMcpWorkflowsError):
    """A print-composer layout name is missing from the loaded project."""

    def __init__(self, name: str, available: list[str]) -> None:
        avail = ", ".join(available) if available else "(none)"
        super().__init__(
            f"Layout {name!r} not found in project. Available: {avail}. "
            f"Next: call qgis_project_load to list available layouts, then retry with one of those names."
        )
        self.name = name
        self.available = available


class AtlasDisabledError(QgisMcpWorkflowsError):
    """The named print layout has no atlas coverage layer."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Layout {name!r} has no atlas coverage layer. "
            f"Next: call qgis_export_layout for a single page, or qgis_batch_render "
            f"to fan out per attribute."
        )
        self.name = name


class DRMNetworkNotFoundError(QgisMcpWorkflowsError):
    """The DRM network GeoPackage is missing — qgis_render_link_density needs it."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"drm_network_path={path!r} not found. "
            f"Build the DRM network GeoPackage once with: "
            f"`uv run --no-sync --extra drm scripts/build_drm_network.py --output {path}`. "
            f"Next: run the prep script, then retry qgis_render_link_density with the same path."
        )
        self.path = path


class NetworkExtraMissingError(QgisMcpWorkflowsError):
    """networkx (the [network] extra) is required for section-load assignment."""

    def __init__(self, extra_hint: str | None = None) -> None:
        extra = extra_hint or "Install with: uv sync --extra network"
        super().__init__(
            f"qgis_assign_section_load / qgis_route_on_network needs networkx. {extra} "
            f"Next: uv sync --extra network, then retry."
        )
        self.extra_hint = extra_hint


class SpatialJoinEmptyError(QgisMcpWorkflowsError):
    """A spatial join matched zero target features."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} "
            f"Likely CRS mismatch or the layers do not overlap. "
            f"Next: call qgis_layer_inspect on both paths and compare crs + extent."
        )
        self.detail = detail


class ZonalStatsError(QgisMcpWorkflowsError):
    """Zonal statistics could not run (bad raster, non-polygon zones, write fail)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} "
            f"Next: call qgis_layer_inspect on the zones path and the raster path, "
            f"then retry qgis_zonal_stats."
        )
        self.detail = detail


class ExpressionError(QgisMcpWorkflowsError):
    """A QGIS expression failed to parse or to evaluate."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} "
            f"Next: call qgis_layer_inspect to see the field names, then retry qgis_expression."
        )
        self.detail = detail


class ExpressionUnsafeError(QgisMcpWorkflowsError):
    """The expression calls env or a function that is not a QGIS built-in."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} "
            f"The expression may use fields, geometry, and built-in functions. "
            f"Python stays on qgis_eval. "
            f"Next: drop the refused function and retry qgis_expression, or call qgis_eval for Python."
        )
        self.detail = detail


class ExpressionEmptyError(QgisMcpWorkflowsError):
    """A filter expression kept zero features."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"{detail} "
            f"Next: call qgis_layer_inspect on the layer and check the filter expression, "
            f"then retry qgis_expression."
        )
        self.detail = detail


class PythonEvalDisabledError(QgisMcpWorkflowsError):
    """qgis_eval was called while allow_python_eval is false."""

    def __init__(self) -> None:
        super().__init__(
            "qgis_eval is off because allow_python_eval is false. "
            "A calculated column or a filter is qgis_expression "
            "(layer_path, output_path, expression=..., field_name=..., filter=...). "
            "Next: call qgis_expression, or set allow_python_eval = true in "
            "~/.config/qgis-mcp-workflows/config.toml "
            "(or QGIS_MCP_WORKFLOWS_ALLOW_PYTHON_EVAL=1) and retry qgis_eval."
        )

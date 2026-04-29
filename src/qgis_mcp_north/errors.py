"""Typed exceptions for qgis-mcp-north tools.

Every exception message ends with one suggested next tool call, per
``docs/DESIGN.md`` §5 ("actionable error messages"). Tools raise these;
FastMCP turns them into MCP error responses.
"""

from __future__ import annotations


class QgisMcpNorthError(Exception):
    """Base class for all qgis-mcp-north tool errors."""


class PluginUnavailableError(QgisMcpNorthError):
    """The QGIS plugin socket isn't reachable."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(
            f"Cannot reach QGIS plugin at {host}:{port}. "
            f"Open QGIS, enable the 'QGIS MCP North' plugin, click Start. "
            f"Next: retry the same tool call."
        )
        self.host = host
        self.port = port


class ExecutorError(QgisMcpNorthError):
    """Generic plugin-side error (unmapped)."""

    def __init__(self, command: str, message: str) -> None:
        super().__init__(
            f"Plugin command '{command}' failed: {message}. "
            f"Next: inspect inputs, then retry. If persistent, escape via qgis_eval."
        )
        self.command = command
        self.message = message


class LayerNotFoundError(QgisMcpNorthError):
    """A layer file or layer_id couldn't be resolved."""

    def __init__(self, path_or_id: str) -> None:
        super().__init__(
            f"Layer not found: {path_or_id!r}. "
            f"Next: call qgis_layer_inspect with an absolute path to verify the file exists."
        )
        self.path_or_id = path_or_id


class FieldNotFoundError(QgisMcpNorthError):
    """A field name isn't present on the layer / CSV."""

    def __init__(self, field: str, available: list[str]) -> None:
        avail = ", ".join(available) if available else "(none)"
        super().__init__(
            f"Field {field!r} not found. Available: {avail}. "
            f"Next: call qgis_layer_inspect to see actual field names, then retry with the correct value_field."
        )
        self.field = field
        self.available = available


class JoinError(QgisMcpNorthError):
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

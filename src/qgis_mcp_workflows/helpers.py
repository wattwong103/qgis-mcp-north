"""Shared helpers for server.py.

Imports only from ``mcp`` and stdlib — no circular-import risk.
"""

from __future__ import annotations

import base64
import functools
import importlib.metadata
import json
import os
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.types import Annotations, ImageContent, ResourceLink, TextContent

# Skip attaching a preview when the PNG is missing, empty, or larger than this.
# No resampling (no Pillow dep): oversize files stay path-only.
PREVIEW_MAX_BYTES = 1_500_000

# ---------------------------------------------------------------------------
# Protocol constants — single source of truth for defaults across all modules
# ---------------------------------------------------------------------------

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9877  # qgis-mcp-workflows uses 9877 (vs upstream nkarasiak on 9876)
TIMEOUT_DEFAULT = 30  # seconds — most tool commands
TIMEOUT_LONG = 60  # seconds — execute_processing, render_map, execute_code, batch
RECV_CHUNK_SIZE = 65536  # bytes per recv/recv_into call
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB — plugin-side buffer/message limit
HEADER_STRUCT = struct.Struct(">I")  # 4-byte big-endian uint32 length prefix

# Sekimoto-lab widescreen (matches 21_decks/_templates/Seki_Lab_Template_latest.pptx)
SEKILAB_SLIDE_WIDTH = 18288000
SEKILAB_SLIDE_HEIGHT = 10287000


def repo_root() -> Path:
    """Repo root when running from a checkout; otherwise the package parent."""
    return Path(__file__).resolve().parents[2]


def bundled_asset(*parts: str) -> str | None:
    """Absolute path to a file under the repo, or None if it is not on disk."""
    path = repo_root().joinpath(*parts)
    return str(path) if path.is_file() else None

BATCH_BLOCKED_COMMANDS = frozenset(
    {
        "execute_code",
        "remove_layer",
        "delete_features",
        "set_setting",
        "reload_plugin",
    }
)


def enrich_diagnose(result: dict) -> dict:
    """Append server/plugin version-match check to a diagnose result."""
    try:
        server_version = importlib.metadata.version("qgis-mcp-workflows")
    except importlib.metadata.PackageNotFoundError:
        server_version = "unknown (editable install?)"

    plugin_version = None
    for check in result.get("checks", []):
        if check["name"] == "plugin_version":
            plugin_version = check.get("detail")
            break

    version_match = "ok" if plugin_version == server_version else "mismatch"
    result["checks"].append(
        {
            "name": "version_match",
            "status": version_match,
            "detail": {"server": server_version, "plugin": plugin_version},
        }
    )
    if version_match == "mismatch" and result["status"] == "healthy":
        result["status"] = "degraded"

    return result


def make_layer_response(result: dict, fallback_name: str = "Layer") -> list:
    """Build [TextContent, ResourceLink] for a layer-mutating tool response."""
    layer_id = result.get("layer_id", result.get("id", ""))
    return [
        TextContent(type="text", text=json.dumps(result)),
        ResourceLink(
            type="resource_link",
            uri=f"qgis://layers/{layer_id}/info",
            name=result.get("name", fallback_name),
        ),
    ]


def make_project_response(result: dict) -> list:
    """Build [TextContent, ResourceLink] for a project-mutating tool response."""
    return [
        TextContent(type="text", text=json.dumps(result)),
        ResourceLink(type="resource_link", uri="qgis://project", name="Project Info"),
    ]


def png_preview_content(path: str | None) -> ImageContent | None:
    """Return MCP image content for an existing PNG under ``PREVIEW_MAX_BYTES``.

    Missing / non-PNG / unreadable / oversize paths return ``None`` so FakeExecutor
    tests (fake ``/tmp/*.png`` paths) keep receiving the Pydantic model unchanged.
    """
    if not path or not str(path).lower().endswith(".png"):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= 0 or size > PREVIEW_MAX_BYTES:
        return None
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return None
    return ImageContent(
        type="image",
        data=data,
        mimeType="image/png",
        annotations=Annotations(audience=["user", "assistant"], priority=1.0),
    )


def maybe_preview(result: Any) -> Any:
    """Attach ``ImageContent`` when ``result.output_path`` is a real PNG.

    Otherwise return ``result`` unchanged (the Python/test contract).
    """
    img = png_preview_content(getattr(result, "output_path", None))
    if img is None:
        return result
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    return [
        TextContent(type="text", text=json.dumps(payload)),
        img,
    ]


def with_png_preview(fn: Callable) -> Callable:
    """Decorator: wrap a tool that returns a model with ``output_path``."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return maybe_preview(fn(*args, **kwargs))

    return wrapper


def make_render_response(result: dict, width: int, height: int, path: str | None) -> list:
    """Build [ImageContent, optional TextContent] for a render_map response."""
    content: list = [
        ImageContent(
            type="image",
            data=result["base64_data"],
            mimeType="image/png",
            annotations=Annotations(audience=["user", "assistant"], priority=1.0),
        )
    ]
    if path:
        content.append(
            TextContent(
                type="text",
                text=json.dumps({"saved": path, "width": width, "height": height}),
                annotations=Annotations(audience=["assistant"], priority=0.5),
            )
        )
    return content

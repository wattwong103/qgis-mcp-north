"""PNG preview helper — no QGIS required."""

from __future__ import annotations

from pydantic import BaseModel

from qgis_mcp_workflows.helpers import (
    PREVIEW_MAX_BYTES,
    maybe_preview,
    png_preview_content,
)


class _Result(BaseModel):
    output_path: str
    n: int = 1


def test_png_preview_none_when_missing():
    assert png_preview_content("/tmp/does-not-exist.png") is None


def test_png_preview_none_when_not_png(tmp_path):
    p = tmp_path / "out.pdf"
    p.write_bytes(b"%PDF")
    assert png_preview_content(str(p)) is None


def test_png_preview_reads_small_png(tmp_path):
    p = tmp_path / "out.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    img = png_preview_content(str(p))
    assert img is not None
    assert img.mimeType == "image/png"
    assert img.data


def test_png_preview_skips_oversize(tmp_path):
    p = tmp_path / "huge.png"
    p.write_bytes(b"\x89PNG" + b"\x00" * (PREVIEW_MAX_BYTES + 1))
    assert png_preview_content(str(p)) is None


def test_maybe_preview_passthrough_when_missing():
    result = _Result(output_path="/tmp/nope.png")
    assert maybe_preview(result) is result


def test_maybe_preview_attaches_image(tmp_path):
    """A bare [text, image] list fails FastMCP's output validation for tools
    whose return annotation is a model (every render tool returned isError
    although the PNG was written). A CallToolResult carries both the content
    blocks and the structured payload, which FastMCP validates and passes on."""
    from mcp.types import CallToolResult

    p = tmp_path / "out.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    result = _Result(output_path=str(p), n=3)
    out = maybe_preview(result)
    assert isinstance(out, CallToolResult)
    assert out.structuredContent == {"output_path": str(p), "n": 3}
    text, image = out.content
    assert '"n": 3' in text.text or '"n":3' in text.text.replace(" ", "")
    assert image.mimeType == "image/png"
    assert not out.isError


async def test_registered_render_tool_returns_image_and_structured(fake_executor, tmp_path):
    """Drive the real FastMCP registration of qgis_render_choropleth with a
    real PNG on disk: this is the path that raised the validation error."""
    import importlib

    from mcp.types import CallToolResult, ImageContent

    # Not `from qgis_mcp_workflows import server`: test_compound_mode reloads
    # the package under TOOL_MODE=compound and restores sys.modules, but the
    # package attribute can still point at the compound-mode copy (no
    # qgis_render_choropleth registered there).
    server = importlib.import_module("qgis_mcp_workflows.server")

    png = tmp_path / "choropleth.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    fake_executor.responses["render_choropleth"] = {
        "output_path": str(png),
        "width": 800, "height": 600, "dpi": 150,
        "extent": [139.5, 35.5, 140.0, 35.9], "crs": "EPSG:4326", "n_layers": 1,
        "field": "fid", "n_classes": 5,
        "breaks": [1.0, 5.4, 9.8, 14.2, 18.6, 23.0], "mode": "quantile",
        "min_value": 1.0, "max_value": 23.0,
        "n_features": 23, "n_matched": 23, "n_unmatched": 0,
    }
    out = await server.mcp.call_tool(
        "qgis_render_choropleth",
        {"zones_path": "/zones.gpkg", "value_field": "fid", "output_png": str(png)},
    )
    assert isinstance(out, CallToolResult)
    assert any(isinstance(b, ImageContent) for b in out.content)
    assert out.structuredContent["output_path"] == str(png)

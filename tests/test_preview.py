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
    p = tmp_path / "out.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    result = _Result(output_path=str(p), n=3)
    out = maybe_preview(result)
    assert isinstance(out, list)
    assert len(out) == 2
    assert '"n": 3' in out[0].text or '"n":3' in out[0].text.replace(" ", "")
    assert out[1].mimeType == "image/png"

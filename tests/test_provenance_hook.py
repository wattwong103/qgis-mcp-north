"""Sidecars through the registered MCP tools (full mode). No QGIS needed."""

from __future__ import annotations

import hashlib
import importlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance


def _png() -> bytes:
    """A real 1x1 PNG: python-pptx (PIL) refuses a header followed by zeros."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


PNG = _png()


@pytest.fixture
def server():
    # Not `from qgis_mcp_workflows import server`: test_compound_mode reloads the
    # package and the attribute can point at the compound-mode copy.
    return importlib.import_module("qgis_mcp_workflows.server")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PROVENANCE", raising=False)
    provenance.reset_for_tests()
    return tmp_path


def _choropleth_response(params):
    Path(params["output_png"]).write_bytes(PNG)
    return {
        "output_path": params["output_png"], "width": 800, "height": 600, "dpi": 150,
        "extent": [139.5, 35.5, 140.0, 35.9], "crs": "EPSG:4326", "n_layers": 1,
        "field": "n", "n_classes": 5, "breaks": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "mode": "quantile",
        "min_value": 1.0, "max_value": 6.0, "n_features": 23, "n_matched": 23, "n_unmatched": 0,
    }


def _sidecar(path: Path) -> dict:
    return json.loads(Path(str(path) + ".provenance.json").read_text(encoding="utf-8"))


async def _render(server, root: Path, name: str = "fig.png") -> Path:
    zones = root / "zones.gpkg"
    zones.write_bytes(b"zones")
    png = root / name
    await server.mcp.call_tool(
        "qgis_render_choropleth",
        {"zones_path": str(zones), "value_field": "n", "output_png": str(png)},
    )
    return png


async def test_render_through_mcp_writes_a_sidecar(server, fake_executor, root):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)

    record = _sidecar(png)
    assert record["schema"] == provenance.SCHEMA
    assert record["figure"] == "${DROPBOX_ROOT}/fig.png"
    assert record["figure_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert record["call"]["tool"] == "qgis_render_choropleth" and record["call"]["module"] == "server"
    assert record["call"]["arguments"]["mode"] == "quantile"            # default recorded
    assert record["call"]["arguments"]["zones_path"] == "${DROPBOX_ROOT}/zones.gpkg"
    zones = record["inputs"][0]
    assert zones["argument"] == "zones_path"
    assert zones["sha256"] == hashlib.sha256(b"zones").hexdigest()
    assert zones["changed_during_call"] is False and zones["made_by"] is None
    assert record["outputs"] == ["${DROPBOX_ROOT}/fig.png"]
    assert record["result_summary"]["n_features"] == 23
    assert record["result_summary"]["breaks"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert record["session_id"] == provenance.SESSION_ID and isinstance(record["seq"], int)
    assert record["environment"]["transport"] == "fake"
    assert record["machine_specific"] == []


async def test_direct_python_call_writes_no_sidecar(server, fake_executor, root):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    zones = root / "zones.gpkg"
    zones.write_bytes(b"zones")
    server.qgis_render_choropleth(zones_path=str(zones), value_field="n", output_png=str(root / "d.png"))
    assert not (root / "d.png.provenance.json").exists()


async def test_disabled_writes_no_sidecar(server, fake_executor, root, monkeypatch):
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_PROVENANCE", "0")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    assert not Path(str(png) + ".provenance.json").exists()


async def test_failed_call_writes_no_sidecar(server, fake_executor, root):
    from qgis_mcp_workflows.errors import ExecutorError

    def fail(params):
        Path(params["output_png"]).write_bytes(PNG)  # even a half-written figure
        raise ExecutorError("render_choropleth", "boom")

    fake_executor.responses["render_choropleth"] = fail
    with pytest.raises(Exception, match="boom"):
        await _render(server, root)
    assert not (root / "fig.png.provenance.json").exists()


async def test_tool_without_output_writes_no_sidecar(server, fake_executor, root):
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}
    await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L1", "field": "mode"})
    assert not list(root.glob("*.provenance.json"))


async def test_deck_links_its_figure_and_skips_qgis(server, fake_executor, root):
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    calls_before = len(fake_executor.calls)
    deck = root / "deck.pptx"
    await server.mcp.call_tool("qgis_figures_to_pptx", {"figure_paths": [str(png)], "pptx_path": str(deck)})

    record = _sidecar(deck)
    figure_input = next(i for i in record["inputs"] if i["argument"] == "figure_paths")
    assert figure_input["made_by"] == "${DROPBOX_ROOT}/fig.png.provenance.json"
    assert record["environment"]["qgis"] == "not queried"
    assert len(fake_executor.calls) == calls_before                    # no diagnose dispatch
    # assets/sekilab_blank.pptx ships in the repo, so the default template is recorded.
    assert [i["argument"] for i in record["implicit_inputs"]] == ["template_pptx (bundled)"]
    assert record["implicit_inputs"][0]["hashed"] is True


async def test_overwritten_producer_breaks_the_link(server, fake_executor, root):
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    png.write_bytes(PNG + b"edited by a script")                         # no new sidecar
    deck = root / "deck.pptx"
    await server.mcp.call_tool("qgis_figures_to_pptx", {"figure_paths": [str(png)], "pptx_path": str(deck)})
    figure_input = next(i for i in _sidecar(deck)["inputs"] if i["argument"] == "figure_paths")
    assert figure_input["made_by"] is None


async def test_in_place_deck_append_is_flagged(server, fake_executor, root):
    pptx = pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    deck = root / "deck.pptx"
    pptx.Presentation().save(str(deck))
    await server.mcp.call_tool("qgis_figures_to_pptx", {
        "figure_paths": [str(png)], "pptx_path": str(deck), "template_pptx": str(deck)})
    record = _sidecar(deck)
    assert "input overwritten by this call: template_pptx" in record["unrecorded_state"]
    template = next(i for i in record["inputs"] if i["argument"] == "template_pptx")
    assert template["changed_during_call"] is True

"""qgis_export_session through the tool, and the replay round trip. No QGIS needed."""

from __future__ import annotations

import importlib
import runpy
import struct
import sys
import zlib
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance, replay
from qgis_mcp_workflows.errors import QgisMcpWorkflowsError
from tests.mcp_compat import field


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


PNG = _png()


@pytest.fixture
def server():
    return importlib.import_module("qgis_mcp_workflows.server")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PROVENANCE", raising=False)
    provenance.reset_for_tests()
    return tmp_path


def _choropleth(params):
    Path(params["output_png"]).write_bytes(PNG)
    return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
            "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1, "field": "n", "n_classes": 5,
            "breaks": [1.0, 2.0], "mode": "quantile", "min_value": 1.0, "max_value": 2.0,
            "n_features": 2, "n_matched": 2, "n_unmatched": 0}


async def _record_session(server, fake_executor, root):
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth
    zones = root / "zones.gpkg"
    zones.write_bytes(b"zones")
    await server.mcp.call_tool("qgis_render_choropleth",
                               {"zones_path": str(zones), "value_field": "n", "output_png": str(root / "fig.png")})
    await server.mcp.call_tool("qgis_figures_to_pptx",
                               {"figure_paths": [str(root / "fig.png")], "pptx_path": str(root / "deck.pptx")})
    return [(c, p) for c, p in fake_executor.calls if c != "diagnose"]


async def test_export_follows_the_chain_and_writes_a_script(server, fake_executor, root):
    await _record_session(server, fake_executor, root)
    result = server.qgis_export_session(output_py=str(root / "replay.py"), figures=[str(root / "deck.pptx")])
    assert (result.n_calls, result.n_figures, result.skipped) == (2, 2, [])
    compile((root / "replay.py").read_text(encoding="utf-8"), "replay.py", "exec")


async def test_round_trip_dispatches_the_same_calls(server, fake_executor, root, monkeypatch):
    original = await _record_session(server, fake_executor, root)
    script = root / "replay.py"
    server.qgis_export_session(output_py=str(script), figures=[str(root / "deck.pptx")])
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    monkeypatch.setattr(sys, "argv", [str(script), "--out-dir", str(root / "out")])
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(script), run_name="__main__")
    assert done.value.code == 0
    remapped = str((root / "out" / "DROPBOX_ROOT" / "fig.png").resolve())
    expected = [(c, {**p, "output_png": remapped}) for c, p in original]
    assert [(c, p) for c, p in fake_executor.calls if c != "diagnose"] == expected
    assert (root / "out" / "DROPBOX_ROOT" / "deck.pptx").is_file()   # pure-Python step ran for real


def test_export_needs_exactly_one_source(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match="exactly one of figures or folder"):
        server.qgis_export_session(output_py=str(root / "r.py"))


def test_export_refuses_an_existing_script(server, root):
    (root / "r.py").write_text("# mine", encoding="utf-8")
    with pytest.raises(QgisMcpWorkflowsError, match="overwrite=True"):
        server.qgis_export_session(output_py=str(root / "r.py"), folder=str(root))


def test_export_needs_a_py_file(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match=r"\.py"):
        server.qgis_export_session(output_py=str(root / "r.txt"), folder=str(root))


async def test_export_session_writes_no_sidecar_of_its_own(server, fake_executor, root):
    await _record_session(server, fake_executor, root)
    await server.mcp.call_tool("qgis_export_session",
                               {"output_py": str(root / "replay.py"), "figures": [str(root / "deck.pptx")]})
    assert not (root / "replay.py.provenance.json").exists()


def _run_script(script, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", [str(script), *argv])
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(script), run_name="__main__")
    return done.value.code


async def test_a_deck_appended_in_place_is_not_appended_again(server, fake_executor, root, monkeypatch):
    pptx = pytest.importorskip("pptx")
    fig, deck = root / "fig.png", root / "deck.pptx"
    fig.write_bytes(PNG)
    server.qgis_figures_to_pptx(figure_paths=[str(fig)], pptx_path=str(deck))    # not through MCP: no sidecar
    await server.mcp.call_tool("qgis_figures_to_pptx", {"figure_paths": [str(fig)], "pptx_path": str(deck),
                                                        "template_pptx": str(deck)})
    script = root / "replay.py"
    server.qgis_export_session(output_py=str(script), figures=[str(deck)])
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    assert _run_script(script, monkeypatch, "--in-place", "--yes") == 2
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out")) == 2   # the template changed since
    assert len(pptx.Presentation(str(deck)).slides) == 2


def test_export_refuses_a_missing_folder(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match="not a folder"):
        server.qgis_export_session(output_py=str(root / "r.py"), folder=str(root / "typo"))


def test_export_refuses_an_empty_figure_list(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match="figures is empty"):
        server.qgis_export_session(output_py=str(root / "r.py"), figures=[])


def test_export_fails_when_nothing_can_be_replayed(server, root):
    with pytest.raises(QgisMcpWorkflowsError, match="no sidecar"):
        server.qgis_export_session(output_py=str(root / "r.py"), figures=[str(root / "none.png")])
    assert not (root / "r.py").exists()


async def test_export_session_is_marked_destructive(server):
    [tool] = [t for t in await server.mcp.list_tools() if t.name == "qgis_export_session"]
    assert field(tool.annotations, "destructiveHint") is True   # overwrite=True replaces a file

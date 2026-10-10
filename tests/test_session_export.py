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


# --- TASK-15: state-reading figures and evals -----------------------------------------


def _layer_responses(fake_executor, ids):
    queue = list(ids)
    fake_executor.responses["add_vector_layer"] = lambda p: {"id": queue.pop(0), "name": "zones"}
    fake_executor.responses["get_layer_info"] = {
        "type": "vector_2", "crs": "EPSG:4326", "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
        "feature_count": 4, "fields": [{"name": "zone_id", "type": "String", "n_unique": 4}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}

    def render(params):
        Path(params["output_png"]).write_bytes(PNG)
        return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
                "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1}

    fake_executor.responses["render_layers_to_path"] = render


async def test_render_map_round_trip_restyles_a_b_a_on_the_new_layer(server, fake_executor, root, monkeypatch):
    _layer_responses(fake_executor, ["L1", "L9"])            # the replay's load returns a different id
    zones = root / "zones.geojson"
    zones.write_text("{}", encoding="utf-8")
    await server.mcp.call_tool("qgis_load_layer", {"path": str(zones)})
    for field_name in ("a", "b", "a"):
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L1", "field": field_name})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "map.png")})
    script = root / "replay.py"
    result = server.qgis_export_session(output_py=str(script), figures=[str(root / "map.png")])
    assert (result.n_calls, result.n_evals, result.skipped) == (1, 0, [])
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out")) == 0
    styles = [p for c, p in fake_executor.calls if c == "set_layer_style"]
    assert [(p["layer_id"], p["field"]) for p in styles] == [("L9", "a"), ("L9", "b"), ("L9", "a")]
    [render] = [p for c, p in fake_executor.calls if c == "render_layers_to_path"]
    assert render["layer_ids"] == ["L9"]
    assert render["output_png"] == str((root / "out" / "DROPBOX_ROOT" / "map.png").resolve())


async def test_eval_round_trip_needs_allow_eval(server, fake_executor, root, monkeypatch, capsys):
    _layer_responses(fake_executor, ["L1", "L2", "L3"])
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": ""}
    (root / "z.geojson").write_text("{}", encoding="utf-8")  # a source input the replay checks
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    script = root / "replay.py"
    assert server.qgis_export_session(output_py=str(script), figures=[str(root / "m.png")]).n_evals == 1
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out")) == 3
    assert fake_executor.calls == [] and "x = 1" in capsys.readouterr().err
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out"), "--allow-eval") == 0
    assert next(c for c, _ in fake_executor.calls) == "execute_code"


async def test_include_evals_false_writes_a_script_without_them(server, fake_executor, root):
    _layer_responses(fake_executor, ["L1"])
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": ""}
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    result = server.qgis_export_session(output_py=str(root / "r.py"), figures=[str(root / "m.png")],
                                        include_evals=False)
    assert result.n_evals == 0 and "server.qgis_eval(" not in (root / "r.py").read_text(encoding="utf-8")
    assert any("include_evals=False" in w for w in result.warnings)



async def test_a_changed_project_datasource_stops_the_replay(server, fake_executor, root, monkeypatch, capsys):
    from xml.sax.saxutils import escape

    gpkg = root / "data" / "zones.gpkg"
    gpkg.parent.mkdir(parents=True)
    gpkg.write_bytes(b"gpkg v1")
    project = root / "p.qgs"
    project.write_text("<qgis><projectlayers><maplayer><datasource>" + escape("./data/zones.gpkg|layername=z")
                       + "</datasource><provider>ogr</provider></maplayer></projectlayers></qgis>", encoding="utf-8")

    def export(params):
        Path(params["output_path"]).write_bytes(PNG)
        return {"output_path": params["output_path"], "format": "png", "n_pages": 1, "layout_name": "A4"}

    fake_executor.responses["export_layout"] = export
    await server.mcp.call_tool("qgis_export_layout", {"qgz_path": str(project), "layout_name": "A4",
                                                      "output_path": str(root / "a4.png")})
    script = root / "replay.py"
    server.qgis_export_session(output_py=str(script), figures=[str(root / "a4.png")])
    gpkg.write_bytes(b"gpkg v2")                      # the data behind the project changed
    fake_executor.calls.clear()
    monkeypatch.setattr(replay, "_new_executor", lambda: fake_executor)
    assert _run_script(script, monkeypatch, "--out-dir", str(root / "out")) == 2
    assert "data/zones.gpkg" in capsys.readouterr().out and fake_executor.calls == []

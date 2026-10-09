"""The ledger through the registered MCP tools (full mode). No QGIS needed."""

from __future__ import annotations

import importlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from qgis_mcp_workflows import ledger, provenance


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


@pytest.fixture
def server():
    return importlib.import_module("qgis_mcp_workflows.server")


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_PROVENANCE", raising=False)
    provenance.reset_for_tests()
    yield tmp_path
    provenance.reset_for_tests()


def _render(params):
    Path(params["output_png"]).write_bytes(_png())
    return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
            "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": len(params["layer_ids"])}


def _layer_responses(fake_executor, ids=("L1",)):
    queue = list(ids)
    fake_executor.responses["add_vector_layer"] = lambda p: {"id": queue.pop(0), "name": "zones"}
    fake_executor.responses["get_layer_info"] = {
        "type": "vector_2", "crs": "EPSG:4326", "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
        "feature_count": 4, "fields": [{"name": "zone_id", "type": "String", "n_unique": 4}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}
    fake_executor.responses["render_layers_to_path"] = _render


def _sidecar(path: Path) -> dict:
    return json.loads(Path(str(path) + ".provenance.json").read_text(encoding="utf-8"))


async def test_render_map_records_load_and_every_style_call(server, fake_executor, root):
    _layer_responses(fake_executor)
    zones = root / "zones.geojson"
    zones.write_text("{}", encoding="utf-8")
    await server.mcp.call_tool("qgis_load_layer", {"path": str(zones)})
    for field in ("a", "b", "a"):
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L1", "field": field})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "map.png")})
    deps = _sidecar(root / "map.png")["depends_on"]
    assert [d["call"]["tool"] for d in deps] == ["qgis_load_layer"] + ["qgis_style_categorized"] * 3
    assert [d["call"]["arguments"]["field"] for d in deps[1:]] == ["a", "b", "a"]
    assert deps[0]["layer_id"] == "L1"
    assert deps[0]["inputs"][0]["path"] == "${DROPBOX_ROOT}/zones.geojson"
    assert "_path" not in deps[0]["inputs"][0]


async def test_successful_eval_is_recorded_and_a_failed_one_is_not(server, fake_executor, root):
    _layer_responses(fake_executor)
    fake_executor.responses["execute_code"] = lambda p: (
        {"executed": False, "traceback": "boom"} if "boom" in p["code"] else {"executed": True, "stdout": ""})
    await server.mcp.call_tool("qgis_eval", {"code": "raise RuntimeError('boom')"})
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    record = _sidecar(root / "m.png")
    evals = [d for d in record["depends_on"] if d["call"]["tool"] == "qgis_eval"]
    assert [e["call"]["arguments"]["code"] for e in evals] == ["x = 1"]
    assert ledger.EVAL_NOTE in record["unrecorded_state"]


async def test_atomic_figure_after_eval_gets_a_note_only(server, fake_executor, root):
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": ""}

    def choropleth(params):
        Path(params["output_png"]).write_bytes(_png())
        return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
                "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1, "field": "n", "n_classes": 5,
                "breaks": [1.0, 2.0], "mode": "quantile", "min_value": 1.0, "max_value": 2.0,
                "n_features": 2, "n_matched": 2, "n_unmatched": 0}

    fake_executor.responses["render_choropleth"] = choropleth
    await server.mcp.call_tool("qgis_eval", {"code": "x = 1"})
    await server.mcp.call_tool("qgis_render_choropleth", {"zones_path": str(root / "z.gpkg"), "value_field": "n",
                                                          "output_png": str(root / "c.png")})
    record = _sidecar(root / "c.png")
    assert record["depends_on"] == [] and ledger.EVAL_NOTE in record["unrecorded_state"]


async def test_plugin_transport_state_reading_figure_is_flagged(server, fake_executor, root, monkeypatch):
    from qgis_mcp_workflows import executors

    class PluginExecutor:  # only the class name matters to the hook
        def dispatch(self, command, params=None, timeout=None):
            return fake_executor.dispatch(command, params, timeout)

    _layer_responses(fake_executor)
    monkeypatch.setattr(executors, "_current", PluginExecutor())
    await server.mcp.call_tool("qgis_load_layer", {"path": str(root / "z.geojson")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L1"], "output_png": str(root / "m.png")})
    assert ledger.DESKTOP_NOTE in _sidecar(root / "m.png")["unrecorded_state"]


async def test_project_export_after_restyle_is_state_reading(server, fake_executor, root):
    qgz = root / "p.qgz"
    qgz.write_bytes(b"qgz")
    fake_executor.responses["project_load"] = {
        "project_path": str(qgz), "crs": "EPSG:4326", "extent": [0, 0, 1, 1],
        "layers": [{"layer_id": "P1", "name": "zones", "geometry_type": "polygon", "visible": True}],
        "layouts": [{"name": "A4"}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}

    def export(params):
        Path(params["output_path"]).write_bytes(_png())
        return {"output_path": params["output_path"], "format": "png", "n_pages": 1, "layout_name": "A4"}

    fake_executor.responses["export_layout"] = export
    await server.mcp.call_tool("qgis_project_load", {"qgz_path": str(qgz)})
    await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "P1", "field": "zone_id"})
    await server.mcp.call_tool("qgis_export_layout", {"qgz_path": str(qgz), "layout_name": "A4",
                                                      "output_path": str(root / "a4.png")})
    deps = _sidecar(root / "a4.png")["depends_on"]
    assert [d["call"]["tool"] for d in deps] == ["qgis_project_load", "qgis_style_categorized"]


async def test_a_failed_project_export_forgets_the_project(server, fake_executor, root):
    """The plugin opens the project before it can fail (e.g. no such layout): QGIS may
    hold a different project afterwards, so the ledger must not vouch for the old one."""
    qgz = root / "p.qgz"
    qgz.write_bytes(b"qgz")
    fake_executor.responses["project_load"] = {
        "project_path": str(qgz), "crs": "EPSG:4326", "extent": [0, 0, 1, 1],
        "layers": [{"layer_id": "P1", "name": "zones", "geometry_type": "polygon", "visible": True}],
        "layouts": [{"name": "A4"}],
    }
    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 1, "classes": []}

    def broken(params):
        raise RuntimeError("plugin failed after opening the project")

    fake_executor.responses["export_layout"] = broken
    fake_executor.responses["render_layers_to_path"] = _render
    await server.mcp.call_tool("qgis_project_load", {"qgz_path": str(qgz)})
    await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "P1", "field": "zone_id"})
    with pytest.raises(Exception, match=r"plugin failed|Error executing tool"):  # mcp 2.x masks untyped errors
        await server.mcp.call_tool("qgis_export_layout", {"qgz_path": str(root / "other.qgz"), "layout_name": "A4",
                                                          "output_path": str(root / "a4.png")})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["P1"], "output_png": str(root / "m.png")})
    assert _sidecar(root / "m.png")["depends_on"] == []

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
    assert record["remote"] == []                                       # basemap="none" default
    assert record["figure_bytes"] == len(PNG) and record["figure_mtime"].endswith("Z")


async def test_recording_failure_removes_a_stale_sidecar(server, fake_executor, root, monkeypatch):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    stale = root / "fig.png.provenance.json"
    stale.write_text('{"figure_sha256": "old"}', encoding="utf-8")

    def broken(result):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(provenance, "result_summary", broken)
    png = await _render(server, root)
    assert png.read_bytes() == PNG                                     # the call itself succeeded
    assert not stale.exists()                                          # cannot vouch for the new PNG


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
    pytest.importorskip("pptx")
    fake_executor.responses["render_choropleth"] = _choropleth_response
    png = await _render(server, root)
    deck = root / "deck.pptx"
    # First call creates the deck (and its sidecar); the second appends in place.
    await server.mcp.call_tool("qgis_figures_to_pptx", {"figure_paths": [str(png)], "pptx_path": str(deck)})
    before = hashlib.sha256(deck.read_bytes()).hexdigest()
    await server.mcp.call_tool("qgis_figures_to_pptx", {
        "figure_paths": [str(png)], "pptx_path": str(deck), "template_pptx": str(deck)})
    record = _sidecar(deck)
    assert "input overwritten by this call: template_pptx" in record["unrecorded_state"]
    template = next(i for i in record["inputs"] if i["argument"] == "template_pptx")
    assert template["changed_during_call"] is True
    assert template["sha256"] == before        # fingerprinted before the call, not after
    assert template["made_by"] is None         # not a link to the sidecar this call overwrites


# --- TASK-16: project and shapefile datasources ----------------------------------------


def _project_with_layers(root: Path, outside: Path) -> Path:
    from xml.sax.saxutils import escape

    gpkg = root / "data" / "zones.gpkg"
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    gpkg.write_bytes(b"gpkg v1")
    outside.write_bytes(b"elsewhere")
    layers = [
        ("ogr", "../data/zones.gpkg|layername=zones"),
        ("postgres", "dbname='gis' host=db.internal user='north' password='hunter2' table=\"z\" (geom)"),
        ("ogr", str(outside)),
    ]
    body = "".join(f"<maplayer><id>L{i}</id><datasource>{escape(s)}</datasource>"
                   f"<provider>{p}</provider></maplayer>" for i, (p, s) in enumerate(layers))
    project = root / "proj" / "p.qgs"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(f"<qgis><projectlayers>{body}</projectlayers></qgis>", encoding="utf-8")
    return project


def _export_response(params):
    Path(params["output_path"]).write_bytes(PNG)
    return {"output_path": params["output_path"], "format": "png", "n_pages": 1, "layout_name": "A4"}


async def test_project_datasources_reach_the_sidecar(server, fake_executor, root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "far.gpkg"
    project = _project_with_layers(root, outside)
    fake_executor.responses["export_layout"] = _export_response
    await server.mcp.call_tool("qgis_export_layout", {"qgz_path": str(project), "layout_name": "A4",
                                                      "output_path": str(root / "a4.png")})
    record = _sidecar(root / "a4.png")
    [gpkg] = [i for i in record["implicit_inputs"] if i["argument"] == "qgz_path:datasource"]
    assert gpkg["path"] == "${DROPBOX_ROOT}/data/zones.gpkg" and gpkg["sha256"]
    [db] = record["remote"]
    assert db["argument"] == "qgz_path:datasource" and "hunter2" not in db["value"] and "north" not in db["value"]
    assert any("outside the project folder and DROPBOX_ROOT" in n for n in record["unrecorded_state"])


async def test_a_shapefile_input_carries_its_siblings(server, fake_executor, root):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    for ext in (".shp", ".dbf", ".prj"):
        (root / f"zones{ext}").write_bytes(b"x")
    await server.mcp.call_tool("qgis_render_choropleth", {"zones_path": str(root / "zones.shp"), "value_field": "n",
                                                          "output_png": str(root / "c.png")})
    arguments = sorted(i["argument"] for i in _sidecar(root / "c.png")["implicit_inputs"])
    assert arguments == ["zones_path:.dbf", "zones_path:.prj"]


async def test_a_loaded_projects_sources_reach_a_later_map(server, fake_executor, root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "far.gpkg"
    project = _project_with_layers(root, outside)
    fake_executor.responses["project_load"] = {
        "project_path": str(project), "crs": "EPSG:4326", "extent": [0, 0, 1, 1],
        "layers": [{"layer_id": "L0", "name": "zones", "geometry_type": "polygon", "visible": True}],
        "layouts": [],
    }

    def render(params):
        Path(params["output_png"]).write_bytes(PNG)
        return {"output_path": params["output_png"], "width": 1, "height": 1, "dpi": 150,
                "extent": [0, 0, 1, 1], "crs": "EPSG:4326", "n_layers": 1}

    fake_executor.responses["render_layers_to_path"] = render
    await server.mcp.call_tool("qgis_project_load", {"qgz_path": str(project)})
    await server.mcp.call_tool("qgis_render_map", {"layer_ids": ["L0"], "output_png": str(root / "m.png")})
    record = _sidecar(root / "m.png")
    [load] = record["depends_on"]
    assert any(i["argument"] == "qgz_path:datasource" for i in load["inputs"])
    assert [r["argument"] for r in record["remote"]] == ["qgz_path:datasource"]
    assert any("outside the project folder" in n for n in record["unrecorded_state"])


async def test_a_datasource_failure_keeps_the_rest_of_the_record(server, fake_executor, root, monkeypatch):
    from qgis_mcp_workflows import datasources

    def broken(explicit):
        raise ValueError("unexpected project content")

    monkeypatch.setattr(datasources, "discover", broken)
    png = await _render_with(server, fake_executor, root)
    record = _sidecar(png)
    assert any("datasources not recorded" in n for n in record["unrecorded_state"])


async def _render_with(server, fake_executor, root):
    fake_executor.responses["render_choropleth"] = _choropleth_response
    return await _render(server, root, "d.png")

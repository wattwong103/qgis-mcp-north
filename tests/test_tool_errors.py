"""Tool errors must reach the client on every mcp release we allow (<3).

mcp >= 2.1 replaces the message of any exception that is not the SDK's
``ToolError`` with "Error executing tool <name>" (``UnexpectedToolError``), so
every ``Next:`` hint and every argument error was invisible to the agent there.
CI runs the locked mcp 1.x, where any message got through, so these tests pin
the class relationships that make it work on both.
"""

from __future__ import annotations

import importlib

import pytest

from qgis_mcp_workflows.errors import LayerNotFoundError, QgisMcpWorkflowsError


def _sdk_tool_error() -> type[Exception]:
    try:
        from mcp.server.mcpserver.exceptions import ToolError  # mcp >= 2.0
    except ModuleNotFoundError:
        from mcp.server.fastmcp.exceptions import ToolError
    return ToolError


def test_every_typed_error_is_an_sdk_tool_error():
    assert issubclass(QgisMcpWorkflowsError, _sdk_tool_error())


def test_compound_argument_error_is_a_tool_error_with_a_next_hint():
    from qgis_mcp_workflows.compound import qgis_inspect

    with pytest.raises(_sdk_tool_error(), match=r"requires path.*Next:"):
        qgis_inspect(kind="layer")


def test_server_argument_error_is_a_tool_error_with_a_next_hint(tmp_path):
    from qgis_mcp_workflows.server import qgis_render_link_density

    drm = tmp_path / "drm.gpkg"
    drm.write_bytes(b"")
    with pytest.raises(_sdk_tool_error(), match=r"trajectory_csvs or load_csv.*Next:"):
        qgis_render_link_density(drm_network_path=str(drm), output_png=str(tmp_path / "x.png"))


def test_argument_errors_still_catch_as_value_error():
    from qgis_mcp_workflows.compound import qgis_inspect

    with pytest.raises(ValueError):
        qgis_inspect(kind="project")


async def test_registered_tool_failure_carries_the_next_hint(fake_executor):
    # Only bites on mcp >= 2.1: 1.x wraps every exception's text into its
    # ToolError, so on the locked 1.26 the class tests above are the guard.
    server = importlib.import_module("qgis_mcp_workflows.server")

    def missing_layer(params):
        raise LayerNotFoundError("L9")

    fake_executor.responses["set_layer_style"] = missing_layer
    with pytest.raises(_sdk_tool_error()) as info:
        await server.mcp.call_tool("qgis_style_categorized", {"layer_id": "L9", "field": "mode"})
    assert "Layer not found" in str(info.value)
    assert "Next:" in str(info.value)


def test_dropped_plugin_connection_is_a_tool_error(monkeypatch):
    from qgis_mcp_workflows.errors import PluginUnavailableError
    from qgis_mcp_workflows.executors import plugin as plugin_executor

    class DroppingClient:
        def __init__(self, host, port):
            pass

        def connect(self):
            return True

        def send_command(self, command, params, timeout=None):
            raise ConnectionResetError("QGIS closed the socket")

        def disconnect(self):
            pass

    monkeypatch.setattr(plugin_executor, "QgisMCPClient", DroppingClient)
    with pytest.raises(PluginUnavailableError, match="Next:"):
        plugin_executor.PluginExecutor("localhost", 9877).dispatch("ping")


def test_missing_input_csv_is_a_tool_error_and_still_file_not_found(fake_executor, tmp_path):
    from qgis_mcp_workflows.server import qgis_render_od_flows

    zones = tmp_path / "zones.geojson"
    zones.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    missing = tmp_path / "no_such_od.csv"
    with pytest.raises(_sdk_tool_error(), match=r"no_such_od\.csv.*Next:") as info:
        qgis_render_od_flows(
            od_csv=str(missing), zones_layer_path=str(zones), output_png=str(tmp_path / "x.png")
        )
    assert isinstance(info.value, FileNotFoundError)


def test_missing_figure_is_a_tool_error(tmp_path):
    from qgis_mcp_workflows.server import qgis_figures_to_pptx

    pytest.importorskip("pptx")
    with pytest.raises(_sdk_tool_error(), match=r"gone\.png.*Next:"):
        qgis_figures_to_pptx(
            figure_paths=[str(tmp_path / "gone.png")], pptx_path=str(tmp_path / "deck.pptx")
        )


def test_network_without_line_rows_is_an_argument_error(tmp_path):
    from qgis_mcp_workflows.route import load_tsv_network

    pytest.importorskip("networkx")
    net = tmp_path / "net.tsv"
    net.write_text("nodeA\tnodeB\tfromLon\tfromLat\ttoLon\ttoLat\tWKT\n", encoding="utf-8")
    with pytest.raises(_sdk_tool_error(), match=r"no LINESTRING rows.*Next:"):
        load_tsv_network(str(net))

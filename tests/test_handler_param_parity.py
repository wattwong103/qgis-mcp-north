"""The params the MCP side dispatches must be params the plugin handler takes.

The plugin dispatches with ``handler(**params)``; a handler that ends in
``**kwargs`` silently swallows a misspelled or unimplemented key. That is how
``qgis_style_categorized(classes=...)`` shipped as a no-op: the server sent
``classes_subset``, ``set_layer_style`` never read it. ``FakeExecutor`` now
checks every dispatch against the plugin's command table (read from the AST,
since the plugin imports PyQGIS), so every FakeExecutor test is also a
contract test.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from tests import plugin_contract


def test_contract_reads_named_params_from_the_command_table():
    accepted = plugin_contract.accepted_params("set_layer_style")
    assert {"layer_id", "style_type", "field", "color_ramp", "mode"} <= accepted


def test_handlers_that_forward_kwargs_accept_the_furniture_keys():
    accepted = plugin_contract.accepted_params("render_choropleth")
    assert {"scale_bar", "north_arrow", "title", "legend_items"} <= accepted


def test_handlers_that_ignore_kwargs_do_not_accept_furniture_keys():
    assert "scale_bar" not in plugin_contract.accepted_params("set_layer_style")


def test_unknown_param_is_refused(fake_executor):
    fake_executor.responses["set_layer_style"] = {"ok": True}
    with pytest.raises(plugin_contract.PluginContractError, match="bogus"):
        fake_executor.dispatch(
            "set_layer_style", {"layer_id": "L1", "style_type": "single", "bogus": 1}
        )


def test_unknown_command_is_refused(fake_executor):
    fake_executor.responses["no_such_command"] = {}
    with pytest.raises(plugin_contract.PluginContractError, match="no_such_command"):
        fake_executor.dispatch("no_such_command", {})


def test_missing_required_param_is_refused(fake_executor):
    fake_executor.responses["set_layer_style"] = {"ok": True}
    with pytest.raises(plugin_contract.PluginContractError, match="style_type"):
        fake_executor.dispatch("set_layer_style", {"layer_id": "L1"})


def test_categorized_subset_is_a_param_the_plugin_takes(fake_executor):
    from qgis_mcp_workflows.server import qgis_style_categorized

    fake_executor.responses["set_layer_style"] = {"ok": True, "n_classes": 2, "classes": []}
    qgis_style_categorized(layer_id="L1", field="mode", classes=["walk", "rail"])

    command, params = fake_executor.calls[-1]
    assert command == "set_layer_style"
    assert params["classes_subset"] == ["walk", "rail"]


def _handler(source: str) -> plugin_contract.Handler:
    return plugin_contract.Handler(ast.parse(textwrap.dedent(source)).body[0])


def test_handler_reads_positional_only_keyword_only_and_defaults():
    handler = _handler("""
        def h(self, a, /, b, c=1, *, d, e=2, **kwargs):
            pass
    """)
    assert handler.named == {"a", "b", "c", "d", "e"}
    assert handler.required == {"a", "b", "d"}
    assert not handler.forwards_kwargs


def test_handler_forwards_kwargs_only_when_the_body_uses_them():
    forwarding = """
        def h(self, **kw):
            self._save_map_image(1, 2, 3, kw)
    """
    ignoring = """
        def h(self, x=None, **kw):
            return x
    """
    assert _handler(forwarding).forwards_kwargs
    assert not _handler(ignoring).forwards_kwargs

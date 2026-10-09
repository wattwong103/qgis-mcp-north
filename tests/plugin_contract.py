"""What the plugin's command table accepts, read from plugin.py's AST.

The plugin imports PyQGIS, so the unit suite cannot import it. ``FakeExecutor``
uses this module to refuse a dispatch the real plugin would mishandle: an
unknown command, a missing required param, or a key the handler would swallow
in ``**kwargs`` without reading it.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "qgis_mcp_workflows_plugin" / "plugin.py"
SERVER_CLASS = "QgisMCPServer"
# Render handlers pass their **kwargs to this helper, which reads the map
# furniture options out of it.
FURNITURE_HELPER = "_save_map_image"


class PluginContractError(BaseException):
    """A dispatch the real plugin would mishandle.

    BaseException, not AssertionError: tool code such as the MCP resource
    handlers wraps ``dispatch`` in ``except Exception``, which would turn a
    contract violation into a JSON error string the test never looks at.
    """


class Handler:
    def __init__(self, fn: ast.FunctionDef) -> None:
        args = fn.args
        positional = [a.arg for a in (*args.posonlyargs, *args.args) if a.arg != "self"]
        n_required = len(positional) - len(args.defaults)
        self.named = set(positional) | {a.arg for a in args.kwonlyargs}
        self.required = set(positional[:n_required]) | {
            a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None
        }
        kwarg = args.kwarg.arg if args.kwarg else None
        self.forwards_kwargs = kwarg is not None and any(
            isinstance(node, ast.Name) and node.id == kwarg and isinstance(node.ctx, ast.Load)
            for node in ast.walk(fn)
        )


def _methods(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == SERVER_CLASS:
            return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
    raise AssertionError(f"{SERVER_CLASS} not found in {PLUGIN_PATH}")


def _command_table(execute_command: ast.FunctionDef) -> dict[str, str]:
    for node in ast.walk(execute_command):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "handlers" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return {
                key.value: value.attr
                for key, value in zip(node.value.keys, node.value.values, strict=True)
                if isinstance(key, ast.Constant) and isinstance(value, ast.Attribute)
            }
    raise AssertionError("execute_command has no `handlers = {...}` table")


def _furniture_keys(helper: ast.FunctionDef) -> set[str]:
    return {
        node.args[0].value
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "kwargs"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


@lru_cache(maxsize=1)
def _contract() -> tuple[dict[str, Handler], frozenset[str]]:
    methods = _methods(ast.parse(PLUGIN_PATH.read_text(encoding="utf-8")))
    table = _command_table(methods["execute_command"])
    missing = sorted(name for name in table.values() if name not in methods)
    if missing:
        raise AssertionError(f"execute_command maps to undefined methods: {missing}")
    handlers = {command: Handler(methods[name]) for command, name in table.items()}
    return handlers, frozenset(_furniture_keys(methods[FURNITURE_HELPER]))


def accepted_params(command: str) -> set[str]:
    handlers, furniture = _contract()
    if command not in handlers:
        raise PluginContractError(f"plugin has no command {command!r} in execute_command")
    handler = handlers[command]
    return handler.named | (set(furniture) if handler.forwards_kwargs else set())


def check_dispatch(command: str, params: dict | None) -> None:
    """Raise PluginContractError if the real plugin would mishandle this dispatch."""
    handlers, _ = _contract()
    keys = set(params or {})
    unknown = keys - accepted_params(command)
    if unknown:
        raise PluginContractError(
            f"plugin handler for {command!r} does not take {sorted(unknown)}; "
            "it would swallow them in **kwargs or raise TypeError"
        )
    missing = handlers[command].required - keys
    if missing:
        raise PluginContractError(f"dispatch of {command!r} is missing required {sorted(missing)}")

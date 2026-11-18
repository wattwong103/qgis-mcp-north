"""qgis_expression and allow_python_eval — FakeExecutor, no QGIS."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from qgis_mcp_workflows import server
from qgis_mcp_workflows.errors import (
    ExecutorError,
    ExpressionEmptyError,
    ExpressionError,
    ExpressionUnsafeError,
    InvalidArgumentError,
    LayerNotFoundError,
    PythonEvalDisabledError,
)
from qgis_mcp_workflows.server import qgis_eval, qgis_expression

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_eval_config(monkeypatch, tmp_path):
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_ALLOW_PYTHON_EVAL", raising=False)
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(server, "config_path", lambda: cfg)
    return cfg


def _ok(tmp_path):
    out = tmp_path / "diff.gpkg"
    return {
        "output_path": str(out),
        "n_input": 23,
        "n_output": 23,
        "field_added": "diff",
    }


def test_expression_adds_a_column(fake_executor, tmp_path):
    fake_executor.responses["apply_expression"] = _ok(tmp_path)
    result = qgis_expression(
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "diff.gpkg"),
        expression='"sim" - "obs"',
        field_name="diff",
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "apply_expression"
    assert params["layer_path"].endswith("wards.gpkg") and params["layer_path"].startswith("/")
    assert params["output_path"].endswith("diff.gpkg")
    assert params["expression"] == '"sim" - "obs"'
    assert params["field_name"] == "diff"
    assert "filter" not in params
    assert result.n_input == 23
    assert result.n_output == 23
    assert result.field_added == "diff"
    assert result.output_path.endswith("diff.gpkg")


def test_filter_only_omits_the_column(fake_executor, tmp_path):
    out = tmp_path / "kept.geojson"
    fake_executor.responses["apply_expression"] = {
        "output_path": str(out),
        "n_input": 10,
        "n_output": 4,
        "field_added": None,
    }
    result = qgis_expression(
        layer_path="~/data/wards.gpkg",
        output_path=str(out),
        filter='"pop" > 0',
    )
    params = fake_executor.calls[0][1]
    assert params["filter"] == '"pop" > 0'
    assert "expression" not in params
    assert "field_name" not in params
    assert params["layer_path"].startswith("/") and "~" not in params["layer_path"]
    assert result.field_added is None
    assert result.n_output == 4


def test_filter_runs_with_the_column(fake_executor, tmp_path):
    fake_executor.responses["apply_expression"] = _ok(tmp_path)
    qgis_expression(
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "share.json"),
        expression='"trips" / sum("trips")',
        field_name="share",
        filter='"trips" > 0',
    )
    params = fake_executor.calls[0][1]
    assert params["expression"] == '"trips" / sum("trips")'
    assert params["field_name"] == "share"
    assert params["filter"] == '"trips" > 0'
    assert params["output_path"].endswith(".json")


def test_expression_or_filter_is_required(fake_executor, tmp_path):
    with pytest.raises(InvalidArgumentError, match="Next:") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            expression="  ",
            filter="",
        )
    assert "expression or filter" in str(exc.value)
    assert fake_executor.calls == []


def test_expression_requires_field_name(fake_executor, tmp_path):
    with pytest.raises(InvalidArgumentError, match="field_name") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            expression='"sim" - "obs"',
        )
    assert "Next:" in str(exc.value)
    assert fake_executor.calls == []


def test_output_must_be_gpkg_or_geojson(fake_executor, tmp_path):
    with pytest.raises(InvalidArgumentError, match="geojson") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.shp"),
            filter='"pop" > 0',
        )
    assert "Next:" in str(exc.value)
    assert fake_executor.calls == []


def test_gpkg_extension_is_case_insensitive(fake_executor, tmp_path):
    out = tmp_path / "OUT.GPKG"
    fake_executor.responses["apply_expression"] = {
        "output_path": str(out),
        "n_input": 1,
        "n_output": 1,
        "field_added": None,
    }
    qgis_expression(layer_path="/tmp/wards.gpkg", output_path=str(out), filter="1")
    assert fake_executor.calls[0][1]["output_path"].endswith("OUT.GPKG")


def test_empty_filter_raises_with_inspect_hint(fake_executor, tmp_path):
    def fail(_params):
        raise ExecutorError("apply_expression", "EXPRESSION_EMPTY: filter kept 0 of 23 features.")

    fake_executor.responses["apply_expression"] = fail
    with pytest.raises(ExpressionEmptyError, match="qgis_layer_inspect") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            filter='"pop" > 999',
        )
    assert "Next:" in str(exc.value)


def test_parse_error_names_the_qgis_message_and_fields(fake_executor, tmp_path):
    def fail(_params):
        raise ExecutorError(
            "apply_expression",
            "EXPRESSION_PARSE: syntax error, unexpected COMMA. field names: zone_id, pop",
        )

    fake_executor.responses["apply_expression"] = fail
    with pytest.raises(ExpressionError, match="syntax error") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            expression='"pop",',
            field_name="bad",
        )
    assert "zone_id" in str(exc.value)
    assert "Next:" in str(exc.value)


def test_eval_error_uses_the_same_hint(fake_executor, tmp_path):
    def fail(_params):
        raise ExecutorError(
            "apply_expression",
            "EXPRESSION_EVAL: cannot convert. field names: zone_id",
        )

    fake_executor.responses["apply_expression"] = fail
    with pytest.raises(ExpressionError, match="cannot convert"):
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            filter='"zone_id"',
        )


def test_unsafe_function_points_at_builtins_and_eval(fake_executor, tmp_path):
    def fail(_params):
        raise ExecutorError("apply_expression", "EXPRESSION_UNSAFE: env")

    fake_executor.responses["apply_expression"] = fail
    with pytest.raises(ExpressionUnsafeError, match="qgis_eval") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            expression="env('HOME')",
            field_name="home",
        )
    text = str(exc.value)
    assert "built-in" in text
    assert "Next:" in text


def test_missing_layer_and_existing_field(fake_executor, tmp_path):
    def missing(_params):
        raise ExecutorError("apply_expression", "LAYER_NOT_FOUND: /tmp/wards.gpkg")

    fake_executor.responses["apply_expression"] = missing
    with pytest.raises(LayerNotFoundError):
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            filter="1",
        )

    def exists(_params):
        raise ExecutorError("apply_expression", "FIELD_EXISTS: diff. Available: zone_id")

    fake_executor.responses["apply_expression"] = exists
    with pytest.raises(InvalidArgumentError, match="field_name") as exc:
        qgis_expression(
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
            expression="1",
            field_name="diff",
        )
    assert "Next:" in str(exc.value)


def test_expression_result_is_a_recorded_file():
    from qgis_mcp_workflows.provenance import WRITTEN_BY_MODEL
    from qgis_mcp_workflows.server import ExpressionResult

    out = "/tmp/diff.gpkg"
    result = ExpressionResult(output_path=out, n_input=2, n_output=1, field_added="diff")
    assert WRITTEN_BY_MODEL["ExpressionResult"](result) == [out]


def test_compound_module_does_not_grow_an_expression_kind():
    text = (REPO / "src" / "qgis_mcp_workflows" / "compound.py").read_text(encoding="utf-8")
    assert "qgis_expression" not in text
    assert "apply_expression" not in text


def test_plugin_checks_builtins_and_does_not_touch_iface():
    source = (REPO / "qgis_mcp_workflows_plugin" / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (
            node.name == "apply_expression" or node.name.startswith("_expression") or node.name == "_prepare_safe_expression"
        ):
            found.append(node.name)
            body = ast.unparse(node)
            assert "iface" not in body
            assert "addMapLayer" not in body
    assert "apply_expression" in found
    prepare = ast.get_source_segment(
        source,
        next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_prepare_safe_expression"),
    )
    assert prepare is not None
    assert "builtinFunctions" in prepare
    assert "referencedFunctions" in prepare
    assert '"env"' in prepare or "'env'" in prepare
    assert "python/expressions" in ast.get_source_segment(
        source,
        next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_expression_context"),
    )


def test_eval_stays_available_by_default(fake_executor, isolated_eval_config):
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": "", "stderr": ""}
    result = qgis_eval(code="x = 1")
    assert fake_executor.calls[0][0] == "execute_code"
    assert result.exception is None
    assert server.allow_python_eval() is True


def test_config_file_can_turn_eval_off(fake_executor, isolated_eval_config):
    isolated_eval_config.write_text("allow_python_eval = false\n", encoding="utf-8")
    with pytest.raises(PythonEvalDisabledError, match="qgis_expression") as exc:
        qgis_eval(code="x = 1")
    text = str(exc.value)
    assert "allow_python_eval" in text
    assert "true" in text
    assert "Next:" in text
    assert fake_executor.calls == []


def test_env_overrides_the_config_file(fake_executor, isolated_eval_config, monkeypatch):
    isolated_eval_config.write_text("allow_python_eval = false\n", encoding="utf-8")
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_ALLOW_PYTHON_EVAL", "1")
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": "", "stderr": ""}
    qgis_eval(code="x = 1")
    assert fake_executor.calls[0][0] == "execute_code"

    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_ALLOW_PYTHON_EVAL", "0")
    isolated_eval_config.write_text("allow_python_eval = true\n", encoding="utf-8")
    with pytest.raises(PythonEvalDisabledError):
        qgis_eval(code="x = 1")
    assert len(fake_executor.calls) == 1


def test_unknown_env_falls_through_to_the_file(isolated_eval_config, monkeypatch):
    isolated_eval_config.write_text("allow_python_eval = false\n", encoding="utf-8")
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_ALLOW_PYTHON_EVAL", "yes")
    assert server.allow_python_eval() is False


def test_eval_docstring_names_expression_before_other_tools():
    doc = qgis_eval.__doc__ or ""
    assert doc.find("qgis_expression") < doc.find("qgis_style")

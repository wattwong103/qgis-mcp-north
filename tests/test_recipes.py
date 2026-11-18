"""Named recipes — qgis_recipe. FakeExecutor, no QGIS.

Built-in recipes cover PyQGIS that qgis_expression and the geometry writers do
not. A user recipe directory stays off unless config enables it. User recipes
are JSON, not a Python sandbox. qgis_eval stays available unless
allow_python_eval is false.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from qgis_mcp_workflows import provenance, server, session_export
from qgis_mcp_workflows.errors import (
    InvalidArgumentError,
    LayerNotFoundError,
    PythonEvalDisabledError,
    RecipeCrsError,
    RecipeEmptyError,
)
from qgis_mcp_workflows.recipes import BUILTIN_NAMES
from qgis_mcp_workflows.server import qgis_eval, qgis_recipe

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "qgis_mcp_workflows_plugin" / "plugin.py"


@pytest.fixture
def isolated_recipe_config(monkeypatch, tmp_path):
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_RECIPE_DIR", raising=False)
    monkeypatch.delenv("QGIS_MCP_WORKFLOWS_ALLOW_PYTHON_EVAL", raising=False)
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(server, "config_path", lambda: cfg)
    return cfg


def _ok(tmp_path, **extra):
    body = {
        "output_path": str(tmp_path / "out.gpkg"),
        "n_input": 23,
        "n_output": 23,
        "recipe": "reproject",
        "crs": "EPSG:6677",
        "source_crs": "EPSG:4326",
        "extent": None,
    }
    body.update(extra)
    return body


def test_reproject_dispatches_only_its_arguments(fake_executor, tmp_path, isolated_recipe_config):
    fake_executor.responses["run_recipe"] = _ok(tmp_path)
    result = qgis_recipe(
        name="reproject",
        layer_path="~/data/wards.gpkg",
        output_path=str(tmp_path / "out.gpkg"),
        crs=" EPSG:6677 ",
    )
    cmd, params = fake_executor.calls[0]
    assert cmd == "run_recipe"
    assert params["recipe"] == "reproject"
    assert params["layer_path"].startswith("/") and "~" not in params["layer_path"]
    assert params["output_path"].endswith("out.gpkg")
    assert params["crs"] == "EPSG:6677"
    assert "lon_field" not in params and "lat_field" not in params
    assert result.name == "reproject"
    assert result.recipe == "reproject"
    assert result.source == "builtin"
    assert result.n_input == 23
    assert result.crs == "EPSG:6677"
    assert result.output_path.endswith("out.gpkg")


def test_centroids_and_explode_send_no_extra_keys(fake_executor, tmp_path, isolated_recipe_config):
    fake_executor.responses["run_recipe"] = _ok(tmp_path, recipe="centroids", crs="EPSG:4326")
    qgis_recipe(
        name="centroids",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "c.gpkg"),
    )
    params = fake_executor.calls[0][1]
    assert params["recipe"] == "centroids"
    assert set(params) == {"recipe", "layer_path", "output_path"}

    fake_executor.calls.clear()
    fake_executor.responses["run_recipe"] = _ok(tmp_path, recipe="explode")
    qgis_recipe(name="explode", layer_path="/tmp/wards.gpkg", output_path=str(tmp_path / "e.geojson"))
    assert fake_executor.calls[0][1]["recipe"] == "explode"
    assert "crs" not in fake_executor.calls[0][1]


def test_points_from_xy_defaults_the_crs(fake_executor, tmp_path, isolated_recipe_config):
    fake_executor.responses["run_recipe"] = _ok(tmp_path, recipe="points_from_xy", crs="EPSG:4326", n_output=10)
    result = qgis_recipe(
        name="points_from_xy",
        layer_path="/tmp/stops.gpkg",
        output_path=str(tmp_path / "pts.json"),
        lon_field=" lon ",
        lat_field="lat",
    )
    params = fake_executor.calls[0][1]
    assert params["recipe"] == "points_from_xy"
    assert params["lon_field"] == "lon"
    assert params["lat_field"] == "lat"
    assert params["crs"] == "EPSG:4326"
    assert result.n_output == 10
    assert result.source == "builtin"


def test_extent_recipe_returns_the_box(fake_executor, tmp_path, isolated_recipe_config):
    box = [0.0, 1.0, 2.0, 3.0]
    fake_executor.responses["run_recipe"] = _ok(
        tmp_path, recipe="extent", n_output=1, extent=box, crs="EPSG:32654"
    )
    result = qgis_recipe(
        name="extent",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "box.gpkg"),
    )
    assert fake_executor.calls[0][1]["recipe"] == "extent"
    assert result.extent == box
    assert result.n_output == 1


def test_unknown_name_lists_builtins_and_does_not_dispatch(fake_executor, tmp_path, isolated_recipe_config):
    with pytest.raises(InvalidArgumentError, match="Next:") as exc:
        qgis_recipe(name="reproj", layer_path="/tmp/wards.gpkg", output_path=str(tmp_path / "out.gpkg"))
    text = str(exc.value)
    for name in BUILTIN_NAMES:
        assert name in text
    assert "recipe_dir" in text
    assert fake_executor.calls == []


def test_recipe_rejects_arguments_it_does_not_take(fake_executor, tmp_path, isolated_recipe_config):
    out = str(tmp_path / "out.gpkg")
    with pytest.raises(InvalidArgumentError, match="lon_field") as exc:
        qgis_recipe(
            name="reproject",
            layer_path="/tmp/wards.gpkg",
            output_path=out,
            crs="EPSG:6677",
            lon_field="lon",
        )
    assert "reproject" in str(exc.value)
    assert fake_executor.calls == []
    with pytest.raises(InvalidArgumentError, match="crs"):
        qgis_recipe(name="centroids", layer_path="/tmp/wards.gpkg", output_path=out, crs="EPSG:4326")
    with pytest.raises(InvalidArgumentError, match="lon_field"):
        qgis_recipe(
            name="points_from_xy",
            layer_path="/tmp/wards.gpkg",
            output_path=out,
            lat_field="lat",
        )
    with pytest.raises(InvalidArgumentError, match=r"\.gpkg"):
        qgis_recipe(name="extent", layer_path="/tmp/wards.gpkg", output_path=str(tmp_path / "out.csv"))


def test_a_builtin_file_cannot_replace_the_builtin(fake_executor, tmp_path, isolated_recipe_config):
    directory = tmp_path / "recipes"
    directory.mkdir()
    (directory / "reproject.json").write_text(
        json.dumps({"recipe": "centroids", "code": "import os"}),
        encoding="utf-8",
    )
    isolated_recipe_config.write_text(f'recipe_dir = "{directory}"\n', encoding="utf-8")
    fake_executor.responses["run_recipe"] = _ok(tmp_path)
    result = qgis_recipe(
        name="reproject",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "out.gpkg"),
        crs="EPSG:6677",
    )
    assert result.source == "builtin"
    assert fake_executor.calls[0][1]["recipe"] == "reproject"
    assert fake_executor.calls[0][1]["crs"] == "EPSG:6677"


def test_user_recipe_is_off_until_the_directory_is_set(fake_executor, tmp_path, isolated_recipe_config):
    with pytest.raises(InvalidArgumentError, match="recipe_dir") as exc:
        qgis_recipe(
            name="tokyo_jgd2011",
            layer_path="/tmp/wards.gpkg",
            output_path=str(tmp_path / "out.gpkg"),
        )
    assert "tokyo_jgd2011" in str(exc.value)
    assert fake_executor.calls == []


def test_user_recipe_pins_a_builtin_and_the_call_overrides_it(
    fake_executor, tmp_path, isolated_recipe_config
):
    directory = tmp_path / "recipes"
    directory.mkdir()
    (directory / "tokyo_jgd2011.json").write_text(
        json.dumps({"recipe": "reproject", "crs": "EPSG:6677"}),
        encoding="utf-8",
    )
    isolated_recipe_config.write_text(f'recipe_dir = "{directory}"\n', encoding="utf-8")
    fake_executor.responses["run_recipe"] = _ok(tmp_path)
    result = qgis_recipe(
        name="tokyo_jgd2011",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "out.gpkg"),
    )
    params = fake_executor.calls[0][1]
    assert params["recipe"] == "reproject"
    assert params["crs"] == "EPSG:6677"
    assert result.name == "tokyo_jgd2011"
    assert result.recipe == "reproject"
    assert result.source == "user"

    fake_executor.calls.clear()
    qgis_recipe(
        name="tokyo_jgd2011",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "other.gpkg"),
        crs="EPSG:32654",
    )
    assert fake_executor.calls[0][1]["crs"] == "EPSG:32654"


def test_user_recipe_refuses_python_and_paths(fake_executor, tmp_path, isolated_recipe_config):
    directory = tmp_path / "recipes"
    directory.mkdir()
    isolated_recipe_config.write_text(f'recipe_dir = "{directory}"\n', encoding="utf-8")
    out = str(tmp_path / "out.gpkg")
    (directory / "with_code.json").write_text(
        json.dumps({"recipe": "centroids", "code": "import os\nprint(1)"}),
        encoding="utf-8",
    )
    with pytest.raises(InvalidArgumentError, match="qgis_eval") as exc:
        qgis_recipe(name="with_code", layer_path="/tmp/wards.gpkg", output_path=out)
    assert "Python" in str(exc.value) or "code" in str(exc.value)
    assert fake_executor.calls == []

    (directory / "with_path.json").write_text(
        json.dumps({"recipe": "centroids", "layer_path": "/etc/passwd"}),
        encoding="utf-8",
    )
    with pytest.raises(InvalidArgumentError, match="layer_path"):
        qgis_recipe(name="with_path", layer_path="/tmp/wards.gpkg", output_path=out)
    with pytest.raises(InvalidArgumentError, match="Next:"):
        qgis_recipe(name="../escape", layer_path="/tmp/wards.gpkg", output_path=out)
    assert fake_executor.calls == []


def test_env_overrides_the_recipe_directory(fake_executor, tmp_path, isolated_recipe_config, monkeypatch):
    file_dir = tmp_path / "from-file"
    env_dir = tmp_path / "from-env"
    file_dir.mkdir()
    env_dir.mkdir()
    (file_dir / "from_file.json").write_text(json.dumps({"recipe": "centroids"}), encoding="utf-8")
    (env_dir / "from_env.json").write_text(json.dumps({"recipe": "explode"}), encoding="utf-8")
    isolated_recipe_config.write_text(f'recipe_dir = "{file_dir}"\n', encoding="utf-8")
    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_RECIPE_DIR", str(env_dir))
    fake_executor.responses["run_recipe"] = _ok(tmp_path, recipe="explode")
    result = qgis_recipe(
        name="from_env",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "out.gpkg"),
    )
    assert result.recipe == "explode"
    assert result.source == "user"
    with pytest.raises(InvalidArgumentError):
        qgis_recipe(name="from_file", layer_path="/tmp/wards.gpkg", output_path=str(tmp_path / "out.gpkg"))

    monkeypatch.setenv("QGIS_MCP_WORKFLOWS_RECIPE_DIR", "0")
    with pytest.raises(InvalidArgumentError, match="recipe_dir"):
        qgis_recipe(name="from_env", layer_path="/tmp/wards.gpkg", output_path=str(tmp_path / "out.gpkg"))


def test_eval_off_still_runs_a_recipe_and_eval_stays_on_by_default(
    fake_executor, tmp_path, isolated_recipe_config
):
    isolated_recipe_config.write_text("allow_python_eval = false\n", encoding="utf-8")
    fake_executor.responses["run_recipe"] = _ok(tmp_path, recipe="centroids", crs="EPSG:4326")
    result = qgis_recipe(
        name="centroids",
        layer_path="/tmp/wards.gpkg",
        output_path=str(tmp_path / "out.gpkg"),
    )
    assert result.source == "builtin"
    assert fake_executor.calls[0][0] == "run_recipe"
    with pytest.raises(PythonEvalDisabledError):
        qgis_eval(code="x = 1")
    assert all(cmd != "execute_code" for cmd, _ in fake_executor.calls)

    isolated_recipe_config.write_text("", encoding="utf-8")
    fake_executor.responses["execute_code"] = {"executed": True, "stdout": "", "stderr": ""}
    eval_result = qgis_eval(code="x = 1")
    assert eval_result.exception is None
    assert server.allow_python_eval() is True


def test_plugin_errors_become_typed_errors(fake_executor, tmp_path, isolated_recipe_config):
    from qgis_mcp_workflows.errors import ExecutorError

    out = str(tmp_path / "out.gpkg")

    def boom(message):
        def _raise(_params):
            raise ExecutorError("run_recipe", message)

        return _raise

    fake_executor.responses["run_recipe"] = boom("RECIPE_CRS: 'nope' is not a CRS")
    with pytest.raises(RecipeCrsError, match="Next:") as exc:
        qgis_recipe(name="reproject", layer_path="/tmp/wards.gpkg", output_path=out, crs="nope")
    assert "qgis_recipe" in str(exc.value)

    fake_executor.responses["run_recipe"] = boom("RECIPE_EMPTY: 0 of 23 features. CRS EPSG:4326")
    with pytest.raises(RecipeEmptyError, match="EPSG:4326"):
        qgis_recipe(name="centroids", layer_path="/tmp/wards.gpkg", output_path=out)

    fake_executor.responses["run_recipe"] = boom("LAYER_NOT_FOUND: /tmp/wards.gpkg")
    with pytest.raises(LayerNotFoundError):
        qgis_recipe(name="explode", layer_path="/tmp/wards.gpkg", output_path=out)

    fake_executor.responses["run_recipe"] = boom("RECIPE_FIELDS: lon is not on the layer. Available: name")
    with pytest.raises(InvalidArgumentError, match="lon"):
        qgis_recipe(
            name="points_from_xy",
            layer_path="/tmp/stops.gpkg",
            output_path=out,
            lon_field="lon",
            lat_field="lat",
        )

    fake_executor.responses["run_recipe"] = boom("RECIPE_GEOMETRY: no_geom EPSG:4326")
    with pytest.raises(InvalidArgumentError, match="no_geom"):
        qgis_recipe(name="extent", layer_path="/tmp/table.gpkg", output_path=out)


def test_finite_numbers_skip_blanks_and_booleans():
    from qgis_mcp_workflows_plugin.recipes import finite_number

    assert finite_number(1) == 1.0
    assert finite_number(" 35.6 ") == 35.6
    assert finite_number(True) is None
    assert finite_number("nope") is None
    assert finite_number(float("nan")) is None
    assert finite_number(None) is None


def test_handler_does_not_touch_iface_or_the_project():
    tree = ast.parse(PLUGIN.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (
            node.name == "run_recipe" or node.name.startswith("_recipe_")
        ):
            names.append(node.name)
            body = ast.unparse(node)
            assert "iface" not in body
            assert "addMapLayer" not in body
            assert "exec(" not in body
    assert "run_recipe" in names
    source = PLUGIN.read_text(encoding="utf-8")
    assert '"run_recipe": self.run_recipe' in source


def test_provenance_records_the_written_file():
    from qgis_mcp_workflows.provenance import WRITTEN_BY_MODEL
    from qgis_mcp_workflows.server import RecipeResult

    result = RecipeResult(
        output_path="/tmp/out.gpkg",
        name="reproject",
        recipe="reproject",
        source="builtin",
        n_input=2,
        n_output=2,
        crs="EPSG:6677",
        source_crs="EPSG:4326",
        extent=None,
    )
    assert WRITTEN_BY_MODEL["RecipeResult"](result) == ["/tmp/out.gpkg"]


def _sidecar(tmp_path, name, args, data=b"gpkg", seq=1):
    fig = tmp_path / name
    fig.write_bytes(data)
    record = {
        "schema": provenance.SCHEMA,
        "figure": str(fig),
        "figure_sha256": hashlib.sha256(data).hexdigest(),
        "figure_bytes": len(data),
        "session_id": "s1",
        "seq": seq,
        "call": {"tool": "qgis_recipe", "module": "server", "arguments": args},
        "inputs": [
            {
                "argument": "layer_path",
                "path": args["layer_path"],
                "sha256": None,
                "bytes": None,
                "made_by": None,
            }
        ],
        "implicit_inputs": [],
        "outputs": [str(fig)],
        "unrecorded_state": [],
        "machine_specific": [],
        "remote": [],
    }
    (tmp_path / f"{name}.provenance.json").write_text(json.dumps(record), encoding="utf-8")
    return fig


def test_user_recipes_need_trust_foreign_to_replay(tmp_path):
    builtin = _sidecar(
        tmp_path,
        "built.gpkg",
        {
            "name": "reproject",
            "layer_path": "/tmp/wards.gpkg",
            "output_path": str(tmp_path / "built.gpkg"),
            "crs": "EPSG:6677",
        },
    )
    user = _sidecar(
        tmp_path,
        "user.gpkg",
        {
            "name": "tokyo_jgd2011",
            "layer_path": "/tmp/wards.gpkg",
            "output_path": str(tmp_path / "user.gpkg"),
            "crs": "EPSG:6677",
        },
        data=b"user",
        seq=2,
    )
    script = tmp_path / "replay.py"
    result = session_export.export_session(
        str(script), figures=[str(builtin), str(user)], trust_foreign=False
    )
    text = script.read_text(encoding="utf-8")
    assert "reproject" in text
    assert "tokyo_jgd2011" not in text
    assert any("trust_foreign=True" in warning for warning in result["warnings"])
    assert any(item["reason"] == "user recipe needs trust_foreign" for item in result["skipped"])

    trusted = tmp_path / "trusted.py"
    session_export.export_session(
        str(trusted), figures=[str(builtin), str(user)], trust_foreign=True, overwrite=False
    )
    trusted_text = trusted.read_text(encoding="utf-8")
    assert "tokyo_jgd2011" in trusted_text
    assert "qgis_eval" not in trusted_text

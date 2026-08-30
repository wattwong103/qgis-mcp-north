"""macOS launcher resolution and QGIS.app bundle environment.

These are platform-independent: ``platform.system`` and the filesystem probes
are monkeypatched, so the macOS branches are exercised on Linux/Windows CI too.
"""

from __future__ import annotations

import os

import pytest

from qgis_mcp_workflows.errors import HeadlessUnavailableError
from qgis_mcp_workflows.executors.headless import HeadlessExecutor

BUNDLE = "/Applications/QGIS-LTR.app/Contents"
LAUNCHER = f"{BUNDLE}/MacOS/bin/python3"


@pytest.fixture
def on_macos(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    for var in ("PROJ_LIB", "GDAL_DATA", "QGIS_PREFIX_PATH",
                HeadlessExecutor._LAUNCHER_ENV_VAR):
        monkeypatch.delenv(var, raising=False)


# ── launcher resolution ────────────────────────────────────────────────────


def test_resolves_qgis_app_python(monkeypatch, on_macos):
    monkeypatch.setattr("glob.glob", lambda p: [LAUNCHER] if "QGIS-LTR" in p else [])
    assert HeadlessExecutor._resolve_launcher() == LAUNCHER


def _fake_fs(monkeypatch, *installed: str):
    """Make glob.glob resolve against a pretend /Applications containing *installed*."""
    import fnmatch

    monkeypatch.setattr(
        "glob.glob", lambda pattern: [p for p in installed if fnmatch.fnmatch(p, pattern)]
    )


CURRENT = "/Applications/QGIS.app/Contents/MacOS/bin/python3"
QGIS_318 = "/Applications/QGIS-3.18.app/Contents/MacOS/bin/python3"


def test_prefers_ltr_when_both_installed(monkeypatch, on_macos):
    """A machine with QGIS-LTR.app *and* QGIS.app must get LTR."""
    _fake_fs(monkeypatch, CURRENT, LAUNCHER)  # deliberately not in probe order
    assert HeadlessExecutor._resolve_launcher() == LAUNCHER


def test_falls_through_to_current_when_no_ltr(monkeypatch, on_macos):
    """Without LTR, QGIS.app is used rather than erroring."""
    _fake_fs(monkeypatch, CURRENT)
    assert HeadlessExecutor._resolve_launcher() == CURRENT


def test_falls_through_to_versioned_app(monkeypatch, on_macos):
    """Neither canonical name present — the QGIS*.app glob still finds it."""
    _fake_fs(monkeypatch, QGIS_318)
    assert HeadlessExecutor._resolve_launcher() == QGIS_318


def test_no_qgis_app_raises_actionable_error(monkeypatch, on_macos):
    monkeypatch.setattr("glob.glob", lambda p: [])
    with pytest.raises(HeadlessUnavailableError) as exc:
        HeadlessExecutor._resolve_launcher()
    msg = str(exc.value)
    assert "QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER" in msg
    assert "Next:" in msg


def test_env_override_still_wins(monkeypatch, on_macos):
    monkeypatch.setenv(HeadlessExecutor._LAUNCHER_ENV_VAR, __file__)
    assert HeadlessExecutor._resolve_launcher() == __file__


# ── bundle environment ─────────────────────────────────────────────────────


@pytest.fixture
def fake_bundle(tmp_path):
    """A minimal QGIS.app layout on disk; returns its launcher path."""
    contents = tmp_path / "QGIS-LTR.app" / "Contents"
    (contents / "MacOS" / "bin").mkdir(parents=True)
    (contents / "Resources" / "proj").mkdir(parents=True)
    (contents / "Resources" / "gdal").mkdir(parents=True)
    (contents / "Resources" / "proj" / "proj.db").write_text("")
    (contents / "Resources" / "gdal" / "gdalvrt.xsd").write_text("")
    launcher = contents / "MacOS" / "bin" / "python3"
    launcher.write_text("")
    return contents, str(launcher)


def test_bundle_env_points_at_resources(monkeypatch, on_macos, fake_bundle):
    contents, launcher = fake_bundle
    env = HeadlessExecutor._bundle_env(launcher)
    assert env["PROJ_LIB"] == str(contents / "Resources" / "proj")
    assert env["GDAL_DATA"] == str(contents / "Resources" / "gdal")
    assert env["QGIS_PREFIX_PATH"] == str(contents / "MacOS")


def test_bundle_env_respects_user_overrides(monkeypatch, on_macos, fake_bundle):
    _, launcher = fake_bundle
    monkeypatch.setenv("PROJ_LIB", "/my/custom/grids")
    env = HeadlessExecutor._bundle_env(launcher)
    assert "PROJ_LIB" not in env  # caller's value is left alone
    assert "GDAL_DATA" in env


def test_bundle_env_skips_missing_data_dirs(monkeypatch, on_macos, tmp_path):
    """A bundle without proj.db must not get a bogus PROJ_LIB."""
    contents = tmp_path / "QGIS.app" / "Contents"
    (contents / "MacOS" / "bin").mkdir(parents=True)
    (contents / "Resources").mkdir(parents=True)
    launcher = contents / "MacOS" / "bin" / "python3"
    launcher.write_text("")
    env = HeadlessExecutor._bundle_env(str(launcher))
    assert "PROJ_LIB" not in env
    assert "GDAL_DATA" not in env


def test_bundle_env_empty_off_macos(monkeypatch, fake_bundle):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    _, launcher = fake_bundle
    assert HeadlessExecutor._bundle_env(launcher) == {}


def test_bundle_env_empty_for_non_bundle_launcher(monkeypatch, on_macos, tmp_path):
    """A conda/system python outside any .app must not get bundle paths."""
    p = tmp_path / "bin" / "python3"
    p.parent.mkdir(parents=True)
    p.write_text("")
    assert HeadlessExecutor._bundle_env(str(p)) == {}


# ── plugin code must import under QGIS's bundled Python ────────────────────


def test_plugin_has_no_runtime_type_unions():
    """`isinstance(x, A | B)` is 3.10+; QGIS-LTR on macOS bundles 3.9.

    This is a *runtime* union, not an annotation, so `from __future__ import
    annotations` does not save it and it parses fine on 3.9 — it raises
    "TypeError: unsupported operand type(s) for |" only when the line executes.
    Two of these sat on the attribute-conversion path and broke
    get_layer_features on macOS entirely. Neither compileall nor ruff catches
    them, which is why this walks the AST.
    """
    import ast

    root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "qgis_mcp_workflows_plugin",
    )
    offenders = []
    for dirpath, _, filenames in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "id", None) not in ("isinstance", "issubclass"):
                    continue
                for arg in node.args[1:]:
                    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.BitOr):
                        offenders.append(f"{fn}:{node.lineno}")
    assert not offenders, (
        "runtime type unions (3.10+) in the plugin package, which runs on "
        f"Python 3.9 under QGIS-LTR: {offenders}. Use a tuple instead: "
        "isinstance(x, (A, B))."
    )


def test_plugin_avoids_datetime_utc():
    """``datetime.UTC`` is 3.11+; QGIS-LTR on macOS bundles Python 3.9.

    The plugin package runs under QGIS's interpreter, not the server's, so it
    is pinned to the oldest Python any supported QGIS ships.
    """
    src = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "qgis_mcp_workflows_plugin",
        "plugin.py",
    )
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "from datetime import UTC" not in text
    assert "datetime.UTC" not in text

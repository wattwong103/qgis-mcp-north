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
    for var in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "QGIS_PREFIX_PATH",
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


# QGIS 4 (vcpkg build) has no bin/python3: it ships a raw python3.12 that cannot
# find its stdlib on its own, plus a `python` wrapper that sets PYTHONHOME.
QGIS4_BUNDLE = "/Applications/QGIS-final-4_2_2.app/Contents"
QGIS4_WRAPPER = f"{QGIS4_BUNDLE}/MacOS/python"
QGIS4_RAW = f"{QGIS4_BUNDLE}/MacOS/python3.12"


def test_resolves_qgis4_wrapper(monkeypatch, on_macos):
    """A Mac with only a QGIS 4 bundle resolves to its `python` wrapper."""
    _fake_fs(monkeypatch, QGIS4_WRAPPER, QGIS4_RAW)
    assert HeadlessExecutor._resolve_launcher() == QGIS4_WRAPPER


def test_never_resolves_raw_qgis4_interpreter(monkeypatch, on_macos):
    """The raw python3.12 fails with 'Could not find platform independent
    libraries' (its sys.prefix is the CI build tree), so a bundle without the
    wrapper is not a usable launcher."""
    _fake_fs(monkeypatch, QGIS4_RAW)
    with pytest.raises(HeadlessUnavailableError):
        HeadlessExecutor._resolve_launcher()


def test_qgis3_ltr_still_preferred_over_qgis4(monkeypatch, on_macos):
    _fake_fs(monkeypatch, QGIS4_WRAPPER, LAUNCHER)
    assert HeadlessExecutor._resolve_launcher() == LAUNCHER


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


@pytest.fixture
def fake_qgis4_bundle(tmp_path):
    """The QGIS 4 layout: data under Resources/qgis/, launcher Contents/MacOS/python."""
    app = tmp_path / "QGIS-final-4_2_2.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources" / "qgis" / "proj").mkdir(parents=True)
    (contents / "Resources" / "qgis" / "gdal").mkdir(parents=True)
    (contents / "Resources" / "qgis" / "proj" / "proj.db").write_text("")
    (contents / "Resources" / "qgis" / "gdal" / "gdalvrt.xsd").write_text("")
    launcher = contents / "MacOS" / "python"
    launcher.write_text("")
    return app, str(launcher)


def test_bundle_env_qgis4_layout(monkeypatch, on_macos, fake_qgis4_bundle):
    app, launcher = fake_qgis4_bundle
    env = HeadlessExecutor._bundle_env(launcher)
    resources = app / "Contents" / "Resources" / "qgis"
    assert env["PROJ_LIB"] == str(resources / "proj")
    assert env["PROJ_DATA"] == str(resources / "proj")
    assert env["GDAL_DATA"] == str(resources / "gdal")
    # QGIS 4 derives pkgDataPath as <prefix>/Contents/Resources/qgis, so the
    # QGIS 3 value (<Contents>/MacOS) points it at a directory that does not
    # exist. Observed on QGIS 4.2.2: pkgDataPath = .../MacOS/Contents/Resources/qgis.
    assert env["QGIS_PREFIX_PATH"] == str(app)
    assert env["QGIS_PREFIX_PATH"] != str(app / "Contents" / "MacOS")


def test_bundle_env_qgis3_layout_wins_when_both_present(monkeypatch, on_macos, fake_bundle):
    """A QGIS 3 bundle that also carries a Resources/qgis/ tree must keep the
    QGIS 3 data paths and prefix: misreading it as QGIS 4 would drop PROJ_LIB
    and silently invalidate every CRS."""
    contents, launcher = fake_bundle
    (contents / "Resources" / "qgis" / "resources").mkdir(parents=True)
    (contents / "Resources" / "qgis" / "resources" / "qgis.db").write_text("")
    env = HeadlessExecutor._bundle_env(launcher)
    assert env["PROJ_LIB"] == str(contents / "Resources" / "proj")
    assert env["QGIS_PREFIX_PATH"] == str(contents / "MacOS")


def test_bundle_env_any_proj_override_wins(monkeypatch, on_macos, fake_qgis4_bundle):
    """PROJ 9.1+ prefers PROJ_DATA over PROJ_LIB: injecting one while the user
    set the other would silently override the user's grids."""
    _, launcher = fake_qgis4_bundle
    monkeypatch.setenv("PROJ_LIB", "/my/custom/grids")
    env = HeadlessExecutor._bundle_env(launcher)
    assert "PROJ_LIB" not in env
    assert "PROJ_DATA" not in env
    assert "GDAL_DATA" in env


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


# ── runner profile name ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version_int", "expected"),
    [(32803, "QGIS3"), (34099, "QGIS3"), (40000, "QGIS4"), (40202, "QGIS4")],
)
def test_runner_application_name_follows_qgis_major(version_int, expected):
    """QGIS derives the profile dir from Qt's application name. QGIS 4 Desktop
    uses QGIS4/profiles/default; forcing QGIS3 under 4.x read the QGIS 3
    profile (stale svg paths pointing into QGIS-LTR.app, the wrong QMS
    sources) instead of the one QGIS 4 Desktop writes."""
    from qgis_mcp_workflows.executors.headless_runner import _application_name

    assert _application_name(version_int) == expected


def test_runner_application_name_defaults_to_qgis3_when_version_unknown():
    """Unreadable version (None) must not kill the runner before 'ready'."""
    from qgis_mcp_workflows.executors.headless_runner import _application_name

    assert _application_name(None) == "QGIS3"


# ── plugin code must run under QGIS 4 (PyQt6) ──────────────────────────────

# Unscoped Qt5 enum spellings PyQt6 removed. Each was found failing under QGIS
# 4.2.2 / PyQt 6.11 (AttributeError). The scoped spellings should also work on
# the PyQt5 5.15 that QGIS 3.28+ ships (not yet run on a QGIS 3 build). A
# tripwire for the spellings already hit, not a full Qt6 audit.
_QT5_ONLY = {
    "QPainter": {"Antialiasing", "TextAntialiasing", "SmoothPixmapTransform"},
    "QFont": {"Thin", "Light", "Normal", "DemiBold", "Bold", "Black"},
}


def test_plugin_avoids_qt5_only_enums():
    """`QPainter.Antialiasing` broke every PNG render with map furniture under
    QGIS 4 headless. Use `QPainter.RenderHint.X`, `QFont.Weight.X`,
    `Qt.AlignmentFlag.X`; `compat.py` is exempt (it tries the new form first
    and falls back to the old one on purpose)."""
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
            if not fn.endswith(".py") or fn == "compat.py":
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    base, attr = node.value.id, node.attr
                    if attr in _QT5_ONLY.get(base, ()) or (
                        base == "Qt" and attr.startswith("Align") and attr != "AlignmentFlag"
                    ):
                        offenders.append(f"{fn}:{node.lineno} {base}.{attr}")
                # QFontMetrics.width() was removed in Qt6: horizontalAdvance().
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "width"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ("fm", "metrics", "font_metrics")
                ):
                    offenders.append(f"{fn}:{node.lineno} {node.func.value.id}.width()")
    assert not offenders, f"Qt5-only spellings that fail under QGIS 4 / PyQt6: {offenders}"

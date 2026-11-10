"""compat.py resolves unit enums on QGIS 3.28 and on QGIS 4 (TASK-23).

No QGIS 4 install exists on the dev PCs (2026-11-10: OSGeo4W 3.40.5 and 3.42.1,
QGIS LTR 3.40.14, all Qt5), and on QGIS 3.40+ the old and new spellings are the
same object, so a live run cannot reach either fallback. compat.py is loaded
here against stub qgis modules instead: one shaped like QGIS 3.28 (only the
``QgsUnitTypes`` aliases; ``Qgis.LayoutUnit`` arrived in 3.30) and one shaped
like QGIS 4 (only the scoped ``Qgis`` enums).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from types import SimpleNamespace

COMPAT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "qgis_mcp_workflows_plugin",
    "compat.py",
)


class _Stub:
    """Every attribute resolves to another stub, except the names in ``missing``."""

    def __init__(self, missing=(), **known):
        self.__dict__.update(known)
        self.__dict__["_missing"] = frozenset(missing)

    def __getattr__(self, name):
        if name in self.__dict__["_missing"]:
            raise AttributeError(name)
        return _Stub()


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    module.__getattr__ = lambda attr: _Stub()  # PEP 562: any other import resolves
    return module


def _load_compat(monkeypatch, qgis, unit_types):
    core = _module("qgis.core", Qgis=qgis, QgsUnitTypes=unit_types)
    for name, module in {
        "qgis": _module("qgis", core=core),
        "qgis.core": core,
        "qgis.PyQt": _module("qgis.PyQt"),
        "qgis.PyQt.QtCore": _module("qgis.PyQt.QtCore"),
        "qgis.PyQt.QtGui": _module("qgis.PyQt.QtGui"),
        "qgis.PyQt.QtWidgets": _module("qgis.PyQt.QtWidgets"),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("_compat_under_test", COMPAT)
    compat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compat)
    return compat


def test_qgis_4_uses_the_scoped_enums_without_the_old_aliases(monkeypatch):
    qgis = _Stub(
        LayoutUnit=SimpleNamespace(Millimeters="layout-mm"),
        RenderUnit=SimpleNamespace(Millimeters="render-mm", Pixels="render-px"),
    )
    gone = _Stub(missing={"LayoutMillimeters", "RenderMillimeters", "RenderPixels"})

    compat = _load_compat(monkeypatch, qgis, gone)

    assert (compat.LAYOUT_MM, compat.RENDER_MM, compat.RENDER_PIXELS) == (
        "layout-mm", "render-mm", "render-px")


def test_qgis_3_28_falls_back_to_the_old_aliases(monkeypatch):
    qgis = _Stub(missing={"LayoutUnit", "RenderUnit"})
    aliases = _Stub(LayoutMillimeters="old-layout-mm", RenderMillimeters="old-render-mm",
                    RenderPixels="old-render-px")

    compat = _load_compat(monkeypatch, qgis, aliases)

    assert (compat.LAYOUT_MM, compat.RENDER_MM, compat.RENDER_PIXELS) == (
        "old-layout-mm", "old-render-mm", "old-render-px")

"""QGIS 3.x / 4.x enum compatibility shim.

QGIS 4.x (Qt6/PyQt6) moves most enums into the ``Qgis`` namespace and
fully-qualified enum forms.  This module resolves the correct value at
import time so the rest of the plugin stays clean.

Strategy: try the **new** form first, fall back to the old one.
"""

from qgis.core import (
    Qgis,
    QgsAggregateCalculator,
    QgsGradientFillSymbolLayer,
    QgsLayoutExporter,
    QgsLayoutItemShape,
    QgsMapLayer,
    QgsProcessingParameterDefinition,
    QgsRasterBandStats,
    QgsSymbolLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QIODevice, Qt
from qgis.PyQt.QtGui import QFontDatabase
from qgis.PyQt.QtWidgets import QToolButton

# ── Layer types ──────────────────────────────────────────────────────
try:
    LAYER_VECTOR = Qgis.LayerType.Vector
except AttributeError:
    LAYER_VECTOR = QgsMapLayer.VectorLayer

try:
    LAYER_RASTER = Qgis.LayerType.Raster
except AttributeError:
    LAYER_RASTER = QgsMapLayer.RasterLayer

# ── Message levels ───────────────────────────────────────────────────
try:
    MSG_INFO = Qgis.MessageLevel.Info
except AttributeError:
    MSG_INFO = Qgis.Info

try:
    MSG_WARNING = Qgis.MessageLevel.Warning
except AttributeError:
    MSG_WARNING = Qgis.Warning

try:
    MSG_CRITICAL = Qgis.MessageLevel.Critical
except AttributeError:
    MSG_CRITICAL = Qgis.Critical

# ── Geometry types ───────────────────────────────────────────────────
try:
    GEOM_POLYGON = Qgis.GeometryType.Polygon
except AttributeError:
    GEOM_POLYGON = QgsWkbTypes.PolygonGeometry

try:
    GEOM_LINE = Qgis.GeometryType.Line
except AttributeError:
    GEOM_LINE = QgsWkbTypes.LineGeometry

try:
    GEOM_POINT = Qgis.GeometryType.Point
except AttributeError:
    GEOM_POINT = QgsWkbTypes.PointGeometry

# ── Raster stats ─────────────────────────────────────────────────────
try:
    RASTER_STATS_ALL = Qgis.RasterBandStatistic.All
except AttributeError:
    RASTER_STATS_ALL = QgsRasterBandStats.All

# ── Layout export result ─────────────────────────────────────────────
try:
    LAYOUT_SUCCESS = Qgis.LayoutResult.Success
except AttributeError:
    LAYOUT_SUCCESS = QgsLayoutExporter.Success

# ── Processing parameter flags ───────────────────────────────────────
try:
    PROCESSING_OPTIONAL = Qgis.ProcessingParameterFlag.Optional
except AttributeError:
    PROCESSING_OPTIONAL = QgsProcessingParameterDefinition.FlagOptional

# ── Aggregate functions ──────────────────────────────────────────────
try:
    AGG_COUNT = Qgis.Aggregate.Count
    AGG_SUM = Qgis.Aggregate.Sum
    AGG_MEAN = Qgis.Aggregate.Mean
    AGG_MIN = Qgis.Aggregate.Min
    AGG_MAX = Qgis.Aggregate.Max
    AGG_STDEV = Qgis.Aggregate.StDev
    AGG_ARRAY = Qgis.Aggregate.ArrayAggregate
except AttributeError:
    AGG_COUNT = QgsAggregateCalculator.Count
    AGG_SUM = QgsAggregateCalculator.Sum
    AGG_MEAN = QgsAggregateCalculator.Mean
    AGG_MIN = QgsAggregateCalculator.Min
    AGG_MAX = QgsAggregateCalculator.Max
    AGG_STDEV = QgsAggregateCalculator.StDev
    AGG_ARRAY = QgsAggregateCalculator.ArrayAggregate

# ── Qt IO / widget enums ─────────────────────────────────────────────
try:
    IODEVICE_WRITEONLY = QIODevice.OpenModeFlag.WriteOnly
except AttributeError:
    IODEVICE_WRITEONLY = QIODevice.WriteOnly

try:
    TOOLBUTTON_MENU_POPUP = QToolButton.ToolButtonPopupMode.MenuButtonPopup
except AttributeError:
    TOOLBUTTON_MENU_POPUP = QToolButton.MenuButtonPopup

try:
    TOOLBUTTON_ICON_ONLY = Qt.ToolButtonStyle.ToolButtonIconOnly
except AttributeError:
    TOOLBUTTON_ICON_ONLY = Qt.ToolButtonIconOnly

# ── Symbology and layout items (panel grids) ─────────────────────────
try:
    PROP_FILL_COLOR = QgsSymbolLayer.Property.FillColor
except AttributeError:
    PROP_FILL_COLOR = QgsSymbolLayer.PropertyFillColor

try:
    GRADIENT_FROM_RAMP = Qgis.GradientColorSource.ColorRamp
except AttributeError:
    GRADIENT_FROM_RAMP = QgsGradientFillSymbolLayer.ColorRamp

try:
    SHAPE_RECTANGLE = QgsLayoutItemShape.Shape.Rectangle
except AttributeError:
    SHAPE_RECTANGLE = QgsLayoutItemShape.Rectangle


def font_families():
    """Installed font families: a static call in PyQt6, an instance one in PyQt5."""
    try:
        return set(QFontDatabase.families())
    except TypeError:
        return set(QFontDatabase().families())


def is_offscreen():
    """True under Qt's offscreen platform (headless QGIS), False in QGIS Desktop."""
    from qgis.PyQt.QtGui import QGuiApplication

    return QGuiApplication.platformName() == "offscreen"


_system_fonts_registered = False


def register_system_fonts():
    """Show the OS font folders to Qt, once per process.

    Headless QGIS runs Qt's offscreen platform, which sees only Qt's bundled
    fonts (one family on win, 2026-11-10); registering the OS folders (724 files,
    0.13 s there) makes the installed families usable in layouts.
    """
    global _system_fonts_registered
    if _system_fonts_registered:
        return
    _system_fonts_registered = True
    import glob
    import os
    import sys

    home = os.path.expanduser("~")
    if sys.platform == "win32":
        folders = [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
                   os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        folders = ["/System/Library/Fonts", "/Library/Fonts", os.path.join(home, "Library", "Fonts")]
    else:
        folders = ["/usr/share/fonts", "/usr/local/share/fonts",
                   os.path.join(home, ".local", "share", "fonts"), os.path.join(home, ".fonts")]
    seen = set()
    for folder in folders:
        for pattern in ("*.ttf", "*.otf", "*.ttc", "*.TTF", "*.OTF", "*.TTC"):
            for path in glob.glob(os.path.join(folder, "**", pattern), recursive=True):
                if path not in seen:
                    seen.add(path)
                    QFontDatabase.addApplicationFont(path)

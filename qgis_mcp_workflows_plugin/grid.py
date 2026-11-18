"""Fishnet cell plan. No PyQGIS.

The server copy lives at ``src/qgis_mcp_workflows/grid.py``. The plugin copy
lives at ``qgis_mcp_workflows_plugin/grid.py``. They are the same file. The
installed server wheel does not include the plugin package, and QGIS does not
import the server package. ``tests/test_make_grid.py`` reads both and checks
the text matches.
"""

import math

MAX_CELLS = 250_000
ID_FIELD = "cell_id"


def check_cell_m(cell_m):
    """A positive finite float, or None when ``cell_m`` is not metres."""
    if isinstance(cell_m, bool) or not isinstance(cell_m, (int, float)):
        return None
    value = float(cell_m)
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def check_extent(extent):
    """``(xmin, ymin, xmax, ymax)`` or None.

    Four finite numbers, ``xmin < xmax`` and ``ymin < ymax``. A string is not
    an extent. Booleans are not numbers.
    """
    if isinstance(extent, (str, bytes)) or not isinstance(extent, (list, tuple)):
        return None
    if len(extent) != 4:
        return None
    values = []
    for item in extent:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        value = float(item)
        if not math.isfinite(value):
            return None
        values.append(value)
    xmin, ymin, xmax, ymax = values
    if xmin >= xmax or ymin >= ymax:
        return None
    return (xmin, ymin, xmax, ymax)


def normalize_crs(crs):
    """A CRS string QGIS can be asked about, or None when it is blank.

    A bare integer becomes ``EPSG:<n>``. ``epsg:32654`` becomes ``EPSG:32654``.
    Any other non-blank string is returned stripped. Validity is QGIS's job.
    """
    if not isinstance(crs, str):
        return None
    text = crs.strip()
    if not text:
        return None
    if text.isdigit():
        return "EPSG:" + text
    if len(text) > 5 and text[:5].upper() == "EPSG:" and text[5:].isdigit():
        return "EPSG:" + text[5:]
    return text


def _on_grid_line(value, step):
    """True when ``value`` is a multiple of ``step``, float noise included."""
    nearest = round(value / step)
    delta = abs(value - (nearest * step))
    tol = max(abs(value) * 1e-12, abs(step) * 1e-9, 1e-7)
    return delta <= tol


def index_floor(value, step):
    """Greatest integer ``i`` with ``i * step`` <= ``value``."""
    if _on_grid_line(value, step):
        return round(value / step)
    return math.floor(value / step)


def index_ceil(value, step):
    """Least integer ``i`` with ``i * step`` >= ``value``."""
    if _on_grid_line(value, step):
        return round(value / step)
    return math.ceil(value / step)


def plan_grid(extent, cell_m):
    """The cells that intersect ``extent``, or None when the inputs are unusable.

    Squares of ``cell_m`` aligned to the CRS origin. The written box may
    extend past ``extent`` out to the next cell line. An edge already on a
    cell line does not grow an extra cell. ``n_cells`` may be above
    ``MAX_CELLS``; the caller refuses that before writing.
    """
    box = check_extent(extent)
    step = check_cell_m(cell_m)
    if box is None or step is None:
        return None
    xmin, ymin, xmax, ymax = box
    if not math.isfinite(xmin / step) or not math.isfinite(xmax / step):
        return None
    if not math.isfinite(ymin / step) or not math.isfinite(ymax / step):
        return None
    col0 = index_floor(xmin, step)
    col1 = index_ceil(xmax, step)
    row0 = index_floor(ymin, step)
    row1 = index_ceil(ymax, step)
    n_cols = col1 - col0
    n_rows = row1 - row0
    if n_cols < 1 or n_rows < 1:
        return None
    return {
        "col0": col0,
        "col1": col1,
        "row0": row0,
        "row1": row1,
        "n_cols": n_cols,
        "n_rows": n_rows,
        "n_cells": n_cols * n_rows,
        "cell_m": step,
        "extent": [xmin, ymin, xmax, ymax],
        "grid_extent": [col0 * step, row0 * step, col1 * step, row1 * step],
    }


def cell_box(col, row, step):
    """``(xmin, ymin, xmax, ymax)`` of one cell. Southwest corner is the index."""
    x0 = col * step
    y0 = row * step
    return (x0, y0, x0 + step, y0 + step)


def cell_id(col, row):
    """Join key. Column, then row. Negative indices stay negative."""
    return f"{col}_{row}"


def grid_crs_detail(crs, unit):
    if unit == "invalid":
        return f"GRID_CRS: {crs} is not a CRS QGIS can use."
    shown = unit or "unknown"
    return (
        f"GRID_CRS: {crs} uses map units {shown}. "
        "cell_m is metres, so the CRS must be a projected CRS whose map units are metres."
    )


def grid_too_many_detail(plan):
    return (
        f"GRID_TOO_MANY: {plan['n_rows']} by {plan['n_cols']} cells "
        f"is {plan['n_cells']} at cell_m={plan['cell_m']}, and the cap is {MAX_CELLS}."
    )

"""Validate figure-control arguments and decide which keys to dispatch.

Omitted arguments are absent from the dict, so a v1.17.0 call sends the same
command body it sent before these controls existed.
"""

from __future__ import annotations

import math
import re
from itertools import pairwise

from qgis_mcp_workflows.errors import InvalidArgumentError, PluginOutdatedError

_HEX = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")


def _hex(name: str, value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise InvalidArgumentError(
            f"{name} must be #RGB or #RRGGBB, got {value!r}.",
            next_step=f"pass {name} as #RGB or #RRGGBB",
        )


def _number(
    name: str,
    value: float | None,
    *,
    low: float,
    high: float,
    low_exclusive: bool = False,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidArgumentError(
            f"{name} must be a finite number, got {value!r}.",
            next_step=f"pass {name} as a finite number",
        )
    number = float(value)
    ok = low < number <= high if low_exclusive else low <= number <= high
    if not ok:
        bound = "greater than" if low_exclusive else "from"
        raise InvalidArgumentError(
            f"{name} must be {bound} {low:g} and at most {high:g}, got {value!r}.",
            next_step=f"pass {name} inside that range",
        )


def _set(name: str, value: object) -> dict:
    return {} if value is None else {name: value}


def symbol_params(
    *,
    outline_color: str | None,
    outline_width_mm: float | None,
    fill_opacity: float | None,
) -> dict:
    """Stroke and opacity keys the caller actually set."""
    _hex("outline_color", outline_color)
    _number("outline_width_mm", outline_width_mm, low=0, high=5)
    _number("fill_opacity", fill_opacity, low=0, high=1)
    sent: dict = {}
    sent.update(_set("outline_color", outline_color))
    sent.update(_set("outline_width_mm", outline_width_mm))
    sent.update(_set("fill_opacity", fill_opacity))
    return sent


def break_params(breaks: list[float] | None, *, diverging: bool) -> dict:
    """Explicit class edges. ``mode`` and ``n_classes`` stay on the call and are ignored by the plugin."""
    if breaks is None:
        return {}
    if diverging:
        raise InvalidArgumentError(
            "breaks and diverging both classify the same field.",
            next_step="drop diverging or drop breaks",
        )
    if isinstance(breaks, (str, bytes)) or not isinstance(breaks, (list, tuple)):
        raise InvalidArgumentError(
            f"breaks must be a list of 2 to 16 class edges, got {breaks!r}.",
            next_step="pass a strictly increasing list of class edges",
        )
    if not 2 <= len(breaks) <= 16:
        raise InvalidArgumentError(
            f"breaks must have 2 to 16 edges (1 to 15 classes), got {len(breaks)}.",
            next_step="pass a strictly increasing list of class edges",
        )
    edges: list[float] = []
    for edge in breaks:
        if isinstance(edge, bool) or not isinstance(edge, (int, float)) or not math.isfinite(edge):
            raise InvalidArgumentError(
                f"breaks must be finite numbers, got {edge!r}.",
                next_step="pass a strictly increasing list of class edges",
            )
        edges.append(float(edge))
    for earlier, later in pairwise(edges):
        if not earlier < later:
            raise InvalidArgumentError(
                "breaks must be strictly increasing.",
                next_step="pass class edges in ascending order",
            )
    return {"breaks": edges}


def color_params(colors: list[str] | None, classes: list[str] | None) -> dict:
    """One hex colour per ``classes`` entry. The grey catch-all stays grey."""
    if colors is None:
        return {}
    if classes is None:
        raise InvalidArgumentError(
            "colors needs classes of the same length.",
            next_step="pass classes= with one value per color",
        )
    if len(colors) != len(classes):
        raise InvalidArgumentError(
            f"colors has {len(colors)} entries and classes has {len(classes)}.",
            next_step="pass one #RGB or #RRGGBB per classes entry",
        )
    for color in colors:
        _hex("colors", color)
    return {"colors": list(colors)}


def label_params(
    *,
    label_field: str | None,
    label_size_pt: float | None,
    label_font: str | None,
    label_color: str | None,
    label_halo_mm: float | None,
    label_halo_color: str | None,
) -> dict:
    """Choropleth label type. Any of these requires ``label_field``."""
    provided = {
        "label_size_pt": label_size_pt,
        "label_font": label_font,
        "label_color": label_color,
        "label_halo_mm": label_halo_mm,
        "label_halo_color": label_halo_color,
    }
    if any(value is not None for value in provided.values()) and not label_field:
        raise InvalidArgumentError(
            "label size, font, color, and halo need label_field.",
            next_step="pass label_field",
        )
    _number("label_size_pt", label_size_pt, low=0, high=72, low_exclusive=True)
    _number("label_halo_mm", label_halo_mm, low=0, high=5)
    _hex("label_color", label_color)
    _hex("label_halo_color", label_halo_color)
    return {name: value for name, value in provided.items() if value is not None}


def require_style_applied(result: dict, sent: dict, transport: str) -> None:
    """An older plugin accepts the keys and draws the old figure."""
    if sent and "style_applied" not in result:
        raise PluginOutdatedError(transport)

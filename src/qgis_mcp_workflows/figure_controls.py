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


def control_params(
    *,
    outline_color: str | None,
    outline_width_mm: float | None,
    fill_opacity: float | None,
    label_field: str | None,
    label_size_pt: float | None,
    label_font: str | None,
    label_color: str | None,
    label_halo_mm: float | None,
    label_halo_color: str | None,
    track_label_field: bool = False,
) -> dict:
    """Stroke, opacity and label keys the caller set.

    ``track_label_field`` is for tools that did not label before. OD and link
    density already take ``label_field``, so an older plugin still labels and
    that key alone is not an override.
    """
    sent = symbol_params(
        outline_color=outline_color,
        outline_width_mm=outline_width_mm,
        fill_opacity=fill_opacity,
    )
    sent.update(label_params(
        label_field=label_field,
        label_size_pt=label_size_pt,
        label_font=label_font,
        label_color=label_color,
        label_halo_mm=label_halo_mm,
        label_halo_color=label_halo_color,
    ))
    if track_label_field and label_field is not None:
        sent["label_field"] = label_field
    return sent


def label_params(
    *,
    label_field: str | None,
    label_size_pt: float | None,
    label_font: str | None,
    label_color: str | None,
    label_halo_mm: float | None,
    label_halo_color: str | None,
) -> dict:
    """Label type. Any of these requires ``label_field``."""
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


_LEGEND_CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")


def layout_params(
    *,
    width_mm: float | None,
    height_mm: float | None,
    font_family: str | None,
    title_size_pt: float | None,
    legend_size_pt: float | None,
    legend_corner: str | None,
    title: str | None,
    legend: bool,
    scale_bar: bool,
) -> dict:
    """Page millimetres and type for ``qgis_compose_layout``. Omitted keys stay out."""
    if (width_mm is None) != (height_mm is None):
        raise InvalidArgumentError(
            "width_mm and height_mm are set together.",
            next_step="pass both, or omit both to keep the page preset",
        )
    _number("width_mm", width_mm, low=20, high=1000, low_exclusive=True)
    _number("height_mm", height_mm, low=20, high=1000, low_exclusive=True)
    if isinstance(font_family, str) and not font_family.strip():
        raise InvalidArgumentError(
            "font_family is empty.",
            next_step="pass a family name, or omit font_family",
        )
    if font_family is not None and not isinstance(font_family, str):
        raise InvalidArgumentError(
            f"font_family must be a font family name, got {font_family!r}.",
            next_step="pass a family name, or omit font_family",
        )
    _number("title_size_pt", title_size_pt, low=0, high=72, low_exclusive=True)
    _number("legend_size_pt", legend_size_pt, low=0, high=72, low_exclusive=True)
    if title_size_pt is not None and not title:
        raise InvalidArgumentError(
            "title_size_pt needs a title.",
            next_step="pass title, or omit title_size_pt",
        )
    if legend_size_pt is not None and not legend:
        raise InvalidArgumentError(
            "legend_size_pt needs legend=True.",
            next_step="pass legend=True, or omit legend_size_pt",
        )
    if legend_corner is not None:
        if legend_corner not in _LEGEND_CORNERS:
            raise InvalidArgumentError(
                f"legend_corner must be one of {', '.join(_LEGEND_CORNERS)}, got {legend_corner!r}.",
                next_step="pass top_left, top_right, bottom_left, or bottom_right",
            )
        if not legend:
            raise InvalidArgumentError(
                "legend_corner needs legend=True.",
                next_step="pass legend=True, or omit legend_corner",
            )
    if font_family and not (title or legend or scale_bar):
        raise InvalidArgumentError(
            "font_family needs a title, a legend, or a scale bar.",
            next_step="pass title, or legend=True, or scale_bar=True, or omit font_family",
        )
    sent: dict = {}
    if width_mm is not None:
        sent["width_mm"] = float(width_mm)
        sent["height_mm"] = float(height_mm)
    if font_family:
        sent["font_family"] = font_family
    if title_size_pt is not None:
        sent["title_size_pt"] = float(title_size_pt)
    if legend_size_pt is not None:
        sent["legend_size_pt"] = float(legend_size_pt)
    if legend_corner is not None:
        sent["legend_corner"] = legend_corner
    return sent


def require_style_applied(result: dict, sent: dict, transport: str) -> None:
    """An older plugin accepts the keys and draws the old figure."""
    if sent and "style_applied" not in result:
        raise PluginOutdatedError(transport)


def echoed_style(result: dict, sent: dict, transport: str) -> tuple[dict | None, list]:
    """``style_applied`` from the plugin, after the outdated-plugin check."""
    require_style_applied(result, sent, transport)
    missing = result.get("fonts_missing") or []
    return result.get("style_applied"), list(missing)

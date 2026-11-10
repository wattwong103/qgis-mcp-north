"""Shared colour scales for qgis_compose_panels (TASK-22).

The server decides every scale here (pure, stdlib only) and the QGIS plugin only
colours and draws what it is given, so the numbers a figure shows are the ones
the result reports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SHARES = ("row", "column", "all")


@dataclass(frozen=True)
class GroupScale:
    """One colour scale shared by a group of panels."""

    vmin: float
    vmax: float
    data_max: float
    capped: bool
    panels: tuple[int, ...]
    empty: bool = False  # no panel of the group has any value; vmin, vmax and data_max are placeholders


def panel_group(index: int, rows: int, cols: int, share: str) -> int:
    """The scale group of panel ``index`` (row-major) in a ``rows`` x ``cols`` grid."""
    if share == "row":
        return index // cols
    if share == "column":
        return index % cols
    return 0


def percentile(values: list[float], p: float) -> float:
    """The ``p``-th percentile with numpy's default linear interpolation."""
    ordered = sorted(values)
    rank = p / 100.0 * (len(ordered) - 1)
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def group_scales(columns: list[list[float]], rows: int, cols: int, share: str,
                 vmin: float | None, cap_percentile: float) -> list[GroupScale]:
    """One scale per group, in group order.

    A group runs from ``vmin`` (default ``min(0, group minimum)``) to the
    ``cap_percentile`` percentile of all its panels' values; it is ``capped``
    when that falls below the group's maximum. A group with no values at all is
    ``empty``: it still gets a flat scale at ``vmin`` so the figure renders.
    """
    n_groups = {"row": rows, "column": cols}.get(share, 1)
    scales = []
    for group in range(n_groups):
        members = tuple(i for i in range(len(columns)) if panel_group(i, rows, cols, share) == group)
        values = [v for i in members for v in columns[i]]
        data_max = max(values) if values else 0.0
        low = vmin if vmin is not None else min(0.0, min(values) if values else 0.0)
        high = percentile(values, cap_percentile) if values else low
        scales.append(GroupScale(vmin=low, vmax=high, data_max=data_max, capped=high < data_max,
                                 panels=members, empty=not values))
    return scales


def ticks(scale: GroupScale, n: int, spec: str) -> list[tuple[float, str]]:
    """``n`` evenly spaced (position 0..1, label) pairs; ``≥`` marks a capped top."""
    if scale.vmax == scale.vmin:
        return [(0.0, format(scale.vmin, spec))]
    out = []
    for k in range(n):
        position = k / (n - 1)
        label = format(scale.vmin + position * (scale.vmax - scale.vmin), spec)
        if scale.capped and k == n - 1:
            label = "≥ " + label
        out.append((position, label))
    return out

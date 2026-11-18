"""Shared colour scales for qgis_compose_panels (TASK-22).

The server decides every scale here (pure, stdlib only) and the QGIS plugin only
colours and draws what it is given, so the numbers a figure shows are the ones
the result reports. Difference columns are the same subtraction ``qgis_expression``
writes (``sim - obs``), joined into the CSV before those scales.
"""

from __future__ import annotations

import math
import re
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


@dataclass(frozen=True)
class DifferenceColumn:
    """One panel column defined as ``name = left - right`` on the CSV."""

    name: str
    left: str
    right: str


_PART = r'(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))'
_DIFFERENCE = re.compile(rf"^\s*{_PART}\s*=\s*{_PART}\s*-\s*{_PART}\s*$")


def _part(match: re.Match[str], index: int) -> str:
    quoted, bare = match.group(index), match.group(index + 1)
    return quoted if quoted is not None else bare


def parse_difference_columns(entries: list[str] | None) -> list[DifferenceColumn]:
    """``name = left - right`` entries. ``None`` and ``[]`` are no differences.

    Raises ``ValueError`` when an entry is not that subtraction. The caller
    turns it into ``InvalidArgumentError``.
    """
    if entries is None:
        return []
    if isinstance(entries, (str, bytes)) or not isinstance(entries, (list, tuple)):
        raise ValueError(
            f"difference_columns must be a list of 'name = left - right', got {entries!r}."
        )
    parsed: list[DifferenceColumn] = []
    seen: set[str] = set()
    for entry in entries:
        match = _DIFFERENCE.fullmatch(entry) if isinstance(entry, str) else None
        if match is None:
            raise ValueError(f"difference_columns entry {entry!r} is not name = left - right.")
        name, left, right = _part(match, 1), _part(match, 3), _part(match, 5)
        if name in seen:
            raise ValueError(f"difference_columns names {name!r} twice.")
        seen.add(name)
        parsed.append(DifferenceColumn(name=name, left=left, right=right))
    return parsed


def subtract_columns(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    """``left - right`` for keys present in both. A missing side is left out."""
    return {key: left[key] - right[key] for key in left if key in right}


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

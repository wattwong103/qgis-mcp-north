"""Shared scale groups and colour-bar ticks for qgis_compose_panels (TASK-22).

Pure math, server side: the plugin only colours and draws what these decide.
"""

from __future__ import annotations

import pytest

from qgis_mcp_workflows.panels import GroupScale, group_scales, panel_group, percentile, ticks


def test_panel_group_by_row_column_and_all():
    assert [panel_group(i, 2, 2, "row") for i in range(4)] == [0, 0, 1, 1]
    assert [panel_group(i, 2, 2, "column") for i in range(4)] == [0, 1, 0, 1]
    assert {panel_group(i, 2, 2, "all") for i in range(4)} == {0}


def test_percentile_matches_numpy_linear():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([0, 10], 98) == pytest.approx(9.8)
    assert percentile([5], 98) == 5


def test_row_groups_share_one_capped_scale():
    cols = [[0, 1, 2, 100], [0, 1, 2, 3], [0, 4], [0, 8]]  # 2 x 2, row-major
    a, b = group_scales(cols, 2, 2, "row", None, 98)
    assert a.panels == (0, 1) and b.panels == (2, 3)
    assert a.vmin == 0 and a.vmax == pytest.approx(percentile([0, 1, 2, 100, 0, 1, 2, 3], 98))
    assert a.capped and a.data_max == 100
    assert b.vmax == pytest.approx(percentile([0, 4, 0, 8], 98)) and b.capped


def test_vmin_defaults_to_zero_or_the_negative_minimum():
    assert group_scales([[2, 3]], 1, 1, "all", None, 100)[0].vmin == 0
    assert group_scales([[-2, 3]], 1, 1, "all", None, 100)[0].vmin == -2


def test_ticks_mark_a_capped_top():
    s = GroupScale(vmin=0.0, vmax=0.04, data_max=0.09, capped=True, panels=(0,))
    assert ticks(s, 3, ".0%") == [(0.0, "0%"), (0.5, "2%"), (1.0, "≥ 4%")]


def test_a_flat_group_has_one_tick():
    s = group_scales([[0, 0], [0, 0]], 1, 2, "row", None, 100)[0]
    assert s.vmin == s.vmax == 0 and ticks(s, 5, ".1f") == [(0.0, "0.0")]
    assert not s.empty  # flat is not empty: its zones have values, all zero


def test_a_group_without_values_is_flagged_empty():
    # TASK-24 (North 2026-11-10: render and flag): row 0 has no value at all, row 1 does.
    empty, full = group_scales([[], [], [1.0], [2.0]], 2, 2, "row", None, 100)
    assert empty.empty and not full.empty
    assert (empty.vmin, empty.vmax, empty.data_max, empty.capped) == (0.0, 0.0, 0.0, False)

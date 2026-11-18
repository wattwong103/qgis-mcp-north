"""Explicit class edges, with no PyQGIS import.

A value on an outer edge is inside. An empty value list is outside: there is
nothing to draw.
"""


def outside_breaks(values, breaks):
    """Return ``(n_outside, data_min, data_max, all_outside)``."""
    if not values:
        return 0, None, None, True
    low = breaks[0]
    high = breaks[-1]
    data_min = values[0]
    data_max = values[0]
    n_outside = 0
    for value in values:
        if value < data_min:
            data_min = value
        if value > data_max:
            data_max = value
        if value < low or value > high:
            n_outside += 1
    return n_outside, data_min, data_max, n_outside == len(values)

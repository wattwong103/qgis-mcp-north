"""Message text and number checks for named recipes. No PyQGIS import."""

from __future__ import annotations


def finite_number(value):
    """A finite int or float, or a numeric string. Booleans are not numbers."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def crs_detail(crs):
    return "RECIPE_CRS: %s is not a CRS." % (crs,)


def empty_detail(recipe, n_input, crs):
    return "RECIPE_EMPTY: %s wrote 0 of %s features. CRS %s." % (recipe, n_input, crs)


def fields_detail(missing, available):
    return "RECIPE_FIELDS: %s is not on the layer. Available: %s" % (missing, ", ".join(available))


def geometry_detail(geom, crs):
    return "RECIPE_GEOMETRY: %s %s" % (geom, crs)

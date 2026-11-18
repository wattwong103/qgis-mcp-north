"""Count, sum, or mean join values onto target features.

No PyQGIS import. The plugin decides which features intersect; this module
reduces those matches. A join feature may fall in more than one target.
Within one target it counts once.
"""

STATISTICS = ("count", "sum", "mean")


def normalize_statistic(statistic):
    if statistic is None:
        return "count"
    key = str(statistic).strip().lower()
    if key not in STATISTICS:
        return None
    return key


def default_field_name(statistic):
    if statistic not in STATISTICS:
        raise ValueError(statistic)
    return statistic


def unique_field_name(base, existing):
    """Return ``base``, or ``base_2``, ``base_3``, … if that name is taken.

    Comparison is case-insensitive, matching ``QgsFields.lookupField``.
    """
    used = set()
    for name in existing:
        used.add(str(name).lower())
    if base.lower() not in used:
        return base
    n = 2
    while True:
        candidate = f"{base}_{n}"
        if candidate.lower() not in used:
            return candidate
        n += 1


def _number(value):
    """A float, or None when the value is not a number.

    Booleans are skipped (they are ints in Python). A numeric string is read
    as a number. Null, blank, and NaN are skipped.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def reduce_matches(values, statistic):
    """Reduce one polygon's intersecting join values.

    ``count`` is the number of features, including nulls. ``sum`` adds numeric
    values and is 0 when none are numeric. ``mean`` is null when none are.
    """
    if statistic == "count":
        return len(values)
    total = 0.0
    n_numbers = 0
    for value in values:
        number = _number(value)
        if number is None:
            continue
        total += number
        n_numbers += 1
    if statistic == "sum":
        return total
    if statistic == "mean":
        if n_numbers == 0:
            return None
        return total / float(n_numbers)
    raise ValueError(statistic)


def collect_groups(n_targets, join_values, pairs):
    """Group join values by target index.

    ``pairs`` is ``(target_index, join_index)``. A repeated pair counts once.
    A join index may appear under more than one target.
    """
    seen = []
    groups = []
    for _index in range(n_targets):
        seen.append(set())
        groups.append([])
    for target_i, join_i in pairs:
        if join_i in seen[target_i]:
            continue
        seen[target_i].add(join_i)
        groups[target_i].append(join_values[join_i])
    return groups


def matched_any(groups):
    return any(groups)


def count_with_value(values):
    """How many reduced values are not null. Zero is a value."""
    n = 0
    for value in values:
        if value is not None:
            n += 1
    return n


def empty_detail(n_join, join_geom, join_crs, n_target, target_geom, target_crs):
    return (
        f"AGGREGATE_EMPTY: predicate 'intersects' matched 0 of {n_join} join features "
        f"({join_geom}, {join_crs}) against {n_target} target features ({target_geom}, {target_crs})."
    )


def geometry_detail(target_geom, target_crs, join_geom, join_crs):
    return (
        "AGGREGATE_GEOMETRY: target must be polygon and join must be point or line. "
        f"target is {target_geom} ({target_crs}); join is {join_geom} ({join_crs})."
    )


def geometries_ok(target_geom, join_geom):
    return target_geom == "polygon" and join_geom in ("point", "line")

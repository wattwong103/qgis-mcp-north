"""Class histogram for qgis_zonal_stats.

No PyQGIS import. The plugin counts cells per zone; this module decides which
class ids become columns, and stores 0 where a zone lacks a class that occurs
somewhere else. ``mean`` is not computed here. It stays the numeric stat.
"""

STATS = (
    "count",
    "sum",
    "mean",
    "median",
    "stdev",
    "min",
    "max",
    "majority",
    "histogram",
)

DEFAULT_STATS = ("count", "sum", "mean")


def normalize_stats(stats):
    """Lowercase stat names, drop duplicates, and keep the caller's order.

    None or an empty list is count, sum, and mean. ``mean`` stays that numeric
    stat. ``majority`` stays a numeric stat. ``histogram`` is the class count.
    An unknown name raises ValueError.
    """
    if not stats:
        return list(DEFAULT_STATS)
    cleaned = []
    for name in stats:
        key = str(name).strip().lower()
        if key in cleaned:
            continue
        if key not in STATS:
            raise ValueError(
                f"unknown stat {key!r}. Available: {', '.join(STATS)}. mean remains the numeric stat"
            )
        cleaned.append(key)
    if not cleaned:
        return list(DEFAULT_STATS)
    return cleaned


def _class_id(value, nodata, has_nodata):
    """Integer class id, or None when the value is the raster's nodata.

    A value that is not a whole number raises ValueError. The message names
    ``mean``, which is the stat for a continuous band.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"histogram needs integer class ids, got {value!r}. mean remains the numeric stat"
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(
            f"histogram needs integer class ids, got {value!r}. mean remains the numeric stat"
        )
    if has_nodata and nodata is not None and not isinstance(nodata, bool):
        try:
            nodata_number = float(nodata)
        except (TypeError, ValueError):
            nodata_number = None
        if nodata_number is not None and number == nodata_number:
            return None
    nearest = round(number)
    if abs(number - nearest) > 1e-6:
        raise ValueError(
            f"histogram needs integer class ids, got {number}. mean remains the numeric stat"
        )
    return nearest


def histogram_columns(zone_counts, prefix, nodata=None, has_nodata=False):
    """One integer column per class id that occurs in ``zone_counts``.

    ``zone_counts`` is one ``{cell value: count}`` dict per zone. Nodata is
    dropped. A class that never occurs is omitted, so a class absent from the
    zones has no column. A zone that lacks a class which occurs elsewhere gets
    0. Names are ``prefix + class id``, sorted by class id. Counts are ints.
    """
    prefix = prefix or ""
    parsed = []
    seen = []
    for zone in zone_counts:
        counts = {}
        for raw, count in zone.items():
            class_id = _class_id(raw, nodata, has_nodata)
            if class_id is None:
                continue
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"histogram counts must be non-negative integers, got {count!r}")
            counts[class_id] = counts.get(class_id, 0) + count
            if class_id not in seen:
                seen.append(class_id)
        parsed.append(counts)
    seen.sort()
    names = [f"{prefix}{class_id}" for class_id in seen]
    rows = [[int(counts.get(class_id, 0)) for class_id in seen] for counts in parsed]
    return names, rows

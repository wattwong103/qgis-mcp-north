"""Read the QuickMapServices catalog and turn an entry into a ``basemap_spec``.

QuickMapServices (QMS) is a widely-installed QGIS plugin that ships ~100 curated
tile services as ``metadata.ini`` files. This module reads that catalog directly:
we do not import QMS or need it loaded, only present on disk, so it works
unchanged in headless mode where no plugin GUI exists.

Resolution happens **here, plugin-side**, rather than in the MCP server, for one
decisive reason: the catalog lives inside the QGIS user profile, which is
guaranteed present wherever QGIS is running and *not* guaranteed on the machine
running the MCP server. With ``--transport=plugin`` pointed at another host,
MCP-side resolution would read the wrong profile or none at all.

Not every catalog entry is usable through our renderer. ``_load_basemap_layer``
builds a bare ``type=xyz`` provider URI, which — unlike QMS's own loader — does
no CRS assignment and no axis handling. Feeding it the wrong entry yields tiles
that draw flipped or misregistered: a map that looks fine and is geographically
wrong. :func:`catalog` therefore filters to what that URI can honestly serve and
reports why each rejection happened.

This module is the single implementation. Nothing else should parse QMS INIs.

Python 3.9 compatible: it runs under QGIS's bundled interpreter.
"""

from __future__ import annotations

import configparser
import os

# QMS's own defaults, from quick_map_services/qgis_map_helpers.py: absent zmin/zmax
# fall back to 0/18, and only an explicit y_origin_top of 0 flips {y} -> {-y}.
_DEFAULT_ZMIN = 0
_DEFAULT_ZMAX = 18

# Providers whose terms restrict tile access to their own SDKs/apps. QMS shipping
# a definition is not permission to use it: these are excluded from the catalog
# and named explicitly if asked for, so the refusal is legible rather than a
# mysterious absence.
_RESTRICTED_GROUPS = {
    "google": "Google Maps Platform terms permit tile access only through Google's own APIs/SDKs.",
    "bing": "Bing Maps terms require an API key and their own control.",
    "yandex": "Yandex Maps terms restrict tile use to Yandex services.",
    "here": "HERE terms require an authenticated plan.",
    "2gis": "2GIS terms restrict tile use to their own products.",
    "autonavi": "AutoNavi/Amap terms restrict tile use to their own products.",
    "waze": "Waze tiles are not offered for third-party reuse.",
    "mapbox": "Mapbox requires an access token.",
}


# Hosts observed to require an API key. Substring match on the URL, so a bare
# domain covers its CDN prefixes ("basemaps.cartocdn.com" catches
# "a.basemaps.cartocdn.com"). Verified against the shipped catalog rather than
# assumed: Stamen serves from BOTH stamen-tiles.a.ssl.fastly.net and
# tile.stamen.com, and listing only the first let 11 key-walled sources through.
#
# This annotates the catalog; it never asserts a source is live. CARTO began
# requiring a key without changing its URLs, and its tiles still return HTTP 200
# with an "API KEY REQUIRED" watermark — see tests/test_basemap_liveness.py.
KEYED_TILE_HOSTS = (
    "basemaps.cartocdn.com",
    "cartodb-basemaps",
    "tiles.stadiamaps.com",
    "stamen-tiles",
    "tile.stamen.com",
    "api.mapbox.com",
)


def is_keyed(url):
    """True when ``url`` is served from a host known to require an API key."""
    return any(host in url for host in KEYED_TILE_HOSTS)


class QmsUnavailableError(Exception):
    """QuickMapServices is not installed in the active QGIS profile."""


class QmsSourceError(Exception):
    """The requested QMS source is absent, restricted, or unrenderable here."""


def _profile_dir():
    from qgis.core import QgsApplication

    return QgsApplication.qgisSettingsDirPath()


def _catalog_dirs(profile=None):
    """Directories holding ``<id>/metadata.ini`` entries, contrib then user-added."""
    profile = profile or _profile_dir()
    return [
        os.path.join(profile, "python", "plugins", "quick_map_services",
                     "quickmapservices_contrib", "data_sources"),
        os.path.join(profile, "QuickMapServices", "User"),
    ]


def _read_ini(path):
    cp = configparser.ConfigParser()
    # QMS INIs are UTF-8 and contain © in copyright_text.
    cp.read(path, encoding="utf-8")
    return cp


def _entry(ds_id, ini_path):
    """Parse one metadata.ini.

    Returns ``(entry, None)`` for a usable TMS source, or ``(None, detail)``
    explaining why it isn't one. The detail matters: QMS carries MVT (vector
    tile) and WMS sources that look perfectly valid in the QGIS UI, and a bare
    "not found" for one of those sends the caller hunting for a typo that
    isn't there.
    """
    cp = _read_ini(ini_path)
    if not cp.has_section("general"):
        return None, "no [general] section"
    declared = (cp["general"].get("type") or "").strip().upper()
    if declared != "TMS":
        return None, (
            "declares type=%s; only TMS (raster XYZ) sources can be drawn as a "
            "basemap here" % (declared or "?")
        )
    if not cp.has_section("tms"):
        return None, "declares type=TMS but has no [tms] section"

    tms = cp["tms"]
    ui = cp["ui"] if cp.has_section("ui") else {}
    lic = cp["license"] if cp.has_section("license") else {}
    url = (tms.get("url") or "").strip()
    if not url:
        return None, "no tile URL in [tms]"

    epsg = (tms.get("epsg_crs_id") or "").strip() or None
    y_origin_top = (tms.get("y_origin_top") or "").strip() or None
    group = (ui.get("group") or "").strip()

    return {
        "id": ds_id,
        "alias": (ui.get("alias") or ds_id).strip(),
        "group": group,
        "url": url,
        "zmin": int(tms.get("zmin") or _DEFAULT_ZMIN),
        "zmax": int(tms.get("zmax") or _DEFAULT_ZMAX),
        "epsg": epsg,
        "y_origin_top": y_origin_top,
        "attribution": (lic.get("copyright_text") or "").strip(),
        "licence": (lic.get("name") or "").strip(),
        "terms_of_use": (lic.get("terms_of_use") or "").strip(),
    }, None


def _rejection(entry):
    """Why this entry can't be rendered through a bare type=xyz URI, or None."""
    epsg = entry["epsg"]
    if epsg is not None and epsg != "3857":
        # QMS assigns the CRS separately via set_tile_layer_proj(); our URI can't.
        return "crs", "declares EPSG:%s; the XYZ provider here assumes 3857" % epsg
    restricted = _RESTRICTED_GROUPS.get(entry["group"].lower())
    if restricted:
        return "licence", restricted
    return None


def catalog(profile=None, include_rejected=False):
    """Return the usable QMS sources, sorted by id.

    ``include_rejected`` adds a ``rejected`` list of ``(id, reason_kind, detail)``
    so callers can explain an absence instead of silently omitting it.
    """
    seen = {}
    rejected = []
    found_any_dir = False
    for base in _catalog_dirs(profile):
        if not os.path.isdir(base):
            continue
        found_any_dir = True
        for ds_id in sorted(os.listdir(base)):
            ini = os.path.join(base, ds_id, "metadata.ini")
            if not os.path.isfile(ini):
                continue
            try:
                entry, skip = _entry(ds_id, ini)
            except (configparser.Error, ValueError, UnicodeDecodeError):
                rejected.append((ds_id, "unparsable", "metadata.ini could not be read"))
                continue
            if entry is None:
                rejected.append((ds_id, "unsupported", skip))
                continue
            why = _rejection(entry)
            if why is not None:
                rejected.append((ds_id, why[0], why[1]))
                continue
            seen[ds_id] = entry  # user-added entries shadow contrib ones by id

    if not found_any_dir:
        raise QmsUnavailableError(
            "QuickMapServices is not installed in this QGIS profile "
            "(looked in %s). Install it from the QGIS plugin manager, or use a "
            "built-in preset instead. Next: retry with basemap='light'."
            % ", ".join(_catalog_dirs(profile))
        )

    entries = [seen[k] for k in sorted(seen)]
    if include_rejected:
        return entries, rejected
    return entries


def _near_matches(ds_id, entries, limit=5):
    """Ids sharing a token or prefix with ``ds_id`` — for an actionable error."""
    needle = ds_id.lower()
    scored = []
    for e in entries:
        cand = e["id"].lower()
        if cand.startswith(needle[:4]) or needle[:4] in cand:
            scored.append((0, e["id"]))
        elif any(tok and tok in cand for tok in needle.split("_")):
            scored.append((1, e["id"]))
    scored.sort()
    return [i for _, i in scored[:limit]]


def resolve(ds_id, opacity=1.0, profile=None):
    """Build a ``basemap_spec`` for one QMS source id.

    Mirrors QMS's own loader: zmin/zmax defaults, the ``{y}`` -> ``{-y}`` flip for
    bottom-origin schemes, and the ``=``/``&`` percent-escaping QMS applies before
    embedding a URL in a provider URI (our own URI builder does not do this, and
    an unescaped query string would corrupt the URI).
    """
    entries, rejected = catalog(profile=profile, include_rejected=True)
    by_id = {e["id"]: e for e in entries}

    entry = by_id.get(ds_id)
    if entry is None:
        for bad_id, kind, detail in rejected:
            if bad_id != ds_id:
                continue
            if kind == "licence":
                raise QmsSourceError(
                    "QuickMapServices source %r is excluded on licence grounds: %s "
                    "Next: pick another with qgis_list_basemaps()." % (ds_id, detail)
                )
            raise QmsSourceError(
                "QuickMapServices source %r is not usable here: %s. "
                "Next: pick another with qgis_list_basemaps()." % (ds_id, detail)
            )
        near = _near_matches(ds_id, entries)
        hint = (" Did you mean: %s?" % ", ".join(near)) if near else ""
        raise QmsSourceError(
            "QuickMapServices source %r not found (%d available).%s "
            "Next: call qgis_list_basemaps() for the catalog."
            % (ds_id, len(entries), hint)
        )

    url = entry["url"]
    if entry["y_origin_top"] == "0":
        url = url.replace("{y}", "{-y}")
    url = url.replace("=", "%3D").replace("&", "%26")

    return {
        "kind": "xyz",
        "name": entry["alias"],
        "source_id": entry["id"],
        "url": url,
        "zmin": entry["zmin"],
        "zmax": entry["zmax"],
        "opacity": float(opacity),
        "attribution": entry["attribution"],
        "licence": entry["licence"],
        "terms_of_use": entry["terms_of_use"],
    }

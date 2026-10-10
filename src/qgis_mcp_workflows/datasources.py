"""Files a figure reads through a project or a shapefile (TASK-16, spec §9).

A ``.shp`` is read together with its ``.dbf .shx .prj .cpg`` siblings, and a
``.qgz``/``.qgs`` project reads whatever its layers point at. ``discover``
turns a call's explicit inputs into the extra files to fingerprint, notes for
what it could not record, and redacted ``remote`` entries for databases and
web services.

Project files are untrusted input (they arrive through shared folders): the
XML is read from a single ``.qgs`` member with a byte cap enforced by reading,
documents with DOCTYPE/ENTITY declarations are refused, network paths are
never opened, and only regular files under the project folder or
DROPBOX_ROOT are fingerprinted.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

SIBLINGS = (".dbf", ".shx", ".prj", ".cpg")
MAX_PROJECT_BYTES = 64 * 1024 * 1024
MAX_DATASOURCES = 200
_ECHO = 200
_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_REMOTE_PROVIDERS = frozenset({
    "postgres", "postgresraster", "mssql", "oracle", "hana", "db2", "wms", "wfs", "wcs", "oapif",
    "arcgisfeatureserver", "arcgismapserver", "xyz", "vectortile", "sensorthings",
})
_FILE_PROVIDERS = frozenset({"ogr", "gdal", "mdal", "pdal"})
_SECRET = re.compile(
    r"\b(password|passwd|user|username|apikey|api_key|token|access_token|authcfg)\s*=\s*"
    r"('[^']*'|\"[^\"]*\"|[^\s&'\"]+)", re.IGNORECASE)
_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s@'\"]*@", re.IGNORECASE)
_QUERY = re.compile(r"\?[^\s'\"]*")
_DBNAME = re.compile(r"dbname='((?:[^'\\]|\\.)*)'|dbname=(\S+)")
_DRIVE = re.compile(r"[A-Za-z]:[/\\]")


def _echo(text: object) -> str:
    """Printable and short, for notes that end up in sidecars and on terminals."""
    return "".join(c if c.isprintable() else " " for c in str(text))[:_ECHO]


def redact(source: str) -> str:
    """A database or web source without passwords, users, keys, tokens, userinfo or query strings."""
    text = _SECRET.sub(lambda m: f"{m.group(1)}=***", unquote(source))
    text = _USERINFO.sub(r"\1***@", text)
    return _echo(_QUERY.sub("?***", text))


def shapefile_siblings(argument: str, path: str) -> list[tuple[str, str]]:
    """The .dbf .shx .prj .cpg files read together with a .shp, when they exist."""
    from qgis_mcp_workflows.provenance import _regular_stat

    stem = path[:-4]
    found: list[tuple[str, str]] = []
    for ext in SIBLINGS:
        for candidate in (stem + ext, stem + ext.upper()):
            if _regular_stat(candidate) is not None:
                found.append((f"{argument}:{ext}", candidate))
                break
    return found


def read_project_xml(path: str) -> tuple[str | None, str | None]:
    """(project XML, None) or (None, why its datasources are not recorded)."""
    name = _echo(os.path.basename(path))
    try:
        if path.lower().endswith(".qgz"):
            with zipfile.ZipFile(path) as archive:
                members = [m for m in archive.infolist() if m.filename.lower().endswith(".qgs") and not m.is_dir()]
                if len(members) != 1:
                    return None, f"{name}: expected one .qgs inside, found {len(members)}; datasources not recorded"
                with archive.open(members[0]) as fh:  # the declared size is not trusted: read with a limit
                    data = fh.read(MAX_PROJECT_BYTES + 1)
        else:
            with open(path, "rb") as fh:
                data = fh.read(MAX_PROJECT_BYTES + 1)
    except Exception as err:  # corrupt zip, unsupported compression, I/O: fail safe, never abort the call
        return None, f"{name}: could not be read ({type(err).__name__}); datasources not recorded"
    if len(data) > MAX_PROJECT_BYTES:
        return None, f"{name}: project XML larger than {MAX_PROJECT_BYTES} bytes; datasources not recorded"
    if _DECLARATION.search(data):
        return None, f"{name}: has a DOCTYPE or ENTITY declaration; datasources not read"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{name}: project XML is not UTF-8; datasources not recorded"


def project_sources(text: str) -> list[tuple[str, str, str]]:
    """(layer name, provider, datasource) for every map layer in the project XML."""
    found: list[tuple[str, str, str]] = []
    for layer in ET.fromstring(text).iter("maplayer"):
        source = (layer.findtext("datasource") or "").strip()
        provider = (layer.findtext("provider") or "").strip().lower()
        name = (layer.findtext("layername") or layer.findtext("id") or "").strip()
        if source:
            found.append((name, provider, source))
    return found


def _local(path: str, project_dir: str) -> tuple[str, str]:
    unified = path.replace("\\", "/")
    if unified.startswith(("//", "/??/")):
        return "note", f"network datasource not opened: {_echo(path)}"
    if not os.path.isabs(path) and not _DRIVE.match(path):
        path = os.path.join(project_dir, path)
    return "file", os.path.normpath(path)


def classify(provider: str, source: str, project_dir: str) -> tuple[str, str]:
    """("file", path), ("remote", redacted source) or ("note", text) for one datasource."""
    if provider in _REMOTE_PROVIDERS:
        return "remote", redact(source)
    if provider == "delimitedtext":
        parts = urlsplit(source)
        if parts.scheme.lower() != "file":
            return "note", f"unparsed delimited-text datasource: {redact(source)}"
        if parts.netloc not in ("", "localhost"):
            return "note", f"network datasource not opened: {redact(source)}"
        path = unquote(parts.path)
        if re.match(r"/[A-Za-z]:/", path):
            path = path[1:]
        return _local(path, project_dir)
    if provider == "spatialite" or (provider not in _FILE_PROVIDERS and "dbname=" in source):
        match = _DBNAME.search(source)
        if not match:
            return "note", f"unparsed {provider or 'database'} datasource: {redact(source)}"
        return _local((match.group(1) or match.group(2)).replace("\\'", "'"), project_dir)
    if provider in _FILE_PROVIDERS:
        path = source.split("|", 1)[0]
        if "://" in path or path.lower().startswith("/vsi"):
            return "remote", redact(source)
        return _local(path, project_dir)
    if provider in ("memory", "virtual"):
        return "note", f"{provider} layer: its features are not in a file"
    return "note", f"unparsed datasource ({_echo(provider) or 'no provider'}): {redact(source)}"


def _project(argument: str, path: str, root: str, add: Callable[[str, str], None],
             notes: list[str], remote: list[dict]) -> None:
    from qgis_mcp_workflows.provenance import _regular_stat, _relative_under, normalise

    text, problem = read_project_xml(path)
    if problem:
        notes.append(problem)
        return
    try:
        sources = project_sources(text or "")
    except ET.ParseError:
        notes.append(f"{_echo(os.path.basename(path))}: not valid project XML; datasources not recorded")
        return
    project_dir = os.path.dirname(normalise(path))
    roots = [normalise(project_dir)] + ([normalise(root)] if root else [])
    label = f"{argument}:datasource"
    files = 0
    for _name, provider, source in sources:
        kind, value = classify(provider, source, project_dir)
        if kind == "remote":
            remote.append({"argument": label, "value": value, "note": "database or web service, not pinned"})
            continue
        if kind == "note":
            notes.append(f"{argument}: {value}")
            continue
        norm = normalise(value)
        if not any(_relative_under(norm, r) is not None for r in roots):
            notes.append(f"{argument}: datasource outside the project folder and DROPBOX_ROOT, "
                         f"not fingerprinted: {_echo(norm)}")
            continue
        if _regular_stat(norm) is None:
            notes.append(f"{argument}: datasource missing or not a regular file: {_echo(norm)}")
            continue
        if files >= MAX_DATASOURCES:
            notes.append(f"{argument}: more than {MAX_DATASOURCES} datasources; the rest were not fingerprinted")
            break
        files += 1
        add(label, norm)
        if norm.lower().endswith(".shp"):
            for sibling_argument, sibling in shapefile_siblings(label, norm):
                add(sibling_argument, sibling)


def discover(explicit: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str], list[dict]]:
    """(extra (argument, path) inputs, unrecorded notes, remote entries) for a call's explicit inputs."""
    from qgis_mcp_workflows.provenance import _regular_stat, normalise

    root = os.environ.get("DROPBOX_ROOT", "").strip()
    extra: list[tuple[str, str]] = []
    notes: list[str] = []
    remote: list[dict] = []
    seen = {normalise(p) for _, p in explicit}

    def add(argument: str, path: str) -> None:
        norm = normalise(path)
        if norm not in seen:
            seen.add(norm)
            extra.append((argument, norm))

    for argument, path in explicit:
        lowered = path.lower()
        if lowered.endswith(".shp"):
            for sibling_argument, sibling in shapefile_siblings(argument, path):
                add(sibling_argument, sibling)
        elif lowered.endswith((".qgz", ".qgs")) and _regular_stat(path) is not None:
            _project(argument, path, root, add, notes, remote)
    return extra, list(dict.fromkeys(notes)), remote

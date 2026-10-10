"""Files a figure reads through a project or a shapefile (TASK-16, spec §9).

A ``.shp`` is read together with its ``.dbf .shx .prj .cpg`` siblings, and a
``.qgz``/``.qgs`` project reads whatever its layers point at. ``discover``
turns a call's explicit inputs into the extra files to fingerprint, notes for
what it could not record, and redacted ``remote`` entries for databases and
web services.

Project files are untrusted input (they arrive through shared folders): the
XML is read from a single ``.qgs`` member with a byte cap enforced by reading,
documents with DOCTYPE/ENTITY declarations are refused (except the fixed one
QGIS writes), network paths are never opened, links below a root are never
followed, only regular files under the project folder or DROPBOX_ROOT are
fingerprinted, and every echoed source or path is redacted.
"""

from __future__ import annotations

import os
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from urllib.parse import unquote, urlsplit

SIBLINGS = (".dbf", ".shx", ".prj", ".cpg")
MAX_PROJECT_BYTES = 64 * 1024 * 1024
# Entries per project: layers classified (files, remote, notes) and files added
# (siblings included). Keeps a sidecar's depends_on under the replay's size check.
MAX_DATASOURCES = 200
_ECHO = 200
_REDACT_INPUT_MAX = 4096  # redaction works on a bounded prefix; output is capped anyway
_FEED_CHUNK = 1 << 20
_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
# QGIS writes this exact declaration at the top of every project: no internal
# subset, so it defines no entities and nothing is fetched. It alone is allowed.
_QGIS_DOCTYPE = re.compile(
    rb"\A(\xef\xbb\xbf)?\s*(<\?xml[^>]*\?>\s*)?"
    rb"<!DOCTYPE\s+qgis\s+PUBLIC\s+(['\"])http://mrcc\.com/qgis\.dtd\3\s+(['\"])SYSTEM\4\s*>")
_REMOTE_PROVIDERS = frozenset({
    "postgres", "postgresraster", "mssql", "oracle", "hana", "db2", "wms", "wfs", "wcs", "oapif",
    "arcgisfeatureserver", "arcgismapserver", "xyz", "vectortile", "sensorthings",
})
_FILE_PROVIDERS = frozenset({"ogr", "gdal", "mdal", "pdal"})
# Any key that looks like a credential, in key=value sources (space, &, ; or | separated).
_KEY = r"[\w:.-]*?(?:pass|pwd|user|uid|key|token|secret|auth|cred)[\w.-]*"
_VALUE = r"'(?:[^'\\]|\\.)*'?|\"(?:[^\"\\]|\\.)*\"?|[^\s&;|]+"
_SECRET = re.compile(rf"(^|[\s&;?,(|])({_KEY})\s*=\s*({_VALUE})", re.IGNORECASE)
_SCHEME_AUTH = re.compile(r"\b(bearer|basic|digest|token)\s+[^\s&;|'\"]+", re.IGNORECASE)
_USERINFO = re.compile(r"(?<![a-z0-9+.-])([a-z][a-z0-9+.-]{0,31}://)[^\s'\"]*@", re.IGNORECASE)
_SLASH_CREDENTIALS = re.compile(r"(^|[\s:=])[^\s/@:'\"]+/[^\s@'\"]+@")  # OCI:user/password@db
_QUERY = re.compile(r"\?[^\s'\"]*")
_DBNAME = re.compile(r"dbname='((?:[^'\\]|\\.)*)'|dbname=(\S+)")
_DRIVE = re.compile(r"[A-Za-z]:[/\\]")
# GDAL/OGR connection strings and driver-prefixed sources: PG:, MSSQL:, OCI:, MySQL:, GPKG:, NETCDF: ...
_PREFIXED = re.compile(r"\s*[A-Za-z][A-Za-z0-9_]+:(?![/\\])")
_LOCAL_ARCHIVE = re.compile(r"/vsi(?:zip|gzip|tar)/(.+?\.(?:zip|gz|tgz|tar))(?:/|$)", re.IGNORECASE)


def _echo(text: object) -> str:
    """Printable and short, for notes that end up in sidecars and on terminals."""
    return "".join(c if c.isprintable() else " " for c in str(text))[:_ECHO]


def _scrub(text: str) -> str:
    text = _SCHEME_AUTH.sub(lambda m: f"{m.group(1)} ***", text)
    text = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}=***", text)
    text = _USERINFO.sub(r"\1***@", text)
    text = _SLASH_CREDENTIALS.sub(r"\1***@", text)
    return _QUERY.sub("?***", text)


def redact(source: object) -> str:
    """A source or path without passwords, users, keys, tokens, userinfo or query strings.

    Scrubbed as written and again after percent-decoding, so neither an encoded
    terminator (%26, %27) nor an encoded key (apikey%3D...) lets a secret through.
    """
    text = str(source)[:_REDACT_INPUT_MAX]
    return _echo(_scrub(unquote(_scrub(text))))


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
    data = _QGIS_DOCTYPE.sub(lambda m: (m.group(1) or b"") + (m.group(2) or b""), data, count=1)
    if _DECLARATION.search(data):
        return None, f"{name}: has a DOCTYPE or ENTITY declaration; datasources not read"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{name}: project XML is not UTF-8; datasources not recorded"


def project_sources(text: str, limit: int | None = None) -> list[tuple[str, str, str]]:
    """(layer name, provider, datasource) per map layer, streamed; at most ``limit`` + 1 entries."""
    limit = MAX_DATASOURCES if limit is None else limit
    parser = ET.XMLPullParser(events=("end",))
    found: list[tuple[str, str, str]] = []
    for start in range(0, len(text), _FEED_CHUNK):
        parser.feed(text[start:start + _FEED_CHUNK])
        for _event, element in parser.read_events():
            if element.tag != "maplayer":
                continue
            source = (element.findtext("datasource") or "").strip()
            provider = (element.findtext("provider") or "").strip().lower()
            name = (element.findtext("layername") or element.findtext("id") or "").strip()
            element.clear()
            if source:
                found.append((name, provider, source))
                if len(found) > limit:
                    return found
    parser.close()
    return found


def _local(path: str, project_dir: str) -> tuple[str, str]:
    path = path.replace("\\", "/")  # one separator, so ".." is a component on every OS
    if path.startswith(("//", "/??/")):
        return "note", f"network datasource not opened: {redact(path)}"
    if not os.path.isabs(path) and not _DRIVE.match(path):
        path = os.path.join(project_dir.replace("\\", "/"), path)
    return "file", os.path.normpath(path).replace("\\", "/")


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
        if re.match(r"/[A-Za-z]:[/\\]", path):
            path = path[1:]
        return _local(path, project_dir)
    if provider == "spatialite" or (provider not in _FILE_PROVIDERS and "dbname=" in source):
        match = _DBNAME.search(source)
        if not match:
            return "note", f"unparsed {_echo(provider) or 'database'} datasource: {redact(source)}"
        return _local((match.group(1) or match.group(2)).replace("\\'", "'"), project_dir)
    if provider in _FILE_PROVIDERS:
        path = source.split("|", 1)[0].strip()
        archive = _LOCAL_ARCHIVE.match(path)
        if archive:
            return _local(archive.group(1), project_dir)
        if "://" in path or path.lower().startswith("/vsi") or path.startswith("<") or _PREFIXED.match(path):
            return "remote", redact(source)
        return _local(path, project_dir)
    if provider in ("memory", "virtual"):
        return "note", f"{provider} layer: its features are not in a file"
    return "note", f"unparsed datasource ({_echo(provider) or 'no provider'}): {redact(source)}"


def _through_link(path: str, root: str) -> bool:
    """Whether a component of ``path`` below ``root`` is a symlink or junction (never followed)."""
    from qgis_mcp_workflows.provenance import _relative_under

    rest = _relative_under(path, root)
    current = root.rstrip("/")
    for part in (rest or "").split("/"):
        if not part:
            continue
        current = f"{current}/{part}"
        try:
            st = os.lstat(current)
        except (OSError, ValueError):
            return False
        if stat.S_ISLNK(st.st_mode) or os.path.isjunction(current):
            return True
    return False


class _Budget:
    def __init__(self) -> None:
        self.entries = 0
        self.files = 0


def _classify_one(argument: str, provider: str, source: str, project_dir: str, roots: list[str],
                  add: Callable[[str, str], bool], notes: list[str], remote: list[dict], budget: _Budget) -> None:
    from qgis_mcp_workflows.provenance import _regular_stat, _relative_under, normalise

    label = f"{argument}:datasource"
    kind, value = classify(provider, source, project_dir)
    if kind == "remote":
        remote.append({"argument": label, "value": value,
                       "note": "database, web service or driver-prefixed source, not pinned"})
        return
    if kind == "note":
        notes.append(f"{argument}: {value}")
        return
    norm = normalise(value)
    inside = [r for r in roots if _relative_under(norm, r) is not None]
    if not inside:
        notes.append(f"{argument}: datasource outside the project folder and DROPBOX_ROOT, "
                     f"not fingerprinted: {redact(norm)}")
        return
    if any(_through_link(norm, r) for r in inside):
        notes.append(f"{argument}: datasource reached through a link, not followed: {redact(norm)}")
        return
    if _regular_stat(norm) is None:
        notes.append(f"{argument}: datasource missing or not a regular file: {redact(norm)}")
        return
    if not add(label, norm, budget):
        return
    if norm.lower().endswith(".shp"):
        for sibling_argument, sibling in shapefile_siblings(label, norm):
            add(sibling_argument, sibling, budget)


def _project(argument: str, path: str, root: str, add: Callable[..., bool],
             notes: list[str], remote: list[dict]) -> None:
    from qgis_mcp_workflows.provenance import normalise

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
    roots = [normalise(project_dir)]
    if root:
        roots += [normalise(root), normalise(os.path.realpath(root))]  # macOS: ~/Dropbox -> CloudStorage
    budget = _Budget()
    for _name, provider, source in sources:
        if budget.entries >= MAX_DATASOURCES or budget.files >= MAX_DATASOURCES:
            notes.append(f"{argument}: more than {MAX_DATASOURCES} datasources; the rest were not recorded")
            break
        budget.entries += 1
        try:
            _classify_one(argument, provider, source, project_dir, roots, add, notes, remote, budget)
        except (ValueError, OSError):  # malformed URL, NUL in a path: a note, never an aborted record
            notes.append(f"{argument}: unparsed datasource ({_echo(provider) or 'no provider'}): {redact(source)}")


def discover(explicit: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str], list[dict]]:
    """(extra (argument, path) inputs, unrecorded notes, remote entries) for a call's explicit inputs."""
    from qgis_mcp_workflows.provenance import _regular_stat, normalise

    root = os.environ.get("DROPBOX_ROOT", "").strip()
    extra: list[tuple[str, str]] = []
    notes: list[str] = []
    remote: list[dict] = []
    seen = {normalise(p) for _, p in explicit}

    def add(argument: str, path: str, budget: _Budget | None = None) -> bool:
        norm = normalise(path)
        if norm in seen:
            return False
        if budget is not None:
            if budget.files >= MAX_DATASOURCES:
                return False
            budget.files += 1
        seen.add(norm)
        extra.append((argument, norm))
        return True

    for argument, path in explicit:
        lowered = path.lower()
        if lowered.endswith(".shp"):
            for sibling_argument, sibling in shapefile_siblings(argument, path):
                add(sibling_argument, sibling)
        elif lowered.endswith((".qgz", ".qgs")) and _regular_stat(path) is not None:
            _project(argument, path, root, add, notes, remote)
    return extra, list(dict.fromkeys(notes)), remote

"""Project and shapefile datasources (TASK-16, spec §9). Pure Python, no QGIS."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from qgis_mcp_workflows import datasources, provenance


def _qgs(*layers: tuple[str, str]) -> str:
    body = "".join(
        f'<maplayer><id>L{i}</id><datasource>{escape(source)}</datasource><layername>l{i}</layername>'
        f'<provider encoding="UTF-8">{provider}</provider></maplayer>'
        for i, (provider, source) in enumerate(layers))
    return f'<qgis version="3.40.0"><projectlayers>{body}</projectlayers></qgis>'


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _args(extra):
    return sorted(argument for argument, _ in extra)


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path))
    return tmp_path


def test_shapefile_siblings_are_fingerprinted(root):
    shp = _touch(root / "z.shp")
    for ext in (".dbf", ".shx", ".prj"):
        _touch(root / f"z{ext}")
    extra, notes, remote = datasources.discover([("zones_path", str(shp))])
    assert _args(extra) == ["zones_path:.dbf", "zones_path:.prj", "zones_path:.shx"]
    assert notes == [] and remote == []


def test_project_datasources_are_fingerprinted(root):
    project = root / "proj" / "p.qgs"
    gpkg = _touch(root / "proj" / "data" / "a.gpkg")
    csv = _touch(root / "tables" / "b c.csv")
    sqlite = _touch(root / "c.sqlite")
    shp = _touch(root / "shp" / "z.shp")
    _touch(root / "shp" / "z.dbf")
    url = "file:///" + str(csv).replace("\\", "/").lstrip("/").replace(" ", "%20") + "?type=csv&delimiter=,"
    _touch(project, _qgs(("ogr", "./data/a.gpkg|layername=a"),
                         ("delimitedtext", url),
                         ("spatialite", "dbname='../c.sqlite' table=\"t\" (geom)"),
                         ("ogr", "../shp/z.shp")).encode("utf-8"))
    extra, notes, remote = datasources.discover([("qgz_path", str(project))])
    paths = {provenance.normalise(p) for _, p in extra}
    for f in (gpkg, csv, sqlite, shp, root / "shp" / "z.dbf"):
        assert provenance.normalise(str(f)) in paths
    assert _args(extra).count("qgz_path:datasource") == 4
    assert "qgz_path:datasource:.dbf" in _args(extra)
    assert notes == [] and remote == []


def test_qgz_reads_its_single_project_member(root):
    _touch(root / "a.gpkg")
    qgz = root / "p.qgz"
    with zipfile.ZipFile(qgz, "w") as zf:
        zf.writestr("p.qgs", _qgs(("ogr", "./a.gpkg")))
        zf.writestr("p.qgd", b"auxiliary storage")
    extra, notes, _ = datasources.discover([("qgz_path", str(qgz))])
    assert _args(extra) == ["qgz_path:datasource"] and notes == []


@pytest.mark.parametrize("members", [[], ["a.qgs", "b.qgs"]])
def test_qgz_without_exactly_one_project_file_is_noted(root, members):
    qgz = root / "p.qgz"
    with zipfile.ZipFile(qgz, "w") as zf:
        zf.writestr("readme.txt", "x")
        for member in members:
            zf.writestr(member, _qgs())
    extra, notes, _ = datasources.discover([("qgz_path", str(qgz))])
    assert extra == [] and len(notes) == 1 and "one .qgs" in notes[0]


QGIS_HEADER = "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>" + chr(10)


def test_the_fixed_doctype_qgis_writes_is_accepted(root):
    _touch(root / "a.gpkg")
    project = _touch(root / "p.qgs", (QGIS_HEADER + _qgs(("ogr", "./a.gpkg"))).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert _args(extra) == ["qgz_path:datasource"] and notes == []


@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE qgis SYSTEM "x">',
    '<!ENTITY e "boom">',
    "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM' [<!ENTITY e 'boom'>]>",
    QGIS_HEADER + '<!DOCTYPE qgis SYSTEM "again">',
])
def test_doctype_and_entity_documents_are_refused(root, declaration):
    _touch(root / "a.gpkg")
    project = _touch(root / "p.qgs", (declaration + _qgs(("ogr", "./a.gpkg"))).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and "DOCTYPE or ENTITY" in notes[0]


def test_size_cap_is_enforced_by_reading_not_by_declared_size(root, monkeypatch):
    monkeypatch.setattr(datasources, "MAX_PROJECT_BYTES", 200)
    big = _qgs(("ogr", "./a.gpkg")) + "<!-- " + "a" * 5000 + " -->"
    project = _touch(root / "p.qgs", big.encode("utf-8"))
    qgz = root / "p.qgz"
    with zipfile.ZipFile(qgz, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("p.qgs", big)                      # compresses far below the cap
    for path in (project, qgz):
        extra, notes, _ = datasources.discover([("qgz_path", str(path))])
        assert extra == [] and "larger than" in notes[0]


def test_network_datasources_are_never_opened(root, monkeypatch):
    opened = []
    real = provenance._regular_stat
    monkeypatch.setattr(provenance, "_regular_stat", lambda p: opened.append(str(p)) or real(p))
    project = _touch(root / "p.qgs", _qgs(
        ("ogr", "//evil.invalid/share/x.gpkg"),
        ("ogr", "\\\\evil.invalid\\share\\y.gpkg"),
        ("ogr", "\\??\\UNC\\evil.invalid\\share\\z.gpkg"),
        ("delimitedtext", "file://evil.invalid/share/x.csv?type=csv"),
    ).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and len(notes) == 4 and all("not opened" in n for n in notes)
    assert not any("evil" in p for p in opened)


def test_a_datasource_outside_the_project_and_dropbox_root_is_noted(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "root"))
    outside = _touch(tmp_path / "other" / "x.gpkg")
    project = _touch(tmp_path / "root" / "proj" / "p.qgs", _qgs(("ogr", str(outside))).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and "outside the project folder and DROPBOX_ROOT" in notes[0]


def test_a_relative_datasource_climbing_into_dropbox_root_is_fingerprinted(root):
    target = _touch(root / "shared" / "x.gpkg")
    project = _touch(root / "a" / "b" / "p.qgs", _qgs(("ogr", "../../shared/x.gpkg")).encode("utf-8"))
    extra, _, _ = datasources.discover([("qgz_path", str(project))])
    assert [provenance.normalise(p) for _, p in extra] == [provenance.normalise(str(target))]


def test_directories_and_missing_files_are_not_fingerprinted(root):
    (root / "dir.gpkg").mkdir()
    project = _touch(root / "p.qgs", _qgs(("ogr", "./dir.gpkg"), ("ogr", "./missing.gpkg")).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and len(notes) == 2 and all("missing or not a regular file" in n for n in notes)


def test_databases_and_web_services_are_remote_with_credentials_redacted(root):
    postgres = ("dbname='gis' host=db.internal port=5432 user='north' password='hunter2' sslmode=disable "
                "key='gid' table=\"public\".\"zones\" (geom)")
    xyz = ("type=xyz&url=https://bob:pw42@tiles.example/%7Bz%7D/%7Bx%7D/%7By%7D.png?apikey%3Dabc123"
           "&token=t0k3n&zmax=19")
    project = _touch(root / "p.qgs", _qgs(("postgres", postgres), ("wms", xyz)).encode("utf-8"))
    extra, notes, remote = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and notes == [] and len(remote) == 2
    text = " ".join(r["value"] for r in remote)
    for secret in ("hunter2", "north", "bob", "pw42", "abc123", "t0k3n"):
        assert secret not in text
    assert all(r["argument"] == "qgz_path:datasource" for r in remote)


def test_memory_and_unknown_providers_are_noted(root):
    project = _touch(root / "p.qgs", _qgs(("memory", "Point?crs=EPSG:4326"), ("mystery", "abc")).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and len(notes) == 2


def test_datasource_count_is_capped(root, monkeypatch):
    monkeypatch.setattr(datasources, "MAX_DATASOURCES", 2)
    for name in ("a", "b", "c"):
        _touch(root / f"{name}.gpkg")
    project = _touch(root / "p.qgs", _qgs(*[("ogr", f"./{n}.gpkg") for n in "abc"]).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert len(extra) == 2 and any("more than 2" in n for n in notes)


def test_invalid_project_xml_is_noted(root):
    project = _touch(root / "p.qgs", b"<qgis><projectlayers><maplayer>")
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and "not valid project XML" in notes[0]


def test_redact_keeps_short_printable_text():
    redacted = datasources.redact("password='x" + chr(0x202E) + "' " + "y" * 500)
    assert chr(0x202E) not in redacted and len(redacted) <= 200 and "password=***" in redacted


# --- final-review fixes ------------------------------------------------------------------


@pytest.mark.parametrize("provider, source", [
    ("ogr", "PG:dbname='gis' host=db user='SECRETUSER' password='SECRETPW'"),
    ("gdal", "PG: dbname='r' host=h user='u' password='SECRETPW' table='t'"),
    ("ogr", "MSSQL:server=db;database=gis;uid=SECRETUSER;pwd=SECRETPW"),
    ("ogr", "OCI:SECRETUSER/SECRETPW@db"),
])
def test_ogr_connection_strings_are_remote_and_redacted(root, provider, source):
    project = _touch(root / "p.qgs", _qgs((provider, source)).encode("utf-8"))
    extra, notes, remote = datasources.discover([("qgz_path", str(project))])
    text = " ".join(notes) + " ".join(r["value"] for r in remote)
    assert extra == [] and len(remote) == 1 and "SECRET" not in text


@pytest.mark.parametrize("source", [
    "password=ab%26SECRET&x=1",
    "password=%27SECRET",
    "password='ab" + chr(92) + "'SECRET'",
    "postgresql://u:SEC/RET@h/db",
    "postgresql://u:SEC%2FRET%40x@h/db",
    "pwd=SECRET;uid=SECRET",
    "http-header:Authorization=Bearer SECRET&type=xyz",
    "sslpassword=SECRET user_password=SECRET",
    "url=https://h/t?apikey%3DSECRET",
])
def test_redact_resists_encoded_and_escaped_terminators(source):
    redacted = datasources.redact(source)
    assert "SECRET" not in redacted and "RET" not in redacted.replace("***", "")


def test_redact_is_fast_on_long_sources():
    import time

    started = time.perf_counter()
    datasources.redact("a" * 200_000 + "://x")
    assert time.perf_counter() - started < 1.0


def test_malformed_datasources_become_notes_not_errors(root):
    project = _touch(root / "p.qgs", _qgs(("delimitedtext", "file://[abc/x.csv?type=csv"),
                                          ("delimitedtext", "file:./x%00.csv")).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and len(notes) == 2


def _link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError):
            pytest.skip("cannot create a symlink or junction here")


def test_a_link_below_the_root_is_not_followed(root):
    _touch(root / ".." / f"{root.name}-outside" / "private.gpkg")
    _link(root / "proj" / "link", root.parent / f"{root.name}-outside")
    project = _touch(root / "proj" / "p.qgs", _qgs(("ogr", "./link/private.gpkg")).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert extra == [] and "link" in notes[0]


def test_dropbox_root_reached_through_its_real_path_counts_as_inside(tmp_path, monkeypatch):
    real = tmp_path / "CloudStorage" / "Dropbox"
    _link(tmp_path / "Dropbox", real)
    monkeypatch.setenv("DROPBOX_ROOT", str(tmp_path / "Dropbox"))
    data = _touch(real / "shared" / "x.gpkg")
    project = _touch(tmp_path / "Dropbox" / "proj" / "p.qgs", _qgs(("ogr", str(data))).encode("utf-8"))
    extra, notes, _ = datasources.discover([("qgz_path", str(project))])
    assert len(extra) == 1 and notes == []


def test_backslash_relative_paths_cannot_climb_out_on_posix(monkeypatch):
    import posixpath

    monkeypatch.setattr(datasources.os, "path", posixpath)
    kind, value = datasources.classify("ogr", "..\\..\\..\\..\\etc\\passwd", "/home/u/Dropbox/proj")
    assert kind == "file" and ".." not in value.split("/") and value == "/etc/passwd"


def test_a_local_zip_archive_is_fingerprinted_not_called_remote(root):
    archive = _touch(root / "data" / "x.zip")
    project = _touch(root / "p.qgs", _qgs(("ogr", "/vsizip/./data/x.zip/x.shp")).encode("utf-8"))
    extra, notes, remote = datasources.discover([("qgz_path", str(project))])
    assert [provenance.normalise(p) for _, p in extra] == [provenance.normalise(str(archive))]
    assert remote == [] and notes == []


def test_every_kind_of_entry_is_capped(root, monkeypatch):
    monkeypatch.setattr(datasources, "MAX_DATASOURCES", 3)
    layers = [("postgres", f"dbname='g{i}' host=h") for i in range(10)]
    project = _touch(root / "p.qgs", _qgs(*layers).encode("utf-8"))
    _, notes, remote = datasources.discover([("qgz_path", str(project))])
    assert len(remote) == 3 and any("more than 3" in n for n in notes)

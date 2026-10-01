"""Headless transport executor — long-lived QGIS Python subprocess on stdin/stdout.

Spawns ``python-qgis-ltr.bat`` (Windows / OSGeo4W) or ``qgis_python``
(Linux / macOS) running ``headless_runner.py``. Holds the subprocess open
across multiple ``dispatch()`` calls so that ``QgsApplication.initQgis`` —
which costs ~1-2 seconds — only pays once per server session.

Wire protocol: length-prefixed JSON, identical to the plugin transport's
TCP framing (``HEADER_STRUCT``: 4-byte big-endian uint32). The runner emits
``{"status": "ready"}`` once, then we exchange one request → one response
per ``dispatch`` call.

Launcher detection (in priority order):
    1. ``QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER`` env var (full path).
    2. Windows: ``M:\\QGIS LTR\\bin\\python-qgis-ltr.bat``,
       ``C:\\OSGeo4W\\bin\\python-qgis-ltr.bat``, ``C:\\Program Files\\QGIS *\\bin\\python-qgis*.bat``.
    3. macOS: the Python bundled inside QGIS.app —
       ``/Applications/QGIS-LTR.app/Contents/MacOS/bin/python3`` (LTR preferred),
       then ``QGIS.app``, then any ``/Applications/QGIS*.app``; QGIS 4 bundles
       last, via their ``Contents/MacOS/python`` wrapper. Homebrew's
       ``/opt/homebrew/bin/python3`` is *not* used: it has no PyQGIS.
    4. Linux: assume ``sys.executable`` already has PyQGIS importable
       (apt/conda installs usually do).

macOS also needs ``PROJ_LIB`` and ``GDAL_DATA`` pointed into the app bundle's
``Contents/Resources`` (QGIS 3) or ``Contents/Resources/qgis`` (QGIS 4).
QGIS.app sets these itself when launched normally, but a
subprocess spawned from outside the bundle inherits nothing — and without them
PROJ cannot open ``proj.db``, so *every* CRS silently comes back invalid
(``QgsCoordinateReferenceSystem("EPSG:4326").isValid()`` is ``False``) and
renders reproject wrong rather than failing loudly. ``_bundle_env`` derives
both paths from the launcher and injects them.

Raises ``HeadlessUnavailableError`` when no launcher is reachable; the user
should either install OSGeo4W, set the env var, or use ``--transport=plugin``.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import platform
import subprocess
import sys
import threading
from typing import ClassVar

from qgis_mcp_workflows.errors import (
    ExecutorError,
    HeadlessUnavailableError,
    LayerNotFoundError,
)
from qgis_mcp_workflows.helpers import HEADER_STRUCT, TIMEOUT_DEFAULT


class HeadlessExecutor:
    """Subprocess-backed executor satisfying the ``Executor`` Protocol."""

    _LAUNCHER_ENV_VAR: ClassVar[str] = "QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER"
    _DEFAULT_WINDOWS_LAUNCHERS: ClassVar[tuple[str, ...]] = (
        r"M:\QGIS LTR\bin\python-qgis-ltr.bat",
        r"C:\OSGeo4W\bin\python-qgis-ltr.bat",
        r"C:\OSGeo4W\bin\python-qgis.bat",
        r"C:\OSGeo4W64\bin\python-qgis-ltr.bat",
        r"C:\OSGeo4W64\bin\python-qgis.bat",
    )
    _WINDOWS_LAUNCHER_GLOBS: ClassVar[tuple[str, ...]] = (
        r"C:\Program Files\QGIS *\bin\python-qgis-ltr.bat",
        r"C:\Program Files\QGIS *\bin\python-qgis.bat",
    )
    # macOS: QGIS ships its own Python inside the .app bundle. Order matters —
    # LTR first (the version this project targets), then current, then any.
    # QGIS 4 bundles (vcpkg builds) have no bin/python3; their ``python`` is a
    # wrapper that sets PYTHONHOME before exec'ing the raw python3.12, which
    # cannot find its stdlib on its own. Never resolve (or realpath) past the
    # wrapper: it locates the bundle from its own $0.
    _MACOS_LAUNCHER_GLOBS: ClassVar[tuple[str, ...]] = (
        "/Applications/QGIS-LTR.app/Contents/MacOS/bin/python3",
        "/Applications/QGIS.app/Contents/MacOS/bin/python3",
        "/Applications/QGIS*.app/Contents/MacOS/bin/python3",
        "/Applications/QGIS*.app/Contents/MacOS/python",
    )

    def __init__(self, launcher: str | None = None) -> None:
        self._launcher = launcher or self._resolve_launcher()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()  # serialize dispatch — one outstanding command at a time

    # -----------------------------------------------------------------
    # Launcher resolution
    # -----------------------------------------------------------------

    @classmethod
    def _resolve_launcher(cls) -> str:
        env_override = os.environ.get(cls._LAUNCHER_ENV_VAR)
        if env_override:
            if not os.path.exists(env_override):
                raise HeadlessUnavailableError(
                    f"{cls._LAUNCHER_ENV_VAR}={env_override!r} but file does not exist"
                )
            return env_override

        if platform.system() == "Windows":
            for candidate in cls._DEFAULT_WINDOWS_LAUNCHERS:
                if os.path.exists(candidate):
                    return candidate
            for pattern in cls._WINDOWS_LAUNCHER_GLOBS:
                matches = sorted(glob.glob(pattern))
                if matches:
                    return matches[-1]  # newest version wins (lex sort)
            raise HeadlessUnavailableError(
                "no python-qgis(-ltr).bat found in M:\\QGIS LTR, C:\\OSGeo4W, "
                "or C:\\Program Files\\QGIS *"
            )
        if platform.system() == "Darwin":
            for pattern in cls._MACOS_LAUNCHER_GLOBS:
                # Exact paths hit the first two patterns; the third globs.
                matches = sorted(glob.glob(pattern))
                if matches:
                    return matches[-1]  # newest version wins (lex sort)
            raise HeadlessUnavailableError(
                "no QGIS.app found under /Applications. Install QGIS from "
                "qgis.org, or set QGIS_MCP_WORKFLOWS_QGIS_LAUNCHER to the "
                "python inside its bundle "
                "(<QGIS.app>/Contents/MacOS/bin/python3 for QGIS 3, "
                "<QGIS.app>/Contents/MacOS/python for QGIS 4). "
                "Next: qgis_render_map(..., transport='plugin') if QGIS Desktop is already running."
            )

        # Linux: assume sys.executable already has PyQGIS importable.
        # Users who installed via apt/conda usually do, but if not they'll get
        # a clear ImportError from the runner.
        return sys.executable

    # -----------------------------------------------------------------
    # Subprocess lifecycle
    # -----------------------------------------------------------------

    def _ensure_proc(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc

        # Run the script by absolute path rather than via ``-m``: the QGIS
        # Python doesn't have ``qgis_mcp_workflows`` installed in its site-packages,
        # so the import-system resolution would fail before the runner could
        # bootstrap sys.path. Direct script invocation sidesteps that — the
        # runner then adds the repo root to sys.path so it can import
        # ``qgis_mcp_workflows_plugin``.
        runner_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "headless_runner.py"
        )
        cmd = [self._launcher, runner_path]

        # Pass the repo root explicitly so the runner can put it on sys.path.
        env = os.environ.copy()
        env["QGIS_MCP_WORKFLOWS_REPO_ROOT"] = self._repo_root()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.update(self._bundle_env(self._launcher))

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                # On Windows, .bat files require shell=False with the launcher
                # as argv[0]; subprocess handles that correctly.
            )
        except (FileNotFoundError, OSError) as exc:
            raise HeadlessUnavailableError(f"failed to spawn {self._launcher!r}: {exc}") from exc

        # Wait for the runner's "ready" signal.
        ready = self._read_message(proc)
        if ready is None or ready.get("status") != "ready":
            stderr_tail = self._drain_stderr(proc)
            with contextlib.suppress(Exception):
                proc.kill()
            raise HeadlessUnavailableError(
                f"runner did not signal ready; stderr tail: {stderr_tail[-1000:]!r}"
            )

        self._proc = proc
        return proc

    @staticmethod
    def _bundle_env(launcher: str) -> dict[str, str]:
        """PROJ/GDAL data paths for a launcher living inside a macOS QGIS.app.

        Returns ``{}`` on every other platform and for any launcher that isn't
        inside an app bundle — Windows OSGeo4W and Linux packages set these up
        themselves. Existing values in the environment are respected: a user who
        has deliberately pointed PROJ_LIB at a custom grid directory keeps it.

        Two bundle layouts:

        - QGIS 3: launcher ``Contents/MacOS/bin/python3``, data in
          ``Contents/Resources/{proj,gdal}``, prefix ``Contents/MacOS``
          (QGIS resolves pkgDataPath as ``<prefix>/../Resources``).
        - QGIS 4: launcher ``Contents/MacOS/python``, data in
          ``Contents/Resources/qgis/{proj,gdal}``, prefix = the ``.app``
          itself (pkgDataPath is ``<prefix>/Contents/Resources/qgis``; the
          QGIS 3 prefix would point it at a directory that does not exist).
        """
        if platform.system() != "Darwin":
            return {}
        contents = _bundle_contents_dir(launcher)
        if contents is None:
            return {}
        resources = os.path.join(contents, "Resources")
        qgis4_data = os.path.join(resources, "qgis")
        # A QGIS 3 data tree wins outright: misreading a QGIS 3 bundle as QGIS 4
        # would drop PROJ_LIB and silently invalidate every CRS.
        is_qgis4 = not os.path.exists(os.path.join(resources, "proj", "proj.db")) and any(
            os.path.exists(os.path.join(qgis4_data, *marker))
            for marker in (("resources", "qgis.db"), ("proj", "proj.db"))
        )
        data_root = qgis4_data if is_qgis4 else resources
        if not os.path.isdir(data_root):
            return {}

        out: dict[str, str] = {}
        proj_dir = os.path.join(data_root, "proj")
        # PROJ 9.1+ reads PROJ_DATA before PROJ_LIB, so a user value in either
        # one means hands off both.
        if not (os.environ.get("PROJ_LIB") or os.environ.get("PROJ_DATA")) and os.path.exists(
            os.path.join(proj_dir, "proj.db")
        ):
            out["PROJ_LIB"] = proj_dir
            out["PROJ_DATA"] = proj_dir
        gdal_dir = os.path.join(data_root, "gdal")
        if not os.environ.get("GDAL_DATA") and os.path.exists(
            os.path.join(gdal_dir, "gdalvrt.xsd")
        ):
            out["GDAL_DATA"] = gdal_dir

        if not os.environ.get("QGIS_PREFIX_PATH"):
            if is_qgis4:
                out["QGIS_PREFIX_PATH"] = os.path.dirname(contents)
            elif os.path.isdir(os.path.join(contents, "MacOS")):
                out["QGIS_PREFIX_PATH"] = os.path.join(contents, "MacOS")
        return out

    def _repo_root(self) -> str:
        # src/qgis_mcp_workflows/executors/headless.py → repo root is 4 levels up
        here = os.path.abspath(__file__)
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen, max_bytes: int = 4000) -> str:
        try:
            data = proc.stderr.read(max_bytes) if proc.stderr else b""
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    # -----------------------------------------------------------------
    # Wire protocol
    # -----------------------------------------------------------------

    @staticmethod
    def _write_message(proc: subprocess.Popen, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        assert proc.stdin is not None
        proc.stdin.write(HEADER_STRUCT.pack(len(body)))
        proc.stdin.write(body)
        proc.stdin.flush()

    @staticmethod
    def _read_message(proc: subprocess.Popen) -> dict | None:
        assert proc.stdout is not None
        header = proc.stdout.read(4)
        if len(header) < 4:
            return None
        (length,) = HEADER_STRUCT.unpack(header)
        body = b""
        while len(body) < length:
            chunk = proc.stdout.read(length - len(body))
            if not chunk:
                return None
            body += chunk
        return json.loads(body.decode("utf-8"))

    # -----------------------------------------------------------------
    # Executor Protocol
    # -----------------------------------------------------------------

    def dispatch(
        self, command: str, params: dict | None = None, timeout: int | None = None
    ) -> dict:
        with self._lock:
            proc = self._ensure_proc()
            self._write_message(proc, {"type": command, "params": params or {}})
            response = self._read_message(proc)
            if response is None:
                stderr_tail = self._drain_stderr(proc)
                raise HeadlessUnavailableError(
                    f"runner closed stdout while handling {command!r}; "
                    f"stderr tail: {stderr_tail[-1000:]!r}"
                )

        status = response.get("status")
        if status == "success":
            return response.get("result", {})

        message = response.get("message", "(no message)")
        raise _map_error(command, message)

    def shutdown(self) -> None:
        """Cleanly terminate the subprocess. Idempotent."""
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            return
        try:
            with self._lock:
                if self._proc.poll() is None:
                    with contextlib.suppress(Exception):
                        self._write_message(self._proc, {"type": "shutdown"})
                        # drain the final response so the child sees clean EOF
                        self._read_message(self._proc)
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
        finally:
            self._proc = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.shutdown()


def _bundle_contents_dir(launcher: str) -> str | None:
    """``<X>.app/Contents`` for a launcher inside a macOS app bundle, else None.

    Walks up from the launcher instead of counting levels: QGIS 3 launchers sit
    one directory deeper (``MacOS/bin/python3``) than QGIS 4's (``MacOS/python``).
    ``abspath``, not ``realpath``: the QGIS 4 wrapper is a symlink into
    ``Resources/scripts`` and the bundle is defined by where it is invoked from.
    """
    path = os.path.dirname(os.path.abspath(launcher))
    for _ in range(3):
        parent = os.path.dirname(path)
        if os.path.basename(path) == "Contents" and parent.endswith(".app"):
            return path
        path = parent
    return None


def _map_error(command: str, message: str) -> Exception:
    """Heuristic: runner error strings → typed exceptions."""
    lowered = message.lower()
    if "not found" in lowered and ("layer" in lowered or "path" in lowered or "file" in lowered):
        return LayerNotFoundError(message)
    return ExecutorError(command, message)


# Use TIMEOUT_DEFAULT to avoid an unused-import warning while keeping the
# constant available for future per-command timeout enforcement.
_ = TIMEOUT_DEFAULT

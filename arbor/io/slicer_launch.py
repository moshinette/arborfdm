"""« Open in OrcaSlicer »: export model + supports to a dedicated folder, then open the file
in OrcaSlicer (no Qt here; the GUI and the CLI call these functions).

Why it works this way (OrcaSlicer tag v2.4.2, see docs/SLICERS.md § « Ouvrir dans
OrcaSlicer »):

* ``orca-slicer <file>`` without any action option starts the GUI and loads the file
  (``src/OrcaSlicer.cpp``: ``start_gui = m_actions.empty()``; ``GUI_App::post_init`` ->
  ``Plater::load_files``). By default (preference « Allow only one OrcaSlicer instance »
  off) every call opens a new window; with that preference on, the file is sent to the
  running instance and added to its plate (it asks first when the plate is not empty).
* The file is a 3MF in the ``separate`` layout of :func:`arbor.io.export.export_3mf`:
  two objects that both rest on the plate, so Orca keeps them as they are (no
  « multi-part » question, no drop to the bed); the whole scene is only re-centred on
  the bed (same XY shift for every object, ``Model::center_instances_around_point``).
* Every object carries ``enable_support = 0`` in ``Metadata/model_settings.config``:
  Orca reads that file for any 3MF and puts the keys in the object configuration
  (``bbs_3mf.cpp``), which overrides the process preset, so Orca's own supports stay
  off for these objects even if the process preset enables them.

The exported file goes to a dedicated folder (:func:`to_slicer_dir`), never next to or in
place of the source model; old files there are removed beyond ``KEEP_FILES``, but only
files that ArborFDM wrote and that nothing rewrote since (a project the user saved over
it from Orca is kept).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Mapping, Optional, Sequence

KEEP_FILES = 20
ENV_OUT_DIR = "ARBOR_TO_SLICER_DIR"
WINDOWS_EXE = "orca-slicer.exe"
PATH_NAMES = ("orca-slicer", "OrcaSlicer", "orcaslicer")
FLATPAK_IDS = ("com.orcaslicer.OrcaSlicer", "io.github.softfever.OrcaSlicer")
MAC_APP = "OrcaSlicer.app"
# Registry keys written by the Windows installer (CPack NSIS, OrcaSlicer CMakeLists.txt:
# CPACK_PACKAGE_VENDOR "SoftFever", CPACK_PACKAGE_INSTALL_REGISTRY_KEY "OrcaSlicer").
WIN_REG_INSTALL_KEY = r"Software\SoftFever\OrcaSlicer"
WIN_REG_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OrcaSlicer"
WIN_REG_APP_PATHS_KEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths\orca-slicer.exe"
_ARBOR_MARK = b'<metadata name="Application">ArborFDM</metadata>'


class SlicerNotFound(Exception):
    """OrcaSlicer executable missing / invalid; str(exc) is meant for the user."""


class SlicerLaunchError(Exception):
    """OrcaSlicer could not be started; str(exc) is meant for the user."""


class SlicerExportError(Exception):
    """The file for the slicer could not be written or failed validation."""


# ---------------------------------------------------------------------------
# platform helpers
# ---------------------------------------------------------------------------
def _plat(platform: Optional[str] = None) -> str:
    p = (platform or sys.platform).lower()
    if p.startswith("win"):
        return "win"
    if p == "darwin" or p.startswith("mac"):
        return "mac"
    return "linux"


def _home(env: Mapping[str, str]) -> Path:
    h = env.get("HOME") or env.get("USERPROFILE")
    return Path(h) if h else Path.home()


def _app_bundle(path: Path) -> Optional[Path]:
    """The enclosing ``*.app`` bundle of ``path`` (or ``path`` itself), if any."""
    for p in (path, *path.parents):
        if p.suffix.lower() == ".app":
            return p
    return None


def check_orca_path(path, platform: Optional[str] = None) -> Optional[str]:
    """None if ``path`` can be launched as OrcaSlicer, else the reason (for the user)."""
    if path is None or not str(path).strip():
        return "no path given"
    p = Path(str(path).strip().strip('"')).expanduser()
    plat = _plat(platform)
    if not p.exists():
        return f"{p} does not exist"
    if plat == "mac" and p.is_dir():
        if p.suffix.lower() == ".app":
            return None
        return f"{p} is a folder, not OrcaSlicer.app"
    if p.is_dir():
        return f"{p} is a folder; choose the OrcaSlicer program itself" + (
            f" ({WINDOWS_EXE})" if plat == "win" else "")
    if plat == "win":
        if p.suffix.lower() != ".exe":
            return f"{p} is not a .exe program"
        return None
    if not os.access(p, os.X_OK):
        return (f"{p} is not executable" + (" (AppImage: run 'chmod +x' on it)"
                                            if p.suffix.lower() == ".appimage" else ""))
    return None


def is_valid_orca(path, platform: Optional[str] = None) -> bool:
    return check_orca_path(path, platform) is None


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------
def _windows_registry_dirs() -> list[Path]:
    """Install folders / executables recorded in the Windows registry (HKCU and HKLM,
    64- and 32-bit views). Empty list elsewhere or on any error."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import winreg
    except ImportError:
        return []
    found: list[Path] = []
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))

    def read(root, key, name, view):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    for root in roots:
        for view in views:
            v = read(root, WIN_REG_APP_PATHS_KEY, "", view)
            if isinstance(v, str) and v.strip():
                found.append(Path(os.path.expandvars(v.strip().strip('"'))))
            v = read(root, WIN_REG_INSTALL_KEY, "", view)
            if isinstance(v, str) and v.strip():
                found.append(Path(v.strip().strip('"')) / WINDOWS_EXE)
            for name in ("InstallLocation", "UninstallString", "DisplayIcon"):
                v = read(root, WIN_REG_UNINSTALL_KEY, name, view)
                if not isinstance(v, str) or not v.strip():
                    continue
                s = v.strip().split(",")[0].strip().strip('"')
                p = Path(os.path.expandvars(s))
                found.append(p / WINDOWS_EXE if name == "InstallLocation" else p.parent / WINDOWS_EXE)
    return found


def _which(env: Mapping[str, str], plat: str) -> list[Path]:
    out = []
    for n in PATH_NAMES:
        w = shutil.which(n, path=env.get("PATH", ""))
        if w:
            out.append(Path(w))
    return out


def _appimages(dirs: Sequence[Path]) -> list[Path]:
    hits: list[Path] = []
    for d in dirs:
        try:
            for p in d.iterdir():
                n = p.name.lower()
                if n.endswith(".appimage") and "orca" in n and p.is_file():
                    hits.append(p)
        except OSError:
            continue
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    return sorted(hits, key=mtime, reverse=True)


def candidate_paths(platform: Optional[str] = None,
                    env: Optional[Mapping[str, str]] = None) -> list[Path]:
    """Every location the automatic detection looks at, in order (existing or not).
    Executables found on PATH come first."""
    env = os.environ if env is None else env
    plat = _plat(platform)
    home = _home(env)
    c: list[Path] = list(_which(env, plat))
    if plat == "win":
        if platform is None or sys.platform.startswith("win"):
            c += _windows_registry_dirs()
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            if env.get(var):
                c.append(Path(env[var]) / "OrcaSlicer" / WINDOWS_EXE)
        if env.get("LOCALAPPDATA"):
            c.append(Path(env["LOCALAPPDATA"]) / "Programs" / "OrcaSlicer" / WINDOWS_EXE)
        # per-user install of the NSIS installer ("Just me"): $DOCUMENTS\OrcaSlicer
        c.append(home / "Documents" / "OrcaSlicer" / WINDOWS_EXE)
        if env.get("OneDrive"):
            c.append(Path(env["OneDrive"]) / "Documents" / "OrcaSlicer" / WINDOWS_EXE)
        c.append(home / "scoop" / "apps" / "orcaslicer" / "current" / WINDOWS_EXE)
    elif plat == "mac":
        c += [Path("/Applications") / MAC_APP, home / "Applications" / MAC_APP]
    else:
        data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
        for app_id in FLATPAK_IDS:
            c.append(data / "flatpak" / "exports" / "bin" / app_id)
            c.append(Path("/var/lib/flatpak/exports/bin") / app_id)
        c += [Path("/usr/bin/orca-slicer"), Path("/usr/local/bin/orca-slicer"),
              Path("/opt/OrcaSlicer/bin/orca-slicer"), Path("/opt/orcaslicer/bin/orca-slicer")]
        img_dirs = [home / "Applications", home / "AppImages", home / "Downloads",
                    home / "Desktop", home / ".local" / "bin", home / "bin", Path("/opt")]
        imgs = _appimages(img_dirs)
        c += imgs if imgs else [d / "*Orca*.AppImage" for d in img_dirs]
    seen, out = set(), []
    for p in c:
        key = str(p).lower() if plat == "win" else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def find_orcaslicer(configured: str | os.PathLike | None = None, *,
                    platform: Optional[str] = None,
                    env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The configured path if it is valid, else the first valid automatic candidate,
    else None. Never raises."""
    if configured is not None and str(configured).strip():
        if check_orca_path(configured, platform) is None:
            return Path(str(configured).strip().strip('"')).expanduser()
    for p in candidate_paths(platform, env):
        if "*" in p.name:
            continue
        try:
            if check_orca_path(p, platform) is None:
                return p
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def to_slicer_dir(platform: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Path:
    """Dedicated folder for the files sent to the slicer:
    Windows %LOCALAPPDATA%\\ArborFDM\\to_slicer, macOS ~/Library/Application Support/
    ArborFDM/to_slicer, Linux $XDG_DATA_HOME/arborfdm/to_slicer (~/.local/share);
    ARBOR_TO_SLICER_DIR overrides it."""
    env = os.environ if env is None else env
    if env.get(ENV_OUT_DIR, "").strip():
        return Path(env[ENV_OUT_DIR].strip()).expanduser()
    plat = _plat(platform)
    home = _home(env)
    if plat == "win":
        base = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "ArborFDM" / "to_slicer"
    if plat == "mac":
        return home / "Library" / "Application Support" / "ArborFDM" / "to_slicer"
    base = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "arborfdm" / "to_slicer"


def safe_stem(name: str) -> str:
    s = re.sub(r"[^\w.-]+", "_", str(name or ""), flags=re.UNICODE).strip("._-")
    return (s or "model")[:50]


def _is_ours(path: Path) -> bool:
    """True if ``path`` is a 3MF written by ArborFDM (not re-saved by a slicer)."""
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open("3D/3dmodel.model") as fh:
                return _ARBOR_MARK in fh.read(4096)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return False


def cleanup_to_slicer_dir(directory: Path, keep: int = KEEP_FILES,
                          protect: Sequence[Path] = ()) -> list[Path]:
    """Delete the oldest ArborFDM 3MF files of ``directory`` so that at most ``keep``
    remain (files in ``protect`` count but are never deleted). Files that are not ours
    (or were re-saved by the slicer) are never touched. Errors (file in use...) are
    ignored. Returns the deleted files."""
    directory = Path(directory)
    prot = {Path(p).resolve() for p in protect}
    try:
        files = [p for p in directory.iterdir() if p.suffix.lower() == ".3mf" and p.is_file()]
    except OSError:
        return []
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    ours = [p for p in files if p.resolve() in prot or _is_ours(p)]
    ours.sort(key=lambda p: (p.resolve() in prot, mtime(p)), reverse=True)
    deleted = []
    for p in ours[max(int(keep), 1):]:
        if p.resolve() in prot:
            continue
        try:
            p.unlink()
            deleted.append(p)
        except OSError:
            pass
    now = time.time()
    for p in directory.glob("*.3mf.part"):          # interrupted exports
        try:
            if now - p.stat().st_mtime > 3600:
                p.unlink()
        except OSError:
            pass
    return deleted


def _reserve(directory: Path, stem: str) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for i in range(1000):
        p = directory / (f"{stem}_{stamp}.3mf" if i == 0 else f"{stem}_{stamp}_{i + 1}.3mf")
        try:
            with open(p, "x"):
                return p
        except FileExistsError:
            continue
    raise SlicerExportError(f"cannot find a free file name in {directory}")


def export_for_slicer(model, supports, raft, params, name: str,
                      out_dir: Optional[Path] = None, *,
                      layer_heights: Optional[dict] = None,
                      disable_slicer_supports: bool = True,
                      keep: int = KEEP_FILES,
                      notes: Optional[list] = None) -> Path:
    """Write model + supports (+ raft) as a 3MF for OrcaSlicer and return its path.

    Layout ``separate`` (two objects resting on the plate, or, when the model rests on the
    raft, one object with parts model / supports / raft; positions and scale kept),
    ``enable_support = 0`` on every object unless ``disable_slicer_supports`` is False,
    optional per-object ``layer_heights`` ({"model": mm, "supports": mm}; not possible in the
    one-object case, a note is added). The file name
    is ``<name>_<YYYYmmdd-HHMMSS>.3mf`` (unique, never overwrites anything) in ``out_dir``
    (default :func:`to_slicer_dir`). Old files are then pruned (:func:`cleanup_to_slicer_dir`).
    The meshes are not modified. Raises SlicerExportError (invalid file, write error) or
    ValueError (empty model, bad options)."""
    from arbor.io.export import export_3mf, validate_export
    directory = Path(out_dir) if out_dir is not None else to_slicer_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SlicerExportError(f"cannot create the folder {directory}: {exc}") from None
    path = _reserve(directory, safe_stem(name))
    settings = {"enable_support": False} if disable_slicer_supports else None
    try:
        er = export_3mf(path, model, supports, raft, params, model_name=str(name or "model"),
                        layout="separate", layer_heights=layer_heights,
                        object_settings=settings)
    except BaseException as exc:
        try:
            path.unlink()
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise SlicerExportError(f"cannot write {path}: {exc}") from None
        raise
    problems = validate_export(path)
    if problems:
        raise SlicerExportError(f"the exported file {path} is invalid: " + "; ".join(problems))
    if notes is not None:
        notes.extend(er.notes)
    cleanup_to_slicer_dir(directory, keep, protect=[path])
    return path


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------
def launch_command(file: Path, exe: Path, platform: Optional[str] = None) -> list[str]:
    """Command line used to open ``file`` in OrcaSlicer ``exe``."""
    file, exe = Path(file), Path(exe)
    if _plat(platform) == "mac":
        bundle = _app_bundle(exe)
        if bundle is not None:
            return ["open", "-a", str(bundle), str(file)]
    return [str(exe), str(file)]


def _child_env() -> dict:
    """Environment for the slicer: undo what a frozen (PyInstaller) ArborFDM adds to the
    library search paths, which would otherwise be inherited by OrcaSlicer."""
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        return env
    meipass = getattr(sys, "_MEIPASS", None)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif var in env and meipass and meipass in env[var]:
            env.pop(var)
    if meipass and env.get("PATH"):
        env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                      if not p.startswith(meipass))
    for var in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        if meipass and meipass in env.get(var, ""):
            env.pop(var)
    return env


def launch_orcaslicer(file: Path, exe: Path, *, check_seconds: float = 0.3) -> None:
    """Open ``file`` in OrcaSlicer without waiting for it (detached process).

    Raises SlicerNotFound if ``exe`` is not a usable OrcaSlicer program, SlicerLaunchError
    if the file is missing, the program cannot be started, or it exits at once with an
    error (checked during ``check_seconds``; ``open -a`` on macOS is waited for, max 10 s)."""
    file = Path(file)
    if not file.is_file():
        raise SlicerLaunchError(f"the file to open does not exist: {file}")
    why = check_orca_path(exe)
    if why is not None:
        raise SlicerNotFound(f"OrcaSlicer not usable: {why}")
    exe = Path(str(exe).strip().strip('"')).expanduser()
    cmd = launch_command(file.resolve(), exe)
    kw: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True, env=_child_env())
    if sys.platform.startswith("win"):
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
        kw["cwd"] = str(exe.parent)
    else:
        kw["start_new_session"] = True
        if cmd[0] != "open":
            kw["cwd"] = str(exe.parent)
    try:
        proc = subprocess.Popen(cmd, **kw)
    except OSError as exc:
        raise SlicerLaunchError(f"cannot start {cmd[0]}: {exc}") from None
    try:
        code = proc.wait(timeout=10.0 if cmd[0] == "open" else max(check_seconds, 0.0))
    except subprocess.TimeoutExpired:
        return                                  # still running: the slicer is starting
    if code != 0:
        raise SlicerLaunchError(f"{' '.join(cmd[:-1])} exited immediately with code {code}")

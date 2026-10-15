# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ArborFDM (onedir, two executables in one folder).

    pyinstaller --noconfirm --clean packaging/arborfdm.spec

Output: dist/ArborFDM/
    ArborFDM(.exe)        GUI, windowed (no console)
    arborfdm-cli(.exe)    command line, console
    LICENSE.txt           ArborFDM license
    THIRD_PARTY_NOTICES.txt  licenses of everything bundled (packaging/collect_notices.py)
    README.md
    _internal/            Python runtime, libraries, and profiles/ (= sys._MEIPASS)

Profiles: arbor/engine/params.py computes
    PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"
In a PyInstaller >= 6 onedir build, __file__ of arbor.engine.params is
<_MEIPASS>/arbor/engine/params.pyc, so PROFILE_DIR resolves to <_MEIPASS>/profiles.
The profiles are therefore collected to "profiles" (relative to _MEIPASS). This was
checked on a Linux build (see docs/PACKAGING.md), not on Windows.

Environment variable ARBOR_NO_GUI=1 builds only the CLI (no PySide6 needed).
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
PKG = os.path.join(ROOT, "packaging")
BUILD_GUI = os.environ.get("ARBOR_NO_GUI", "") not in ("1", "true", "yes")

datas = [(os.path.join(ROOT, "profiles"), "profiles")]
datas += collect_data_files("trimesh")            # trimesh/resources (templates, units...)
try:
    datas += copy_metadata("arborfdm")             # importlib.metadata.version() when frozen
except Exception:
    pass                                            # not pip-installed: see arbor/cli.py _version()
binaries = collect_dynamic_libs("manifold3d")

# Engine modules imported lazily (inside functions) are found by static analysis because
# they are plain imports; listed here as a safety net.
hidden = [
    "arbor.cli", "arbor.io.export",
    "arbor.core.mesh_io", "arbor.core.repair", "arbor.core.orientation", "arbor.core.transform",
    "arbor.core.project",
    "arbor.engine.pipeline", "arbor.engine.analysis", "arbor.engine.contacts",
    "arbor.engine.tree", "arbor.engine.build", "arbor.engine.verify", "arbor.engine.params",
    "scipy.spatial", "scipy.sparse.csgraph", "shapely", "networkx",
]
# optional dependencies of trimesh / scipy that ArborFDM never uses (saved ~100 MB on the
# Linux test build); trimesh guards these imports itself.
excludes = ["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2",
            "sklearn", "pandas", "skimage", "sympy", "cryptography", "lxml", "gi",
            "jupyter_rfb", "pyqtgraph.jupyter", "pyqtgraph.examples"]
cli_excludes = excludes + ["PySide6", "pyqtgraph", "OpenGL", "shiboken6"]

# ---------------------------------------------------------------------------
a_cli = Analysis(
    [os.path.join(PKG, "entry_cli.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=cli_excludes,
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli, a_cli.scripts, [],
    exclude_binaries=True,
    name="arborfdm-cli",
    console=True,
    upx=False,
)

collect_args = [exe_cli, a_cli.binaries, a_cli.datas]

if BUILD_GUI:
    a_gui = Analysis(
        [os.path.join(PKG, "entry_gui.py")],
        pathex=[ROOT],
        binaries=binaries,
        datas=datas,
        hiddenimports=hidden + ["arbor.app.main", "arbor.app.main_window", "arbor.app.viewport",
                                "arbor.app.panels", "arbor.app.worker", "arbor.app.theme",
                                "pyqtgraph.opengl", "OpenGL.GL"],
        excludes=excludes,
        noarchive=False,
    )
    pyz_gui = PYZ(a_gui.pure)
    exe_gui = EXE(
        pyz_gui, a_gui.scripts, [],
        exclude_binaries=True,
        name="ArborFDM",
        console=False,                 # windowed on Windows / macOS
        upx=False,
    )
    collect_args = [exe_gui, a_gui.binaries, a_gui.datas] + collect_args

coll = COLLECT(*collect_args, strip=False, upx=False, name="ArborFDM")

# ---------------------------------------------------------------------------
# License files next to the executables (required to redistribute the LGPL Qt/PySide6
# and GEOS libraries, the GCC runtime notices, the PSF license...).
import shutil
from pathlib import Path

sys.path.insert(0, PKG)
import collect_notices  # noqa: E402

OUT = os.path.join(DISTPATH, "ArborFDM")
NOTICES = collect_notices.build(Path(OUT) / "THIRD_PARTY_NOTICES.txt", gui=BUILD_GUI)
shutil.copyfile(os.path.join(ROOT, "LICENSE"), os.path.join(OUT, "LICENSE.txt"))
shutil.copyfile(os.path.join(ROOT, "README.md"), os.path.join(OUT, "README.md"))
print(f"notices written to {NOTICES}")

"""Build THIRD_PARTY_NOTICES.txt for a binary release of ArborFDM.

The notices are read from the packages actually installed in the build environment
(license files shipped in each wheel's .dist-info), so a Windows build lists the
Windows wheels' bundled libraries (OpenBLAS, GEOS, MSVC runtime...) and a Linux build
lists the Linux ones. Texts that a wheel does not ship (PySide6 / shiboken6 wheels
carry no LGPL text) come from packaging/licenses/.

    python packaging/collect_notices.py [--out build/THIRD_PARTY_NOTICES.txt] [--no-gui]

Also imported by packaging/arborfdm.spec, which copies the result next to the
executables in dist/ArborFDM/.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib.metadata as md
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSE_DIR = Path(__file__).resolve().parent / "licenses"

RUNTIME = ["numpy", "scipy", "trimesh", "manifold3d", "shapely", "networkx"]
GUI = ["PySide6", "pyqtgraph", "PyOpenGL"]
# not imported at run time, but its bootloader is inside every .exe
BUILD = ["pyinstaller"]
# Qt WebEngine is never imported (and Chromium has its own long notice list): skip it.
# PySide6-Addons / PySide6-Pdf stay: PyInstaller's Qt plugins pull libQt6Pdf, Qml, Quick...
SKIP = {"pyside6-webengine"}
QT_DISTS = {"pyside6", "pyside6-essentials", "pyside6-addons", "pyside6-pdf", "shiboken6"}

LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors|copyright)[^/]*$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _closure(roots: list[str]) -> list[md.Distribution]:
    try:
        from packaging.markers import default_environment
        from packaging.requirements import Requirement
    except ImportError:                       # packaging is a PyInstaller dependency
        Requirement = None                    # type: ignore[assignment]
    env = default_environment() if Requirement else {}
    seen: dict[str, md.Distribution] = {}
    todo = list(roots)
    while todo:
        name = todo.pop()
        key = _norm(name)
        if key in seen or key in SKIP:
            continue
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        seen[key] = dist
        for req in dist.requires or []:
            if Requirement is None:
                if "extra" not in req:
                    todo.append(re.split(r"[\s<>=!~\[;(]", req)[0])
                continue
            r = Requirement(req)
            if r.marker is not None and not r.marker.evaluate({**env, "extra": ""}):
                continue
            todo.append(r.name)
    return sorted(seen.values(), key=lambda d: _norm(d.metadata["Name"]))


def _license_label(dist: md.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) < 120 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                   if c.startswith("License ::")]
    return "; ".join(classifiers) or "see license text below"


def _license_files(dist: md.Distribution) -> list[tuple[str, str]]:
    out = []
    for f in dist.files or []:
        s = str(f).replace("\\", "/")
        if ".dist-info/" not in s or not LICENSE_FILE.search(s):
            continue
        try:
            text = Path(f.locate()).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        out.append((s.split(".dist-info/", 1)[1], text))
    return out


def _python_license() -> str:
    candidates = [Path(sys.base_prefix) / "LICENSE.txt",
                  Path(sys.base_prefix) / "LICENSE",
                  Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "LICENSE.txt"]
    import sysconfig
    candidates.append(Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt")
    for c in candidates:
        if c.is_file():
            return c.read_text(encoding="utf-8", errors="replace")
    return ("Python Software Foundation License Version 2, see "
            "https://docs.python.org/3/license.html (license file not found in this build environment)")


def _arbor_version() -> str:
    try:
        return md.version("arborfdm")
    except md.PackageNotFoundError:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "arbor" / "__init__.py").read_text())
        return m.group(1) if m else "unknown"


def build(out: Path, gui: bool = True) -> Path:
    dists = _closure(RUNTIME + (GUI if gui else []))
    for name in BUILD:                        # the package itself, not its build-time deps
        try:
            dists.append(md.distribution(name))
        except md.PackageNotFoundError:
            pass
    sep = "=" * 78
    parts: list[str] = []
    parts.append(f"ArborFDM {_arbor_version()}: third-party notices\n"
                 f"Generated {_dt.date.today().isoformat()} on {platform.system()} "
                 f"{platform.machine()}, Python {platform.python_version()}\n\n"
                 "This binary distribution bundles the software listed below. Each component\n"
                 "is distributed under its own license, reproduced in full in this file.\n"
                 "ArborFDM's own license is in LICENSE.txt.\n")
    summary = (ROOT / "THIRD_PARTY_NOTICES.md")
    if summary.is_file():
        parts.append(sep + "\nSUMMARY AND LGPL INFORMATION\n" + sep + "\n" + summary.read_text(encoding="utf-8"))

    parts.append(sep + "\nINDEX\n" + sep)
    parts.append(f"Python {platform.python_version()} (CPython runtime): PSF-2.0 and bundled licenses")
    for d in dists:
        parts.append(f"{d.metadata['Name']} {d.version}: {_license_label(d)}")

    parts.append("\n" + sep + f"\nPython {platform.python_version()}\n" + sep)
    parts.append(_python_license())

    qt_seen = False
    for d in dists:
        name = d.metadata["Name"]
        home = d.metadata.get("Home-page") or ""
        for u in d.metadata.get_all("Project-URL") or []:
            if not home:
                home = u.split(",", 1)[-1].strip()
        parts.append("\n" + sep + f"\n{name} {d.version}\nLicense: {_license_label(d)}\n"
                     + (f"Project: {home}\n" if home else "") + sep)
        files = _license_files(d)
        for rel, text in files:
            parts.append(f"--- {rel} ---\n{text}")
        if _norm(name) in QT_DISTS:
            qt_seen = True
            if not files or all("Commercial" in rel for rel, _ in files):
                parts.append("Used under the GNU LGPL version 3 (full text at the end of this file).")
        elif not files:
            parts.append("(no license file in the wheel; see the project page for the full text)")

    if qt_seen:
        for fname, title in (("LGPL-3.0.txt", "GNU LESSER GENERAL PUBLIC LICENSE v3 (Qt, PySide6, shiboken6)"),
                             ("GPL-3.0.txt", "GNU GENERAL PUBLIC LICENSE v3 (referenced by the LGPL v3)")):
            parts.append("\n" + sep + f"\n{title}\n" + sep)
            parts.append((LICENSE_DIR / fname).read_text(encoding="utf-8"))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "build" / "THIRD_PARTY_NOTICES.txt"))
    ap.add_argument("--no-gui", action="store_true", help="CLI-only build (no PySide6)")
    a = ap.parse_args(argv)
    path = build(Path(a.out), gui=not a.no_gui)
    print(f"wrote {path} ({path.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

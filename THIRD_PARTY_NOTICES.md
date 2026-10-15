# Third-party notices

ArborFDM's own code is under the license in `LICENSE`. The Windows release (`ArborFDM-<version>-windows-x64.zip`) also contains the third-party software below. The release zip includes `THIRD_PARTY_NOTICES.txt`, generated at build time by `packaging/collect_notices.py` from the exact packages bundled, with every license text in full. This file is the human-readable summary.

| Component | License | Bundled in the release |
|---|---|---|
| CPython runtime | PSF-2.0 (+ bundled zlib, OpenSSL, libffi, bzip2, xz, sqlite notices) | yes |
| numpy | BSD-3-Clause; bundles OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI), GCC runtime (GPL-3.0-or-later WITH GCC-exception-3.1) | yes |
| scipy | BSD-3-Clause; bundles OpenBLAS, LAPACK, GCC runtime (as numpy), Qhull and others listed in its license file | yes |
| trimesh | MIT | yes |
| manifold3d | Apache-2.0 | yes |
| shapely | BSD-3-Clause; bundles **GEOS (LGPL-2.1)** as separate DLLs | yes |
| networkx | BSD-3-Clause | yes |
| PySide6, shiboken6, Qt 6 | **LGPL-3.0-only** (or GPL-2.0/GPL-3.0) | yes (GUI only) |
| pyqtgraph | MIT | yes (GUI only) |
| PyOpenGL | BSD-style (PyOpenGL license) | yes (GUI only) |
| PyInstaller bootloader | GPL-2.0-or-later with the bootloader exception | yes (inside each .exe) |
| Microsoft Visual C++ runtime DLLs | Microsoft redistributable terms | yes |

Not bundled (development and tests only): pytest (MIT), lxml (BSD-3-Clause), rtree (MIT, libspatialindex MIT), PyInstaller itself (GPL-2.0-or-later, used only to build).

## LGPL components (Qt / PySide6, GEOS)

- These libraries are **dynamically linked** and shipped as separate files in `ArborFDM\_internal\` (for example `Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`, `pyside6.abi3.dll`, `shiboken6.abi3.dll`, `geos.dll`, `geos_c.dll`). ArborFDM is not statically linked to them.
- You may replace them with your own compatible build (same major version and ABI) to use a modified version of the library. ArborFDM's license does not forbid modifying it for your own use, or reverse engineering for debugging such modifications.
- Source code of the exact versions used (see the versions in `THIRD_PARTY_NOTICES.txt`):
  - Qt 6: https://download.qt.io/official_releases/qt/ (archive: https://download.qt.io/archive/qt/)
  - PySide6 / shiboken6: https://download.qt.io/official_releases/QtForPython/ and https://code.qt.io/cgit/pyside/pyside-setup.git (tag `v<version>`)
  - GEOS: https://libgeos.org/usage/download/
- On request, the ArborFDM maintainers will also provide a copy of these sources for at least three years after the release (open an issue on the project page).
- Qt itself contains third-party code under other permissive licenses: https://doc.qt.io/qt-6/licenses-used-in-qt.html

## Contact

Project page and issues: see `README.md`.

# Changelog

All notable changes to ArborFDM. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/) (0.x: anything may still change).

## [0.2.0] - unreleased

First public release. Profiles are still **not validated by systematic print tests**: see `docs/LIMITATIONS.md`.

### Added
- User presets: save, rename and delete your own parameter sets next to the built-in profiles (stored in `%APPDATA%\ArborFDM\presets` on Windows, `~/.config/arborfdm/presets` elsewhere).
- Overhang colouring by angle in the 3D view.
- Support self-check: floating regions and unsupported areas reported after generation, with suggested fixes.
- 3MF "separate objects" layout (model and supports as two objects, each with its own layer height) for OrcaSlicer and Bambu Studio, in the CLI (`--layout separate`, `--model-layer-height`, `--support-layer-height`) and the GUI.
- "Open in OrcaSlicer" from the GUI and the CLI (`--open-in-orca`, `arborfdm-cli orca`), with an application preferences dialog for the OrcaSlicer path. *(GUI launch covered by automated tests with a fake program; still to be tried with a real OrcaSlicer on Windows before release)*
- Reworked desktop UI (layout, icons, feedback during long computations, contact info card, Del / Esc and grouped contact deletion, issues grouped by code).
- Publication files: `LICENSE` (MIT), `THIRD_PARTY_NOTICES.md` and a generated `THIRD_PARTY_NOTICES.txt` in the Windows zip, `docs/LICENSES.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and pull request templates.
- GitHub Actions: tests on Linux and Windows (`tests.yml`), Windows build and draft release with SHA-256 checksums on `v*` tags (`release.yml`).

### Changed
- Tips are never closer than their base width plus one line width (0.92 mm with the 0.2 mm nozzle), and narrow parts (a sword blade) get one central row of contacts instead of one per edge: the crowns of tiny tips under the hands printed as strings (2nd print, OrcaSlicer G-code measured before/after, `docs/PHYSICAL_TESTS.md`).
- With a raft, the default 3MF export (GUI and CLI) now uses the separate-objects layout: slicers that drop every object to the bed lowered the supports by the raft thickness, leaving air under every tip.
- Islands that start in a point (round bottom of a hand, a ball) are anchored by a ring of 6 thicker contacts around their lowest point (`island_anchor_height`, `island_anchor_spacing`, `island_tip_diameter`), after a print where both hands of the figurine failed.
- After the first real prints: thinner single-piece raft, feet scaled with tree height, sturdier branches for the 0.2 mm nozzle, thicker diagonal braces (0.2: 0.5 to 0.9 mm, 0.4: 0.8 to 1.2 mm), zero-volume fragments dropped.
- Supported Python is now declared as 3.10.1 to 3.14 (`manifold3d` has no wheel for 3.15+, Python 3.10.0 crashes PyInstaller). Minimum dependency versions raised to versions that pass the test suite (see `pyproject.toml`).
- Version is defined once in `arbor/__init__.py`.

### Fixed
- The height cut is reset on every model import, project open and new project, and an active cut is highlighted.

## [0.1.0] - 2026-10-15

First working prototype (not published).

- Engine: layer-by-layer analysis (islands, overhangs, hanging edges, flat-face corners, low points), contact placement with a reason per contact, tree routing with merges, trunks that thicken, flared feet, braces, raft, supports resting on the model (option), short supports for low clearance.
- Verification stage after generation: unsupported contacts, supports in the void, floating supports, disconnected branches, unstable trunks, collisions with the model, invalid mesh.
- Mesh import (STL, OBJ, 3MF), diagnosis and repair on a copy, scaling in mm or %, rotation, drop to plate, suggested orientations.
- Desktop GUI (PySide6 + pyqtgraph OpenGL): manual contact editing (left click adds, right click removes), undo/redo, branch-only rebuild, height cut, locking while computing, broken-mesh warnings.
- Profiles for 0.2 and 0.4 mm nozzles, Light / Normal / Dense.
- Exports: 3MF (model + supports + raft) and STL, re-read and validated after writing (lib3mf strict mode: 0 warnings).
- CLI: `generate`, `check`, `profiles`, JSON reports, exit codes.
- Windows packaging with PyInstaller (onedir, GUI + CLI executables), `scripts/build_windows.bat` validated on Windows with Python 3.12.10.
- First slicer check: OrcaSlicer 2.4.2 (preview, Bambu A1 mini, 0.2 mm nozzle).
- Independent QA review (18 defects found, all fixed), 469 automated tests.


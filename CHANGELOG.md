# Changelog

All notable changes to ArborFDM. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/) (0.x: anything may still change).

## [0.3.0] - unreleased

### CI and platform support
- Python 3.12 minimum: on 3.10 and 3.11, PySide6 6.12 aborts with "deallocating None" during background tasks (reproduced locally). CI now runs 3.12, 3.13, 3.14 on Linux and 3.12 on Windows.
- `tests/test_qa_review.py` imports on Windows (the POSIX `resource` module is optional; memory-limited tests are skipped there).
- CI reports pytest failures as annotations (`scripts/ci_annotate.py`).

- License changed from MIT to GPL-3.0-or-later (maintainer decision before the first publication).

- Model resting on the raft (default): the 3MF for OrcaSlicer / Bambu Studio is one object with separate parts (model, supports, raft) instead of one merged mesh; alignment kept, per-part settings (lighter supports) applied to the supports and raft parts only (checked with OrcaSlicer 2.4.2 CLI: 9.96 g to 8.73 g). Layer height stays per object.

- Tall support trees in an unstable group (narrower than height / 5, e.g. the arm-end trees of the figurine that were knocked over in print test 4) are braced to the nearest neighbouring trunks on several levels (printable angle, up to 1.5 x brace_max_length); groups that cannot be braced are reported as `isolated_tall_tree`.

Raft, layers and orientation work on top of 0.2. Nothing here is validated by a new print: the figures come from the OrcaSlicer command line (see `docs/ALGORITHM.md`, sections 4b and 4c).

### Added
- Raft coverage `raft_coverage`: `footprint` (default: one plate under the model base and feet, the model resting on it with a release gap), `bed` (bounding rectangle of model and feet, never the whole printer bed) and `feet` (version 0.1 behaviour).
- Release gap `raft_release_gap` between raft and model (one layer in the built-in profiles; 0 welds the base to the raft).
- Model lift above the raft: automatic (`raft_lift_auto`, up to 5 mm, refused with warning `raft_lift_refused` when the flat base exceeds 25 mm²) or manual (`raft_lift`). Only applies when the model rests on the raft.
- Hollow supports `hollow_supports` (off by default, wall `hollow_wall` = 3 line widths): -13 % filament and -4 % time on the supports.
- Lighter supports in the slicer `light_slicer_supports` (off by default): 2 walls, 10 % infill and no minimum sparse infill area, on the supports object only: -31 % filament and -24 % time on the supports. Needs raft `feet` or no raft.
- Overhang surface percentage for each orientation candidate, coloured green, orange or red.
- Mouse wheel on the Cut slider: one layer per notch, Shift for 10 layers (3D rebuild throttled to 60 ms).
- Labels and tooltips for all new parameters in the Advanced tab.

### Changed
- Raft enabled by default (parameters and built-in profiles). Projects `.arbor` saved earlier keep raft coverage `feet`; format-1 user presets keep their raft state.
- When the model rests on the raft, the "separate objects" 3MF is a single object: per-object layer heights and lighter supports cannot apply, and the export now says so explicitly ("Lighter supports not applied...").

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


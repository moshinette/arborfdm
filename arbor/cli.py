"""ArborFDM command line interface.

    arborfdm-cli generate in.stl -o out.3mf [--profile nozzle_04_normal] [--set key=value ...]
                 [--stl-supports s.stl] [--repair] [--auto-orient] [--json report.json]
                 [--layout objects|components|separate]
                 [--model-layer-height MM] [--support-layer-height MM]
                 [--open-in-orca [--orca-path PATH]]   (-o optional with --open-in-orca)
    arborfdm-cli check in.stl [--json report.json]
    arborfdm-cli orca --find | --set-path PATH | --clear-path
    arborfdm-cli profiles [--json]

Exit codes
    0  success (warnings may have been printed)
    1  the work was done but problems remain: verification errors after generation,
       export validation problems, or (``check``) an invalid mesh
    2  input / usage error (unreadable file, unknown profile or parameter, invalid mesh
       without --repair, failed repair, nothing to support...)
    130 interrupted (Ctrl+C)

Pipeline of ``generate`` (same steps as the GUI, no Qt involved):
    load (arbor.core.mesh_io) -> check (arbor.core.repair) -> optional repair of a copy
    -> optional automatic orientation (arbor.core.orientation, best candidate)
    -> placement on the plate (arbor.core.transform: XY centred, min z = 0)
    -> arbor.engine.pipeline.generate -> export 3MF (or STL) -> validation of the file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import Any, Optional, Sequence

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class CliError(Exception):
    """Input / usage error: message printed, exit code 2."""


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------
class _Out:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def info(self, msg: str = "") -> None:
        if not self.quiet:
            print(msg, flush=True)

    def warn(self, msg: str) -> None:
        print(f"warning: {msg}", file=sys.stderr, flush=True)

    def error(self, msg: str) -> None:
        print(f"error: {msg}", file=sys.stderr, flush=True)


class _ProgressPrinter:
    """Engine progress callback -> one line per new message (or every 10 %)."""

    def __init__(self, out: _Out):
        self.out = out
        self.last_key: Optional[str] = None
        self.last_pct = -100

    def __call__(self, fraction: float, message: str) -> None:
        if self.out.quiet:
            return
        pct = int(round(100 * fraction))
        key = re.sub(r"\d+", "#", message)     # "Slicing layer 6/250" ~ "Slicing layer 1/250"
        if key != self.last_key or pct - self.last_pct >= 10:
            print(f"  [{pct:3d}%] {message}", file=sys.stderr, flush=True)
            self.last_key, self.last_pct = key, pct


def _jsonable(x: Any) -> Any:
    import numpy as np
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, Path):
        return str(x)
    if hasattr(x, "value") and not isinstance(x, (str, int, float, bool)):   # Enum
        return x.value
    return x


def _write_json(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_jsonable(data), indent=2, ensure_ascii=False), encoding="utf-8")


def _mesh_report_dict(r) -> dict:
    return {
        "faces": r.n_faces, "vertices": r.n_vertices, "components": r.n_components,
        "watertight": r.watertight, "winding_consistent": r.winding_consistent,
        "holes": r.n_holes, "boundary_edges": r.n_boundary_edges,
        "non_manifold_edges": r.n_non_manifold_edges, "degenerate_faces": r.n_degenerate_faces,
        "duplicate_faces": r.n_duplicate_faces, "inverted": r.inverted,
        "volume_mm3": r.volume, "bbox_mm": r.bbox, "size_mm": r.size, "valid": r.is_valid,
        "problems": [{"severity": p.severity, "code": p.code, "message": p.message}
                     for p in r.problems],
    }


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------
def _parse_bool(text: str) -> bool:
    t = text.strip().lower()
    if t in ("1", "true", "yes", "on"):
        return True
    if t in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {text!r}")


def apply_overrides(params, assignments: Sequence[str]):
    """Return a copy of ``params`` with ``key=value`` assignments applied (typed from the
    current value). Raises CliError on unknown key / bad value / invalid result."""
    names = {f.name for f in fields(params)}
    changes: dict[str, Any] = {}
    for a in assignments:
        if "=" not in a:
            raise CliError(f"--set expects key=value, got {a!r}")
        key, raw = (s.strip() for s in a.split("=", 1))
        if key not in names:
            close = [n for n in sorted(names) if key.lower() in n or n in key.lower()]
            hint = f" (did you mean: {', '.join(close[:5])})" if close else ""
            raise CliError(f"unknown parameter {key!r}{hint}; see 'arborfdm-cli profiles --params'")
        cur = getattr(params, key)
        try:
            if isinstance(cur, bool):
                val: Any = _parse_bool(raw)
            elif isinstance(cur, int):
                val = int(raw)
            elif isinstance(cur, float):
                val = float(raw)
            else:
                val = raw
        except ValueError as exc:
            raise CliError(f"--set {key}: {exc}") from None
        changes[key] = val
    new = params.updated(**changes) if changes else params
    problems = new.validate()
    if problems:
        raise CliError("invalid parameters: " + "; ".join(problems))
    return new


def _load_profiles():
    from arbor.engine.params import load_profiles
    try:
        return load_profiles()
    except Exception as exc:   # missing directory / bad JSON
        raise CliError(f"cannot load profiles: {exc}") from exc


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_profiles(args, out: _Out) -> int:
    profiles = _load_profiles()
    if args.json:
        data = {k: {"label": p.label, "description": p.description, "params": p.params.to_dict()}
                for k, p in profiles.items()}
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return EXIT_OK
    if not profiles:
        out.error("no profile found")
        return EXIT_USAGE
    width = max(len(k) for k in profiles)
    for k, p in profiles.items():
        print(f"{k:<{width}}  {p.label}")
        if p.description:
            print(f"{'':<{width}}  {p.description}")
    if args.params:
        from arbor.engine.params import SupportParams
        print("\nParameters (--set key=value), defaults of SupportParams:")
        for f in fields(SupportParams):
            print(f"  {f.name} = {getattr(SupportParams(), f.name)!r}")
    return EXIT_OK


def _load(path: str, out: _Out):
    from arbor.core.mesh_io import MeshLoadError, load_mesh
    try:
        lm = load_mesh(path)
    except MeshLoadError as exc:
        raise CliError(str(exc)) from None
    out.info(f"Loaded {lm.source_path.name} ({lm.file_format}): {len(lm.mesh.faces)} triangles, "
             f"{len(lm.mesh.vertices)} vertices")
    for w in lm.warnings:
        out.warn(w)
    return lm


def cmd_check(args, out: _Out) -> int:
    from arbor.core.repair import check_mesh
    lm = _load(args.input, out)
    rep = check_mesh(lm.mesh)
    s = rep.size
    out.info(f"Size: {s[0]:.2f} x {s[1]:.2f} x {s[2]:.2f} mm")
    if rep.volume is not None:
        out.info(f"Volume: {abs(rep.volume):.2f} mm3")
    for p in rep.problems:
        (out.error if p.severity == "error" else out.warn if p.severity == "warning" else out.info)(
            p.message if p.severity != "info" else f"info: {p.message}")
    out.info("Mesh OK" if rep.is_valid else "Mesh has errors (try: arborfdm-cli generate --repair)")
    if args.json:
        _write_json(args.json, {"input": lm.source_path, "format": lm.file_format,
                                "load_warnings": lm.warnings, "check": _mesh_report_dict(rep)})
    return EXIT_OK if rep.is_valid else EXIT_PROBLEMS


def _configured_orca_path(out: Optional[_Out] = None) -> Optional[str]:
    import warnings
    from arbor.core.settings import load_settings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        s = load_settings()
    if out is not None:
        for w in caught:
            out.warn(str(w.message))
    return s.get("orca_path")


def _resolve_orca(explicit: Optional[str], out: _Out):
    """OrcaSlicer executable for --open-in-orca: --orca-path (must be valid), else the
    path saved in the settings (if valid), else automatic detection. None if not found
    (a warning is printed; the file is still exported)."""
    from arbor.io.slicer_launch import check_orca_path, find_orcaslicer
    if explicit:
        why = check_orca_path(explicit)
        if why is not None:
            raise CliError(f"--orca-path: {why}")
        return Path(explicit).expanduser()
    configured = _configured_orca_path(out)
    if configured:
        why = check_orca_path(configured)
        if why is not None:
            out.warn(f"OrcaSlicer path in the settings ignored ({why}); trying automatic detection")
    exe = find_orcaslicer(configured)
    if exe is None:
        out.warn("OrcaSlicer not found (see 'arborfdm-cli orca --find'); the file will be "
                 "exported but not opened. Set the path with --orca-path or "
                 "'arborfdm-cli orca --set-path PATH'.")
    return exe


def _open_in_orca(model, sup, raft, params, name, layer_heights, exe, out: _Out,
                  notes: list, files: dict, report: dict) -> bool:
    """Export the file for OrcaSlicer and open it. Returns True if it could not be opened."""
    from arbor.io.slicer_launch import (SlicerExportError, SlicerLaunchError, SlicerNotFound,
                                        export_for_slicer, launch_orcaslicer)
    try:
        path = export_for_slicer(model, sup, raft, params, name,
                                 layer_heights=layer_heights, notes=notes)
    except (SlicerExportError, ValueError) as exc:
        out.error(f"open in OrcaSlicer: {exc}")
        report["orca"] = {"file": None, "exe": str(exe) if exe else None, "opened": False,
                          "error": str(exc)}
        return True
    files["orca"] = str(path)
    out.info(f"Wrote {path} (for OrcaSlicer: 2 objects, OrcaSlicer supports disabled per object)")
    info = {"file": str(path), "exe": str(exe) if exe else None, "opened": False}
    report["orca"] = info
    if exe is None:
        info["error"] = "OrcaSlicer not found"
        out.error(f"OrcaSlicer not found: open {path} yourself")
        return True
    try:
        launch_orcaslicer(path, exe)
    except (SlicerNotFound, SlicerLaunchError) as exc:
        info["error"] = str(exc)
        out.error(f"open in OrcaSlicer: {exc}")
        return True
    info["opened"] = True
    out.info(f"Opening in OrcaSlicer ({exe})")
    return False


def cmd_orca(args, out: _Out) -> int:
    from arbor.io.slicer_launch import candidate_paths, check_orca_path, find_orcaslicer
    if args.set_path is not None or args.clear_path:
        from arbor.core.settings import SettingsError, settings_path, update_settings
        if args.set_path is not None:
            why = check_orca_path(args.set_path)
            if why is not None:
                raise CliError(f"--set-path: {why}")
            value: Optional[str] = str(Path(args.set_path).expanduser().resolve())
        else:
            value = None
        try:
            update_settings(orca_path=value)
        except SettingsError as exc:
            raise CliError(str(exc)) from None
        out.info(f"OrcaSlicer path {'set to ' + value if value else 'cleared (automatic detection)'} "
                 f"in {settings_path()}")
        return EXIT_OK
    configured = _configured_orca_path(out)
    if configured:
        why = check_orca_path(configured)
        print(f"Configured path: {configured}" + (f" (INVALID: {why})" if why else " (ok)"))
    exe = find_orcaslicer(configured)
    if exe is not None:
        print(f"OrcaSlicer: {exe}")
        return EXIT_OK
    print("OrcaSlicer not found. Locations tried:")
    for p in candidate_paths():
        print(f"  {p}")
    from arbor.io.slicer_launch import PATH_NAMES
    print(f"  (and PATH: {', '.join(PATH_NAMES)})")
    print("Set the path with: arborfdm-cli orca --set-path PATH")
    return EXIT_PROBLEMS


def cmd_generate(args, out: _Out) -> int:
    import numpy as np
    from arbor.core.repair import check_mesh, repair_mesh
    from arbor.core.transform import apply_transform, plate_matrix
    from arbor.engine import pipeline
    from arbor.engine.types import Progress
    from arbor.io.export import export_3mf, export_stl, validate_export

    if not args.output and not args.open_in_orca:
        raise CliError("-o/--output is required (or use --open-in-orca)")
    if args.orca_path and not args.open_in_orca:
        raise CliError("--orca-path needs --open-in-orca")
    out_path = Path(args.output) if args.output else None
    if out_path is not None and out_path.suffix.lower() not in (".3mf", ".stl"):
        raise CliError("output must end with .3mf or .stl")
    layer_heights = {k: v for k, v in (("model", args.model_layer_height),
                                       ("supports", args.support_layer_height)) if v is not None}
    sep_output = (out_path is not None and args.layout == "separate"
                  and out_path.suffix.lower() == ".3mf")
    if layer_heights and not (sep_output or args.open_in_orca):
        raise CliError("--model-layer-height / --support-layer-height need a .3mf output "
                       "and --layout separate (or --open-in-orca)")
    orca_exe = _resolve_orca(args.orca_path, out) if args.open_in_orca else None
    for k, v in layer_heights.items():
        if not 0.0 < v <= 2.0:
            raise CliError(f"--{'support' if k == 'supports' else 'model'}-layer-height must be in (0, 2] mm")
    profiles = _load_profiles()
    if args.profile not in profiles:
        raise CliError(f"unknown profile {args.profile!r}; available: {', '.join(profiles)}")
    params = apply_overrides(profiles[args.profile].params, args.set or [])
    report: dict[str, Any] = {"input": str(Path(args.input).resolve()), "profile": args.profile,
                              "overrides": list(args.set or []), "params": params.to_dict()}
    t0 = time.perf_counter()

    lm = _load(args.input, out)
    report["load_warnings"] = list(lm.warnings)
    mesh = lm.mesh

    # 1. diagnosis / repair (on a copy; the loaded mesh is never modified)
    rep = check_mesh(mesh)
    report["check"] = _mesh_report_dict(rep)
    for p in rep.problems:
        if p.severity in ("error", "warning"):
            (out.error if p.severity == "error" and not args.repair else out.warn)(f"mesh: {p.message}")
    if not rep.is_valid:
        if not args.repair:
            if args.json:
                report["exit_code"] = EXIT_USAGE
                _write_json(args.json, report)
            raise CliError("the mesh is not a valid closed solid; rerun with --repair "
                           "(a repaired copy is used, the input file is not touched)")
    if args.repair and (not rep.is_valid or rep.n_degenerate_faces or rep.n_duplicate_faces):
        mesh, rr = repair_mesh(mesh)
        report["repair"] = {"success": rr.success, "actions": rr.actions,
                            "failures": rr.failure_reasons, "after": _mesh_report_dict(rr.after)}
        for a in rr.actions:
            out.info(f"repair: {a}")
        for f in rr.failure_reasons:
            out.error(f"repair: {f}")
        if not rr.success:
            if args.json:
                report["exit_code"] = EXIT_USAGE
                _write_json(args.json, report)
            raise CliError("repair failed; the mesh cannot be used as is")

    # 2. orientation + placement
    matrix = np.eye(4)
    if args.auto_orient:
        from arbor.core.orientation import suggest_orientations
        cands = suggest_orientations(mesh, overhang_angle_deg=params.overhang_angle_deg, n=3)
        if cands:
            best = cands[0]
            out.info(f"Orientation: {best.describe()}")
            matrix = np.asarray(best.matrix, float)
            report["orientation"] = {"label": best.label, "rotation_deg": best.rotation_deg,
                                     "cost": best.cost, "criteria": best.criteria,
                                     "alternatives": [{"label": c.label, "cost": c.cost,
                                                       "rotation_deg": c.rotation_deg}
                                                      for c in cands[1:]]}
    if not np.allclose(matrix, np.eye(4)):
        mesh = apply_transform(mesh, matrix)
    place = plate_matrix(mesh)
    mesh = apply_transform(mesh, place)
    report["plate_transform"] = (place @ matrix)
    s = mesh.bounds[1] - mesh.bounds[0]
    out.info(f"On plate: {s[0]:.2f} x {s[1]:.2f} x {s[2]:.2f} mm (XY centred, min z = 0)")

    # 3. generation
    out.info(f"Generating supports (profile {args.profile})...")
    progress = Progress(_ProgressPrinter(out))
    try:
        res = pipeline.generate(mesh, params, progress=progress)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    stats = res.tree.stats()
    n_contacts = sum(1 for c in res.contacts if c.enabled)
    sup = res.support_mesh
    has_sup = sup is not None and len(getattr(sup, "faces", [])) > 0
    out.info(f"Contacts: {n_contacts}, trees: {stats['trees']}, tips: {stats['tips']}, "
             f"feet: {stats['feet']} (+{stats['model_feet']} on the model)")
    report["contacts"] = [c.to_dict() for c in res.contacts]
    report["tree"] = stats
    report["timings_s"] = res.timings
    report["issues"] = [{"severity": i.severity, "code": i.code, "message": i.message,
                         "position": i.position, "contact_id": i.contact_id} for i in res.issues]
    for i in res.issues:
        if i.severity == "error":
            out.error(f"verify: {i.message}")
        elif i.severity == "warning":
            out.warn(f"verify: {i.message}")
        else:
            out.info(f"info: {i.message}")

    # 4. export
    name = lm.name or Path(args.input).stem
    notes: list[str] = []
    files: dict[str, str] = {}
    problems: list[str] = []
    report["export"] = {}
    if out_path is None:
        pass
    elif out_path.suffix.lower() == ".3mf":
        er = export_3mf(out_path, mesh, sup if has_sup else None, res.raft_mesh, params, model_name=name,
                        layout=args.layout, layer_heights=(layer_heights or None) if sep_output else None)
        notes += er.notes
        problems = validate_export(out_path)
        report["export"] = {"path": out_path, "objects": er.objects, "layout": er.layout,
                            "offset_mm": er.offset, "triangles": er.n_triangles,
                            "layer_heights": layer_heights if sep_output else {}}
    else:
        parts = [m for m in (mesh, sup if has_sup else None, res.raft_mesh)
                 if m is not None and len(m.faces)]
        export_stl(parts, out_path, notes=notes,
                   names=[name, f"{name} supports", f"{name} raft"][:len(parts)])
        problems = []
        report["export"] = {"path": out_path, "objects": ["model + supports (one STL)"]}
    if out_path is not None:
        files["output"] = str(out_path)
        out.info(f"Wrote {out_path}")
    if args.stl_supports:
        sparts = [m for m in (sup if has_sup else None, res.raft_mesh) if m is not None and len(m.faces)]
        if sparts:
            export_stl(sparts, args.stl_supports, notes=notes,
                       names=[f"{name} supports (STL)", f"{name} raft (STL)"][:len(sparts)])
            files["stl_supports"] = str(args.stl_supports)
            out.info(f"Wrote {args.stl_supports}")
        else:
            out.warn("no support generated: --stl-supports file not written")
    launch_failed = False
    if args.open_in_orca:
        launch_failed = _open_in_orca(mesh, sup if has_sup else None, res.raft_mesh, params, name,
                                      layer_heights or None, orca_exe, out, notes, files, report)
    for n in notes:
        out.info(f"export: {n}")
    for p in problems:
        out.error(f"export validation: {p}")
    report["export"]["notes"] = notes
    report["export"]["validation_problems"] = problems
    report["files"] = files
    if not has_sup:
        out.warn("no support was generated (nothing to support?)")
    if out_path is not None:
        out.info("Reminder: disable the slicer's own supports when printing this file.")
    if out_path is None:
        pass
    elif out_path.suffix.lower() == ".3mf" and args.layout == "separate":
        out.info("Separate objects, each resting on the plate: OrcaSlicer / Bambu Studio keep them "
                 "as separate objects; PrusaSlicer may ask 'Multi-part object detected', both "
                 "answers keep the alignment (see docs/SLICERS.md).")
    elif out_path.suffix.lower() == ".3mf":
        out.info("PrusaSlicer / OrcaSlicer / Bambu Studio: answer 'Yes' to 'Multi-part object "
                 "detected' to keep the model on its supports, or export with --layout separate "
                 "(see docs/SLICERS.md).")

    code = EXIT_PROBLEMS if (res.errors or problems or launch_failed) else EXIT_OK
    report["errors"] = len(res.errors) + len(problems)
    report["warnings"] = len(res.warnings)
    report["exit_code"] = code
    report["total_time_s"] = time.perf_counter() - t0
    if args.json:
        _write_json(args.json, report)
        out.info(f"Wrote {args.json}")
    out.info(f"Done in {report['total_time_s']:.1f} s: {len(res.errors) + len(problems)} error(s), "
             f"{len(res.warnings)} warning(s)")
    return code


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _version() -> str:
    try:
        from importlib.metadata import version
        return version("arborfdm")
    except Exception:
        return "0.1.0"


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="arborfdm-cli",
                                 description="ArborFDM: hybrid tree supports for FDM miniatures.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print warnings and errors")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="only print warnings and errors")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    g = sub.add_parser("generate", parents=[common], help="generate supports and export a 3MF (or STL)")
    g.add_argument("input", help="model file (STL, OBJ or 3MF), millimetres")
    g.add_argument("-o", "--output", help="output .3mf (model + supports + raft) or .stl "
                   "(optional with --open-in-orca)")
    g.add_argument("--profile", default="nozzle_04_normal", help="profile key (see 'profiles')")
    g.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="override a parameter, repeatable (e.g. --set tip_diameter=0.5)")
    g.add_argument("--stl-supports", metavar="FILE", help="also write supports (+ raft) as one STL")
    g.add_argument("--repair", action="store_true",
                   help="repair a copy of the mesh if it is not a valid closed solid")
    g.add_argument("--auto-orient", action="store_true",
                   help="apply the best automatic orientation before generating")
    g.add_argument("--layout", choices=("objects", "components", "separate"), default="objects",
                   help="3MF layout: one build item per part (default), one assembly "
                        "(components), or separate objects that each rest on the plate, raft "
                        "merged with the supports (separate: for OrcaSlicer / Bambu Studio)")
    g.add_argument("--model-layer-height", type=float, metavar="MM",
                   help="with --layout separate: layer height of the model object, written for "
                        "OrcaSlicer / Bambu Studio (Metadata/model_settings.config)")
    g.add_argument("--support-layer-height", type=float, metavar="MM",
                   help="with --layout separate: layer height of the supports (+ raft) object "
                        "for OrcaSlicer / Bambu Studio")
    g.add_argument("--open-in-orca", action="store_true",
                   help="also write a 3MF for OrcaSlicer in ArborFDM's to_slicer folder (separate "
                        "objects, OrcaSlicer supports disabled per object) and open it in OrcaSlicer")
    g.add_argument("--orca-path", metavar="PATH",
                   help="OrcaSlicer program to use with --open-in-orca (default: saved path, "
                        "then automatic detection)")
    g.add_argument("--json", metavar="FILE", help="write a JSON report")
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("check", parents=[common], help="diagnose a mesh (holes, non-manifold edges, winding...)")
    c.add_argument("input")
    c.add_argument("--json", metavar="FILE", help="write a JSON report")
    c.set_defaults(func=cmd_check)

    p = sub.add_parser("profiles", parents=[common], help="list the support profiles")
    p.add_argument("--json", action="store_true", help="print profiles and values as JSON")
    p.add_argument("--params", action="store_true", help="also list every parameter name")
    p.set_defaults(func=cmd_profiles)

    o = sub.add_parser("orca", parents=[common], help="find OrcaSlicer / set its path")
    og = o.add_mutually_exclusive_group(required=True)
    og.add_argument("--find", action="store_true",
                    help="print the OrcaSlicer program found (exit 1 and the locations tried if none)")
    og.add_argument("--set-path", metavar="PATH", help="save the OrcaSlicer program path in the settings")
    og.add_argument("--clear-path", action="store_true",
                    help="forget the saved path (back to automatic detection)")
    o.set_defaults(func=cmd_orca)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = make_parser()
    try:
        args = ap.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:          # --help / --version / usage error
        return int(exc.code or 0)
    out = _Out(quiet=getattr(args, "quiet", False))
    try:
        return int(args.func(args, out))
    except CliError as exc:
        out.error(str(exc))
        return EXIT_USAGE
    except KeyboardInterrupt:
        out.error("interrupted")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())

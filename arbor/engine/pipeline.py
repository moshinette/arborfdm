"""Engine entry points. The GUI and the CLI only talk to the engine through this module.

Stages (each in its own module, testable alone):
  analysis.analyze      -> AnalysisResult        (layer by layer slicing, islands, overhangs)
  contacts.place_contacts -> list[Contact]       (where tips touch the model, with reasons)
  tree.build_tree       -> SupportTree           (tips -> branches -> trunks -> feet)
  build.mesh_tree       -> trimesh.Trimesh       (manifold union of all support solids)
  build.mesh_raft       -> trimesh.Trimesh|None
  verify.verify         -> list[Issue]           (post generation checks)
  verify.support_stats  -> dict                  (GenerationResult.stats: volume, mass, marks...)
"""
from __future__ import annotations

import dataclasses
import time
from typing import Optional

import numpy as np
import trimesh

from . import analysis as _analysis
from . import build as _build
from . import contacts as _contacts
from . import placement as _placement
from . import tree as _tree
from . import verify as _verify
from .params import SupportParams, model_on_raft
from .types import AnalysisResult, Contact, GenerationResult, Issue, Progress


# Largest model extent accepted (mm). Desktop FDM build volumes are <= ~500 mm; anything
# larger is almost always a unit or scale mistake and would exhaust memory (QA-8).
MAX_MODEL_EXTENT_MM = 1000.0


def check_model_size(mesh: trimesh.Trimesh) -> None:
    if len(mesh.vertices) == 0:
        raise ValueError("The model is empty")
    ext = float(max(mesh.extents))
    if ext > MAX_MODEL_EXTENT_MM:
        raise ValueError(f"The model is {ext:.0f} mm long, larger than {MAX_MODEL_EXTENT_MM:.0f} mm: "
                         "check the units or the scale before generating supports")


def analyze(mesh: trimesh.Trimesh, params: SupportParams,
            progress: Optional[Progress] = None) -> AnalysisResult:
    return _analysis.analyze(mesh, params, progress or Progress())


def generate(mesh: trimesh.Trimesh, params: SupportParams,
             manual_contacts: Optional[list[Contact]] = None,
             progress: Optional[Progress] = None,
             analysis: Optional[AnalysisResult] = None,
             contacts: Optional[list[Contact]] = None) -> GenerationResult:
    """Full generation.

    analysis: reuse a previous analysis (same mesh + detection params; ignored if it
              was computed with another model lift).
    contacts: if given, skip automatic placement and use exactly these contacts
              (used after manual editing: 'regenerate branches only').

    Everything returned is in the frame of `mesh` (the model is never moved). With a
    raft under the model the raft lies below z = 0 and, when the model is lifted
    (placement.plan_lift), so do the supports; see GenerationResult.model_lift/plate_z.
    """
    progress = progress or Progress()
    problems = params.validate()
    if problems:
        raise ValueError("Invalid parameters: " + "; ".join(problems))
    check_model_size(mesh)
    plan = _placement.plan_lift(mesh, params)
    L = float(plan.lift)
    if analysis is not None and abs(float(analysis.stats.get("model_lift", 0.0)) - L) > 1e-9:
        analysis = None                      # computed for another placement
    if L <= 0.0:
        res = _generate(mesh, params, manual_contacts, progress, analysis, contacts)
    else:
        up = np.array([0.0, 0.0, L])
        mesh_e = mesh.copy()
        mesh_e.apply_translation(up)
        # caller's contacts: moved in place for the run and restored exactly afterwards
        # (the tree builder sets rests_on_model on these objects)
        given = list({id(c): c for c in (manual_contacts or []) + (contacts or [])}.values())
        saved = {id(c): c.position for c in given}
        for c in given:
            c.position = np.asarray(c.position, float) + up
        try:
            res = _generate(mesh_e, params, manual_contacts, progress,
                            _shift_analysis(analysis, L) if analysis is not None else None, contacts)
        finally:
            for c in given:
                c.position = saved[id(c)]
        _shift_result(res, -L, skip={id(c) for c in given})
    res.model_lift = L
    res.plate_z = (-(float(params.raft_thickness) + float(params.raft_release_gap) + L)
                   if model_on_raft(params) else 0.0)
    res.analysis.stats["model_lift"] = L
    res.stats["model_lift_mm"] = L
    res.stats["plate_z_mm"] = res.plate_z
    res.stats["lift_plan"] = plan.as_dict()
    if L > 0:
        res.issues.append(Issue("info", "model_lifted", "Model raised above the raft: " + plan.reason,
                                np.array([*mesh.bounds.mean(axis=0)[:2], float(mesh.bounds[0][2])])))
    elif plan.refused:
        res.issues.append(Issue("warning", "raft_lift_refused",
                                "Overhangs close to the raft keep short supports: " + plan.reason,
                                np.array([*mesh.bounds.mean(axis=0)[:2], float(mesh.bounds[0][2])])))
    by = res.stats.get("issues_by_code")
    if isinstance(by, dict) and res.issues and res.issues[-1].code in ("model_lifted", "raft_lift_refused"):
        by[res.issues[-1].code] = by.get(res.issues[-1].code, 0) + 1
    return res


def _shift_analysis(a: AnalysisResult, dz: float) -> AnalysisResult:
    """Copy of an analysis translated by dz along Z (XY polygons and face mask unchanged)."""
    sh = lambda p: np.asarray(p, float) + np.array([0.0, 0.0, dz])
    regions = [dataclasses.replace(r, z=float(r.z) + dz) for r in a.regions]
    corners = sh(a.corners).reshape(-1, 3) if len(a.corners) else a.corners
    lows = sh(a.low_points).reshape(-1, 3) if len(a.low_points) else a.low_points
    stats = dict(a.stats)
    if "z_min" in stats:
        stats["z_min"] = float(stats["z_min"]) + dz
    return dataclasses.replace(a, layer_z=np.asarray(a.layer_z, float) + dz, regions=regions,
                               hanging_edges=[(sh(p), sh(q)) for p, q in a.hanging_edges],
                               corners=corners, low_points=lows, stats=stats)


def _shift_mesh(m, dz: float):
    if m is None or len(getattr(m, "vertices", [])) == 0:
        return m
    out = m.copy()
    out.apply_translation((0.0, 0.0, dz))
    return out


def _shift_result(res: GenerationResult, dz: float, skip: set) -> None:
    """Translate a GenerationResult by dz (engine frame -> user frame), in place.
    Contacts whose id() is in skip already are in the user frame (restored by the caller)."""
    up = np.array([0.0, 0.0, dz])
    res.support_mesh = _shift_mesh(res.support_mesh, dz)
    res.raft_mesh = _shift_mesh(res.raft_mesh, dz)
    for c in res.contacts:
        if id(c) not in skip:
            c.position = np.asarray(c.position, float) + up
    t = res.tree
    for n in t.nodes.values():
        n.position = n.position + up
    if hasattr(t, "braces"):
        t.braces = [(np.asarray(p0, float) + up, np.asarray(p1, float) + up, rb) for p0, p1, rb in t.braces]
    if hasattr(t, "z_base"):
        t.z_base = float(t.z_base) + dz
    for i in res.issues:
        if i.position is not None:
            i.position = np.asarray(i.position, float) + up
    res.analysis = _shift_analysis(res.analysis, dz)


def _generate(mesh, params, manual_contacts, progress, analysis, contacts) -> GenerationResult:
    timings: dict[str, float] = {}

    t = time.perf_counter()
    if analysis is None:
        analysis = _analysis.analyze(mesh, params, progress.sub(0.0, 0.35))
    timings["analysis"] = time.perf_counter() - t

    t = time.perf_counter()
    if contacts is None:
        contacts = _contacts.place_contacts(mesh, analysis, params, manual_contacts or [],
                                            progress.sub(0.35, 0.45))
    timings["contacts"] = time.perf_counter() - t

    t = time.perf_counter()
    active = [c for c in contacts if c.enabled]
    tree = _tree.build_tree(mesh, active, params, progress.sub(0.45, 0.7))
    timings["tree"] = time.perf_counter() - t

    t = time.perf_counter()
    support_mesh = _build.mesh_tree(tree, params, progress.sub(0.7, 0.92))
    raft_mesh = _build.mesh_raft(tree, params) if params.raft_enabled else None
    timings["mesh"] = time.perf_counter() - t

    t = time.perf_counter()
    issues: list[Issue] = _verify.verify(mesh, analysis, contacts, tree, support_mesh,
                                         params, progress.sub(0.92, 1.0))
    stats = _verify.support_stats(mesh, contacts, tree, support_mesh, raft_mesh, issues, params)
    timings["verify"] = time.perf_counter() - t
    progress.update(1.0, "Done")
    return GenerationResult(contacts, tree, support_mesh, raft_mesh, issues, analysis, timings, stats)

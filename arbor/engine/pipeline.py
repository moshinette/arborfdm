"""Engine entry points. The GUI and the CLI only talk to the engine through this module.

Stages (each in its own module, testable alone):
  analysis.analyze      -> AnalysisResult        (layer by layer slicing, islands, overhangs)
  contacts.place_contacts -> list[Contact]       (where tips touch the model, with reasons)
  tree.build_tree       -> SupportTree           (tips -> branches -> trunks -> feet)
  build.mesh_tree       -> trimesh.Trimesh       (manifold union of all support solids)
  build.mesh_raft       -> trimesh.Trimesh|None
  verify.verify         -> list[Issue]           (post generation checks)
"""
from __future__ import annotations

import time
from typing import Optional

import trimesh

from . import analysis as _analysis
from . import build as _build
from . import contacts as _contacts
from . import tree as _tree
from . import verify as _verify
from .params import SupportParams
from .types import AnalysisResult, Contact, GenerationResult, Issue, Progress


def analyze(mesh: trimesh.Trimesh, params: SupportParams,
            progress: Optional[Progress] = None) -> AnalysisResult:
    return _analysis.analyze(mesh, params, progress or Progress())


def generate(mesh: trimesh.Trimesh, params: SupportParams,
             manual_contacts: Optional[list[Contact]] = None,
             progress: Optional[Progress] = None,
             analysis: Optional[AnalysisResult] = None,
             contacts: Optional[list[Contact]] = None) -> GenerationResult:
    """Full generation.

    analysis: reuse a previous analysis (same mesh + detection params).
    contacts: if given, skip automatic placement and use exactly these contacts
              (used after manual editing: 'regenerate branches only').
    """
    progress = progress or Progress()
    problems = params.validate()
    if problems:
        raise ValueError("Invalid parameters: " + "; ".join(problems))
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
    timings["verify"] = time.perf_counter() - t
    progress.update(1.0, "Done")
    return GenerationResult(contacts, tree, support_mesh, raft_mesh, issues, analysis, timings)

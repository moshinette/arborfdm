"""Post generation checks.

The verifier never claims a model is printable. It reports what it could check and what
failed, as Issue objects (severity error / warning / info, machine readable code, optional
3D position for the viewer).

Checks (code -> meaning):
  unrouted_contact     a contact could not be connected to the plate or the model (error)
  sub_layer_gap        contact less than one layer above a surface, nothing to build (warning)
  unsupported_region   an island / overhang region from the layer analysis has no routed
                       contact close enough below it (error for islands and large areas,
                       warning for small overhang slivers)
  floating_support     a support component neither reaches the plate nor stands on the model
  disconnected_branch  a tree node whose chain does not end on a foot
  unstable_trunk       tall, slender column with a small foot and no brace near it
  model_collision      supports overlap the model more than the tips' allowed penetration
  below_plate          support geometry under z = 0
  invalid_support_mesh the merged support mesh is not a closed 2-manifold
  contacts_too_close   two active contacts closer than min_contact_distance
  invalid_model_mesh   the input model is not closed / consistently oriented (warning)
  rests_on_model       informational: supports standing on the model (marks on the part)
  collision_check_skipped  model is not a valid manifold, overlap could not be measured
  geometric_only       informational reminder that checks are geometric, not a print test
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .params import SupportParams
from .types import AnalysisResult, Contact, Issue, NodeKind, Progress, SupportTree

PLATE_EPS = 0.3          # a foot is on the plate if within this of z_base
SLENDER_RATIO = 18.0     # column height / foot radius above which a trunk is flagged
COLLISION_ERROR_FACTOR = 3.0   # overlap above this multiple of the tip budget is an error
MODEL_FOOT_TOL = 0.3     # max distance (mm) between a model foot and the model surface


def _routed_contact_ids(tree: SupportTree) -> set[int]:
    return {n.contact_id for n in tree.nodes.values()
            if n.kind == NodeKind.TIP and n.contact_id is not None}


def _root_of(tree: SupportTree):
    cache: dict[int, int] = {}

    def root(i: int) -> int:
        path = []
        seen = set()
        while i not in cache:
            n = tree.nodes[i]
            if n.parent is None or n.parent not in tree.nodes or i in seen:
                cache[i] = i
                break
            seen.add(i)
            path.append(i)
            i = n.parent
        r = cache[i]
        for p in path:
            cache[p] = r
        return r
    return root


def _check_unrouted(contacts, tree, issues):
    failed = getattr(tree, "failed", {}) or {}
    routed = _routed_contact_ids(tree)
    by_id = {c.id: c for c in contacts}
    for c in contacts:
        if not c.enabled:
            continue
        if c.id in routed and c.id not in failed:
            continue
        why = failed.get(c.id, "no support was built for this contact")
        if "less than one layer" in why:
            # the slicer prints this sliver directly from the surface below
            issues.append(Issue("warning", "sub_layer_gap",
                                f"Contact {c.id} is less than one layer above another surface; "
                                f"no support built ({why})",
                                np.asarray(c.position, float).copy(), c.id))
            continue
        issues.append(Issue("error", "unrouted_contact",
                            f"Contact {c.id} ({c.reason.value}) is not supported: {why}",
                            np.asarray(c.position, float).copy(), c.id))
    return {cid for cid in routed if cid not in failed and cid in by_id}


def _check_regions(analysis: AnalysisResult, contacts, routed_ids, params, issues):
    """Every region found by the layer analysis must have a routed contact near it."""
    if not analysis.regions:
        return
    pts = np.array([c.position for c in contacts if c.enabled and c.id in routed_ids], float)
    pts = pts.reshape(-1, 3)
    lh = analysis.layer_height
    reach = max(params.contact_spacing, params.edge_spacing) / max(params.contact_density, 1e-6)
    z_below = max(4 * lh, reach)           # supports below a region keep building upward
    try:
        from shapely.geometry import Point  # noqa: F401
        from shapely import points as _sh_points, distance as _sh_distance
    except Exception:  # pragma: no cover
        _sh_points = None
    big_area = 0.25 * params.contact_spacing ** 2
    for reg in analysis.regions:
        if len(pts):
            zmask = (pts[:, 2] >= reg.z - z_below) & (pts[:, 2] <= reg.z + 2 * lh + 0.05)
            cand = pts[zmask]
        else:
            cand = pts
        covered = False
        if len(cand):
            if _sh_points is not None:
                d = _sh_distance(reg.polygon, _sh_points(cand[:, :2]))
            else:  # pragma: no cover
                from shapely.geometry import Point
                d = np.array([reg.polygon.distance(Point(p[:2])) for p in cand])
            covered = bool(np.min(d) <= reach)
        if covered:
            continue
        c = reg.polygon.representative_point()
        pos = np.array([c.x, c.y, reg.z])
        severe = reg.kind == "island" or reg.area >= big_area
        issues.append(Issue("error" if severe else "warning", "unsupported_region",
                            f"{reg.kind.capitalize()} of {reg.area:.2f} mm2 at z={reg.z:.2f} mm "
                            f"has no support contact within {reach:.1f} mm: it would print in the air",
                            pos))


def _check_tree(tree: SupportTree, params, issues):
    if not tree.nodes:
        return
    z_base = float(getattr(tree, "z_base", 0.0))
    root = _root_of(tree)
    bad_roots: dict[int, list[int]] = {}
    for nid in tree.nodes:
        r = root(nid)
        rn = tree.nodes[r]
        ok = (rn.kind == NodeKind.FOOT and rn.position[2] <= z_base + PLATE_EPS) or \
            rn.kind == NodeKind.MODEL_FOOT
        if not ok:
            bad_roots.setdefault(r, []).append(nid)
    for r, members in bad_roots.items():
        rn = tree.nodes[r]
        cids = [tree.nodes[m].contact_id for m in members if tree.nodes[m].contact_id is not None]
        issues.append(Issue("error", "disconnected_branch",
                            f"Support branch ending at z={rn.position[2]:.2f} mm ({rn.kind.value}) "
                            f"is not connected to the plate or the model ({len(members)} nodes)",
                            rn.position.copy(), cids[0] if cids else None))

    # slender trunks: height of the highest node above each plate foot vs foot radius
    foot_r = getattr(tree, "foot_radius", {}) or {}
    braces = getattr(tree, "braces", []) or []
    brace_pts = np.array([0.5 * (np.asarray(a) + np.asarray(b)) for a, b, _ in braces]).reshape(-1, 3)
    top: dict[int, float] = {}
    for nid, n in tree.nodes.items():
        r = root(nid)
        top[r] = max(top.get(r, -1e9), float(n.position[2]))
    for r, ztop in top.items():
        rn = tree.nodes[r]
        if rn.kind != NodeKind.FOOT:
            continue
        h = ztop - z_base
        fr = float(foot_r.get(r, params.foot_diameter / 2))
        if h / max(fr, 1e-6) <= SLENDER_RATIO:
            continue
        braced = len(brace_pts) and np.any(np.linalg.norm(brace_pts[:, :2] - rn.position[:2], axis=1)
                                           <= params.brace_max_length)
        if braced:
            continue
        hints = [x for x, on in (("braces", not params.braces_enabled),
                                 ("a raft", not params.raft_enabled), ("a larger foot", True)) if on]
        issues.append(Issue("warning", "unstable_trunk",
                            f"Tree of height {h:.1f} mm stands on a {2 * fr:.1f} mm foot with no "
                            f"brace near it: it may wobble or detach (consider {', '.join(hints)})",
                            rn.position.copy()))

    model_feet = [n for n in tree.nodes.values() if n.kind == NodeKind.MODEL_FOOT]
    if model_feet:
        issues.append(Issue("info", "rests_on_model",
                            f"{len(model_feet)} support(s) stand on the model and will leave marks "
                            "there; check they can be removed and do not enclose a part of the model",
                            model_feet[0].position.copy()))


def _check_mesh(mesh, support_mesh, tree, contacts, params, issues):
    if support_mesh is None or len(getattr(support_mesh, "faces", [])) == 0:
        return
    if not support_mesh.is_watertight or not support_mesh.is_winding_consistent:
        issues.append(Issue("error", "invalid_support_mesh",
                            "The merged support mesh is not closed / consistently oriented; "
                            "slicers may misread it"))

    z_base = float(getattr(tree, "z_base", 0.0))
    if support_mesh.bounds[0, 2] < -1e-3:
        issues.append(Issue("error", "below_plate",
                            f"Support geometry goes {-support_mesh.bounds[0, 2]:.2f} mm below the "
                            "build plate"))

    # floating components (mesh level, independent of the tree bookkeeping)
    mf_pts = np.array([n.position for n in tree.nodes.values()
                       if n.kind == NodeKind.MODEL_FOOT]).reshape(-1, 3)
    try:
        comps = support_mesh.split(only_watertight=False)
    except Exception:
        comps = [support_mesh]
    for comp in comps:
        if comp.is_watertight and comp.volume < 0:
            continue  # inverted shell = closed void inside a solid trunk, not a separate part
        lo, hi = comp.bounds
        if lo[2] <= z_base + PLATE_EPS:
            continue
        if len(mf_pts) and np.any(np.all((mf_pts >= lo - 1.0) & (mf_pts <= hi + 1.0), axis=1)):
            continue
        issues.append(Issue("error", "floating_support",
                            f"A support part between z={lo[2]:.2f} and {hi[2]:.2f} mm floats in the air",
                            np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])))

    # collision with the model
    try:
        from .build import trimesh_to_manifold
        a = trimesh_to_manifold(mesh)
        b = trimesh_to_manifold(support_mesh)
        import manifold3d as mf
        if a.status() != mf.Error.NoError or b.status() != mf.Error.NoError:
            raise ValueError("not a manifold")
        inter = a ^ b
        vol = float(inter.volume())
    except Exception:
        issues.append(Issue("info", "collision_check_skipped",
                            "The model is not a closed manifold, overlap between supports and model "
                            "could not be measured (repair the mesh to enable this check)"))
        return
    n = sum(1 for c in contacts if c.enabled)
    r = params.tip_base_diameter / 2
    budget = n * np.pi * r * r * (params.tip_penetration + 0.1) + 0.2
    if vol > budget:
        from .build import manifold_to_trimesh
        pos = None
        try:
            t = manifold_to_trimesh(inter)
            parts = t.split(only_watertight=False)
            biggest = max(parts, key=lambda p: abs(p.volume)) if parts else t
            pos = biggest.bounds.mean(axis=0)
        except Exception:
            pass
        severe = vol > COLLISION_ERROR_FACTOR * budget
        issues.append(Issue("error" if severe else "warning", "model_collision",
                            f"Supports overlap the model by {vol:.2f} mm3 (allowed for tips: "
                            f"{budget:.2f} mm3): a branch "
                            f"{'cuts through' if severe else 'may cut into'} the part", pos))


def _check_model_feet(mesh, tree, params, issues):
    """A MODEL_FOOT must actually touch the model (do not trust the tree's label)."""
    feet = [n for n in tree.nodes.values() if n.kind == NodeKind.MODEL_FOOT]
    if not feet or len(mesh.faces) == 0:
        return
    pts = np.array([n.position for n in feet])
    try:
        _, dist, _ = trimesh.proximity.closest_point(mesh, pts)
    except Exception:
        return
    tol = MODEL_FOOT_TOL + params.tip_penetration
    for n, d in zip(feet, dist):
        if d > tol:
            issues.append(Issue("error", "floating_support",
                                f"A support meant to stand on the model ends {d:.2f} mm away from it "
                                f"(z={n.position[2]:.2f} mm): it would print in the air",
                                n.position.copy(), n.contact_id))


def _check_model_mesh(mesh, issues):
    try:
        ok = mesh.is_watertight and mesh.is_winding_consistent
    except Exception:
        ok = False
    if not ok:
        issues.append(Issue("warning", "invalid_model_mesh",
                            "The model mesh is not closed or has inconsistent normals: overhang "
                            "detection may be wrong and the collision check is unreliable. "
                            "Run the mesh check and repair it, then regenerate."))


def _check_spacing(contacts, params, issues):
    act = [c for c in contacts if c.enabled]
    if len(act) < 2:
        return
    P = np.array([c.position for c in act], float)
    pairs = cKDTree(P).query_pairs(params.min_contact_distance * 0.999)
    reported = 0
    for i, j in sorted(pairs):
        if reported >= 20:
            break
        a, b = act[i], act[j]
        if not (a.manual or b.manual):
            continue  # automatic placement already enforces spacing
        d = float(np.linalg.norm(P[i] - P[j]))
        issues.append(Issue("warning", "contacts_too_close",
                            f"Contacts {a.id} and {b.id} are {d:.2f} mm apart "
                            f"(min {params.min_contact_distance:.2f}): tips may fuse",
                            P[i].copy(), a.id))
        reported += 1


def verify(mesh: trimesh.Trimesh, analysis: AnalysisResult, contacts: list[Contact],
           tree: SupportTree, support_mesh, params: SupportParams,
           progress: Optional[Progress] = None) -> list[Issue]:
    progress = progress or Progress()
    issues: list[Issue] = []
    progress.update(0.0, "Checking contacts")
    routed = _check_unrouted(contacts, tree, issues)
    progress.update(0.15, "Checking coverage")
    _check_regions(analysis, contacts, routed, params, issues)
    progress.update(0.4, "Checking trees")
    _check_model_mesh(mesh, issues)
    _check_tree(tree, params, issues)
    _check_model_feet(mesh, tree, params, issues)
    _check_spacing(contacts, params, issues)
    progress.update(0.55, "Checking meshes")
    _check_mesh(mesh, support_mesh, tree, contacts, params, issues)
    issues.append(Issue("info", "geometric_only",
                        "These checks are geometric. They do not prove the print will succeed; "
                        "turn off the slicer's own supports and inspect the preview."))
    progress.update(1.0, "Checks done")
    return issues

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
  support_floating_region  the supports sliced alone, layer by layer like a slicer: a
                       support part starting in the air (error) or overhanging beyond the
                       overhang angle (warning); what rests on the model / plate is fine
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


def _check_mesh(mesh, support_mesh, tree, contacts, params, issues, support_man=None):
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
        b = support_man if support_man is not None else trimesh_to_manifold(support_mesh)
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


# ---------------------------------------------------------------------------
# supports sliced alone, like a slicer does (support_floating_region)
# ---------------------------------------------------------------------------
def support_layer_regions(support_mesh, params: SupportParams, z_base: float = 0.0,
                          model_mesh=None, model_layers=None, progress: Optional[Progress] = None,
                          support_man=None):
    """Slice the support mesh on the print layer grid z_i = h (i + 1/2) and compare every
    layer with the one below, exactly like analysis.analyze does for the model: the
    supports are printed without support of their own, so any part of them that starts
    in the air (island) or grows beyond the printable overhang (overhang) fails.

    What holds a support layer up: the support layer below, the model layer below (feet
    and pads standing on the model; `model_layers` = analysis.layer_polygons on the same
    grid, or the model is sliced here), the model inside the same layer (penetrating
    tips) and the plate / raft for the first layer above z_base. Pieces smaller than
    min_region_area are ignored, like in the model analysis.

    Returns a list of dicts {kind: "island"|"overhang", z, area, polygon (shapely),
    position (3,)} or None when the support mesh cannot be sliced. support_man: the
    support mesh already converted to a manifold3d Manifold (optional)."""
    import math
    import manifold3d as mf
    import shapely
    from shapely.geometry import Polygon
    from .analysis import cs_components
    from .build import trimesh_to_manifold

    if support_mesh is None or len(getattr(support_mesh, "faces", [])) == 0:
        return []
    sm = support_man if support_man is not None else trimesh_to_manifold(support_mesh)
    if sm.status() != mf.Error.NoError or sm.is_empty():
        return None
    h = float(params.layer_height)
    d = params.max_unsupported_overhang if params.max_unsupported_overhang > 0 else \
        h * math.tan(math.radians(params.overhang_angle_deg))
    min_area = float(params.min_region_area)
    zmax = float(support_mesh.bounds[1, 2])
    i0 = max(0, int(math.floor(z_base / h - 0.5)) + 1)      # first layer centre above z_base
    i1 = int(math.ceil(zmax / h - 0.5))
    if i1 <= i0:
        return []

    model_cache: dict[int, object] = {}
    model_man = [None, False]

    def model_poly(i: int):
        """Model cross-section at layer i (shapely), empty geometry if none."""
        if i in model_cache:
            return model_cache[i]
        g = None
        if model_layers is not None and 0 <= i < len(model_layers):
            g = model_layers[i]
        elif model_mesh is not None and len(getattr(model_mesh, "faces", [])):
            if not model_man[1]:
                model_man[1] = True
                try:
                    mm = trimesh_to_manifold(model_mesh)
                    model_man[0] = mm if mm.status() == mf.Error.NoError else None
                except Exception:
                    model_man[0] = None
            if model_man[0] is not None:
                ps = [p for _, p in cs_components(model_man[0].slice(float(h * (i + 0.5))))]
                g = shapely.union_all(ps) if ps else None
        g = g if g is not None and not g.is_empty else Polygon()
        model_cache[i] = g
        return g

    out: list[dict] = []
    pending: list[float] = []                  # first z of every (small) island seen
    prev_cs = None                             # None: plate / raft below
    prev_polys = None                          # lazily computed components of prev_cs
    prev_label = None                          # None: every component of prev_cs is printed
    n = i1 - i0
    for i in range(i0, i1):
        if progress is not None and (i - i0) % 64 == 0:
            progress.update((i - i0) / max(n, 1), "Slicing the supports alone")
        z = h * (i + 0.5)
        cur = sm.slice(float(z))
        if prev_cs is None:                    # first layer: on the plate / raft
            prev_cs, prev_polys, prev_label = cur, None, None
            continue
        grown = prev_cs.offset(d, mf.JoinType.Miter, 2.0) if not prev_cs.is_empty() else prev_cs
        unsup = cur - grown if not grown.is_empty() else cur
        if (unsup.is_empty() or unsup.area() < 1e-9) and prev_label is None:
            # fast path (most layers): everything rests on the printed layer below
            prev_cs, prev_polys, prev_label = cur, None, None
            continue
        if prev_polys is None:
            prev_polys = [p for _, p in cs_components(prev_cs)]
        lab_prev = prev_label if prev_label is not None else np.full(len(prev_polys), -1, np.int64)
        comps = cs_components(cur)
        polys = [p for _, p in comps]
        label = np.full(len(comps), -2, np.int64)
        if prev_polys and polys:
            qi, pi = shapely.STRtree(prev_polys).query(polys, predicate="intersects")
            for a_, b_ in zip(qi, pi):
                lb = lab_prev[b_]
                if lb == -1:
                    label[a_] = -1
                elif label[a_] != -1:
                    label[a_] = lb if label[a_] < 0 else min(label[a_], lb)
        island_cs = []
        for k, (cs, poly) in enumerate(comps):
            if label[k] == -1:
                continue
            mp = model_poly(i - 1)
            if not mp.is_empty and poly.intersects(mp):
                label[k] = -1                      # stands on the model
                continue
            if label[k] == -2:
                pending.append(float(z))
                label[k] = len(pending) - 1
            island_cs.append(cs)
            if poly.area >= min_area:
                out.append(_region("island", pending[label[k]], poly))
                label[k] = -1                      # reported once, then counted as printed
        if island_cs:
            unsup = unsup - (mf.CrossSection.batch_boolean(island_cs, mf.OpType.Add)
                             if len(island_cs) > 1 else island_cs[0])
        if not unsup.is_empty() and unsup.area() >= min_area:
            for _, poly in cs_components(unsup):
                if poly.area < min_area:
                    continue
                hold = shapely.union_all([model_poly(i - 1).buffer(d), model_poly(i)])
                rest = poly.difference(hold) if not hold.is_empty else poly
                if rest.is_empty or rest.area < min_area:
                    continue
                out.append(_region("overhang", float(z), rest))
        prev_cs, prev_polys = cur, polys
        prev_label = label if np.any(label >= 0) else None
    return out
    edges = np.diff(np.concatenate([[0], cand.astype(np.int8), [0]]))
    runs = list(zip(np.nonzero(edges == 1)[0] + i0, np.nonzero(edges == -1)[0] + i0))
    pending: list[float] = []                  # first z of every (small) island seen
    done = 0
    total = int(cand.sum())

    for s_, e_ in runs:
        # state below the run: the layer under it is printed (grounded) or the plate
        if s_ == i0:
            prev_cs = None                     # plate / raft
        else:
            prev_cs = sm.slice(float(h * (s_ - 0.5)))
        prev_polys = [p for _, p in cs_components(prev_cs)] if prev_cs is not None else []
        prev_tree = shapely.STRtree(prev_polys) if prev_polys else None
        prev_label = np.full(len(prev_polys), -1, np.int64)
        for i in range(s_, e_):
            if progress is not None and done % 64 == 0:
                progress.update(done / max(total, 1), "Slicing the supports alone")
            done += 1
            z = h * (i + 0.5)
            cur = sm.slice(float(z))
            if prev_cs is None:                # first layer: on the plate / raft
                polys = [p for _, p in cs_components(cur)]
                prev_cs, prev_polys = cur, polys
                prev_tree = shapely.STRtree(polys) if polys else None
                prev_label = np.full(len(polys), -1, np.int64)
                continue
            comps = cs_components(cur)
            polys = [p for _, p in comps]
            label = np.full(len(comps), -2, np.int64)
            if prev_tree is not None and polys:
                qi, pi = prev_tree.query(polys, predicate="intersects")
                for a_, b_ in zip(qi, pi):
                    lb = prev_label[b_]
                    if lb == -1:
                        label[a_] = -1
                    elif label[a_] != -1:
                        label[a_] = lb if label[a_] < 0 else min(label[a_], lb)
            island_cs = []
            for k, (cs, poly) in enumerate(comps):
                if label[k] == -1:
                    continue
                mp = model_poly(i - 1)
                if not mp.is_empty and poly.intersects(mp):
                    label[k] = -1                  # stands on the model
                    continue
                if label[k] == -2:
                    pending.append(float(z))
                    label[k] = len(pending) - 1
                island_cs.append(cs)
                if poly.area >= min_area:
                    out.append(_region("island", pending[label[k]], poly))
                    label[k] = -1                  # reported once, then counted as printed
            support_cs = cur
            if island_cs:
                support_cs = cur - (mf.CrossSection.batch_boolean(island_cs, mf.OpType.Add)
                                    if len(island_cs) > 1 else island_cs[0])
            if not support_cs.is_empty() and not prev_cs.is_empty():
                unsup = support_cs - prev_cs.offset(d, mf.JoinType.Miter, 2.0)
                if not unsup.is_empty() and unsup.area() >= min_area:
                    for _, poly in cs_components(unsup):
                        if poly.area < min_area:
                            continue
                        hold = shapely.union_all([model_poly(i - 1).buffer(d), model_poly(i)])
                        rest = poly.difference(hold) if not hold.is_empty else poly
                        if rest.is_empty or rest.area < min_area:
                            continue
                        out.append(_region("overhang", float(z), rest))
            prev_cs, prev_label = cur, label
            prev_tree = shapely.STRtree(polys) if polys else None
    return out


def _region(kind: str, z: float, poly) -> dict:
    c = poly.representative_point()
    return {"kind": kind, "z": float(z), "area": float(poly.area), "polygon": poly,
            "position": np.array([c.x, c.y, float(z)])}


def _check_support_layers(mesh, analysis, tree, support_mesh, params, issues, progress=None,
                          support_man=None):
    """support_floating_region: the supports, sliced alone, must be printable layer by
    layer (error: island starting in the air; warning: overhang beyond the angle)."""
    z_base = float(getattr(tree, "z_base", 0.0))
    layers = None
    if analysis is not None and analysis.layer_polygons is not None and \
            abs(float(analysis.layer_height) - float(params.layer_height)) < 1e-9:
        layers = analysis.layer_polygons
    regs = support_layer_regions(support_mesh, params, z_base, mesh, layers, progress, support_man)
    if not regs:
        return
    # one issue per cluster (a feature spans several layers)
    pos = np.array([r["position"] for r in regs])
    link = max(1.0, 4 * params.layer_height)
    parent = list(range(len(regs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in cKDTree(pos).query_pairs(link):
        if regs[a]["kind"] == regs[b]["kind"]:
            parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for k in range(len(regs)):
        groups.setdefault(find(k), []).append(k)
    ordered = sorted(groups.values(), key=lambda g: (regs[g[0]]["kind"] != "island",
                                                      -sum(regs[k]["area"] for k in g)))
    shown = 0
    for g in ordered:
        kind = regs[g[0]]["kind"]
        area = sum(regs[k]["area"] for k in g)
        z0 = min(regs[k]["z"] for k in g)
        z1 = max(regs[k]["z"] for k in g)
        p = regs[min(g, key=lambda k: regs[k]["z"])]["position"].copy()
        if shown >= 30:
            break
        shown += 1
        if kind == "island":
            msg = (f"Support part starting in the air at z={z0:.2f} mm ({area:.2f} mm2): sliced "
                   "without the model it has nothing below it and would print in the air")
        else:
            msg = (f"Support overhang beyond {params.overhang_angle_deg:.0f} deg between z={z0:.2f} "
                   f"and {z1:.2f} mm ({area:.2f} mm2 over {len(g)} layer(s)): it may sag or fail")
        issues.append(Issue("error" if kind == "island" else "warning",
                            "support_floating_region", msg, p))


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
    support_man = None
    if support_mesh is not None and len(getattr(support_mesh, "faces", [])):
        try:
            from .build import trimesh_to_manifold
            support_man = trimesh_to_manifold(support_mesh)   # shared by both checks
        except Exception:
            support_man = None
    _check_mesh(mesh, support_mesh, tree, contacts, params, issues, support_man)
    progress.update(0.75, "Slicing the supports alone")
    _check_support_layers(mesh, analysis, tree, support_mesh, params, issues,
                          progress.sub(0.75, 1.0), support_man)
    issues.append(Issue("info", "geometric_only",
                        "These checks are geometric. They do not prove the print will succeed; "
                        "turn off the slicer's own supports and inspect the preview."))
    progress.update(1.0, "Checks done")
    return issues

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
  fragile_support      a support part may bend or snap while printing (warning): a section
                       carrying too much height for its diameter, a column standing on the
                       model on a pad too small for its height, a brace too thin / too long
  hard_to_remove       supports hard to remove or likely to leave visible marks (warning):
                       a support standing on the model in an enclosed / narrow place, or
                       contacts so dense that their marks cover a large part of a small area

Numbers useful to compare configurations are computed by support_stats() (stored in
GenerationResult.stats by the pipeline).
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

# --- fragile_support (rules of thumb, not calibrated by print tests; docs/ALGORITHM.md) ---
# Section slenderness = lever / diameter: lever = height of everything the section carries
# (top of its subtree minus the section height), the arm of the sideways force the nozzle
# applies while printing the top of the support (bending stress grows with lever / d^3,
# deflection with lever^3 / d^4).
THIN_LINE_WIDTHS = 3.0    # below 3 line widths a branch is a single perimeter loop, no infill
SLENDER_MAX = 25.0        # sections >= THIN_LINE_WIDTHS line widths (examples max: 23.1)
SLENDER_MAX_THIN = 10.0   # thinner sections (examples max: 7.5)
PAD_SLENDER_MAX = 20.0    # column standing on the model: height / pad diameter
BRACE_SLENDER_MAX = 20.0  # brace length / diameter (examples max 16.4; judged fragile: 28)
BRACE_MIN_LINE_WIDTHS = 2.0   # a brace thinner than 2 line widths has no closed loop
GROUP_LINK = 10.0         # mm: findings of neighbouring trees closer than this are grouped
# --- hard_to_remove ---
ACCESS_RANGE = 20.0       # mm: a direction is open if no model surface within this range
ACCESS_MIN_OPEN = 0.10    # open fraction below which a support is enclosed (figurine min: 0.34)
WEDGE_GAP = 2.5           # mm: model-to-model horizontal width around a support (examples min: 3.1)
MARK_RADIUS = 1.5         # mm: radius of the disc in which mark coverage is measured
MARK_COVERAGE_MAX = 0.30  # fraction of that disc covered by contact marks (examples max: 0.24)
MARK_MIN_CONTACTS = 4     # a dense spot needs at least this many contacts
PLA_DENSITY = 1.24        # g/cm3, for the mass estimate in support_stats


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
        if params.raft_enabled:
            # the foot is part of one raft plate (build.raft_polygon joins the pieces): the
            # anchoring is the raft, not the foot disc alone
            issues.append(Issue("info", "tall_tree_on_raft",
                                f"Tree of height {h:.1f} mm stands on a {2 * fr:.1f} mm foot, "
                                "anchored by the raft", rn.position.copy()))
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


# ---------------------------------------------------------------------------
# fragile supports (fragile_support)
# ---------------------------------------------------------------------------
def _subtree_info(tree: SupportTree):
    """Walk the tree from its roots. Returns (children, order (parents first), root of
    each node, ztop: highest z in the subtree of each node, cid: a contact id of the
    subtree or None). Nodes not reachable from a root (malformed tree) are left out."""
    ch: dict[int, list[int]] = {i: [] for i in tree.nodes}
    stack = []
    for n in tree.nodes.values():
        if n.parent is not None and n.parent in tree.nodes and n.parent != n.id:
            ch[n.parent].append(n.id)
        else:                          # root (a dangling parent counts as a root here)
            stack.append(n.id)
    order: list[int] = []
    root: dict[int, int] = {i: i for i in stack}
    seen: set[int] = set()
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        order.append(i)
        for c in ch[i]:
            root[c] = root[i]
            stack.append(c)
    ztop: dict[int, float] = {}
    cid: dict[int, Optional[int]] = {}
    for i in reversed(order):
        n = tree.nodes[i]
        z = float(n.position[2])
        c = n.contact_id if n.kind == NodeKind.TIP else None
        for k in ch[i]:
            if k in ztop:
                z = max(z, ztop[k])
                if c is None:
                    c = cid[k]
        ztop[i], cid[i] = z, c
    return ch, order, root, ztop, cid


def section_slenderness(tree: SupportTree, params: SupportParams) -> list[dict]:
    """Slenderness of every support section that can bend while printing.

    For every branch / trunk node (the section of the tube there) and every pad standing
    on the model: lever = height of the top of everything above it (its subtree) minus
    the height of the section, diameter = 2 x node radius (pad: the on-model pad), and
    slenderness = lever / diameter. The nozzle pushes sideways on the top of the
    support while it prints it, the section works as the root of a cantilever of that
    length. The radius is linear along a segment and so is the lever: the largest ratio
    of a segment is at one of its ends, which is why nodes are enough.

    Plate feet are not sections (their disc is an adhesion problem: unstable_trunk). For
    the trunk segment standing on a foot, the section is taken on top of the collar
    (foot_height + collar_height above the foot), which reinforces what is below.
    Tip cones are not sections (short, they rest on the model above once printed).

    Each record: node, root, kind ("section" | "pad"), lever, diameter, slenderness,
    limit (the threshold that applies), position (3,), contact_id."""
    if not tree.nodes:
        return []
    P = params
    ch, order, root, ztop, cid = _subtree_info(tree)
    thin_d = THIN_LINE_WIDTHS * P.line_width
    collar_top = P.foot_height + P.collar_height
    out = []
    for i in order:
        n = tree.nodes[i]
        if n.kind in (NodeKind.TIP, NodeKind.FOOT):
            continue
        d = 2.0 * float(n.radius)
        z = float(n.position[2])
        if n.kind == NodeKind.MODEL_FOOT:
            kind, limit = "pad", PAD_SLENDER_MAX
        else:
            kind = "section"
            limit = SLENDER_MAX_THIN if d < thin_d - 1e-9 else SLENDER_MAX
            par = tree.nodes.get(n.parent) if n.parent is not None else None
            if par is not None and par.kind == NodeKind.FOOT:
                # the collar reinforces the trunk up to collar_top: first free section there
                z = min(max(z, float(par.position[2]) + collar_top), ztop[i])
        lever = ztop[i] - z
        if lever <= 1e-9 or d <= 1e-9:
            continue
        out.append({"node": i, "root": root[i], "kind": kind, "lever": float(lever),
                    "diameter": d, "slenderness": float(lever / d), "limit": float(limit),
                    "position": np.array([n.position[0], n.position[1], z], float),
                    "contact_id": cid[i]})
    return out


def _check_fragile(tree: SupportTree, params: SupportParams, issues, sections=None):
    """fragile_support: the worst section of every tree (if beyond its limit) and every
    brace too thin or too long for its diameter."""
    P = params
    lw = P.line_width
    thin_d = THIN_LINE_WIDTHS * lw
    recs = sections if sections is not None else section_slenderness(tree, P)
    worst: dict[int, dict] = {}
    for r in recs:
        if r["slenderness"] <= r["limit"]:
            continue
        w = worst.get(r["root"])
        if w is None or r["slenderness"] / r["limit"] > w["slenderness"] / w["limit"]:
            worst[r["root"]] = r
    found = []
    # one issue per group of neighbouring trees with the same problem (a row of supports
    # standing on the same ledge is one finding, not twenty)
    for kind in ("pad", "section"):
        rs = [r for r in worst.values() if r["kind"] == kind]
        if not rs:
            continue
        for g in _clusters(np.array([r["position"] for r in rs]), GROUP_LINK):
            r = max((rs[k] for k in g), key=lambda q: q["slenderness"] / q["limit"])
            more = f" ({len(g)} supports like this nearby)" if len(g) > 1 else ""
            if kind == "pad":
                msg = (f"Support standing on the model is {r['lever']:.1f} mm tall on a "
                       f"{r['diameter']:.2f} mm pad (height / pad {r['slenderness']:.0f} > "
                       f"{r['limit']:.0f}){more}: it may snap off the model while printing; "
                       "prefer a route to the plate, or check it in the slicer preview")
            else:
                thin = r["diameter"] < thin_d - 1e-9
                what = (f"thinner than {THIN_LINE_WIDTHS:.0f} line widths ({thin_d:.2f} mm)"
                        if thin else f"of {THIN_LINE_WIDTHS:.0f} line widths or more")
                msg = (f"Support section {r['diameter']:.2f} mm thick carries {r['lever']:.1f} mm "
                       f"of support above it (slenderness {r['slenderness']:.1f} > "
                       f"{r['limit']:.0f} for sections {what}){more}: it may bend or snap while "
                       "printing (consider thicker branches or braces)")
            found.append((r["slenderness"] / r["limit"],
                          Issue("warning", "fragile_support", msg, r["position"].copy(),
                                r["contact_id"])))

    for p0, p1, rb in getattr(tree, "braces", []) or []:
        p0 = np.asarray(p0, float)
        p1 = np.asarray(p1, float)
        L = float(np.linalg.norm(p1 - p0))
        d = 2.0 * float(rb)
        if d <= 0 or L <= 0:
            continue
        too_thin = d < BRACE_MIN_LINE_WIDTHS * lw - 1e-9
        too_long = L / d > BRACE_SLENDER_MAX
        if not (too_thin or too_long):
            continue
        why = []
        if too_thin:
            why.append(f"thinner than {BRACE_MIN_LINE_WIDTHS:.0f} line widths "
                       f"({BRACE_MIN_LINE_WIDTHS * lw:.2f} mm)")
        if too_long:
            why.append(f"length / diameter {L / d:.0f} > {BRACE_SLENDER_MAX:.0f}")
        score = max(L / d / BRACE_SLENDER_MAX, BRACE_MIN_LINE_WIDTHS * lw / d)
        found.append((score, Issue("warning", "fragile_support",
                                   f"Brace {d:.2f} mm thick and {L:.1f} mm long ({'; '.join(why)}): "
                                   "it may break while printing or when handled (consider a "
                                   "thicker brace_diameter or a shorter brace_max_length)",
                                   0.5 * (p0 + p1))))
    found.sort(key=lambda t: -t[0])
    issues.extend(i for _, i in found[:30])


# ---------------------------------------------------------------------------
# supports hard to remove (hard_to_remove)
# ---------------------------------------------------------------------------
def _access_directions() -> np.ndarray:
    """Unit directions: 16 azimuths at elevations -30, 0, 30, 60 deg, plus straight up.
    Rows 16..31 are the horizontal ones (k and k + 8 are opposite)."""
    import math
    out = []
    for el in (-30.0, 0.0, 30.0, 60.0):
        e = math.radians(el)
        for k in range(16):
            a = k * math.pi / 8 + 1e-3        # off the axes: no ray runs along a face edge
            out.append([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    out.append([0.0, 0.0, 1.0])
    return np.array(out)


ACCESS_GRID_RES = 0.3      # mm: column grid used to march the access rays


def _grid_inside(grid, P: np.ndarray) -> np.ndarray:
    """Vectorised ModelGrid.inside for an (N, 3) array of points."""
    i, j, ok = grid.cell_index(P[:, :2])
    cell = j * grid.nx + i
    a = np.searchsorted(grid.iv_cell, cell, side="left")
    b = np.searchsorted(grid.iv_cell, cell, side="right")
    out = np.zeros(len(P), bool)
    kmax = int((b - a).max()) if len(P) else 0
    for k in range(kmax):
        idx = a + k
        v = ok & (idx < b)
        if not v.any():
            break
        ii = idx[v]
        z = P[v, 2]
        out[v] |= (grid.iv_lo[ii] <= z) & (grid.iv_hi[ii] >= z)
    return out


def _ray_distances(grid, origins: np.ndarray, dirs: np.ndarray, max_dist: float) -> np.ndarray:
    """Distance along each (origin, dir) ray to the first model voxel column interval,
    marched every half grid cell; inf if none within max_dist. (len(origins), len(dirs))."""
    step = 0.5 * grid.res
    s = np.arange(step, max_dist + 1e-9, step)
    pts = origins[:, None, None, :] + dirs[None, :, None, :] * s[None, None, :, None]
    ins = _grid_inside(grid, pts.reshape(-1, 3)).reshape(len(origins), len(dirs), len(s))
    return np.where(ins.any(axis=2), s[np.argmax(ins, axis=2)], np.inf)


def _access_samples(tree, ch, sub_ids, pad) -> np.ndarray:
    """Points of a support standing on the model where a tool must reach: the support
    nodes nearest to half and nine tenths of the column height (never the pad itself:
    rays grazing the surface it stands on would all hit it)."""
    pts = np.array([tree.nodes[i].position for i in sub_ids
                    if tree.nodes[i].kind != NodeKind.TIP]).reshape(-1, 3)
    z0 = float(pad.position[2])
    ztop = max(float(tree.nodes[i].position[2]) for i in sub_ids)
    out = []
    for f in (0.5, 0.9):
        zq = z0 + f * (ztop - z0)
        if len(pts) > 1:
            k = int(np.argmin(np.abs(pts[:, 2] - zq) + 1e3 * (pts[:, 2] <= z0 + 1e-6)))
            p = pts[k].copy()
            if p[2] <= z0 + 1e-6:
                p = np.array([pad.position[0], pad.position[1], zq])
        else:
            p = np.array([pad.position[0], pad.position[1], zq])
        out.append(p)
    return np.unique(np.round(np.array(out), 6), axis=0)


def _clusters(points: np.ndarray, link: float) -> list[list[int]]:
    parent = list(range(len(points)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    if len(points) > 1:
        for a, b in cKDTree(points).query_pairs(link):
            parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for k in range(len(points)):
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


def _check_removal(mesh, tree: SupportTree, params: SupportParams, issues):
    """hard_to_remove, (1) supports standing on the model in enclosed or narrow places,
    (2) contacts so dense that their marks cover a large part of a small area."""
    P = params
    if not tree.nodes:
        return
    ch, order, root, ztop, cid = _subtree_info(tree)
    pads = [tree.nodes[i] for i in order if tree.nodes[i].kind == NodeKind.MODEL_FOOT
            and tree.nodes[i].parent is None]

    # (1) access to supports standing on the model --------------------------------------
    if pads and len(getattr(mesh, "faces", [])):
        members: dict[int, list[int]] = {}
        for i in order:
            members.setdefault(root[i], []).append(i)
        from .tree import ModelGrid
        try:
            grid = ModelGrid(mesh, ACCESS_GRID_RES, margin=1.0)
        except Exception:
            grid = None
        dirs = _access_directions()
        flagged = []      # (pad, open fraction, width, cid)
        for pad in (pads if grid is not None else []):
            S = _access_samples(tree, ch, members.get(pad.id, [pad.id]), pad)
            dist = _ray_distances(grid, S, dirs, ACCESS_RANGE)
            open_frac = float(np.isinf(dist).mean())
            hz = dist[:, 16:32]
            width = float(np.min(hz[:, :8] + hz[:, 8:]))
            if open_frac < ACCESS_MIN_OPEN or width < WEDGE_GAP:
                flagged.append((pad, open_frac, width, cid.get(pad.id)))
        if flagged:
            pts = np.array([f[0].position for f in flagged])
            for g in _clusters(pts, GROUP_LINK):
                fs = [flagged[k] for k in g]
                open_min = min(f[1] for f in fs)
                w_min = min(f[2] for f in fs)
                why = []
                if open_min < ACCESS_MIN_OPEN:
                    why.append(f"enclosed: {100 * open_min:.0f}% of directions free of the model "
                               f"within {ACCESS_RANGE:.0f} mm")
                if w_min < WEDGE_GAP:
                    why.append(f"wedged in a {w_min:.1f} mm gap between parts of the model")
                first = fs[0]
                issues.append(Issue(
                    "warning", "hard_to_remove",
                    f"{len(fs)} support(s) standing on the model near z={first[0].position[2]:.1f} mm "
                    f"are hard to reach ({'; '.join(why)}): removing them may break the part or "
                    "leave pieces inside; consider reorienting the model",
                    first[0].position.copy(), first[3]))

    # (2) dense contact marks ------------------------------------------------------------
    marks = [n for n in tree.nodes.values() if n.kind in (NodeKind.TIP, NodeKind.MODEL_FOOT)]
    if len(marks) < MARK_MIN_CONTACTS:
        return
    X = np.array([n.position for n in marks], float)
    dm = np.maximum(2.0 * np.array([n.radius for n in marks], float), P.line_width)
    A = np.pi * (dm / 2) ** 2
    disc = np.pi * MARK_RADIUS ** 2
    nb = cKDTree(X).query_ball_point(X, MARK_RADIUS)
    cov = np.array([A[l].sum() for l in nb]) / disc
    cnt = np.array([len(l) for l in nb])
    bad = np.nonzero((cov > MARK_COVERAGE_MAX) & (cnt >= MARK_MIN_CONTACTS))[0]
    if not len(bad):
        return
    shown = 0
    for g in sorted(_clusters(X[bad], MARK_RADIUS), key=lambda g: -max(cov[bad[k]] for k in g)):
        if shown >= 20:
            break
        shown += 1
        k = bad[max(g, key=lambda k: cov[bad[k]])]
        n = marks[k]
        c = n.contact_id if n.kind == NodeKind.TIP else cid.get(n.id)
        issues.append(Issue(
            "warning", "hard_to_remove",
            f"{cnt[k]} contacts within {MARK_RADIUS:.1f} mm: their marks cover "
            f"{100 * cov[k]:.0f}% of that area (more than {100 * MARK_COVERAGE_MAX:.0f}%), the "
            "surface will look scarred; remove some contacts or use thinner tips",
            X[k].copy(), c))


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------
def support_stats(mesh, contacts, tree: SupportTree, support_mesh, raft_mesh,
                  issues: list[Issue], params: SupportParams, sections=None) -> dict:
    """Numbers to judge and compare generations (GenerationResult.stats).

    support_volume_mm3  volume of the merged support mesh (raft excluded)
    support_mass_g      support_volume_mm3 x PLA density (1.24 g/cm3), solid: an upper
                        bound, the slicer prints walls + sparse infill in thick trunks
    raft_volume_mm3, raft_mass_g   same for the raft (0 without raft)
    contact_area_mm2    sum of the areas where supports touch the model (tip discs and
                        pads on the model, pi r^2 of their node radius): proxy of the marks
    model_contacts      number of places where supports touch the model (tips + pads)
    tip_contacts, model_feet       the two parts of model_contacts
    max_tree_height_mm  highest top of a tree above its root (plate foot or pad)
    max_slenderness     largest section slenderness (lever / diameter, see
                        section_slenderness; pads included)
    max_fragility       largest slenderness / limit (> 1: fragile_support reported)
    issues_by_code      {code: count} of the issues (all severities)"""
    def vol(m):
        try:
            return float(m.volume) if m is not None and len(getattr(m, "faces", [])) else 0.0
        except Exception:
            return 0.0
    sv, rv = vol(support_mesh), vol(raft_mesh)
    nodes = list(tree.nodes.values()) if tree is not None else []
    tips = [n for n in nodes if n.kind == NodeKind.TIP]
    pads = [n for n in nodes if n.kind == NodeKind.MODEL_FOOT]
    area = float(sum(np.pi * float(n.radius) ** 2 for n in tips + pads))
    height = 0.0
    if nodes:
        _, order, root, ztop, _ = _subtree_info(tree)
        for r in {root[i] for i in order}:
            height = max(height, ztop[r] - float(tree.nodes[r].position[2]))
    recs = sections if sections is not None else (section_slenderness(tree, params) if nodes else [])
    by_code: dict[str, int] = {}
    for i in issues:
        by_code[i.code] = by_code.get(i.code, 0) + 1
    return {
        "support_volume_mm3": sv,
        "support_mass_g": sv * PLA_DENSITY / 1000.0,
        "raft_volume_mm3": rv,
        "raft_mass_g": rv * PLA_DENSITY / 1000.0,
        "contact_area_mm2": area,
        "model_contacts": len(tips) + len(pads),
        "tip_contacts": len(tips),
        "model_feet": len(pads),
        "max_tree_height_mm": float(height),
        "max_slenderness": float(max((r["slenderness"] for r in recs), default=0.0)),
        "max_fragility": float(max((r["slenderness"] / r["limit"] for r in recs), default=0.0)),
        "issues_by_code": by_code,
    }


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
    _check_fragile(tree, params, issues)
    _check_removal(mesh, tree, params, issues)
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

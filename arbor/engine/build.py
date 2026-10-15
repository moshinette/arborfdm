"""Meshing of the support tree: manifold union of cones, junction spheres, feet, braces.

Every solid is a manifold3d primitive (Manifold.cylinder / Manifold.sphere) placed with an
affine transform; everything is merged with one Manifold.batch_boolean(Add) call per
independent group so the result is watertight by construction.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

import manifold3d as mf
import numpy as np
import trimesh

from .params import SupportParams
from .tree import JOINT_INSET, printable_angle_deg
from .types import NodeKind, Progress, SupportTree

_EMPTY = trimesh.Trimesh(np.zeros((0, 3)), np.zeros((0, 3), np.int64), process=False)


def _frame(d: np.ndarray) -> np.ndarray:
    """3x3 rotation whose third column is the unit vector d."""
    z = d / np.linalg.norm(d)
    a = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = np.cross(a, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def cone_between(p0, p1, r0: float, r1: float, segs: int) -> Optional[mf.Manifold]:
    """Truncated cone from p0 (radius r0) to p1 (radius r1)."""
    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    d = p1 - p0
    h = float(np.linalg.norm(d))
    if h < 1e-6:
        return None
    r0 = max(float(r0), 1e-3)
    r1 = max(float(r1), 1e-3)
    c = mf.Manifold.cylinder(h, r0, r1, segs)
    M = np.zeros((3, 4))
    M[:, :3] = _frame(d)
    M[:, 3] = p0
    return c.transform(M)


def sphere_at(p, r: float, segs: int) -> mf.Manifold:
    # turned by half a facet about Z: a tube ring of the same radius around the same
    # centre (bend breaks: the next chain starts with the node radius) otherwise shares
    # vertices exactly with the sphere's equator and the union leaves zero-volume
    # slivers. Half a facet keeps the sphere mirror symmetric (symmetric models).
    return mf.Manifold.sphere(float(r), segs).rotate((0.0, 0.0, 180.0 / segs)) \
        .translate(tuple(float(v) for v in p))


def manifold_to_trimesh(m: mf.Manifold) -> trimesh.Trimesh:
    if m.status() != mf.Error.NoError:
        raise RuntimeError(f"manifold error: {m.status()}")
    if m.is_empty():
        return _EMPTY.copy()
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    f = np.asarray(mesh.tri_verts, dtype=np.int64)
    # process=False: manifold3d output is already indexed and manifold; trimesh's merge
    # of nearly coincident vertices would pinch thin features and break watertightness
    return trimesh.Trimesh(v, f, process=False)


def trimesh_to_manifold(t: trimesh.Trimesh) -> mf.Manifold:
    v = np.ascontiguousarray(t.vertices, dtype=np.float32)
    f = np.ascontiguousarray(t.faces, dtype=np.uint32)
    return mf.Manifold(mf.Mesh(vert_properties=v, tri_verts=f))


def joint_at(p, R: float, below, r_below_at, phi_deg: float, segs: int) -> mf.Manifold:
    """Sphere of radius R at p (junction, ball tip), with its underside made printable.

    A sphere wider than the branch under it hangs out beyond the overhang angle (its
    bottom is a flat dome). The sphere is merged (convex hull) with a smaller sphere
    placed down the branch axis, inside the branch, far enough that the conical skirt
    between them leans at most phi from vertical: the bottom of the joint is then
    buried in the branch instead of floating around it.

    below: the node under p (parent); r_below_at(s): radius of the branch at distance
    s from p along it."""
    p = np.asarray(p, float)
    ball = sphere_at(p, R, segs)
    if below is None:
        return ball
    v = np.asarray(below, float) - p
    L = float(np.linalg.norm(v))
    if L < 1e-6:
        return ball
    u = v / L
    theta = math.atan2(float(np.linalg.norm(u[:2])), max(-float(u[2]), 1e-12))  # tilt of the branch
    room = math.radians(phi_deg) - theta
    r0, rL = r_below_at(0.0), r_below_at(L)
    k = (rL - r0) / L                      # < 0: the branch gets thinner downwards
    if R <= r0 + 1e-6 and k >= -1e-9:
        return ball                        # the branch is at least as wide: covered
    sin_room = math.sin(max(room, 0.0))
    # skirt end: a sphere JOINT_INSET thinner than the branch at distance s down its
    # axis; smallest s with (R - r(s) + inset) / s <= sin(room) (skirt half-angle). A
    # branch thinning downwards is followed to its lower end.
    if k < 0:
        s_ = L
    else:
        s_ = (R - r0 + JOINT_INSET) / (sin_room + k) if sin_room + k > 1e-6 else math.inf
    if s_ >= L:
        # the skirt reaches the node below: keep the sphere small enough for the skirt
        # to stay printable (large junction_radius_factor, steep or thinning branch;
        # tree._limit_widening keeps node radii within this bound)
        s_ = L
        R_ok = rL - JOINT_INSET + L * sin_room
        if R > R_ok:
            R = max(R_ok, 1e-3)
            ball = sphere_at(p, R, segs)
    r_s = max(min(r_below_at(s_) - JOINT_INSET, R), 1e-3)
    low = sphere_at(p + u * s_, r_s, segs)
    return mf.Manifold.batch_hull([ball, low])


def _sphere_segs(segs: int) -> int:
    return max(8, int(math.ceil(segs / 4.0)) * 4)


@lru_cache(maxsize=None)
def _sphere_inradius(segs: int) -> float:
    """Distance from the centre to the nearest facet of Manifold.sphere(1, segs)."""
    m = mf.Manifold.sphere(1.0, segs).to_mesh()
    v = np.asarray(m.vert_properties, float)[:, :3]
    f = np.asarray(m.tri_verts, np.int64)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1)[:, None]
    return float(np.abs((n * a).sum(axis=1)).min())


def joint_radius_factors(params: SupportParams) -> tuple[float, float]:
    """(junction, bend) sphere radius / node radius. A chain starts at a junction or a
    bend with a ring of the node radius around the node; the faceted sphere must
    contain it strictly (a ring vertex on a facet or a vertex leaves zero-volume
    slivers in the union), hence at least 1 / inradius of the faceted sphere."""
    cover = 1.01 / _sphere_inradius(_sphere_segs(int(params.circle_segments)))
    return max(float(params.junction_radius_factor), cover), cover


def tube(points: np.ndarray, radii: np.ndarray, segs: int) -> Optional[mf.Manifold]:
    """One closed tube through points (bottom to top) with linearly varying radius.

    Rings are perpendicular to the bisector of the adjacent segments and use a parallel
    transported frame (no twist). Callers split chains at sharp bends so rings never
    overlap. Returns None if manifold3d rejects the mesh."""
    pts = np.asarray(points, float)
    rad = np.maximum(np.asarray(radii, float), 1e-3)
    n = len(pts)
    if n < 2:
        return None
    seg = np.diff(pts, axis=0)
    L = np.linalg.norm(seg, axis=1)
    if np.any(L < 1e-6):
        return None
    seg /= L[:, None]
    dirs = np.empty((n, 3))
    dirs[0], dirs[-1] = seg[0], seg[-1]
    if n > 2:
        b = seg[:-1] + seg[1:]
        dirs[1:-1] = b / np.linalg.norm(b, axis=1)[:, None]
    ang = np.arange(segs) * (2 * math.pi / segs)
    ca, sa = np.cos(ang), np.sin(ang)
    F0 = _frame(dirs[0])
    u, v = F0[:, 0], F0[:, 1]
    verts = np.empty((n * segs + 2, 3))
    for i in range(n):
        if i > 0:
            u = u - np.dot(u, dirs[i]) * dirs[i]
            u /= np.linalg.norm(u)
            v = np.cross(dirs[i], u)
        # circular ring in the bisector plane (bends are <= 25 deg, so the section of
        # the tube is pinched by at most 1 - cos(12.5 deg) = 2.4 %)
        verts[i * segs:(i + 1) * segs] = pts[i] + rad[i] * (np.outer(ca, u) + np.outer(sa, v))
    verts[n * segs] = pts[0]
    verts[n * segs + 1] = pts[-1]
    k = np.arange(segs)
    k1 = (k + 1) % segs
    faces = []
    for i in range(n - 1):
        a0, b0 = i * segs + k, i * segs + k1
        a1, b1 = a0 + segs, b0 + segs
        faces.append(np.column_stack([a0, b0, b1]))
        faces.append(np.column_stack([a0, b1, a1]))
    c0, c1 = n * segs, n * segs + 1
    faces.append(np.column_stack([np.full(segs, c0), k1, k]))
    top = (n - 1) * segs
    faces.append(np.column_stack([np.full(segs, c1), top + k, top + k1]))
    f = np.vstack(faces).astype(np.uint32)
    m = mf.Manifold(mf.Mesh(vert_properties=np.ascontiguousarray(verts, np.float32),
                            tri_verts=np.ascontiguousarray(f)))
    if m.status() != mf.Error.NoError or m.is_empty():
        return None
    if m.volume() < 0:
        return None
    return m


def _chains(tree: SupportTree, ch: dict, max_bend_deg: float = 25.0):
    """Split the tree into unbranched chains [bottom, ..., top] (node ids). A chain starts
    at a root or a junction and stops at a tip, a junction or a bend that is too sharp
    for a single lofted tube (ring overlap)."""
    nodes = tree.nodes
    cos_lim = math.cos(math.radians(max_bend_deg))
    out = []
    starts = [(r.id, c) for r in tree.roots() for c in ch[r.id]]
    while starts:
        b, c = starts.pop()
        chain = [b, c]
        cur = c
        while ch[cur]:
            # continue along the thickest child (the main stem); the others start chains
            kids = sorted(ch[cur], key=lambda i: -nodes[i].radius)
            nxt = kids[0]
            d0 = nodes[cur].position - nodes[chain[-2]].position
            d1 = nodes[nxt].position - nodes[cur].position
            l0, l1 = float(np.linalg.norm(d0)), float(np.linalg.norm(d1))
            if l0 < 1e-6 or l1 < 1e-6:
                break
            cs = float(np.dot(d0, d1) / (l0 * l1))
            if cs < cos_lim:
                break
            half = 0.5 * math.acos(min(1.0, cs))
            if 2.0 * nodes[cur].radius * math.tan(half) > 0.8 * min(l0, l1):
                break
            chain.append(nxt)
            for k in kids[1:]:
                starts.append((cur, k))
            cur = nxt
        out.append(chain)
        for k in ch[cur]:
            starts.append((cur, k))
    return out


def tree_solids(tree: SupportTree, params: SupportParams, progress: Optional[Progress] = None,
                tubes: bool = True) -> list[mf.Manifold]:
    """Solids of the tree, not yet merged.

    tubes=True: one lofted tube per unbranched chain (far fewer boolean operands);
    tubes=False: one truncated cone (Manifold.cylinder) per segment. Both add spheres at
    junctions (radius x junction_radius_factor) and at chain breaks, feet with collars,
    on-model pads and braces."""
    P = params
    segs = int(P.circle_segments)
    ssegs = _sphere_segs(segs)
    phi = printable_angle_deg(P)
    f_junction, f_bend = joint_radius_factors(P)
    nodes = tree.nodes
    ch = tree.children()
    out: list[mf.Manifold] = []
    foot_radius = getattr(tree, "foot_radius", {})
    z_base = getattr(tree, "z_base", P.raft_thickness if P.raft_enabled else 0.0)
    ball = P.tip_shape == "ball"

    def seg_radii(child_id: int) -> tuple[float, float]:
        """(bottom, top) radius of the segment child -> parent."""
        n = nodes[child_id]
        par = nodes[n.parent]
        r_top = n.radius * (0.6 if (ball and n.kind == NodeKind.TIP) else 1.0)
        r_low = n.radius if par.kind == NodeKind.FOOT else par.radius
        return r_low, r_top

    def joint(n, R: float):
        """Sphere of radius R at node n whose underside is buried in the branch below."""
        if n.parent is None:
            return sphere_at(n.position, R, ssegs)
        rl, rt = seg_radii(n.id)
        L = float(np.linalg.norm(nodes[n.parent].position - n.position))
        return joint_at(n.position, R, nodes[n.parent].position,
                        lambda s_: rt + (rl - rt) * min(s_ / max(L, 1e-9), 1.0), phi, ssegs)

    if tubes:
        chains = _chains(tree, ch)
        for ci, chain in enumerate(chains):
            if progress is not None and ci % 500 == 0:
                progress.update(0.3 * ci / max(len(chains), 1), "Building support solids")
            pts = np.array([nodes[i].position for i in chain])
            rad = np.empty(len(chain))
            for k in range(1, len(chain)):
                rl, rt = seg_radii(chain[k])
                rad[k] = rt
                if k == 1:
                    rad[0] = rl
            m = tube(pts, rad, segs) if len(chain) > 2 else None
            if m is not None:
                out.append(m)
            else:
                for k in range(1, len(chain)):
                    rl, rt = seg_radii(chain[k])
                    s_ = cone_between(pts[k - 1], pts[k], rl if k == 1 else rad[k - 1], rt, segs)
                    if s_ is not None:
                        out.append(s_)
            top = nodes[chain[-1]]
            if len(ch[top.id]) == 1 and top.kind in (NodeKind.BRANCH, NodeKind.TRUNK):
                out.append(joint(top, top.radius * f_bend))   # bend break
            if len(ch[top.id]) > 1 and top.kind in (NodeKind.BRANCH, NodeKind.TRUNK):
                # the chain stopped at a junction because of a sharp bend: the junction
                # sphere is added below; nothing else to do
                pass
    else:
        for n in nodes.values():
            if n.parent is None:
                continue
            rl, rt = seg_radii(n.id)
            s_ = cone_between(nodes[n.parent].position, n.position, rl, rt, segs)
            if s_ is not None:
                out.append(s_)
            if len(ch[n.id]) == 1 and n.kind in (NodeKind.BRANCH, NodeKind.TRUNK):
                out.append(joint(n, n.radius * f_bend))

    for n in nodes.values():
        k = ch[n.id]
        if ball and n.kind == NodeKind.TIP:
            out.append(joint(n, n.radius))
        if n.kind in (NodeKind.BRANCH, NodeKind.TRUNK, NodeKind.MODEL_FOOT) and len(k) > 1:
            out.append(joint(n, n.radius * f_junction))
        elif n.kind == NodeKind.FOOT:
            fr = max(float(foot_radius.get(n.id, P.foot_diameter / 2)), n.radius)
            base = np.array([n.position[0], n.position[1], z_base])
            fsegs = max(segs, 24)
            r_top = max([nodes[c].radius for c in k], default=n.radius)
            if any(nodes[c].kind == NodeKind.TIP for c in k):
                # short support (contact just above the plate): the foot disk may not
                # rise into the overhang above it, and a collar would swallow the tip
                room = min(nodes[c].position[2] for c in k) - z_base
                fh = min(P.foot_height, 0.5 * room)
                if fh > 1e-3:
                    out.append(cone_between(base, base + [0, 0, fh], fr, fr, fsegs))
                continue
            out.append(cone_between(base, base + [0, 0, P.foot_height], fr, fr, fsegs))
            collar_r = min(max(P.collar_diameter / 2, r_top), fr)
            top = base + [0, 0, P.foot_height + P.collar_height]
            out.append(cone_between(base + [0, 0, 0.5 * P.foot_height], top, collar_r, r_top, fsegs))
    for p0, p1, rb in getattr(tree, "braces", []):
        s_ = cone_between(p0, p1, rb, rb, max(8, segs // 2))
        if s_ is not None:
            out.append(s_)
    return [s_ for s_ in out if s_ is not None]


def _tree_groups(tree: SupportTree) -> list[list[int]]:
    """Node ids grouped by tree (root)."""
    root_of: dict[int, int] = {}
    for i in tree.nodes:
        path = []
        cur = i
        while cur not in root_of and tree.nodes[cur].parent is not None:
            path.append(cur)
            cur = tree.nodes[cur].parent
        r = root_of.get(cur, cur)
        root_of[cur] = r
        for p in path:
            root_of[p] = r
    groups: dict[int, list[int]] = {}
    for i, r in root_of.items():
        groups.setdefault(r, []).append(i)
    return list(groups.values())


MIN_PART_VOLUME = 1e-3   # mm3: separate support fragments below this are dropped


def mesh_tree_manifold(tree: SupportTree, params: SupportParams,
                       progress: Optional[Progress] = None) -> mf.Manifold:
    solids = tree_solids(tree, params, progress)
    if progress is not None:
        progress.update(0.45, f"Union of {len(solids)} solids")
    if not solids:
        return mf.Manifold()
    union = mf.Manifold.batch_boolean(solids, mf.OpType.Add)
    # Tilted tube ends and spheres near the feet can dip below the support base; nothing may
    # exist under it (plate, or raft top which the raft mesh fills).
    z_base = getattr(tree, "z_base", params.raft_thickness if params.raft_enabled else 0.0)
    union = union.trim_by_plane((0.0, 0.0, 1.0), float(z_base))
    # Two solids that almost touch can leave a separate fragment of (near) zero volume after
    # the union; it cannot be printed and would show up as a floating sliver in a slicer.
    parts = union.decompose()
    if len(parts) > 1:
        keep = [q for q in parts if q.volume() >= MIN_PART_VOLUME]
        if 0 < len(keep) < len(parts):
            union = mf.Manifold.compose(keep)
    return union


def mesh_tree(tree: SupportTree, params: SupportParams, progress: Optional[Progress] = None
              ) -> trimesh.Trimesh:
    """Watertight mesh of every support (tips, branches, trunks, feet, braces)."""
    m = mesh_tree_manifold(tree, params, progress)
    out = manifold_to_trimesh(m)
    if progress is not None:
        progress.update(1.0, "Support mesh done")
    return out


# ---------------------------------------------------------------------------
# raft
# ---------------------------------------------------------------------------
def raft_polygon(tree: SupportTree, params: SupportParams):
    """2D raft outline (shapely) or None: convex hull of each cluster of feet, offset by
    raft_margin, minus the model footprint keep-out."""
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import unary_union
    from scipy.spatial import cKDTree

    P = params
    feet = [n for n in tree.nodes.values() if n.kind == NodeKind.FOOT]
    if not feet:
        return None
    fr = getattr(tree, "foot_radius", {})
    xy = np.array([n.position[:2] for n in feet])
    rad = np.array([max(fr.get(n.id, P.foot_diameter / 2), n.radius) for n in feet])
    link = 2 * (rad.max() + P.raft_margin) + P.merge_distance
    parent = list(range(len(feet)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in cKDTree(xy).query_pairs(link):
        parent[find(a)] = find(b)
    clusters: dict[int, list[int]] = {}
    for i in range(len(feet)):
        clusters.setdefault(find(i), []).append(i)
    polys = []
    for mem in clusters.values():
        disks = [Point(xy[i]).buffer(rad[i] + P.raft_margin, 16) for i in mem]
        polys.append(MultiPoint([pt for d in disks for pt in d.exterior.coords]).convex_hull)
    poly = unary_union(polys)
    keep = getattr(tree, "raft_keepout", None)
    if keep is not None and not keep.is_empty:
        # never remove the area right under a foot
        must = unary_union([Point(xy[i]).buffer(rad[i], 16) for i in range(len(feet))])
        poly = poly.difference(keep.difference(must))
        if getattr(poly, "geoms", None) is not None and len(poly.geoms) > 1:
            # The model footprint cut the raft into islands (first print test: small pads
            # came loose). Join them with a ring around the footprint, inside the hull of
            # all feet so the raft does not grow around the whole model needlessly.
            hull = MultiPoint([pt for d in [Point(xy[i]).buffer(rad[i] + P.raft_margin, 16)
                                            for i in range(len(feet))]
                               for pt in d.exterior.coords]).convex_hull
            ring_w = max(P.raft_margin, 2.0)
            ring = keep.buffer(ring_w).difference(keep)
            joined = unary_union([poly, ring.intersection(hull.buffer(ring_w))])
            if len(getattr(joined, "geoms", [joined])) < len(poly.geoms):
                poly = joined
    if poly.is_empty or poly.area < 1e-6:
        return None
    return poly


def mesh_raft(tree: SupportTree, params: SupportParams) -> Optional[trimesh.Trimesh]:
    if not params.raft_enabled:
        return None
    poly = raft_polygon(tree, params)
    if poly is None:
        return None
    geoms = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    contours = []
    for g in geoms:
        if g.geom_type != "Polygon" or g.area < 1e-6:
            continue
        contours.append(np.asarray(g.exterior.coords)[:-1])
        for hole in g.interiors:
            contours.append(np.asarray(hole.coords)[:-1])
    if not contours:
        return None
    cs = mf.CrossSection(contours, mf.FillRule.EvenOdd)
    m = mf.Manifold.extrude(cs, float(params.raft_thickness))
    return manifold_to_trimesh(m)

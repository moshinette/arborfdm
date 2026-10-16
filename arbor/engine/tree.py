"""Tree routing: contact tips -> branches -> trunks -> feet.

Overview (details in docs/ALGORITHM.md):

1. ModelGrid voxelizes the model once into vertical occupancy intervals per XY grid
   column ("depth peeling" of every triangle over the grid centres). It answers
   "is (x, y, z) inside the model", "where is the model top below this point" and
   provides, per horizontal slab of height `segment_length`, a 2D distance field to
   the model (scipy EDT).
2. A bottom-up "reachability" pass (in the spirit of Cura's avoidance areas) marks, for
   every routing level, the XY cells from which the build plate can be reached while
   respecting the clearance and the maximum branch angle.
3. Top-down routing, level by level: each active node moves down one level and
   horizontally (at most dz * tan(branch_angle)) towards its nearest neighbour; nodes that
   can meet in the same step are merged (greedy clustering on a KD-tree). Candidate
   positions are scored (target distance, clearance, reachability). Nodes that cannot
   reach the plate land on the model (MODEL_FOOT) when allowed, otherwise they fail and
   are reported.
4. Braces between tall neighbouring trunks, raft keep-out area.

Extra data that SupportTree has no field for is attached as attributes on the tree
instance (see _init_tree_extras): z_base, braces, foot_radius, failed, raft_keepout.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree

from .params import SupportParams, model_on_raft, support_base_z
from .types import Contact, ContactReason, NodeKind, Progress, SupportTree

# gapped tips whose axis is tilted more than this get a vertical neck of NECK_LAYERS layers
NECK_MIN_TILT_DEG = 10.0
NECK_LAYERS = 2
# contacts whose tip gets the support interface gap (SupportParams.interface_gap_layers)
INTERFACE_GAP_REASONS = (ContactReason.EDGE, ContactReason.CORNER, ContactReason.SURFACE)

BIG = 1000.0  # "far from the model" distance (mm)
# Supports are printed without supports of their own: no support surface may lean further
# from vertical than the overhang angle. Branches keep this margin below it (the slicer
# tests whole layers, polygonal tubes and rounding eat a little of the angle).
PRINTABLE_MARGIN_DEG = 2.0
# The skirt that buries the bottom of a joint sphere (build.joint_at) ends on a sphere
# this much thinner than the branch: never tangent to the branch surface (a tangent
# hull and tube leave zero-volume slivers in the boolean union).
JOINT_INSET = 0.05

# Braced groups (Essai 4, A1 mini 0.2: three 42 mm trees braced only to one another, in a
# line, were knocked over in one piece). A group of plate trees joined by braces (or a lone
# tree) is stable when its base, in its narrowest direction, is at least
# 1/BRACE_GROUP_MAX_ASPECT of its tallest tree: a lateral push F at height H pulls the feet
# with about F*H/w, <= 4F, against 6.6F for the group that fell. Two feet, or feet in a
# line, are only one foot wide across: this subsumes a minimum number of feet.
BRACE_GROUP_MAX_ASPECT = 5.0
# braces that link an isolated group may reach this many brace_max_length (horizontal run):
# the gap between the arms of a figurine and its body is ~1.3x brace_max_length
BRIDGE_LENGTH_FACTOR = 1.5
# such a long brace is thickened (from brace_diameter) to keep length / diameter below this
# (verify flags fragile_support beyond 20)
BRIDGE_SLENDER_MAX = 18.0
# levels tried for such a link until one fits (then one every brace_spacing_z)
BRIDGE_STEP_Z = 2.0


def foot_group_width(xy: np.ndarray, radii: np.ndarray) -> float:
    """Smallest width (mm) of a group of feet: extent of their centres in the narrowest
    direction plus a mean foot diameter."""
    xy = np.asarray(xy, float).reshape(-1, 2)
    r = float(np.mean(radii)) if len(radii) else 0.0
    if len(xy) < 2:
        return 2.0 * r
    a = np.radians(np.arange(0.0, 180.0, 3.0))
    proj = xy @ np.stack([np.cos(a), np.sin(a)])
    return float((proj.max(axis=0) - proj.min(axis=0)).min()) + 2.0 * r


def braced_group_stable(width: float, height: float) -> bool:
    return width * BRACE_GROUP_MAX_ASPECT >= height


def braced_groups(tree: SupportTree, params: SupportParams) -> list[dict]:
    """Groups of plate trees joined by braces (from the tree and its braces only).
    Each group: feet (root ids), height (tallest tree above z_base, mm), width
    (foot_group_width), tall (feet whose tree is above brace_min_height), stable."""
    nodes = tree.nodes
    if not nodes:
        return []
    zb = float(getattr(tree, "z_base", 0.0))
    root_of: dict[int, int] = {}
    for nid in nodes:
        path, i = [], nid
        while i not in root_of and nodes[i].parent is not None:
            path.append(i)
            i = nodes[i].parent
        r = root_of.get(i, i)
        for q in path + [i]:
            root_of[q] = r
    top: dict[int, float] = {}
    for nid, n in nodes.items():
        r = root_of[nid]
        top[r] = max(top.get(r, -1e9), float(n.position[2]))
    feet = [r for r in top if nodes[r].kind == NodeKind.FOOT]
    if not feet:
        return []
    par = {r: r for r in feet}

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    braces = getattr(tree, "braces", []) or []
    if braces:
        ed = [(c, p) for c, p in tree.edges()]
        A = np.array([nodes[c].position for c, _ in ed]).reshape(-1, 3)
        B = np.array([nodes[p].position for _, p in ed]).reshape(-1, 3)
        R = np.array([max(nodes[c].radius, nodes[p].radius) for c, p in ed])
        AB = B - A
        L2 = np.maximum(np.einsum("ij,ij->i", AB, AB), 1e-12)

        def owner(q, rb):
            if not len(ed):
                return None
            t = np.clip(np.einsum("ij,ij->i", q - A, AB) / L2, 0.0, 1.0)
            d = np.linalg.norm(A + t[:, None] * AB - q, axis=1) - R
            k = int(np.argmin(d))
            return root_of[ed[k][0]] if d[k] <= rb + 0.5 else None

        for p0, p1, rb in braces:
            a = owner(np.asarray(p0, float), float(rb))
            b = owner(np.asarray(p1, float), float(rb))
            if a in par and b in par:
                par[find(a)] = find(b)
    comps: dict[int, list[int]] = {}
    for r in feet:
        comps.setdefault(find(r), []).append(r)
    foot_r = getattr(tree, "foot_radius", {}) or {}
    out = []
    for mem in comps.values():
        h = max(top[r] for r in mem) - zb
        xy = np.array([nodes[r].position[:2] for r in mem])
        rad = np.array([float(foot_r.get(r, params.foot_diameter / 2)) for r in mem])
        w = foot_group_width(xy, rad)
        tall = [r for r in mem if top[r] - zb > params.brace_min_height]
        out.append(dict(feet=mem, height=h, width=w, tall=tall,
                        tall_heights=[top[r] - zb for r in tall],
                        stable=braced_group_stable(w, h)))
    return out


def printable_angle_deg(params: SupportParams) -> float:
    """Largest tilt from vertical (deg) any support surface may have: the overhang angle
    minus PRINTABLE_MARGIN_DEG (the supports themselves print without support)."""
    return float(max(0.0, params.overhang_angle_deg - PRINTABLE_MARGIN_DEG))


def routing_angle_deg(params: SupportParams) -> float:
    """Tilt from vertical (deg) the router gives branches, tips and braces:
    branch_angle_deg, capped by printable_angle_deg."""
    return float(max(0.0, min(params.branch_angle_deg, printable_angle_deg(params))))


# ---------------------------------------------------------------------------
# Model occupancy grid
# ---------------------------------------------------------------------------
class ModelGrid:
    """Column voxelization of a (closed) triangle mesh on a regular XY grid.

    For every grid cell centre the vertical line is intersected with all triangles; with
    the sign of each triangle's Z orientation (entering / leaving the solid) the crossings
    give the occupied intervals [lo, hi]. Columns with an inconsistent crossing count
    (open meshes) are treated as fully occupied between their lowest and highest
    crossing, which is conservative.
    """

    def __init__(self, mesh: trimesh.Trimesh, res: float,
                 extra_points_xy: Optional[np.ndarray] = None, margin: float = 10.0):
        V = np.asarray(mesh.vertices, dtype=np.float64)
        F = np.asarray(mesh.faces, dtype=np.int64)
        pts = V[:, :2] if len(V) else np.zeros((0, 2))
        if extra_points_xy is not None and len(extra_points_xy):
            pts = np.vstack([pts, np.asarray(extra_points_xy, float)[:, :2]])
        if len(pts) == 0:
            pts = np.zeros((1, 2))
        lo = pts.min(0) - margin
        hi = pts.max(0) + margin
        self.res = float(res)
        # irrational offset: no cell centre lies exactly on axis aligned model geometry
        self.origin = lo - self.res * np.array([0.3819660113, 0.2763932022])
        self.nx = int(math.ceil((hi[0] - self.origin[0]) / self.res)) + 1
        self.ny = int(math.ceil((hi[1] - self.origin[1]) / self.res)) + 1
        self.ncell = self.nx * self.ny
        self._rasterize(V, F)

    # -- construction --------------------------------------------------------
    def _rasterize(self, V: np.ndarray, F: np.ndarray) -> None:
        res, ox, oy, nx, ny = self.res, self.origin[0], self.origin[1], self.nx, self.ny
        cells_l, z_l, s_l = [], [], []
        if len(F):
            T = V[F]
            x, y, z = T[:, :, 0], T[:, :, 1], T[:, :, 2]
            a2 = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
            keep = np.abs(a2) > 1e-14
            x, y, z, a2 = x[keep], y[keep], z[keep], a2[keep]
            i0 = np.ceil((x.min(1) - ox) / res - 0.5).astype(np.int64)
            i1 = np.floor((x.max(1) - ox) / res - 0.5).astype(np.int64)
            j0 = np.ceil((y.min(1) - oy) / res - 0.5).astype(np.int64)
            j1 = np.floor((y.max(1) - oy) / res - 0.5).astype(np.int64)
            i0, j0 = np.maximum(i0, 0), np.maximum(j0, 0)
            i1, j1 = np.minimum(i1, nx - 1), np.minimum(j1, ny - 1)
            ni = np.maximum(i1 - i0 + 1, 0)
            nj = np.maximum(j1 - j0 + 1, 0)
            cnt = ni * nj
            sel = np.nonzero(cnt > 0)[0]
            # process in chunks bounded by the number of (triangle, cell) pairs
            csum = np.cumsum(cnt[sel])
            chunk = 1_000_000
            start = 0
            while start < len(sel):
                base = csum[start - 1] if start > 0 else 0
                stop = int(np.searchsorted(csum, base + chunk, side="right"))
                stop = max(stop, start + 1)
                t = sel[start:stop]
                start = stop
                c = cnt[t]
                tt = np.repeat(t, c)
                offs = np.arange(c.sum()) - np.repeat(np.cumsum(c) - c, c)
                ii = i0[tt] + offs % ni[tt]
                jj = j0[tt] + offs // ni[tt]
                px = ox + (ii + 0.5) * res
                py = oy + (jj + 0.5) * res
                X, Y, Z, A = x[tt], y[tt], z[tt], a2[tt]
                w0 = ((X[:, 1] - px) * (Y[:, 2] - py) - (X[:, 2] - px) * (Y[:, 1] - py)) / A
                w1 = ((X[:, 2] - px) * (Y[:, 0] - py) - (X[:, 0] - px) * (Y[:, 2] - py)) / A
                w2 = 1.0 - w0 - w1
                eps = -1e-9
                inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
                if not inside.any():
                    continue
                zz = w0 * Z[:, 0] + w1 * Z[:, 1] + w2 * Z[:, 2]
                cells_l.append((jj * nx + ii)[inside])
                z_l.append(zz[inside])
                # a2 > 0: face normal points up: moving up we leave the solid
                s_l.append(np.where(A[inside] > 0, -1, 1).astype(np.int8))
        if cells_l:
            cell = np.concatenate(cells_l)
            zc = np.concatenate(z_l)
            sg = np.concatenate(s_l).astype(np.int64)
        else:
            cell = np.zeros(0, np.int64)
            zc = np.zeros(0)
            sg = np.zeros(0, np.int64)
        order = np.lexsort((sg, zc, cell))
        cell, zc, sg = cell[order], zc[order], sg[order]
        if len(cell):
            dup = np.zeros(len(cell), bool)
            dup[1:] = (cell[1:] == cell[:-1]) & (np.abs(zc[1:] - zc[:-1]) < 1e-7) & (sg[1:] == sg[:-1])
            cell, zc, sg = cell[~dup], zc[~dup], sg[~dup]
        lo_l, hi_l, c_l = [], [], []
        if len(cell):
            cs = np.cumsum(sg)
            first = np.ones(len(cell), bool)
            first[1:] = cell[1:] != cell[:-1]
            grp_start = np.maximum.accumulate(np.where(first, np.arange(len(cell)), 0))
            before_first = np.where(grp_start > 0, cs[np.maximum(grp_start - 1, 0)], 0)
            after = cs - before_first
            before = after - sg
            starts = (before <= 0) & (after > 0)
            ends = (before > 0) & (after <= 0)
            ucell, inv = np.unique(cell, return_inverse=True)
            n_s = np.bincount(inv, weights=starts, minlength=len(ucell))
            n_e = np.bincount(inv, weights=ends, minlength=len(ucell))
            last = np.ones(len(cell), bool)
            last[:-1] = cell[1:] != cell[:-1]
            final = np.zeros(len(ucell))
            final[inv[last]] = after[last]
            good_cell = (n_s == n_e) & (final == 0)
            g = good_cell[inv]
            si = np.nonzero(starts & g)[0]
            ei = np.nonzero(ends & g)[0]
            lo_l.append(zc[si]); hi_l.append(zc[ei]); c_l.append(cell[si])
            bad = np.nonzero(~good_cell)[0]
            if len(bad):
                zmin = np.full(len(ucell), np.inf)
                zmax = np.full(len(ucell), -np.inf)
                np.minimum.at(zmin, inv, zc)
                np.maximum.at(zmax, inv, zc)
                lo_l.append(zmin[bad]); hi_l.append(zmax[bad]); c_l.append(ucell[bad])
            self.n_bad_columns = int(len(bad))
        else:
            self.n_bad_columns = 0
        if c_l:
            ic = np.concatenate(c_l); il = np.concatenate(lo_l); ih = np.concatenate(hi_l)
        else:
            ic = np.zeros(0, np.int64); il = np.zeros(0); ih = np.zeros(0)
        keep = ih > il
        ic, il, ih = ic[keep], il[keep], ih[keep]
        o = np.lexsort((il, ic))
        self.iv_cell, self.iv_lo, self.iv_hi = ic[o], il[o], ih[o]

    # -- queries --------------------------------------------------------------
    def cell_index(self, xy: np.ndarray):
        xy = np.asarray(xy, float).reshape(-1, 2)
        i = np.floor((xy[:, 0] - self.origin[0]) / self.res).astype(np.int64)
        j = np.floor((xy[:, 1] - self.origin[1]) / self.res).astype(np.int64)
        inside = (i >= 0) & (i < self.nx) & (j >= 0) & (j < self.ny)
        return np.clip(i, 0, self.nx - 1), np.clip(j, 0, self.ny - 1), inside

    def _intervals(self, cell: int):
        a = np.searchsorted(self.iv_cell, cell, side="left")
        b = np.searchsorted(self.iv_cell, cell, side="right")
        return self.iv_lo[a:b], self.iv_hi[a:b]

    def inside(self, p) -> bool:
        i, j, ok = self.cell_index(np.asarray(p, float)[:2])
        if not ok[0]:
            return False
        lo, hi = self._intervals(int(j[0] * self.nx + i[0]))
        return bool(np.any((lo <= p[2]) & (hi >= p[2])))

    def top_below(self, xy, z: float) -> Optional[float]:
        """Highest upward facing model surface at or below z in the column of xy.

        None if there is none, or if (xy, z) itself lies inside the material: a point
        beside or inside a wall has no surface "below" it to stand on (returning z there
        used to put model feet on the side of vertical walls)."""
        i, j, ok = self.cell_index(np.asarray(xy, float)[:2])
        if not ok[0]:
            return None
        lo, hi = self._intervals(int(j[0] * self.nx + i[0]))
        if np.any((lo < z - 1e-6) & (hi > z + 1e-6)):
            return None
        m = hi <= z + 1e-6
        if not m.any():
            return None
        return float(hi[m].max())

    def footprint_below(self, z: float) -> np.ndarray:
        """(ny, nx) bool: cells where the model has material below height z."""
        occ = np.zeros(self.ncell, bool)
        occ[self.iv_cell[self.iv_lo < z]] = True
        return occ.reshape(self.ny, self.nx)

    def slab_distances(self, z_base: float, dz: float, n_slabs: int) -> np.ndarray:
        """(n_slabs, ny, nx) float16: conservative XY distance to the model within each
        slab [z_base + k dz, z_base + (k+1) dz]. Slab 0 also covers everything below."""
        out = np.empty((n_slabs, self.ny, self.nx), np.float16)
        eps = 1e-6
        klo = np.floor((self.iv_lo - z_base) / dz + eps).astype(np.int64)
        khi = np.ceil((self.iv_hi - z_base) / dz - eps).astype(np.int64) - 1
        klo = np.clip(klo, 0, None)
        khi = np.clip(khi, 0, None)
        CH = 32
        for k0 in range(0, n_slabs, CH):
            k1 = min(n_slabs, k0 + CH)
            m = (khi >= k0) & (klo < k1)
            diff = np.zeros((k1 - k0 + 1, self.ncell), np.int32)
            if m.any():
                a = np.clip(klo[m], k0, k1) - k0
                b = np.clip(khi[m] + 1, k0, k1) - k0
                np.add.at(diff, (a, self.iv_cell[m]), 1)
                np.add.at(diff, (b, self.iv_cell[m]), -1)
            occ = np.cumsum(diff, axis=0)[:-1] > 0
            for k in range(k1 - k0):
                o = occ[k].reshape(self.ny, self.nx)
                if not o.any():
                    out[k0 + k] = BIG
                    continue
                d = ndimage.distance_transform_edt(~o) * self.res - 0.75 * self.res
                d[o] = 0.0
                out[k0 + k] = np.clip(d, 0.0, BIG)
        return out


def grid_resolution(params: SupportParams, extent_xy: float, n_slabs: int) -> float:
    res = min(max(0.5 * min(params.xy_clearance, params.branch_min_diameter), 0.15), 0.5)
    # memory guard: keep slabs * cells around 40 M (float16 distances + bool masks)
    cells = (extent_xy / res) ** 2
    if cells * max(n_slabs, 1) > 40e6:
        res = extent_xy / math.sqrt(40e6 / max(n_slabs, 1))
    return res


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _init_tree_extras(tree: SupportTree, z_base: float) -> None:
    tree.z_base = z_base            # build plate or raft top
    tree.braces = []                # list of (p0, p1, radius)
    tree.foot_radius = {}           # FOOT node id -> disk radius actually used
    tree.failed = {}                # contact id -> reason (not routed)
    tree.raft_keepout = None        # shapely geometry the raft must avoid (model footprint)
    tree.model_base = None          # model on the raft: hull of its base on the ground
    tree.model_xy_bounds = None     # model on the raft: XY bounds of the model


def _model_outline(tree: SupportTree, mesh: trimesh.Trimesh, params: SupportParams) -> None:
    """XY outlines the raft needs when the model rests on it (build.raft_polygon):
    tree.model_base = convex hull of the model vertices on the first layer above the
    ground (None if the model does not touch the ground, i.e. it is lifted);
    tree.model_xy_bounds = (xmin, ymin, xmax, ymax) of the whole model."""
    from shapely.geometry import MultiPoint
    V = np.asarray(mesh.vertices, float)
    if not len(V):
        return
    tree.model_xy_bounds = tuple(float(x) for x in (*V[:, :2].min(axis=0), *V[:, :2].max(axis=0)))
    low = V[V[:, 2] <= params.layer_height]          # model ground: z = 0
    if len(low) >= 1:
        hull = MultiPoint([tuple(p) for p in low[:, :2]]).convex_hull
        tree.model_base = hull.buffer(max(params.line_width, 0.1)) if hull.area < 1e-6 else hull


def tip_direction(normal: np.ndarray, max_angle_deg: float, extra_tilt: bool = False) -> np.ndarray:
    """Unit vector from the contact point to the tip base: downward, tilted towards the
    surface normal (so the tip leaves the surface) but never more than max_angle_deg
    from vertical."""
    n = np.asarray(normal, float)
    ln = np.linalg.norm(n)
    n = n / ln if ln > 1e-12 else np.array([0.0, 0.0, -1.0])
    amax = math.radians(max_angle_deg)
    nh = n[:2]
    lh = float(np.linalg.norm(nh))
    if lh < 1e-9:
        return np.array([0.0, 0.0, -1.0])
    theta = math.acos(float(np.clip(-n[2], -1.0, 1.0)))
    theta = amax if extra_tilt else min(theta, amax)
    h = nh / lh
    return np.array([math.sin(theta) * h[0], math.sin(theta) * h[1], -math.cos(theta)])


def _disc_pattern() -> np.ndarray:
    pts = [(0.0, 0.0)]
    for frac, n in ((0.34, 8), (0.67, 12), (1.0, 16)):
        a = np.arange(n) * 2 * math.pi / n + (0.2 if n == 12 else 0.0)
        pts += list(zip(frac * np.cos(a), frac * np.sin(a)))
    return np.asarray(pts)


_PATTERN = _disc_pattern()


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------
def build_tree(mesh: trimesh.Trimesh, contacts: list[Contact], params: SupportParams,
               progress: Progress) -> SupportTree:
    progress = progress or Progress()
    z_base = support_base_z(params)
    # Model on the raft with a release gap: the trees are routed on the model's ground
    # (z = 0, the same level grid as without gap, so the gap does not reshuffle the
    # routing), then their feet are lowered onto the raft top (z_base = -gap).
    z_route = max(z_base, 0.0)
    tree = SupportTree()
    _init_tree_extras(tree, z_route)
    if model_on_raft(params):
        _model_outline(tree, mesh, params)
    contacts = [c for c in contacts if c.enabled]
    for c in contacts:
        c.rests_on_model = False
    if contacts:
        router = _Router(mesh, contacts, params, tree, progress)
        router.run()
    if z_base < z_route:
        for n in tree.nodes.values():
            if n.kind == NodeKind.FOOT and n.position[2] <= z_route + 1e-9:
                n.position = np.array([n.position[0], n.position[1], z_base])
        tree.z_base = z_base
    return tree


class _Router:
    def __init__(self, mesh, contacts, params: SupportParams, tree: SupportTree, progress: Progress):
        self.mesh, self.contacts, self.P, self.tree, self.progress = mesh, contacts, params, tree, progress
        self.zb = tree.z_base
        self.dz = max(params.segment_length, 0.2)
        self.angle = routing_angle_deg(params)
        self.tan = math.tan(math.radians(self.angle))
        self.angle_print = printable_angle_deg(params)
        self.tan_print = math.tan(math.radians(self.angle_print))
        self.r_min = params.branch_min_diameter / 2
        self.r_trunk = params.trunk_diameter / 2
        self.r_tipbase = params.tip_base_diameter / 2
        self.hard = 0.1  # mm: minimum gap between a branch surface and the model

    # -- setup ----------------------------------------------------------------
    def run(self) -> None:
        P = self.P
        prog = self.progress
        self._prepare()
        prog.update(0.4, "Tips")
        self._make_tips()
        self._route()
        prog.update(0.9, "Braces")
        self._cleanup()
        self._limit_widening()
        self._stabilize_feet()
        if P.braces_enabled:
            self._braces()
        if P.raft_enabled and not model_on_raft(P):
            self._raft_keepout()
        prog.update(1.0, "Tree done")

    def _prepare(self) -> None:
        """Model grid, slab distance fields and reachability (no tree node created)."""
        P = self.P
        prog = self.progress
        prog.update(0.0, "Preparing model grid")
        cpos = np.array([c.position for c in self.contacts], float)
        zmax = max(float(cpos[:, 2].max()), self.zb) + 1.0
        n_slabs = int(math.ceil((zmax - self.zb) / self.dz)) + 2
        ext = float(np.ptp(np.vstack([np.asarray(self.mesh.vertices)[:, :2], cpos[:, :2]]), axis=0).max()) \
            if len(self.mesh.vertices) else float(np.ptp(cpos[:, :2], axis=0).max())
        margin = max(P.merge_distance, P.foot_diameter, P.brace_max_length) + 5.0
        res = grid_resolution(P, ext + 2 * margin, n_slabs)
        self.grid = ModelGrid(self.mesh, res, cpos[:, :2], margin=margin)
        prog.update(0.15, "Model distance field")
        self.dist = self.grid.slab_distances(self.zb, self.dz, n_slabs)
        self.n_slabs = n_slabs
        prog.update(0.3, "Reachability")
        self._reachability()

    # distance of xy points to the model in slab k (vectorized, k scalar)
    def mdist(self, k: int, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, float).reshape(-1, 2)
        if k < 0:
            k = 0
        if k >= self.n_slabs:
            return np.full(len(xy), BIG)
        i, j, ok = self.grid.cell_index(xy)
        d = self.dist[k, j, i].astype(np.float64)
        d[~ok] = BIG
        return d

    def mdist2(self, k: int, xy: np.ndarray) -> np.ndarray:
        """Distance to the model around a node standing at level k: both the slab above
        it (segment from level k + 1) and the slab below it (segment to level k - 1) are
        touched by the node's radius, so both must keep the clearance."""
        d = self.mdist(k, xy)
        if k >= 1:
            d = np.minimum(d, self.mdist(k - 1, xy))
        return d

    def reach(self, k: int, xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, float).reshape(-1, 2)
        if k < 0:
            k = 0
        if k >= self.n_slabs:
            return np.ones(len(xy), bool)
        i, j, ok = self.grid.cell_index(xy)
        r = self.D[k, j, i].copy()
        r[~ok] = True
        return r

    def _reachability(self) -> None:
        """D[k]: a node may stand at level k (z_base + k dz) at this cell and still reach
        the plate. D[k] = F[k] & dilate(D[k-1], move) with F[k] the cells of slab k that
        keep the reach clearance from the model."""
        c = self.P.xy_clearance + self.r_min
        move = self.dz * self.tan
        rad = move / self.grid.res
        ri = int(math.floor(rad))
        if ri >= 1:
            yy, xx = np.mgrid[-ri:ri + 1, -ri:ri + 1]
            st = (xx ** 2 + yy ** 2) <= rad ** 2 + 1e-9
        else:
            st = None
        D = np.empty(self.dist.shape, bool)
        D[0] = self.dist[0] >= c
        for k in range(1, self.n_slabs):
            # a node at level k touches slab k (above) and slab k - 1 (below)
            F = np.minimum(self.dist[k], self.dist[k - 1]) >= c
            if F.all():
                D[k] = True
                continue
            prev = D[k - 1]
            dil = ndimage.binary_dilation(prev, structure=st) if st is not None else prev
            D[k] = F & dil
        self.D = D

    # -- tips ---------------------------------------------------------------
    def _make_tips(self) -> None:
        P, tree, zb, dz = self.P, self.tree, self.zb, self.dz
        self.pending = []  # (start level, node id, max move, nominal radius)
        for c in self.contacts:
            pos = np.asarray(c.position, float)
            n = np.asarray(c.normal, float)
            ln = np.linalg.norm(n)
            n = n / ln if ln > 1e-12 else np.array([0.0, 0.0, -1.0])
            tip_d = c.tip_diameter if c.tip_diameter else P.tip_diameter
            tip_r = tip_d / 2
            rb = self.tip_base_r(tip_r)
            u = self._tip_dir(pos, n)
            base = pos + u * P.tip_length
            pen, zgap = self.contact_gap(c)
            if P.tip_shape == "ball":
                tip_pos = pos + n * (tip_r - pen)
            else:
                tip_pos = pos - n * pen
            tip_pos = tip_pos - np.array([0.0, 0.0, zgap])
            gapped = zgap > P.z_gap
            # gapped tip on a tilted axis: a short vertical neck keeps its top disc
            # horizontal, otherwise the raised rim forces the whole tip one more layer
            # down (2 air layers measured in Orca's G-code on 19 of 61 flat contacts)
            neck = gapped and P.tip_shape == "cone" and -u[2] < math.cos(math.radians(NECK_MIN_TILT_DEG))
            if gapped:
                tip_pos = self._clear_gap(tip_pos, np.array([0.0, 0.0, 1.0]) if neck else -u, tip_r, zgap)
            if P.raft_enabled and not model_on_raft(P) and pos[2] < zb + self.min_short_gap() - 1e-9:
                # raft under the feet only: the model stands on the plate and the raft
                # top is raft_thickness higher; a contact this low has no room for a
                # support of its own (it would have to start inside the raft)
                tree.failed[c.id] = (f"contact {pos[2]:.2f} mm above the plate, below the raft top "
                                     f"({zb:.2f} mm) + one layer: with the raft under the feet only "
                                     "no support fits there (use raft_coverage 'footprint')")
                continue
            if pos[2] - zb < 0.05:
                tree.failed[c.id] = "contact lies on the build plate"
                continue
            floor = self._floor_below(pos)
            if floor is not None and pos[2] - floor[0] < P.tip_length + self.r_min + self.hard:
                # too little room under the contact for tip + branch: short support
                self._short_support(c, pos, n, tip_r, floor[0], floor[1])
                continue
            if self.grid.inside(base):
                tree.failed[c.id] = "tip base would be inside the model (surface faces up?)"
                continue
            tip = tree.add(tip_pos, tip_r, NodeKind.TIP, contact_id=c.id)
            if gapped:
                tree.interface_gap_ids.add(c.id)
            if base[2] < zb + 0.5 * dz:
                # contact close to the plate: the tip goes straight to a foot
                t = (pos[2] - zb) / max(-u[2], 1e-6)
                fpos = pos + u * t
                fpos[2] = zb
                f = tree.add(fpos, max(rb, tip_r), NodeKind.FOOT)
                tip.parent = f.id
                tree.foot_radius[f.id] = self._foot_r(fpos, f.radius)
                continue
            neck_pos = None
            if neck:
                # the cone below the neck keeps the tip axis u (a steeper segment would
                # overhang more): its base slides back under the tip by hn x tan(tilt)
                hn = NECK_LAYERS * P.layer_height
                neck_pos = tip_pos - np.array([0.0, 0.0, hn])
                t = (neck_pos[2] - base[2]) / max(-u[2], 1e-6)
                base2 = neck_pos + u * t
                if t > 0.25 * P.tip_length and not self.grid.inside(base2):
                    base = base2
                else:
                    neck_pos = None
            b = tree.add(base, rb, NodeKind.BRANCH, contact_id=c.id)
            tip.parent = b.id
            if neck_pos is not None:
                nk = tree.add(neck_pos, tip_r, NodeKind.BRANCH, contact_id=c.id)
                nk.parent = b.id
                tip.parent = nk.id
            kb = self._base_level(base[2])
            move = (base[2] - (zb + kb * dz)) * self.tan
            self.pending.append((kb, b.id, move, max(self.r_min, rb)))
        self.pending.sort(key=lambda t: -t[0])

    def tip_base_r(self, tip_r: float) -> float:
        """Radius where a tip meets its branch. A tip thicker than tip_diameter (per
        contact override, island anchors) keeps the profile's taper: its base is widened
        by the same amount, tip_base_diameter - tip_diameter (0.26 -> 0.7 becomes
        0.45 -> 0.89 in 0.2 mm profiles), so the thicker tip is also stiffer below
        the contact instead of becoming a near cylinder."""
        P = self.P
        return max(self.r_tipbase, tip_r + 0.5 * max(P.tip_base_diameter - P.tip_diameter, 0.0))

    # -- short supports (contact just above a surface) ----------------------------
    def contact_gap(self, c) -> tuple[float, float]:
        """(penetration, vertical gap) of the tip of contact c.

        Support interface (interface_gap_layers > 0): edge, corner and surface tips stop
        z_gap + interface_gap_layers layers below the contact without penetrating, so the
        slicer leaves an air layer between support and model (the tip releases instead of
        being fused). Islands, their anchors, low points and manual contacts stay welded
        (tip_penetration, z_gap): a part starting in the air needs its first layers held."""
        P = self.P
        k = int(getattr(P, "interface_gap_layers", 0) or 0)
        if k > 0 and getattr(c, "reason", None) in INTERFACE_GAP_REASONS:
            return 0.0, float(P.z_gap) + k * float(P.layer_height)
        return float(P.tip_penetration), float(P.z_gap)

    def _clear_gap(self, tip_pos: np.ndarray, axis: np.ndarray, tip_r: float, gap: float,
                   floor: float | None = None) -> np.ndarray:
        """Lower a gapped tip until its whole top disc (radius tip_r, perpendicular to
        axis, the direction from the tip base to the tip) is at least gap below the
        model surface, measured vertically as a slicer sees it. Without this the rim of a
        tilted disc under a sloped face rises into the air layer (measured on the
        figurine: 26 of 87 tips closer than 0.06 mm, some touching). The drop is capped
        at tip_r + gap and never goes below floor."""
        if not hasattr(self, "_gap_caster"):
            from .contacts import VerticalCaster
            self._gap_caster = VerticalCaster(self.mesh)
        a = np.asarray(axis, float)
        a = a / max(np.linalg.norm(a), 1e-12)
        e1 = np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(a, e1)
        ang = np.arange(12) * (2 * math.pi / 12)
        rim = tip_pos + tip_r * (np.cos(ang)[:, None] * e1 + np.sin(ang)[:, None] * e2)
        pts = np.vstack([tip_pos, rim])
        drop = 0.0
        for _ in range(3):
            q = pts - [0.0, 0.0, drop]
            z, _ = self._gap_caster.cast(np.column_stack([q[:, :2], q[:, 2] - 0.5]))
            ok = np.isfinite(z)
            if not ok.any():
                break
            deficit = gap - float((z[ok] - q[ok, 2]).min())
            if deficit < 1e-4:
                break
            drop = min(drop + deficit, tip_r + gap)
        out = tip_pos - [0.0, 0.0, drop]
        if floor is not None and out[2] < floor:
            out[2] = max(floor, tip_pos[2] - drop)
        return out

    def min_short_gap(self) -> float:
        """Smallest free height under a contact for which a support is built.

        layer_height + z_gap: below one layer (after the requested air gap) the slicer has
        no layer left between the surface below and the overhang, so the "support" would
        be fused into both surfaces (pure penetration, no support material of its own).
        From one layer up, the slicer prints at least one layer of support under the
        contact, which is the minimum that can carry it."""
        return self.P.layer_height + max(self.P.z_gap, 0.0)

    def _floor_below(self, pos: np.ndarray):
        """(z, on_plate) of the first surface hit by a vertical ray going down from the
        contact, if that surface is closer than tip_length + a margin; None otherwise.

        The grid answers whether a surface may be close (cheap, conservative); the
        height itself is computed exactly on the triangles (the grid is too coarse for
        sub-millimetre gaps)."""
        P, zb = self.P, self.zb
        reach = P.tip_length + self.r_min + self.hard
        near_plate = pos[2] - zb < reach
        g = self.grid
        i, j, ok = g.cell_index(pos[:2])
        maybe = False
        if ok[0]:
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    ii, jj = int(i[0]) + di, int(j[0]) + dj
                    if not (0 <= ii < g.nx and 0 <= jj < g.ny):
                        continue
                    lo, hi = g._intervals(jj * g.nx + ii)
                    if np.any((hi < pos[2] + g.res) & (hi > pos[2] - reach - g.res)):
                        maybe = True
                        break
                if maybe:
                    break
        if not (maybe or near_plate):
            return None
        z_hit = self._ray_down(pos)
        if z_hit is not None and z_hit > zb:
            fl, on_plate = z_hit, False
        else:
            fl, on_plate = zb, True
        if pos[2] - fl >= reach:
            return None
        return fl, on_plate

    def _ray_down(self, pos: np.ndarray) -> Optional[float]:
        """Highest model surface strictly below pos on the vertical through pos (exact)."""
        if not hasattr(self, "_tri"):
            V = np.asarray(self.mesh.vertices, float)
            F = np.asarray(self.mesh.faces, np.int64)
            T = V[F] if len(F) else np.zeros((0, 3, 3))
            self._tri = T
            self._tri_lo = T.min(axis=1) if len(T) else np.zeros((0, 3))
            self._tri_hi = T.max(axis=1) if len(T) else np.zeros((0, 3))
        T, lo, hi = self._tri, self._tri_lo, self._tri_hi
        if not len(T):
            return None
        px, py, pz = float(pos[0]), float(pos[1]), float(pos[2]) - 1e-4
        m = (lo[:, 0] <= px) & (hi[:, 0] >= px) & (lo[:, 1] <= py) & (hi[:, 1] >= py) & (lo[:, 2] < pz)
        t = T[m]
        if not len(t):
            return None
        x, y, z = t[:, :, 0], t[:, :, 1], t[:, :, 2]
        a2 = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
        k = np.abs(a2) > 1e-14
        x, y, z, a2 = x[k], y[k], z[k], a2[k]
        w0 = ((x[:, 1] - px) * (y[:, 2] - py) - (x[:, 2] - px) * (y[:, 1] - py)) / a2
        w1 = ((x[:, 2] - px) * (y[:, 0] - py) - (x[:, 0] - px) * (y[:, 2] - py)) / a2
        w2 = 1.0 - w0 - w1
        e = -1e-9
        ins = (w0 >= e) & (w1 >= e) & (w2 >= e)
        zz = (w0 * z[:, 0] + w1 * z[:, 1] + w2 * z[:, 2])[ins]
        zz = zz[zz < pz]
        return float(zz.max()) if len(zz) else None

    def _short_support(self, c, pos, n, tip_r: float, floor_z: float, on_plate: bool) -> None:
        """Contact less than tip_length (+ branch room) above a surface: one vertical cone,
        tip_diameter at the contact widening towards tip_base_diameter (in proportion to
        the available height), standing on the plate (FOOT) or on the model (MODEL_FOOT,
        only when allow_on_model). Below min_short_gap() nothing is built and the reason
        goes to tree.failed."""
        P, tree = self.P, self.tree
        pen, zgap = self.contact_gap(c)
        if zgap > P.z_gap and pos[2] - zgap - floor_z < self.min_short_gap() - 1e-9:
            # no room for an air layer and a support layer: welded tip, as without interface
            pen, zgap = float(P.tip_penetration), float(P.z_gap)
        gap = pos[2] - zgap - floor_z
        where = "build plate" if on_plate else "model"
        if gap < self.min_short_gap() - 1e-9:
            tree.failed[c.id] = (f"only {pos[2] - floor_z:.2f} mm free under the contact (surface of the "
                                 f"{where} below): less than one layer (+ z_gap) = "
                                 f"{self.min_short_gap():.2f} mm, no support can be printed there")
            return
        if not on_plate and not P.allow_on_model:
            tree.failed[c.id] = (f"only {pos[2] - floor_z:.2f} mm above the model: a short support "
                                 f"would stand on the model and supports on the model are disabled")
            return
        tip_pos = pos - n * pen - np.array([0.0, 0.0, zgap])
        if zgap > P.z_gap:
            tip_pos = self._clear_gap(tip_pos, np.array([0.0, 0.0, 1.0]), tip_r, zgap,
                                      floor=floor_z + P.layer_height)
        tip = tree.add(tip_pos, tip_r, NodeKind.TIP, contact_id=c.id)
        if zgap > P.z_gap:
            tree.interface_gap_ids.add(c.id)
        frac = min(1.0, max(gap, 0.0) / max(P.tip_length, 1e-6))
        r_low = tip_r + (self.tip_base_r(tip_r) - tip_r) * frac
        if on_plate:
            f = tree.add([tip_pos[0], tip_pos[1], self.zb], max(r_low, tip_r), NodeKind.FOOT)
            tree.foot_radius[f.id] = self._foot_r(f.position, f.radius)
        else:
            f = tree.add([tip_pos[0], tip_pos[1], floor_z - P.tip_penetration],
                         min(r_low, max(P.on_model_tip_diameter / 2, tip_r)), NodeKind.MODEL_FOOT)
        tip.parent = f.id

    def _base_clearance(self, pos: np.ndarray, u: np.ndarray) -> float:
        """Horizontal clearance from the model of a tip base pos + u * tip_length
        (-1 if the tip base or the tip axis is inside the model)."""
        P, zb, dz = self.P, self.zb, self.dz
        base = pos + u * P.tip_length
        if base[2] < zb:
            return -1.0
        for s in (0.5, 0.75, 1.0):
            if self.grid.inside(pos + u * (s * P.tip_length)):
                return -1.0
        # only the slabs the tip base and its first segment really occupy: from the slab
        # holding the base down to the routing level the base descends to. mdist2 (slab
        # below the node as well) was too pessimistic here: depending on where the levels
        # fall (they move with z_base, e.g. with a raft) it caught material up to 2 dz
        # below the base (figurine cape top under the chin, QA-9) and hid every free
        # direction.
        k = int(math.floor((base[2] - zb) / dz + 1e-9))
        kb = self._base_level(base[2])
        return float(min(self.mdist(kk, base[:2])[0] for kk in range(kb, max(k, kb) + 1)))

    def _base_level(self, bz: float) -> int:
        """Routing level a tip base at height bz descends to (its first branch node)."""
        zb, dz = self.zb, self.dz
        kb = int(math.floor((bz - zb) / dz + 1e-9))
        if bz - (zb + kb * dz) < 0.3 * dz and kb >= 1:
            kb -= 1
        return min(kb, self.n_slabs - 1)

    def _tip_dir(self, pos: np.ndarray, n: np.ndarray) -> np.ndarray:
        """Tip axis direction. Default: tip_direction() (down, tilted towards the surface
        normal). When that tip base lacks the clearance a branch needs to leave towards
        the plate (contact next to a wall, a leg, the inner corner of an arm), the tip is
        tilted (up to branch_angle) towards the side with the most room. Without this a
        contact 0.5 mm from a wall starts in a column where no branch fits and ends up
        landing on the model (or crossing it)."""
        P = self.P
        u0 = tip_direction(n, self.angle)
        want = P.xy_clearance + self.r_min
        c0 = self._base_clearance(pos, u0)
        if c0 >= want:
            return u0
        best_u, best_s = u0, (min(c0, want) + 0.05 if c0 >= 0 else -np.inf)
        cands = [tip_direction(n, self.angle, extra_tilt=True)]
        amax = math.radians(self.angle)
        for frac in (0.5, 1.0):
            th = frac * amax
            for az in np.arange(16) * (2 * math.pi / 16):
                cands.append(np.array([math.sin(th) * math.cos(az), math.sin(th) * math.sin(az),
                                       -math.cos(th)]))
        for u in cands:
            c = self._base_clearance(pos, u)
            if c < 0:
                continue
            # prefer clearance, then the direction closest to the default one
            s = min(c, want) + 0.05 * float(np.dot(u, u0))
            if s > best_s + 1e-9:
                best_u, best_s = u, s
        return best_u

    def _foot_r(self, pos, r_node) -> float:
        """Foot disk radius: foot_diameter / 2, shrunk to keep 0.2 mm from the model
        within the foot + collar height, never below the node radius."""
        P = self.P
        k_top = int(math.ceil((P.foot_height + P.collar_height) / self.dz))
        md = min(self.mdist(k, pos[:2])[0] for k in range(0, k_top + 1))
        fr = min(P.foot_diameter / 2, md - 0.2)
        return float(max(fr, r_node))

    # -- routing ---------------------------------------------------------------
    def _route(self) -> None:
        P, tree, zb, dz = self.P, self.tree, self.zb, self.dz
        act_id = np.zeros(0, np.int64)
        act_r = np.zeros(0)          # nominal radius
        act_merged = np.zeros(0, bool)
        pend = self.pending
        pi = 0
        top = pend[0][0] if pend else -1
        full_move = dz * self.tan
        self.landed: list[int] = []
        self.fail_nodes: dict[int, str] = {}
        for j in range(top, -1, -1):
            if j % 4 == 0:
                self.progress.update(0.4 + 0.5 * (1 - j / max(top, 1)), "Routing branches")
            new_ids, new_r, new_m = [], [], []
            while pi < len(pend) and pend[pi][0] == j:
                _, nid, mv, rr = pend[pi]
                new_ids.append(nid); new_m.append(mv); new_r.append(rr)
                pi += 1
            ids = np.concatenate([act_id, np.asarray(new_ids, np.int64)])
            if len(ids) == 0:
                continue
            rn = np.concatenate([act_r, np.asarray(new_r)])
            merged = np.concatenate([act_merged, np.zeros(len(new_ids), bool)])
            move = np.concatenate([np.full(len(act_id), full_move), np.asarray(new_m)])
            act_id, act_r, act_merged = self._step(j, ids, rn, merged, move)
        # anything left active after level 0 has become a foot inside _step

    def _grow(self, r: np.ndarray, merged: np.ndarray, h: float) -> np.ndarray:
        P = self.P
        g = P.trunk_growth_per_mm / 2 * h
        taper = max(P.trunk_growth_per_mm / 2, (self.r_trunk - self.r_min) / 10.0) * h
        branch = (~merged) & (r < self.r_trunk)
        out = r + g
        out[branch] = np.minimum(self.r_trunk, r[branch] + taper[branch])
        return out

    def _step(self, j: int, ids, rn, merged, move):
        P, tree, zb, dz = self.P, self.tree, self.zb, self.dz
        N = len(ids)
        pos = np.array([tree.nodes[i].position for i in ids])
        xy = pos[:, :2]
        zj = zb + j * dz
        h = pos[:, 2] - zj
        r_next = self._grow(rn, merged, np.maximum(h, 0.0))

        # candidates (N, C, 2)
        C = len(_PATTERN)
        cand = xy[:, None, :] + _PATTERN[None, :, :] * move[:, None, None]
        flat = cand.reshape(-1, 2)
        md = self.mdist2(j, flat).reshape(N, C)
        mid = (0.5 * (cand + xy[:, None, :])).reshape(-1, 2)
        md_mid = self.mdist(j, mid).reshape(N, C)
        reach = self.reach(j, flat).reshape(N, C)
        hard_ok = (md >= self.r_min + self.hard) & (md_mid >= 0.5 * self.r_min)
        plate_ok = np.any(hard_ok & reach, axis=1)

        # ----- nearest neighbour targets -------------------------------------
        target = xy.copy()
        nb = np.full(N, -1)
        if N > 1:
            kd = cKDTree(xy)
            k = min(8, N)
            dd, ii = kd.query(xy, k=k)
            for a in range(N):
                for d, b in zip(dd[a][1:], ii[a][1:]):
                    if d > P.merge_distance:
                        break
                    if plate_ok[b] == plate_ok[a]:
                        nb[a] = b
                        target[a] = 0.5 * (xy[a] + xy[b])
                        break
                # repulsion from incompatible close nodes (min_tree_distance)
                for d, b in zip(dd[a][1:], ii[a][1:]):
                    if d > rn[a] + rn[b] + P.min_tree_distance:
                        break
                    if plate_ok[b] != plate_ok[a] and d > 1e-9:
                        target[a] = target[a] + (xy[a] - xy[b]) / d * move[a]

        # nodes that cannot reach the plate head for the nearest reachable cell when
        # they may not stand on the model (or when they have no neighbour to join)
        lost = np.nonzero(~plate_ok)[0]
        if len(lost) and j > 0:
            near = self._nearest_reachable(j - 1)
            if near is not None:
                for a in lost:
                    if P.allow_on_model and nb[a] >= 0:
                        continue
                    i_, j_, ok_ = self.grid.cell_index(xy[a])
                    if not ok_[0]:
                        continue
                    jj, ii = near[0][j_[0], i_[0]], near[1][j_[0], i_[0]]
                    if not P.allow_on_model:
                        target[a] = self.grid.origin + (np.array([ii, jj]) + 0.5) * self.grid.res

        # ----- merging (greedy clustering of mutually reachable pairs) ---------
        uf = _UF(N)
        members = {i: [i] for i in range(N)}
        cen = {i: xy[i].copy() for i in range(N)}
        pairs = []
        if N > 1:
            for a, b in cKDTree(xy).query_pairs(r=2 * float(move.max()) + 1e-9):
                if plate_ok[a] != plate_ok[b]:
                    continue
                d = float(np.linalg.norm(xy[a] - xy[b]))
                if d <= move[a] + move[b] + 1e-9:
                    pairs.append((d, int(a), int(b)))
        pairs.sort()
        for d, a, b in pairs:
            ra, rb = uf.find(a), uf.find(b)
            if ra == rb:
                continue
            mem = members[ra] + members[rb]
            c = self._meeting_point(xy[mem], rn[mem], move[mem])
            if c is None:
                continue
            rr = self._merged_radius(r_next[mem])
            mdc = self.mdist2(j, c)[0]
            if mdc < self.r_min + self.hard:
                continue
            if plate_ok[mem].any() and not self.reach(j, c)[0]:
                continue
            uf.p[rb] = ra
            members[ra] = mem
            cen[ra] = c
            del members[rb]

        new_ids, new_r, new_merged = [], [], []
        kind_level = NodeKind.FOOT if j == 0 else None
        for root, mem in members.items():
            if len(mem) > 1:
                c = cen[root]
                rr = self._merged_radius(r_next[mem])
                mdc = self.mdist2(j, c)[0]
                reff = max(self.r_min, min(rr, mdc - self.hard))
                node = tree.add([c[0], c[1], zj], reff, kind_level or NodeKind.TRUNK)
                for m_ in mem:
                    tree.nodes[int(ids[m_])].parent = node.id
                self._after_place(node, j, rr, True, new_ids, new_r, new_merged)
                continue
            a = mem[0]
            nid = int(ids[a])
            # model-mode node: land on the model when it gets close above it
            if not plate_ok[a] and P.allow_on_model:
                topz = self.grid.top_below(xy[a], pos[a, 2])
                land_len = P.tip_length + 2 * rn[a]
                if topz is not None and pos[a, 2] - topz <= land_len + dz and \
                        self._land_path_clear(nid, xy[a], pos[a, 2], topz):
                    self._land(nid, xy[a], pos[a, 2], topz, rn[a])
                    continue
            ok = hard_ok[a]
            if not ok.any():
                self._blocked(nid, xy[a], pos[a, 2], rn[a])
                continue
            # score the candidates
            d2 = np.sum((cand[a] - target[a]) ** 2, axis=1)
            soft = np.clip(r_next[a] + P.xy_clearance - md[a], 0.0, None)
            score = d2 + 20.0 * soft ** 2 + 0.01 * np.linalg.norm(cand[a] - xy[a], axis=1)
            if plate_ok[a]:
                score = score + np.where(reach[a], 0.0, 1e4)
            score = np.where(ok, score, np.inf)
            # the exact (clamped) target is a better candidate when it is valid
            t = target[a] - xy[a]
            lt = float(np.linalg.norm(t))
            q = cand[a][int(np.argmin(score))]
            best = float(score.min())
            if lt > 1e-9:
                qt = xy[a] + t * min(1.0, move[a] / lt)
                mdt = self.mdist2(j, qt)[0]
                mdm = self.mdist(j, 0.5 * (qt + xy[a]))[0]
                if mdt >= self.r_min + self.hard and mdm >= 0.5 * self.r_min and \
                        (not plate_ok[a] or self.reach(j, qt)[0]):
                    st = float(np.sum((qt - target[a]) ** 2)) + \
                        20.0 * max(0.0, r_next[a] + P.xy_clearance - mdt) ** 2 + 0.01 * min(lt, move[a])
                    if st <= best:
                        q = qt
            mdq = self.mdist2(j, q)[0]
            reff = max(self.r_min, min(r_next[a], mdq - self.hard))
            is_trunk = merged[a] or r_next[a] >= self.r_trunk - 1e-9
            kind = kind_level or (NodeKind.TRUNK if is_trunk else NodeKind.BRANCH)
            node = tree.add([q[0], q[1], zj], reff, kind)
            tree.nodes[nid].parent = node.id
            self._after_place(node, j, r_next[a], bool(merged[a]), new_ids, new_r, new_merged)
        return (np.asarray(new_ids, np.int64), np.asarray(new_r, float), np.asarray(new_merged, bool))

    @staticmethod
    def _meeting_point(pts: np.ndarray, r: np.ndarray, mv: np.ndarray):
        """A point every member can reach in this step (|c - p_i| <= move_i), biased
        towards the thicker members, or None."""
        w = r ** 2
        cands = [(pts * w[:, None]).sum(0) / w.sum(), pts.mean(0)]
        if len(pts) == 2:
            d = float(np.linalg.norm(pts[1] - pts[0]))
            if d > 1e-12:
                t = np.clip(mv[0] / d, 0.0, 1.0)
                lo = max(0.0, 1.0 - mv[1] / d)
                tw = float(np.clip(w[1] / w.sum(), lo, t))
                cands.insert(0, pts[0] + tw * (pts[1] - pts[0]))
        for c in cands:
            if np.all(np.linalg.norm(pts - c, axis=1) <= mv + 1e-9):
                return c
        return None

    def _nearest_reachable(self, k: int):
        if not hasattr(self, "_near_cache"):
            self._near_cache = {}
        if k not in self._near_cache:
            D = self.D[k]
            if not D.any():
                self._near_cache[k] = None
            else:
                _, ind = ndimage.distance_transform_edt(~D, return_indices=True)
                self._near_cache[k] = (ind[0], ind[1])
        return self._near_cache[k]

    def _merged_radius(self, rs: np.ndarray) -> float:
        P = self.P
        area = math.sqrt(float(np.sum(rs ** 2)))
        return max(self.r_trunk, float(rs.max()), min(area, P.branch_max_diameter / 2))

    def _after_place(self, node, j, r_nom, merged, new_ids, new_r, new_merged) -> None:
        if j == 0:
            node.kind = NodeKind.FOOT
            self.tree.foot_radius[node.id] = self._foot_r(node.position, node.radius)
            return
        new_ids.append(node.id)
        new_r.append(r_nom)
        new_merged.append(merged)

    def _land_path_clear(self, nid: int, xy, z: float, topz: float) -> bool:
        """Is the landing path of node nid (at (node xy, z)) to the surface point
        (xy, topz) free of the model? The part above topz + tip_length (branch from the node
        to the tip base) must keep `hard` clearance with its interpolated radius; the
        tip itself touches the surface by design and is not tested. A tilted landing
        must also respect the branch angle."""
        P, zb, dz = self.P, self.zb, self.dz
        node = self.tree.nodes[nid]
        p0 = np.array([node.position[0], node.position[1], z])
        z1 = topz + P.tip_length
        off = float(np.linalg.norm(np.asarray(xy, float) - p0[:2]))
        if z - topz <= P.tip_length + 0.2:
            # no intermediate node: one segment from the node down to the surface
            return off <= max(z - topz - 0.5 * P.tip_length, 0.0) * self.tan + 1e-6
        # with an intermediate node at z1 the tilted segment is node -> (xy, z1); the
        # tip below it is vertical. Measuring the angle down to the surface instead let
        # short drops lean up to ~75 deg: nearly flat branches that print in the air.
        # A landing is a last resort, so it may use the full printable angle.
        if off > (z - z1) * self.tan_print + 1e-6:
            return False
        p1 = np.array([xy[0], xy[1], z1])
        r0, r1 = float(node.radius), max(self.r_min, self.r_tipbase)
        n = max(2, int(math.ceil((z - z1) / (0.5 * dz))) + 1)
        # samples every dz / 2 (so every slab crossed is sampled); the start node itself is
        # skipped: it already exists and its clearance was decided when it was placed
        # (re-testing it rejected every landing of a tight tip base, QA-9)
        for t in np.linspace(0.0, 1.0, n)[1:]:
            q = p0 + t * (p1 - p0)
            k = int(math.floor((q[2] - zb) / dz + 1e-9))
            d = self.mdist(k, q[:2])[0]
            if d < (r0 + t * (r1 - r0)) + self.hard - 1e-9:
                return False
        return True

    def _land(self, nid: int, xy, z: float, topz: float, r_nom: float) -> None:
        P, tree = self.P, self.tree
        parent_id = nid
        if z - topz > P.tip_length + 0.2:
            mid = tree.add([xy[0], xy[1], topz + P.tip_length],
                           max(self.r_min, self.r_tipbase), NodeKind.BRANCH)
            tree.nodes[parent_id].parent = mid.id
            parent_id = mid.id
        foot = tree.add([xy[0], xy[1], topz - P.tip_penetration], P.on_model_tip_diameter / 2,
                        NodeKind.MODEL_FOOT)
        tree.nodes[parent_id].parent = foot.id
        self.landed.append(foot.id)

    def _blocked(self, nid: int, xy, z: float, r_nom: float) -> None:
        """No valid position below: land on the model if allowed, else fail.

        Landing spots are upward facing surfaces in the node's column or in a disc around
        it, reachable within the branch angle by a path that does not cross the model
        (a straight drop used to cut through legs / arms standing between the node and
        the surface below). The highest such surface (shortest support) wins."""
        if self.P.allow_on_model:
            xy = np.asarray(xy, float)
            R = self.dz * self.tan + 2 * r_nom
            spots = []
            for f in (0.0, 0.5, 1.0, 2.0):
                for off in (_PATTERN * (R * f) if f > 0 else _PATTERN[:1]):
                    q = xy + off
                    t = self.grid.top_below(q, z)
                    if t is not None and t > self.zb + 1e-6:
                        spots.append((-t, float(np.linalg.norm(off)), q, t))
            spots.sort(key=lambda s: (s[0], s[1]))
            for _, _, q, t in spots:
                if self._land_path_clear(nid, q, z, t):
                    self._land(nid, q, z, t, r_nom)
                    return
            self.fail_nodes[nid] = ("blocked by the model, no landing surface reachable "
                                    "without crossing the model")
        else:
            self.fail_nodes[nid] = ("cannot reach the build plate within the branch angle "
                                    "and supports on the model are disabled")

    # -- cleanup ------------------------------------------------------------------
    def _cleanup(self) -> None:
        tree = self.tree
        root_of: dict[int, int] = {}

        def root(i):
            path = []
            while i not in root_of:
                n = tree.nodes[i]
                if n.parent is None:
                    root_of[i] = i
                    break
                path.append(i)
                i = n.parent
            r = root_of[i]
            for p in path:
                root_of[p] = r
            return r

        for i in list(tree.nodes):
            root(i)
        bad_roots = {i for i in tree.nodes if tree.nodes[i].parent is None and
                     tree.nodes[i].kind not in (NodeKind.FOOT, NodeKind.MODEL_FOOT)}
        if bad_roots:
            for i in list(tree.nodes):
                r = root_of[i]
                if r in bad_roots:
                    n = tree.nodes[i]
                    if n.kind == NodeKind.TIP and n.contact_id is not None:
                        tree.failed[n.contact_id] = self.fail_nodes.get(r, "could not be routed")
                    del tree.nodes[i]
        model_roots = {i for i, n in tree.nodes.items() if n.kind == NodeKind.MODEL_FOOT}
        by_id = {c.id: c for c in self.contacts}
        for i, n in tree.nodes.items():
            if n.kind == NodeKind.TIP and n.contact_id in by_id:
                by_id[n.contact_id].rests_on_model = root_of[i] in model_roots
        for cid, why in tree.failed.items():
            if cid in by_id:
                by_id[cid].rests_on_model = False

    # -- printable radii -----------------------------------------------------------
    def _limit_widening(self) -> None:
        """Supports print without support: a segment may not widen upwards faster than
        the overhang angle allows. Node radii are clamped next to the model (clearance),
        so a node can be thinner than the one above it, and the segment between them
        then overhangs. The bottom of a node is also a sphere (junction: radius x
        junction_radius_factor, bend: about the node radius) that build.joint_at buries in the
        branch below with a conical skirt of half-angle <= phi - theta.

        Both stay printable when r_child * f <= r_parent - JOINT_INSET + L sin(phi - theta) (theta:
        tilt of the segment, f = build.joint_radius_factors: junction / bend sphere; sin
        is the skirt condition, slightly stricter than the cone's tan). Thinning the
        upper node (never the lower one) cannot hurt the clearance; it runs from the
        feet upwards so each limit uses the final lower radius."""
        from .build import joint_radius_factors
        tree, P = self.tree, self.P
        phi = math.radians(self.angle_print)
        f_junction, f_bend = joint_radius_factors(P)
        ch = tree.children()
        stack = [r.id for r in tree.roots()]
        while stack:
            pid = stack.pop()
            par = tree.nodes[pid]
            for cid in ch[pid]:
                c = tree.nodes[cid]
                stack.append(cid)
                if par.kind == NodeKind.FOOT or c.kind == NodeKind.TIP:
                    continue        # cylinder of the child radius / tip cone (narrows up)
                d = c.position - par.position
                L = float(np.linalg.norm(d))
                if L < 1e-9:
                    continue
                theta = math.atan2(float(np.linalg.norm(d[:2])), max(float(d[2]), 1e-12))
                f = f_junction if len(ch[cid]) > 1 else f_bend
                r_max = (par.radius - JOINT_INSET + L * math.sin(max(phi - theta, 0.0))) / f
                if c.radius > r_max:
                    c.radius = max(r_max, 1e-3)

    # -- braces -----------------------------------------------------------------------
    def _braces(self) -> None:
        P, tree, zb = self.P, self.tree, self.zb
        ch = tree.children()
        trunks = []
        for f in tree.roots():
            if f.kind != NodeKind.FOOT:
                continue
            path = [f.id]
            cur = f.id
            while len(ch[cur]) == 1 and tree.nodes[ch[cur][0]].kind != NodeKind.TIP:
                cur = ch[cur][0]
                path.append(cur)
            pts = np.array([tree.nodes[i].position for i in path])
            rad = np.array([tree.nodes[i].radius for i in path])
            if len(pts) < 2 or pts[-1, 2] - zb <= P.brace_min_height:
                continue
            if np.any(np.diff(pts[:, 2]) <= 0):
                continue
            trunks.append((pts, rad, f.id))
        links: list[tuple[int, int]] = []
        if len(trunks) >= 2:
            fxy = np.array([t[0][0, :2] for t in trunks])
            kd = cKDTree(fxy)
            pairs = set()
            for a in range(len(trunks)):
                d, ii = kd.query(fxy[a], k=min(3, len(trunks)))
                for dist, b in zip(np.atleast_1d(d)[1:], np.atleast_1d(ii)[1:]):
                    if dist <= P.brace_max_length * 1.5:
                        pairs.add((min(a, int(b)), max(a, int(b))))
            for a, b in sorted(pairs):
                if self._brace_pair(trunks[a][:2], trunks[b][:2], P.brace_max_length):
                    links.append((trunks[a][2], trunks[b][2]))
        self._bridge_isolated(ch, links)

    @staticmethod
    def _trunk_at(tr, z):
        pts, rad = tr
        x = np.interp(z, pts[:, 2], pts[:, 0])
        y = np.interp(z, pts[:, 2], pts[:, 1])
        return np.array([x, y, z]), float(np.interp(z, pts[:, 2], rad))

    def _brace_pair(self, ta, tb, max_run: float, bridge: bool = False) -> int:
        """Diagonal braces between two trunks (pts, radii), one every brace_spacing_z,
        alternating direction; returns how many were added. bridge: a link for an isolated
        group (longer, thickened to BRIDGE_SLENDER_MAX, full model clearance, and it may
        climb from a short neighbour onto a taller trunk)."""
        P, zb = self.P, self.zb
        ztop = min(ta[0][-1, 2], tb[0][-1, 2]) - 0.5
        zend = max(ta[0][-1, 2], tb[0][-1, 2]) - 0.5 if bridge else ztop
        added = 0
        # a bridge may start just above the foot collars (short neighbours of ~20 mm leave
        # no room for a 40 deg diagonal starting at brace_spacing_z)
        z_first = zb + (P.foot_height + P.collar_height + 1.0 if bridge else P.brace_spacing_z)
        # bridge: levels tried every BRIDGE_STEP_Z until one fits, then brace_spacing_z
        step = BRIDGE_STEP_Z if bridge else P.brace_spacing_z
        z_next = z_first
        flip = False
        while True:
            z0 = z_next
            if z0 >= zend:
                break
            z_next = z0 + step
            order = [(ta, tb), (tb, ta)] if not flip else [(tb, ta), (ta, tb)]
            for lo_t, hi_t in (order if bridge else order[:1]):
                # bridge: each end only has to stay below the top of its own trunk
                zt = (hi_t[0][-1, 2] - 0.5) if bridge else ztop
                if bridge and z0 >= lo_t[0][-1, 2] - 0.5:
                    continue
                b = self._one_brace(lo_t, hi_t, z0, zt, max_run, bridge)
                if b is not None:
                    self.tree.braces.append(b)
                    added += 1
                    flip = not flip
                    z_next = z0 + P.brace_spacing_z
                    break
        return added

    def _one_brace(self, lo_t, hi_t, z0, ztop, max_run, bridge):
        P, zb = self.P, self.zb
        rb = P.brace_diameter / 2
        tana = math.tan(math.radians(max(self.angle, 1.0)))
        at = self._trunk_at
        p0, r0 = at(lo_t, z0)
        q0, _ = at(hi_t, z0)
        d = float(np.linalg.norm(p0[:2] - q0[:2]))
        if d > max_run or d < r0 + 1.0:
            return None
        rise = d / tana
        z1 = z0 + rise
        if z1 >= ztop:
            return None
        p1, r1 = at(hi_t, z1)
        run = float(np.linalg.norm(p1[:2] - p0[:2]))
        if run > max_run:
            return None
        if run > (z1 - z0) * self.tan_print:
            # the upper trunk moved away between z0 and z1: this brace would lean
            # beyond the printable angle (it prints without support of its own)
            return None
        if not bridge:
            rr = rb
            s = np.linspace(0.15, 0.85, 8)
            clear = rb + 0.5 * P.xy_clearance
        else:
            L = float(np.linalg.norm(p1 - p0))
            rr = max(rb, L / (2.0 * BRIDGE_SLENDER_MAX))
            if rr > 0.85 * min(r0, r1):
                return None          # thicker than the trunks it joins
            n = max(8, int(math.ceil(L / max(0.5 * self.grid.res, 0.25))))
            s = np.linspace(0.0, 1.0, n)
            hx = run * s
            s = s[(hx > r0) & (hx < run - r1)]   # outside both trunks (already clear)
            clear = rr + P.xy_clearance           # like the branches
        pts = p0[None, :] + s[:, None] * (p1 - p0)[None, :]
        for pt in pts:
            k = int((pt[2] - zb) // self.dz)
            if self.mdist(k, pt[:2])[0] < clear:
                return None
        return (p0, p1, rr)

    def _bridge_isolated(self, ch, links) -> None:
        """Link every braced group holding a tree taller than brace_min_height that is not
        stable (braced_group_stable) to its nearest neighbouring trunk, until it is stable
        or no link can be built (verify then reports isolated_tall_tree). Nothing changes
        when no tall tree is in such a group."""
        P, tree, zb = self.P, self.tree, self.zb
        nodes = tree.nodes
        feet = [f.id for f in tree.roots() if f.kind == NodeKind.FOOT]
        if len(feet) < 2:
            return
        top: dict[int, float] = {}
        for f in feet:                           # subtree tops, post-order
            order, st = [], [f]
            while st:
                i = st.pop()
                order.append(i)
                st.extend(ch[i])
            for i in reversed(order):
                top[i] = max([float(nodes[i].position[2])] + [top[c] for c in ch[i]])
        H = {f: top[f] - zb for f in feet}
        if not any(h > P.brace_min_height for h in H.values()):
            return
        spine = {}
        for f in feet:                           # main trunk: tallest trunk child each time
            path, cur = [f], f
            while True:
                kids = [c for c in ch[cur] if nodes[c].kind == NodeKind.TRUNK
                        and nodes[c].position[2] > nodes[cur].position[2]]
                if not kids:
                    break
                cur = max(kids, key=lambda c: top[c])
                path.append(cur)
            if len(path) >= 2 and nodes[path[-1]].position[2] - zb > P.foot_height + P.collar_height + 1.5:
                spine[f] = (np.array([nodes[i].position for i in path]),
                            np.array([nodes[i].radius for i in path]))
        foot_r = tree.foot_radius
        par = {f: f for f in feet}

        def find(a):
            while par[a] != a:
                par[a] = par[par[a]]
                a = par[a]
            return a

        for a, b in links:
            if a in par and b in par:
                par[find(a)] = find(b)

        def groups():
            g: dict[int, list[int]] = {}
            for f in feet:
                g.setdefault(find(f), []).append(f)
            return list(g.values())

        def stable(mem):
            xy = np.array([nodes[f].position[:2] for f in mem])
            rad = np.array([float(foot_r.get(f, P.foot_diameter / 2)) for f in mem])
            return braced_group_stable(foot_group_width(xy, rad), max(H[f] for f in mem))

        def gap(a, b):
            ta, tb = spine[a], spine[b]
            z0 = zb + P.foot_height + P.collar_height + 1.0
            z1 = max(ta[0][-1, 2], tb[0][-1, 2])
            if z1 <= z0:
                return BIG
            zs = np.arange(z0, z1, 1.0)
            pa = np.stack([np.interp(zs, ta[0][:, 2], ta[0][:, k]) for k in (0, 1)], axis=1)
            pb = np.stack([np.interp(zs, tb[0][:, 2], tb[0][:, k]) for k in (0, 1)], axis=1)
            return float(np.linalg.norm(pa - pb, axis=1).min())

        max_run = BRIDGE_LENGTH_FACTOR * P.brace_max_length
        given_up: set[frozenset] = set()
        for _ in range(4 * len(feet)):
            todo = [m for m in groups()
                    if any(H[f] > P.brace_min_height for f in m) and not stable(m)
                    and frozenset(m) not in given_up]
            if not todo:
                break
            todo.sort(key=lambda m: -max(H[f] for f in m))
            m = todo[0]
            ms = set(m)
            cand = []
            for a in m:
                if a not in spine:
                    continue
                for b in spine:
                    if b in ms:
                        continue
                    g = gap(a, b)
                    if g <= max_run:
                        cand.append((g, a, b))
            cand.sort()
            linked = False
            for _, a, b in cand[:12]:
                if self._brace_pair(spine[a], spine[b], max_run, bridge=True):
                    par[find(a)] = find(b)
                    linked = True
                    break
            if not linked:
                given_up.add(frozenset(m))

    # -- stability ---------------------------------------------------------------
    # Height of a tree / radius of its foot above which the foot is enlarged (verify flags
    # unstable_trunk at 18). First print test (A1 mini, 0.2 nozzle): a 49 mm tree on a
    # 3.5 mm foot detached half way through the print.
    FOOT_SLENDERNESS = 14.0

    def _stabilize_feet(self) -> None:
        """Enlarge the foot of tall trees in proportion to their height, as far as the
        clearance to the model allows (the raft, when enabled, then joins them)."""
        tree, P = self.tree, self.P
        if not tree.nodes:
            return
        root_of: dict[int, int] = {}
        top: dict[int, float] = {}
        for nid in tree.nodes:
            path, i = [], nid
            while i not in root_of and tree.nodes[i].parent is not None and len(path) < 100000:
                path.append(i)
                i = tree.nodes[i].parent
            r = root_of.get(i, i)
            for q in path + [i]:
                root_of[q] = r
            top[r] = max(top.get(r, -1e9), float(tree.nodes[nid].position[2]))
        k_top = int(math.ceil((P.foot_height + P.collar_height) / self.dz))
        for r, ztop in top.items():
            n = tree.nodes[r]
            if n.kind != NodeKind.FOOT:
                continue
            h = ztop - self.zb
            cur = float(tree.foot_radius.get(r, P.foot_diameter / 2))
            want = h / self.FOOT_SLENDERNESS
            if want <= cur:
                continue
            md = min(self.mdist(k, n.position[:2])[0] for k in range(0, k_top + 1))
            new = min(want, md - 0.2)
            if new > cur:
                tree.foot_radius[r] = float(new)

    def _raft_keepout(self) -> None:
        from shapely.geometry import box
        from shapely.ops import unary_union
        P = self.P
        occ = self.grid.footprint_below(self.zb + P.foot_height + P.collar_height)
        if not occ.any():
            return
        g = self.grid
        rects = []
        for jj in np.nonzero(occ.any(axis=1))[0]:
            row = occ[jj].astype(np.int8)
            d = np.diff(np.concatenate([[0], row, [0]]))
            st = np.nonzero(d == 1)[0]
            en = np.nonzero(d == -1)[0]
            y0 = g.origin[1] + jj * g.res
            for s, e in zip(st, en):
                rects.append(box(g.origin[0] + s * g.res, y0, g.origin[0] + e * g.res, y0 + g.res))
        self.tree.raft_keepout = unary_union(rects).buffer(P.xy_clearance)

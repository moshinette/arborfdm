"""Contact placement: where support tips touch the model, and why.

Priority order (earlier candidates win, later ones are skipped when an
accepted contact already covers them):

  0. manual contacts (kept unchanged; they also cover their neighbourhood)
  1. one mandatory contact per island (an island without support prints in the air)
  2. low points (local Z minima: arm tips, chin, ...)
  3. corners of flat overhangs
  4. rows along hanging edges: segments are chained into polylines (cut at
     turns > EDGE_BREAK_DEG) and sampled evenly, ends included, with a gap
     <= edge_spacing / contact_density (independent of the tessellation)
  5. island fill + overhang fill from the sliced regions, hex grid at
     contact_spacing / contact_density (thin slope strips are sampled along
     their outline instead)
  6. residual fill: a global XY lattice lifted on the overhanging faces; any
     sample farther than the contact spacing from every contact gets one
     (catches slopes whose per-layer strips fell under min_region_area)

Samples below one layer height lie on the plate and are never kept.

Coverage rule: a candidate is skipped when an accepted contact lies within
min_contact_distance (any kind) or within the coverage radius of its kind:
0.55 x spacing for edge and surface candidates. Grid neighbours (distance =
spacing) therefore survive, while redundant samples are removed; along a
densely sampled line the resulting gaps lie in [0.55, 1.1] x spacing.

Positions are projected on the model with a vertical ray cast upward from
below the region (first downward facing triangle hit). Normals are the
outward normal of the hit triangle (area weighted vertex normal for low
points). Contacts whose downward path hits the model are NOT dropped: the
tree builder decides whether they stand on the model.

Detail avoidance heuristic (edge and surface contacts only): the local
roughness at a point is 1 - |area weighted mean normal| of the downward
facing triangles (n_z < -0.05) whose centroid lies within DETAIL_RADIUS
(0.4 mm). It is 0 on a flat or gently curved patch and grows on fine relief
(scales, hair, teeth). Vertical walls are ignored so the edge of a flat shelf
is not mistaken for a detail. If a point is rough (> 0.02), 6 alternative
positions on a circle of radius min(0.25 x spacing, 0.6 mm) are cast and the
smoothest one is kept if it is clearly smoother (gain > 0.02), hits an
overhanging face and stays within 1 layer of the original height. The
displacement is below half the coverage radius, so coverage is preserved.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
import shapely

from .params import SupportParams
from .types import AnalysisResult, Contact, ContactReason, Progress

COVER_FACTOR = 0.55
ROUGH_THRESHOLD = 0.02
ROUGH_GAIN = 0.02
DETAIL_RADIUS = 0.4
ISLAND_Z_SEARCH = 3.0
FILL_BLOCK = 4096          # candidates per detail-avoidance / offer block
LATTICE_BLOCK = 1_000_000  # lattice candidates generated at once
RESIDUAL_MAX_SAMPLES = 2_000_000  # above this the residual lattice pitch is enlarged
CAST_POINT_BLOCK = 8192    # points per KD-tree query block in VerticalCaster.cast
CAST_MAX_PAIRS = 2_000_000  # max (point, triangle) pairs tested at once
EDGE_BREAK_DEG = 45.0     # a hanging edge polyline is cut where it turns more than this
RESIDUAL_PITCH = 0.2       # lattice pitch of the residual check, fraction of contact spacing


# ---------------------------------------------------------------------------
# vertical ray caster restricted to downward facing triangles
# ---------------------------------------------------------------------------
class VerticalCaster:
    """Cast vertical rays (+Z) from points, return the first downward facing
    triangle hit. Only triangles with n_z < 0 can be hit from below on a
    consistently oriented mesh, which halves the work.

    Small triangles are indexed in a KD-tree of XY centroids queried with the
    maximum small-triangle circumradius; the few large triangles are tested
    by brute force (chunked)."""

    def __init__(self, mesh: trimesh.Trimesh):
        v = np.asarray(mesh.vertices, dtype=np.float64)
        f = np.asarray(mesh.faces, dtype=np.int64)
        n = np.asarray(mesh.face_normals, dtype=np.float64)
        down = np.nonzero(n[:, 2] < -1e-9)[0]
        self.area = np.asarray(mesh.area_faces, dtype=np.float64)[down]
        self.fid = down
        tri = v[f[down]]                     # (k,3,3)
        self.a = tri[:, 0, :2]
        self.e1 = tri[:, 1, :2] - self.a
        self.e2 = tri[:, 2, :2] - self.a
        self.det = self.e1[:, 0] * self.e2[:, 1] - self.e1[:, 1] * self.e2[:, 0]
        self.n = n[down]
        self.d = (self.n * tri[:, 0]).sum(axis=1)
        c = tri[:, :, :2].mean(axis=1)
        r = np.linalg.norm(tri[:, :, :2] - c[:, None, :], axis=2).max(axis=1)
        if len(r):
            # "small" radius: generous w.r.t. the typical triangle so that only
            # genuinely big triangles (CAD flats) go to the brute force list
            self.R = max(float(np.percentile(r, 99.5)), 4.0 * float(np.median(r)), 1e-6)
            if (r > self.R).sum() > 256:
                self.R = float(np.sort(r)[-257])
        else:
            self.R = 1.0
        small = r <= self.R
        self.small = np.nonzero(small)[0]
        self.large = np.nonzero(~small)[0]
        self.tree = cKDTree(c[self.small]) if len(self.small) else None
        self.centroid_tree = cKDTree(tri.mean(axis=1)) if len(down) else None

    def _test(self, pi: np.ndarray, ti: np.ndarray, pts: np.ndarray):
        """Return z of hit for pairs (point index, local triangle index), NaN if miss."""
        p = pts[pi, :2] - self.a[ti]
        det = self.det[ti]
        ok = np.abs(det) > 1e-14
        det = np.where(ok, det, 1.0)
        u = (p[:, 0] * self.e2[ti, 1] - p[:, 1] * self.e2[ti, 0]) / det
        w = (self.e1[ti, 0] * p[:, 1] - self.e1[ti, 1] * p[:, 0]) / det
        eps = 1e-7
        inside = ok & (u >= -eps) & (w >= -eps) & (u + w <= 1 + eps)
        nz = self.n[ti, 2]
        z = (self.d[ti] - self.n[ti, 0] * pts[pi, 0] - self.n[ti, 1] * pts[pi, 1]) / nz
        return np.where(inside, z, np.nan)

    def cast(self, pts: np.ndarray, tol: float = 1e-6):
        """pts (N,3). Returns (z_hit (N,), face index into mesh (N,) or -1)."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        N = len(pts)
        best = np.full(N, np.inf)
        bestf = np.full(N, -1, dtype=np.int64)
        if N == 0 or len(self.fid) == 0:
            return np.full(N, np.nan), bestf

        def consider(pi, ti):
            if len(pi) == 0:
                return
            z = self._test(pi, ti, pts)
            good = np.isfinite(z) & (z >= pts[pi, 2] - tol)
            pi, ti, z = pi[good], ti[good], z[good]
            if len(pi) == 0:
                return
            order = np.lexsort((z, pi))
            pi, ti, z = pi[order], ti[order], z[order]
            first = np.ones(len(pi), dtype=bool)
            first[1:] = pi[1:] != pi[:-1]
            pi, ti, z = pi[first], ti[first], z[first]
            upd = z < best[pi]
            best[pi[upd]] = z[upd]
            bestf[pi[upd]] = ti[upd]

        if self.tree is not None:
            # bounded memory: points are processed in blocks, and the (point,
            # triangle) pairs of a block in slices of at most CAST_MAX_PAIRS. All
            # pairs of one point stay in the same slice, so the result is the
            # same as an unchunked pass.
            for s0 in range(0, N, CAST_POINT_BLOCK):
                blk = np.arange(s0, min(N, s0 + CAST_POINT_BLOCK))
                lists = self.tree.query_ball_point(pts[blk, :2], self.R * 1.0001 + 1e-9)
                lens = np.fromiter((len(x) for x in lists), dtype=np.int64, count=len(blk))
                if not lens.sum():
                    continue
                cum = np.cumsum(lens)
                a = 0
                while a < len(blk):
                    base = cum[a - 1] if a else 0
                    b = int(np.searchsorted(cum, base + CAST_MAX_PAIRS, side="right"))
                    b = max(b, a + 1)
                    sub = lens[a:b]
                    if sub.sum():
                        pi = np.repeat(blk[a:b], sub)
                        ti = self.small[np.concatenate(
                            [np.asarray(x, dtype=np.int64) for x in lists[a:b] if x])]
                        consider(pi, ti)
                    a = b
                del lists
        if len(self.large):
            chunk = max(1, CAST_MAX_PAIRS // len(self.large))
            for s in range(0, N, chunk):
                idx = np.arange(s, min(N, s + chunk))
                pi = np.repeat(idx, len(self.large))
                ti = np.tile(self.large, len(idx))
                consider(pi, ti)
        hit = bestf >= 0
        zout = np.where(hit, best, np.nan)
        fout = np.where(hit, self.fid[np.maximum(bestf, 0)], -1)
        return zout, fout

    def nearest_down_face(self, pts: np.ndarray):
        """Fallback: nearest downward face (by centroid), point projected on its plane."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        if self.centroid_tree is None or len(pts) == 0:
            return pts.copy(), np.full(len(pts), -1, dtype=np.int64)
        _, k = self.centroid_tree.query(pts)
        n = self.n[k]
        dist = (pts * n).sum(axis=1) - self.d[k]
        return pts - dist[:, None] * n, self.fid[k]


# ---------------------------------------------------------------------------
# coverage structure (incremental hash grid)
# ---------------------------------------------------------------------------
class _Coverage:
    def __init__(self, cell: float):
        self.cell = max(cell, 1e-3)
        self.grid: dict[tuple, list[int]] = {}
        self.pts: list[np.ndarray] = []

    def _key(self, p):
        return (int(math.floor(p[0] / self.cell)), int(math.floor(p[1] / self.cell)),
                int(math.floor(p[2] / self.cell)))

    def nearest_within(self, p: np.ndarray, r: float):
        """Index of an accepted point within distance r (closest), or -1."""
        kx, ky, kz = self._key(p)
        span = int(math.ceil(r / self.cell))
        best, bi = r * r, -1
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for dz in range(-span, span + 1):
                    for i in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                        q = self.pts[i]
                        d2 = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2
                        if d2 <= best:
                            best, bi = d2, i
        return bi

    def add(self, p: np.ndarray) -> int:
        self.pts.append(p)
        i = len(self.pts) - 1
        self.grid.setdefault(self._key(p), []).append(i)
        return i


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _hex_grid(poly, s: float) -> np.ndarray:
    minx, miny, maxx, maxy = poly.bounds
    dy = s * math.sqrt(3) / 2
    ys = np.arange(miny + dy / 2, maxy, dy)
    if len(ys) == 0:
        return np.zeros((0, 2))
    xs = np.arange(minx + s / 2, maxx + s, s)
    X, Y = np.meshgrid(xs, ys)
    X = X + (np.arange(len(ys))[:, None] % 2) * (s / 2)
    pts = np.stack([X.ravel(), Y.ravel()], axis=1)
    inside = shapely.contains_xy(poly, pts[:, 0], pts[:, 1])
    return pts[inside]


def _outline_samples(poly, s: float) -> np.ndarray:
    """Samples along the outline of a thin region (spacing s/2)."""
    out = []
    for g in shapely.get_parts(poly):
        ring = g.exterior
        L = ring.length
        n = max(1, int(math.ceil(L / (0.5 * s))))
        d = (np.arange(n) + 0.5) * (L / n)
        pts = shapely.get_coordinates(shapely.line_interpolate_point(ring, d))
        out.append(pts)
    return np.concatenate(out) if out else np.zeros((0, 2))


def _edge_polylines(E: np.ndarray, break_deg: float = EDGE_BREAK_DEG) -> list[np.ndarray]:
    """Chain hanging edge segments (K,2,3) into polylines (list of (n,3) arrays).

    Segments sharing an endpoint are joined; a polyline is cut at branch points
    (degree != 2) and at turns sharper than break_deg (corners), so the result
    depends on the geometry of the edge, not on how many mesh segments it has.
    Closed loops are returned with their first point repeated at the end."""
    if len(E) == 0:
        return []
    ends = E.reshape(-1, 3)
    key = np.round(ends / 1e-6).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    nodes = np.zeros((len(uniq), 3))
    nodes[inv] = ends
    seg = inv.reshape(-1, 2)
    seg = seg[seg[:, 0] != seg[:, 1]]
    if len(seg) == 0:
        return []
    seg = np.unique(np.sort(seg, axis=1), axis=0)
    adj: dict[int, list[int]] = {}
    for a, b in seg:
        adj.setdefault(int(a), []).append(int(b))
        adj.setdefault(int(b), []).append(int(a))
    cos_b = math.cos(math.radians(break_deg))
    brk = set()
    for k, nb in adj.items():
        if len(nb) != 2:
            brk.add(k)
            continue
        d1 = nodes[k] - nodes[nb[0]]
        d2 = nodes[nb[1]] - nodes[k]
        n1, n2 = np.linalg.norm(d1), np.linalg.norm(d2)
        if n1 > 0 and n2 > 0 and float(d1 @ d2) / (n1 * n2) < cos_b:
            brk.add(k)
    used: set[tuple[int, int]] = set()

    def walk(start: int, nxt: int) -> list[int]:
        chain = [start]
        prev, cur = start, nxt
        while True:
            used.add((min(prev, cur), max(prev, cur)))
            chain.append(cur)
            if cur in brk or cur == start:
                return chain
            cand = [n for n in adj[cur] if (min(cur, n), max(cur, n)) not in used]
            if not cand:
                return chain
            prev, cur = cur, cand[0]

    out = []
    for s in sorted(brk):                     # open chains, deterministic order
        for n in sorted(adj[s]):
            if (min(s, n), max(s, n)) not in used:
                out.append(nodes[walk(s, n)])
    for s in sorted(adj):                     # remaining smooth closed loops
        for n in sorted(adj[s]):
            if (min(s, n), max(s, n)) not in used:
                out.append(nodes[walk(s, n)])
    return out


def _sample_polyline(pl: np.ndarray, step: float):
    """Evenly spaced samples along a polyline: ends included, gap <= step.
    Returns (points (n,3), XY tangent (n,2), actual spacing)."""
    d = np.diff(pl, axis=0)
    sl = np.linalg.norm(d, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(sl)])
    L = float(cum[-1])
    closed = len(pl) > 2 and np.linalg.norm(pl[0] - pl[-1]) < 1e-9
    if L <= 1e-9:
        return pl[:1].copy(), np.zeros((1, 2)), 0.0
    if L < 0.5 * step and not closed:
        t = np.array([0.5 * L])
        gap = L
    else:
        n = max(1, int(math.ceil(L / step - 1e-9)))
        t = np.arange(n + 1) * (L / n)
        if closed:
            t = t[:-1]
        gap = L / n
    j = np.clip(np.searchsorted(cum, t, side="right") - 1, 0, len(sl) - 1)
    u = (t - cum[j]) / np.maximum(sl[j], 1e-12)
    pts = pl[j] + u[:, None] * d[j]
    return pts, d[j, :2], gap


def _lattice_on_faces(v: np.ndarray, f: np.ndarray, ids: np.ndarray, g: float,
                      max_points: int | None = None, block: int | None = None,
                      return_pitch: bool = False):
    """Points of the global XY lattice (pitch g, anchored at the origin) that fall
    inside the XY projection of the given faces, lifted onto each face plane.

    The lattice does not depend on the triangulation: the same surface gives the
    same samples whatever its tessellation. Returns (points (N,3), face ids (N,))
    and, with return_pitch, the pitch actually used.

    Memory is bounded: faces are processed in blocks of at most `block` lattice
    candidates (bounding box points), and if the whole surface would need more
    than `max_points` candidates the pitch is enlarged (by sqrt of the excess) so
    that a tiny contact spacing cannot exhaust the memory."""
    max_points = RESIDUAL_MAX_SAMPLES if max_points is None else max_points
    block = LATTICE_BLOCK if block is None else block
    empty = (np.zeros((0, 3)), np.zeros(0, dtype=np.int64))
    if len(ids) == 0:
        return (*empty, g) if return_pitch else empty
    tri = v[f[ids]]
    txy = tri[:, :, :2]
    mn, mx = txy.min(axis=1), txy.max(axis=1)
    for _ in range(8):
        lo = np.ceil(mn / g - 1e-9).astype(np.int64)
        hi = np.floor(mx / g + 1e-9).astype(np.int64)
        nx = np.maximum(hi[:, 0] - lo[:, 0] + 1, 0)
        ny = np.maximum(hi[:, 1] - lo[:, 1] + 1, 0)
        cnt = nx * ny
        total = int(cnt.sum())
        if total <= max_points:
            break
        g *= math.sqrt(total / max_points) * 1.01
    out_p, out_f = [], []
    cum = np.cumsum(cnt)
    f0 = 0
    while f0 < len(ids):
        base = cum[f0 - 1] if f0 else 0
        f1 = max(f0 + 1, int(np.searchsorted(cum, base + block, side="right")))
        sl = slice(f0, f1)
        c = cnt[sl]
        f0 = f1
        if not c.sum():
            continue
        k = np.repeat(np.arange(sl.start, sl.stop), c)
        r = np.arange(len(k)) - np.repeat(np.cumsum(c) - c, c)
        nyk = np.maximum(ny[k], 1)
        x = (lo[k, 0] + r // nyk) * g
        y = (lo[k, 1] + r % nyk) * g
        a = tri[k, 0]
        e1 = tri[k, 1] - a
        e2 = tri[k, 2] - a
        det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
        ok = np.abs(det) > 1e-14
        det = np.where(ok, det, 1.0)
        px, py = x - a[:, 0], y - a[:, 1]
        u = (px * e2[:, 1] - py * e2[:, 0]) / det
        w = (e1[:, 0] * py - e1[:, 1] * px) / det
        eps = 1e-9
        inside = ok & (u >= -eps) & (w >= -eps) & (u + w <= 1 + eps)
        z = a[:, 2] + u * e1[:, 2] + w * e2[:, 2]
        out_p.append(np.column_stack([x, y, z])[inside])
        out_f.append(ids[k[inside]])
    pts = np.concatenate(out_p) if out_p else empty[0]
    fid = np.concatenate(out_f) if out_f else empty[1]
    return (pts, fid, g) if return_pitch else (pts, fid)


def _vertex_normals(v, f, fn, area):
    w = fn * area[:, None]
    vn = np.zeros((len(v), 3))
    for k in range(3):
        for c in range(3):
            vn[:, c] += np.bincount(f[:, k], weights=w[:, c], minlength=len(v))
    nrm = np.linalg.norm(vn, axis=1, keepdims=True)
    return vn / np.maximum(nrm, 1e-12)


def _mask_components(f: np.ndarray, mask: np.ndarray, nv: int):
    """Connected components (sharing an edge) of the masked faces.
    Returns (face ids (k,), labels (k,))."""
    ids = np.nonzero(mask)[0]
    if len(ids) == 0:
        return ids, ids
    sub = f[ids]
    d = np.stack([sub[:, [0, 1]], sub[:, [1, 2]], sub[:, [2, 0]]], axis=1).reshape(-1, 2)
    d = np.sort(d, axis=1)
    key = d[:, 0].astype(np.int64) * nv + d[:, 1]
    owner = np.repeat(np.arange(len(ids)), 3)
    order = np.argsort(key, kind="stable")
    ks, os_ = key[order], owner[order]
    same = ks[1:] == ks[:-1]
    a, b = os_[:-1][same], os_[1:][same]
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(ids), len(ids)))
    _, labels = connected_components(g, directed=False)
    return ids, labels


class _Placer:
    def __init__(self, mesh, analysis, params, manual):
        self.mesh = mesh
        self.params = params
        self.h = analysis.layer_height
        dens = params.contact_density
        self.s = params.contact_spacing / dens
        self.e = params.edge_spacing / dens
        self.dmin = params.min_contact_distance
        self.r_s = max(self.dmin, COVER_FACTOR * self.s)
        self.r_e = max(self.dmin, COVER_FACTOR * self.e)
        self.cov = _Coverage(max(self.r_s, self.r_e))
        self.out: list[Contact] = []
        self.manual = manual
        self.next_id = max([c.id for c in manual], default=-1) + 1
        self.v = np.asarray(mesh.vertices, dtype=np.float64)
        self.f = np.asarray(mesh.faces, dtype=np.int64)
        self.fn = np.asarray(mesh.face_normals, dtype=np.float64)
        self.area = np.asarray(mesh.area_faces, dtype=np.float64)
        self.vn = _vertex_normals(self.v, self.f, self.fn, self.area)
        self.caster = VerticalCaster(mesh)
        self.mask = analysis.overhang_face_mask
        if self.mask is None or len(self.mask) != len(self.f):
            self.mask = np.zeros(len(self.f), dtype=bool)
        self.slot_of: list[int] = []      # coverage index -> index in self.out (-1 manual)
        for c in manual:
            self.cov.add(np.asarray(c.position, float))
            self.slot_of.append(-1)
        self.stats = {"skipped_covered": 0, "projection_fallback": 0, "detail_moved": 0}

    # ------------------------------------------------------------------
    def roughness(self, pts: np.ndarray) -> np.ndarray:
        """1 - |mean normal| of downward faces within DETAIL_RADIUS (0 = flat)."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        r = np.zeros(len(pts))
        ct = self.caster.centroid_tree
        if ct is None or len(pts) == 0:
            return r
        nz = self.caster.n[:, 2]
        w = self.caster.area * (nz < -0.05)
        lists = ct.query_ball_point(pts, DETAIL_RADIUS)
        for i, lst in enumerate(lists):
            if len(lst) < 2:
                continue
            lst = np.asarray(lst)
            ww = w[lst]
            tot = ww.sum()
            if tot <= 0:
                continue
            mean = (self.caster.n[lst] * ww[:, None]).sum(axis=0) / tot
            r[i] = 1.0 - float(np.linalg.norm(mean))
        return r

    def project(self, xy: np.ndarray, z_start: np.ndarray, z_max: np.ndarray):
        """Project XY samples on the first downward surface above z_start.
        Returns positions (N,3), face ids (N,)."""
        N = len(xy)
        pts = np.column_stack([xy, z_start])
        z, fid = self.caster.cast(pts)
        bad = ~(np.isfinite(z) & (z <= z_max))
        pos = np.column_stack([xy, np.where(bad, z_start, z)])
        if bad.any():
            self.stats["projection_fallback"] += int(bad.sum())
            p2, f2 = self.caster.nearest_down_face(pos[bad])
            pos[bad] = p2
            fid[bad] = f2
        return pos, fid

    def avoid_details(self, pos: np.ndarray, fid: np.ndarray, radius: float):
        if len(pos) == 0:
            return pos, fid
        rough = self.roughness(pos)
        idx = np.nonzero(rough > ROUGH_THRESHOLD)[0]
        if len(idx) == 0:
            return pos, fid
        ang = np.linspace(0, 2 * math.pi, 6, endpoint=False)
        off = radius * np.column_stack([np.cos(ang), np.sin(ang)])
        cand_xy = (pos[idx, None, :2] + off[None]).reshape(-1, 2)
        zs = np.repeat(pos[idx, 2], 6)
        cz, cfid = self.caster.cast(np.column_stack([cand_xy, zs - 2 * self.h]))
        cpos = np.column_stack([cand_xy, np.nan_to_num(cz, nan=-1e9)])
        crough = self.roughness(np.where(np.isfinite(cz)[:, None], cpos, pos[np.repeat(idx, 6)]))
        crough = crough.reshape(-1, 6)
        cfid = cfid.reshape(-1, 6)
        cpos = cpos.reshape(-1, 6, 3)
        valid = (cfid >= 0) & (np.abs(cpos[..., 2] - pos[idx, None, 2]) <= self.h) \
            & self.mask[np.maximum(cfid, 0)]
        crough = np.where(valid, crough, np.inf)
        k = crough.argmin(axis=1)
        gain = rough[idx] - crough[np.arange(len(idx)), k]
        move = gain > ROUGH_GAIN
        pos = pos.copy()
        fid = fid.copy()
        sel = idx[move]
        pos[sel] = cpos[move, k[move]]
        fid[sel] = cfid[move, k[move]]
        self.stats["detail_moved"] += int(move.sum())
        return pos, fid

    def prefilter(self, pts: np.ndarray, radius: float, order: np.ndarray | None = None):
        """Cheap vectorised thinning before the exact greedy pass: drop candidates
        already covered by an accepted contact, then keep one candidate per voxel
        of size radius/2 (in priority order). Returns kept indices (sorted by priority)."""
        pts = np.asarray(pts, float).reshape(-1, 3)
        idx = np.arange(len(pts)) if order is None else np.asarray(order)
        if len(idx) == 0:
            return idx
        r = max(radius, self.dmin)
        if self.cov.pts:
            d, _ = cKDTree(np.asarray(self.cov.pts)).query(pts[idx], distance_upper_bound=r)
            self.stats["skipped_covered"] += int(np.isfinite(d).sum())
            idx = idx[~np.isfinite(d)]
        if len(idx) == 0:
            return idx
        key = np.floor(pts[idx] / (0.5 * r)).astype(np.int64)
        _, first = np.unique(key, axis=0, return_index=True)
        self.stats["skipped_covered"] += len(idx) - len(first)
        return idx[np.sort(first)]

    def offer(self, pos, normal, reason: ContactReason, note: str, radius: float) -> bool:
        p = np.asarray(pos, float)
        if p[2] < self.h:
            # lies on the build plate (first layer): printed on the plate, no
            # support needed (e.g. the end of an edge that starts on the plate)
            self.stats["on_plate_skipped"] = self.stats.get("on_plate_skipped", 0) + 1
            return False
        j = self.cov.nearest_within(p, max(radius, self.dmin))
        if j >= 0:
            self.stats["skipped_covered"] += 1
            slot = self.slot_of[j]
            if slot >= 0 and np.linalg.norm(self.cov.pts[j] - p) <= self.dmin:
                c = self.out[slot]
                tag = f"also {reason.value}"
                if c.reason != reason and tag not in c.note:
                    c.note += f"; {tag}"
            return False
        n = np.asarray(normal, float)
        nn = np.linalg.norm(n)
        n = n / nn if nn > 0 else np.array([0.0, 0.0, -1.0])
        c = Contact(id=self.next_id, position=p.copy(), normal=n, reason=reason, note=note)
        self.next_id += 1
        self.out.append(c)
        self.cov.add(p)
        self.slot_of.append(len(self.out) - 1)
        return True

    def offer_stream(self, cand, fid, avoid_r: float, reason: ContactReason, radius: float,
                     note_fn, on_block=None) -> None:
        """Detail avoidance + greedy offer of an ordered candidate list, in blocks of
        FILL_BLOCK candidates so memory stays bounded whatever the spacing.

        Before a block is processed, candidates that are certainly covered are
        dropped: avoid_details moves a point by at most D = hypot(avoid_r, h), so a
        candidate within max(radius, dmin) - D of an accepted contact would be
        rejected by offer() anyway. The result is identical to one unblocked pass
        (detail avoidance is per point), only the useless work is skipped."""
        n = len(cand)
        r_eff = max(radius, self.dmin)
        drop_r = r_eff - math.hypot(avoid_r, self.h) - 1e-9
        for s0 in range(0, n, FILL_BLOCK):
            idx = np.arange(s0, min(n, s0 + FILL_BLOCK))
            if on_block is not None:
                on_block(s0, n)
            if self.cov.pts and drop_r > 0:
                d, _ = cKDTree(np.asarray(self.cov.pts)).query(cand[idx], distance_upper_bound=drop_r)
                self.stats["skipped_covered"] += int(np.isfinite(d).sum())
                idx = idx[~np.isfinite(d)]
            if len(idx) == 0:
                continue
            pos, f2 = self.avoid_details(cand[idx], fid[idx], avoid_r)
            nrm = self.normal_of(f2)
            for j, k in enumerate(idx):
                self.offer(pos[j], nrm[j], reason, note_fn(int(k), pos[j], nrm[j]), radius=radius)

    def normal_of(self, fid):
        fid = np.asarray(fid)
        n = np.tile([0.0, 0.0, -1.0], (len(fid), 1))
        ok = fid >= 0
        n[ok] = self.fn[fid[ok]]
        return n


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def place_contacts(mesh: trimesh.Trimesh, analysis: AnalysisResult, params: SupportParams,
                   manual: list[Contact], progress: Progress) -> list[Contact]:
    progress = progress or Progress()
    manual = list(manual or [])
    progress.update(0.0, "Placing contacts")
    if params.manual_only or len(mesh.faces) == 0:
        progress.update(1.0, "Manual contacts only")
        return list(manual)

    P = _Placer(mesh, analysis, params, manual)
    h = P.h
    tip_r = 0.5 * params.tip_diameter
    islands = [r for r in analysis.regions if r.kind == "island"]
    overhangs = [r for r in analysis.regions if r.kind == "overhang"]

    # ---- 1. one mandatory contact per island -------------------------------
    progress.update(0.05, "Islands")
    lp = np.asarray(analysis.low_points).reshape(-1, 3)
    vids = analysis.stats.get("low_point_vertex_ids") if isinstance(analysis.stats, dict) else None
    lp_n = P.vn[np.asarray(vids, dtype=np.int64)] if vids is not None and len(vids) == len(lp) else None
    if islands:
        xy = np.array([r.polygon.representative_point().coords[0] for r in islands])
        zs = np.array([r.z for r in islands])
        # the region polygon may come from a few layers above the first one
        # (islands smaller than min_region_area are tracked until they grow)
        pos, fid = P.project(xy, zs - h, zs + ISLAND_Z_SEARCH)
        nrm = P.normal_of(fid)
        lp_used = np.zeros(len(lp), dtype=bool)
        for k, r in enumerate(islands):
            note = f"island starting at z={r.z:.2f} mm (area {r.area:.2f} mm2), nothing below it"
            # the true first point of an island is its lowest vertex when it has one
            if len(lp):
                cand = np.nonzero((lp[:, 2] >= r.z - h) & (lp[:, 2] <= r.z + h)
                                  & shapely.contains_xy(r.polygon.buffer(h), lp[:, 0], lp[:, 1]))[0]
                if len(cand):
                    j = cand[np.argmin(lp[cand, 2])]
                    pos[k] = lp[j]
                    if lp_n is not None:
                        nrm[k] = lp_n[j]
                    lp_used[j] = True
                    note += "; at its lowest point (low_point)"
            P.offer(pos[k], nrm[k], ContactReason.ISLAND, note, radius=P.dmin)

    # ---- 2. low points ------------------------------------------------------
    progress.update(0.15, "Low points")
    if len(lp):
        if lp_n is not None:
            nrm = lp_n
        else:
            _, fid = P.project(lp[:, :2], lp[:, 2] - h, lp[:, 2] + h)
            nrm = P.normal_of(fid)
        for k in np.argsort(lp[:, 2], kind="stable"):
            P.offer(lp[k], nrm[k], ContactReason.LOW_POINT,
                    f"local lowest point at z={lp[k, 2]:.2f} mm (tip, chin, ...)", radius=P.dmin)

    # ---- 3/4. corners and hanging edges (inset by the tip radius) ----------
    progress.update(0.25, "Corners and edges")
    edges = analysis.hanging_edges
    E = np.array([[p, q] for p, q in edges]).reshape(-1, 2, 3)

    def inset_project(base: np.ndarray, dirs: np.ndarray):
        """Try base +/- dirs*tip_r, keep the side whose hit stays closest to base z."""
        best_pos = base.copy()
        best_fid = np.full(len(base), -1, dtype=np.int64)
        best_dz = np.full(len(base), np.inf)
        for sgn in (1.0, -1.0, 0.0):
            xy = base[:, :2] + sgn * tip_r * dirs
            z, fid = P.caster.cast(np.column_stack([xy, base[:, 2] - 2 * h]))
            dz = np.abs(z - base[:, 2])
            upd = np.isfinite(z) & (dz < best_dz) & (dz <= max(tip_r, 2 * h))
            if sgn == 0.0:   # exact position only if no inset worked
                upd &= ~np.isfinite(best_dz)
            best_pos[upd] = np.column_stack([xy[upd], z[upd]])
            best_fid[upd] = fid[upd]
            best_dz[upd] = dz[upd]
        miss = best_fid < 0
        if miss.any():
            P.stats["projection_fallback"] += int(miss.sum())
            p2, f2 = P.caster.nearest_down_face(base[miss])
            best_pos[miss], best_fid[miss] = p2, f2
        return best_pos, best_fid

    corners = np.asarray(analysis.corners).reshape(-1, 3)
    if len(corners):
        dirs = np.zeros((len(corners), 2))
        if len(E):
            ends = E.reshape(-1, 3)
            tree = cKDTree(ends)
            for k, c in enumerate(corners):
                acc = np.zeros(2)
                for j in tree.query_ball_point(c, 1e-6):
                    seg = E[j // 2]
                    other = seg[1 - j % 2]
                    dv = (other - c)[:2]
                    nv = np.linalg.norm(dv)
                    if nv > 0:
                        acc += dv / nv
                na = np.linalg.norm(acc)
                if na > 1e-9:
                    dirs[k] = acc / na
        pos, fid = inset_project(corners, dirs)
        nrm = P.normal_of(fid)
        for k in range(len(corners)):
            P.offer(pos[k], nrm[k], ContactReason.CORNER,
                    f"corner of a flat overhang at z={corners[k, 2]:.2f} mm", radius=P.dmin)

    if len(E):
        # the mesh segments are chained into geometric polylines first, then
        # sampled evenly (gap <= edge spacing, ends included): the number and
        # position of edge contacts no longer depend on the tessellation
        base_l, dir_l, rad_l = [], [], []
        for pl in _edge_polylines(E):
            pts, tan, gap = _sample_polyline(pl, P.e)
            base_l.append(pts)
            dir_l.append(tan)
            # own samples must survive (gap may be < r_e); earlier contacts
            # (corners, low points) still cover their neighbourhood
            rad_l.append(np.full(len(pts), max(P.dmin, min(P.r_e, 0.9 * gap)) if len(pts) > 1 else P.r_e))
        base = np.concatenate(base_l)
        dvec = np.concatenate(dir_l)
        rad = np.concatenate(rad_l)
        perp = np.column_stack([-dvec[:, 1], dvec[:, 0]])
        perp /= np.maximum(np.linalg.norm(perp, axis=1, keepdims=True), 1e-12)
        P.stats["edge_polylines"] = len(base_l)
        pos, fid = inset_project(base, perp)
        pos, fid = P.avoid_details(pos, fid, min(0.25 * P.e, 0.6))
        nrm = P.normal_of(fid)
        for k in range(len(pos)):
            if k % 2000 == 0:
                progress.update(0.3 + 0.2 * k / len(pos), "Edge contacts")
            P.offer(pos[k], nrm[k], ContactReason.EDGE,
                    f"along a hanging edge at z={base[k, 2]:.2f} mm", radius=rad[k])

    # ---- 5. fill islands and overhang regions ------------------------------
    progress.update(0.5, "Filling overhang areas")
    for kind, regs in ((ContactReason.ISLAND, islands), (ContactReason.SURFACE, overhangs)):
        xy_all, z_all, note_all = [], [], []
        for r in regs:
            pts = _hex_grid(r.polygon, P.s)
            if len(pts) == 0:
                pts = _outline_samples(r.polygon, P.s)
            xy_all.append(pts)
            z_all.append(np.full(len(pts), r.z))
            note_all.extend([r] * len(pts))
        if not xy_all:
            continue
        xy = np.concatenate(xy_all)
        zz = np.concatenate(z_all)
        if len(xy) == 0:
            continue
        pos, fid = P.project(xy, zz - h, zz + (ISLAND_Z_SEARCH if kind == ContactReason.ISLAND else 1.0))
        if kind == ContactReason.SURFACE:
            # combine with the face based mask: drop slice artefacts on faces
            # that are not overhanging (keeps slope strips from exploding)
            keep = (fid >= 0) & P.mask[np.maximum(fid, 0)]
            pos, fid, zz = pos[keep], fid[keep], zz[keep]
            note_all = [note_all[i] for i in np.nonzero(keep)[0]]
        sel = P.prefilter(pos, P.r_s, np.argsort(pos[:, 2], kind="stable"))
        pos, fid = pos[sel], fid[sel]
        note_all = [note_all[i] for i in sel]

        def fill_note(k, _p, _n, kind=kind, note_all=note_all):
            r = note_all[k]
            if kind == ContactReason.ISLAND:
                return f"fill of island at z={r.z:.2f} mm (area {r.area:.1f} mm2)"
            return f"unsupported area at z={r.z:.2f} mm (area {r.area:.1f} mm2)"

        P.offer_stream(pos, fid, min(0.25 * P.s, 0.6), kind, P.r_s, fill_note,
                       lambda i, n: progress.update(0.5 + 0.3 * i / max(1, n), "Filling overhang areas"))

    # ---- 6. face mask fill --------------------------------------------------
    progress.update(0.8, "Checking overhanging faces")
    ids, labels = _mask_components(P.f, P.mask, len(P.v))
    if len(ids):
        comp_area = np.bincount(labels, weights=P.area[ids])
        big = comp_area[labels] >= params.min_region_area
        ids = ids[big]
        labels = labels[big]
        if len(ids):
            # Residual check on a global XY lattice (pitch RESIDUAL_PITCH x spacing)
            # lifted onto the overhanging faces: same samples whatever the
            # tessellation (face centroids, used before, multiplied the contacts
            # on finely meshed surfaces). A sample farther than the contact
            # spacing from every accepted contact is a hole in the coverage and
            # gets a contact, which then covers the spacing around it, so the
            # residual fill has the same density as the hex fill of step 5.
            g0 = RESIDUAL_PITCH * P.s
            cand, cf, g = _lattice_on_faces(P.v, P.f, ids, g0, return_pitch=True)
            P.stats["residual_pitch"] = float(g)
            P.stats["residual_pitch_capped"] = bool(g > g0 * (1 + 1e-9))
            # a narrow patch may contain no lattice point: one sample per such
            # component (the face centroid closest to its area weighted centre)
            have = np.zeros(int(labels.max()) + 1, dtype=bool)
            if len(cf):
                pos_of = np.full(len(P.f), -1, dtype=np.int64)
                pos_of[ids] = np.arange(len(ids))
                have[labels[pos_of[cf]]] = True
            miss = np.nonzero(~have)[0]
            if len(miss):
                cen = P.v[P.f[ids]].mean(axis=1)
                wa = P.area[ids]
                sel_f = np.isin(labels, miss)
                lab_m, cen_m, w_m, id_m = labels[sel_f], cen[sel_f], wa[sel_f], ids[sel_f]
                cw = np.zeros((int(labels.max()) + 1, 3))
                for c in range(3):
                    cw[:, c] = np.bincount(lab_m, weights=cen_m[:, c] * w_m, minlength=len(cw))
                tw = np.bincount(lab_m, weights=w_m, minlength=len(cw))
                cw /= np.maximum(tw, 1e-12)[:, None]
                dist = np.linalg.norm(cen_m - cw[lab_m], axis=1)
                o = np.lexsort((dist, lab_m))
                firsts = o[np.r_[True, lab_m[o][1:] != lab_m[o][:-1]]]
                cand = np.concatenate([cand, cen_m[firsts]])
                cf = np.concatenate([cf, id_m[firsts]])
            P.stats["residual_samples"] = int(len(cand))
            # deterministic, tessellation independent order: low first, then x, y
            rq = np.round(cand / 1e-4)
            order = np.lexsort((rq[:, 1], rq[:, 0], rq[:, 2]))
            if P.cov.pts and len(order):       # drop samples already covered
                d, _ = cKDTree(np.asarray(P.cov.pts)).query(cand[order], distance_upper_bound=P.s)
                order = order[~np.isfinite(d)]
            cand, cf = cand[order], cf[order]
            coarse_note = ""
            if P.stats.get("residual_pitch_capped"):
                coarse_note = (f"; residual lattice coarsened to {P.stats['residual_pitch']:.2f} mm "
                               f"(more than {RESIDUAL_MAX_SAMPLES} samples at the nominal pitch)")

            def residual_note(_k, pos, nrm):
                tilt = math.degrees(math.acos(max(-1.0, min(1.0, -nrm[2]))))
                return (f"downward face ({tilt:.0f} deg from horizontal) at z={pos[2]:.2f} mm, "
                        f"more than {P.s:.1f} mm from any other contact" + coarse_note)

            P.offer_stream(cand, cf, min(0.25 * P.s, 0.6), ContactReason.SURFACE, P.s, residual_note,
                           lambda i, n: progress.update(0.8 + 0.2 * i / max(1, n),
                                                        "Checking overhanging faces"))

    progress.update(1.0, f"{len(P.out)} contacts placed")
    if isinstance(analysis.stats, dict):
        analysis.stats["contacts"] = dict(P.stats, n_auto=len(P.out), n_manual=len(manual))
    return list(manual) + P.out

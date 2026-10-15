"""Support analysis: where does the model need support?

Two complementary detectors (see docs/research/geometry.md):

1. Virtual slicing (authoritative for "what will the printer see").
   The mesh is cut at every layer centre z_i = layer_height * (i + 0.5).
   For layer i, with d = max_layer_step(params) (layer_height x
   tan(overhang_angle), capped at one line width, or
   params.max_unsupported_overhang when > 0):
       unsupported_i = layer_i - offset(layer_{i-1}, d)
   Connected components of layer_i that do not touch layer_{i-1} at all are
   islands; the remaining unsupported pieces are overhangs. Pieces smaller
   than params.min_region_area are dropped.

2. Face based (cheap, resolution independent, used to place contacts on
   slopes that only show up as thin strips in the slices).
   overhang_face_mask: faces whose downward tilt from vertical exceeds the
   effective overhang angle atan(d / layer_height) (-n_z > sin(angle)),
   excluding faces lying on the plate. Using the angle of d (not the raw
   parameter) keeps both detectors on the same threshold when d is capped
   or given explicitly (docs/research/overhang_angles.md).
   From this mask we derive hanging edges, corners of flat overhangs and
   local low points.

Slicing backend: manifold3d (Manifold.slice, C++, Clipper2) when the mesh is
a valid 2-manifold; otherwise a vectorised numpy slicer whose segments are
chained with GEOS (shapely.line_merge). Open chains (holes in the mesh) that
cannot be closed are dropped and counted in stats["open_chains_dropped"].
2D booleans/offsets are done with manifold3d.CrossSection (Clipper2); the
result is exported as shapely geometry.
"""
from __future__ import annotations

import math
import time

import manifold3d as m3d
import numpy as np
import shapely
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import MultiPolygon, Polygon

from .params import SupportParams
from .types import AnalysisResult, Progress, SupportRegion

_EMPTY_CS = m3d.CrossSection()
HANGING_CREASE_DEG = 25.0  # min dihedral angle for a hanging edge
LOW_POINT_RADIUS = 1.0     # mm, a low point is the lowest vertex within this radius
# Upper bound of the step derived from the angle, in line widths. A layer that
# extends one full line width beyond the layer below leaves its outer bead with
# no overlap at all (0 % overlap = floating extrusion in PrusaSlicer's overhang
# classes, 100 % overhang in OrcaSlicer's): the angle cannot allow more.
MAX_STEP_LINE_WIDTHS = 1.0


def max_layer_step(params: SupportParams) -> tuple[float, str]:
    """Largest horizontal step (mm) a layer may make beyond the layer below
    without support, and where it comes from: "explicit"
    (max_unsupported_overhang > 0, used as given), "angle" (layer_height x
    tan(overhang_angle_deg)) or "line_width" (the angle step capped at
    MAX_STEP_LINE_WIDTHS x line_width)."""
    if params.max_unsupported_overhang > 0:
        return float(params.max_unsupported_overhang), "explicit"
    d = params.layer_height * math.tan(math.radians(params.overhang_angle_deg))
    cap = MAX_STEP_LINE_WIDTHS * params.line_width
    if d > cap:
        return float(cap), "line_width"
    return float(d), "angle"


def effective_overhang_angle_deg(params: SupportParams) -> float:
    """Overhang angle (deg from vertical) matching max_layer_step: a face tilted
    more than this makes each layer step farther than d. Equal to
    overhang_angle_deg unless the step is capped or explicit."""
    d, source = max_layer_step(params)
    if source == "angle":
        return float(params.overhang_angle_deg)
    return math.degrees(math.atan2(d, params.layer_height))


# ---------------------------------------------------------------------------
# helpers: geometry conversion
# ---------------------------------------------------------------------------
def _signed_area(c: np.ndarray) -> float:
    x, y = c[:, 0], c[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def cs_components(cs: m3d.CrossSection) -> list[tuple[m3d.CrossSection, Polygon]]:
    """Connected components of a CrossSection, each with its shapely Polygon."""
    out = []
    if cs.is_empty():
        return out
    for comp in cs.decompose():
        contours = comp.to_polygons()
        if not contours:
            continue
        areas = [_signed_area(np.asarray(c)) for c in contours]
        k = int(np.argmax(areas))
        holes = [np.asarray(c) for i, c in enumerate(contours) if i != k and len(c) >= 3]
        if len(contours[k]) < 3:
            continue
        poly = Polygon(np.asarray(contours[k]), holes)
        if not poly.is_valid:
            poly = shapely.make_valid(poly)
            if not isinstance(poly, Polygon):
                parts = [g for g in shapely.get_parts(poly) if isinstance(g, Polygon)]
                if not parts:
                    continue
                poly = max(parts, key=lambda g: g.area)
        out.append((comp, poly))
    return out


def _to_multipolygon(polys: list[Polygon]):
    if not polys:
        return Polygon()
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def _to_manifold(mesh: trimesh.Trimesh):
    """Return (Manifold or None, status string)."""
    try:
        mm = m3d.Mesh(vert_properties=np.ascontiguousarray(mesh.vertices, dtype=np.float32),
                      tri_verts=np.ascontiguousarray(mesh.faces, dtype=np.uint32))
        man = m3d.Manifold(mm)
        if man.status() == m3d.Error.NoError and not man.is_empty():
            return man, "ok"
        if mm.merge():
            man = m3d.Manifold(mm)
            if man.status() == m3d.Error.NoError and not man.is_empty():
                return man, "ok_after_merge"
        return None, str(man.status())
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"exception: {exc}"


# ---------------------------------------------------------------------------
# fallback slicer for non-manifold / open meshes
# ---------------------------------------------------------------------------
class _FallbackSlicer:
    """Vectorised plane/mesh intersection, tolerant to open meshes.

    Intersection points are computed per edge with a canonical vertex order
    (lower index first) so two faces sharing an edge produce bit-identical
    points, which lets GEOS line_merge chain them into rings.
    """

    def __init__(self, mesh: trimesh.Trimesh, close_gap: float):
        self.v = np.asarray(mesh.vertices, dtype=np.float64)
        self.f = np.asarray(mesh.faces, dtype=np.int64)
        fz = self.v[:, 2][self.f]
        zmin = fz.min(axis=1)
        self.order = np.argsort(zmin, kind="stable")
        self.zmin_sorted = zmin[self.order]
        self.zmax = fz.max(axis=1)
        self.close_gap = close_gap
        self.open_dropped = 0

    def slice(self, z: float) -> m3d.CrossSection:
        n = int(np.searchsorted(self.zmin_sorted, z, side="right"))
        cand = self.order[:n]
        cand = cand[self.zmax[cand] > z]
        if len(cand) == 0:
            return _EMPTY_CS
        tri = self.f[cand]
        e = tri[:, [[0, 1], [1, 2], [2, 0]]]              # (n,3,2)
        e = np.sort(e, axis=2)                             # canonical order
        above = self.v[:, 2] >= z
        s = above[e]
        cross = s[..., 0] != s[..., 1]                     # (n,3)
        good = cross.sum(axis=1) == 2
        e = e[good]
        cross = cross[good]
        a = self.v[e[..., 0]]
        b = self.v[e[..., 1]]
        dz = b[..., 2] - a[..., 2]
        t = (z - a[..., 2]) / np.where(dz == 0, 1.0, dz)
        p = a[..., :2] + t[..., None] * (b[..., :2] - a[..., :2])   # (n,3,2)
        segs = p[cross].reshape(-1, 2, 2)
        if len(segs) == 0:
            return _EMPTY_CS
        lines = shapely.linestrings(segs)
        merged = shapely.line_merge(shapely.multilinestrings(lines))
        contours = []
        for g in shapely.get_parts(merged):
            c = np.asarray(shapely.get_coordinates(g))
            if len(c) < 3:
                continue
            if not shapely.is_closed(g):
                if np.linalg.norm(c[0] - c[-1]) > self.close_gap:
                    self.open_dropped += 1
                    continue
            contours.append(c)
        if not contours:
            return _EMPTY_CS
        return m3d.CrossSection(contours, m3d.FillRule.EvenOdd)


# ---------------------------------------------------------------------------
# face based analysis
# ---------------------------------------------------------------------------
def _unique_edges(faces: np.ndarray, n_vertices: int):
    """Undirected edge table. Returns (edge_vertices (E,2) sorted, inverse (3F,),
    counts (E,), face_of_directed (3F,), local index (3F,))."""
    f = faces
    d = np.stack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], axis=1).reshape(-1, 2)
    srt = np.sort(d, axis=1)
    key = srt[:, 0].astype(np.int64) * np.int64(n_vertices) + srt[:, 1]
    uniq, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
    ev = np.stack([uniq // n_vertices, uniq % n_vertices], axis=1)
    face_of = np.repeat(np.arange(len(f)), 3)
    local = np.tile(np.arange(3), len(f))
    return ev, inverse, counts, face_of, local


def _inward_z(v: np.ndarray, u: np.ndarray, w: np.ndarray, opp: np.ndarray) -> np.ndarray:
    """Z component of the unit in-plane direction from edge (u,w) towards vertex opp."""
    e = w - u
    e /= np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
    r = opp - u
    r = r - (r * e).sum(axis=1, keepdims=True) * e
    r /= np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-12)
    return r[:, 2]


def _face_analysis(mesh: trimesh.Trimesh, params: SupportParams, stats: dict):
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)
    h = params.layer_height
    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    fz = v[:, 2][f]
    sin_t = math.sin(math.radians(effective_overhang_angle_deg(params)))
    on_plate = fz.max(axis=1) < h
    mask = (-normals[:, 2] > sin_t) & ~on_plate
    areas = np.asarray(mesh.area_faces)

    ev, inverse, counts, face_of, local = _unique_edges(f, len(v))
    stats["n_nonmanifold_edges"] = int((counts > 2).sum())
    stats["n_boundary_edges"] = int((counts == 1).sum())

    # ---- low points: strict local minima (ties broken by vertex index) -----
    vz = v[:, 2]
    a, b = ev[:, 0], ev[:, 1]
    not_min = np.zeros(len(v), dtype=bool)
    # strict minimum: every neighbour strictly higher (a flat bottom has none)
    not_min[a[vz[b] <= vz[a]]] = True
    not_min[b[vz[a] <= vz[b]]] = True
    used = np.zeros(len(v), dtype=bool)
    used[f.ravel()] = True
    low_idx = np.nonzero(used & ~not_min & (vz > h))[0]
    stats["n_low_point_candidates"] = int(len(low_idx))
    if len(low_idx):
        # significance filter (noise / scanned surfaces create thousands of tiny
        # 1-ring minima): the vertex must face down (area weighted normal) and be
        # the lowest vertex within LOW_POINT_RADIUS in 3D.
        vn = np.zeros((len(v), 3))
        w = normals * np.asarray(mesh.area_faces)[:, None]
        for k in range(3):
            for c in range(3):
                vn[:, c] += np.bincount(f[:, k], weights=w[:, c], minlength=len(v))
        vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
        low_idx = low_idx[vn[low_idx, 2] < -0.5]
        if len(low_idx):
            r = max(LOW_POINT_RADIUS, params.min_contact_distance)
            tree = cKDTree(v[used])
            vu = np.nonzero(used)[0]
            keep = np.ones(len(low_idx), dtype=bool)
            for j, nb in enumerate(tree.query_ball_point(v[low_idx], r)):
                keep[j] = vz[vu[nb]].min() >= vz[low_idx[j]]
            low_idx = low_idx[keep]

    # ---- hanging edges ------------------------------------------------------
    hanging_edges: list[tuple[np.ndarray, np.ndarray]] = []
    corners = np.zeros((0, 3))
    corner_idx = np.zeros(0, dtype=np.int64)
    hang_ev = np.zeros((0, 2), dtype=np.int64)
    # speckle filter (scanned / sculpted surfaces): small holes inside an
    # overhang patch are filled, then patches smaller than min_region_area are
    # dropped. Without this, noise creates spurious hanging edges and corners.
    if mask.any():
        order_e = np.argsort(inverse, kind="stable")
        e_s, f_s = inverse[order_e], face_of[order_e]
        same = e_s[1:] == e_s[:-1]
        fa_, fb_ = f_s[:-1][same], f_s[1:][same]          # face pairs sharing an edge

        def components(sel):
            ids = np.nonzero(sel)[0]
            remap = np.full(len(f), -1, dtype=np.int64)
            remap[ids] = np.arange(len(ids))
            both = sel[fa_] & sel[fb_]
            g = coo_matrix((np.ones(int(both.sum())), (remap[fa_[both]], remap[fb_[both]])),
                           shape=(len(ids), len(ids)))
            _, lab = connected_components(g, directed=False)
            return ids, lab, np.bincount(lab, weights=areas[ids])

        # 1. fill small enclosed holes (non mask components touching the mask)
        ids, lab, comp_area = components(~mask)
        touch = np.zeros(len(comp_area), dtype=bool)
        remap = np.full(len(f), -1, dtype=np.int64)
        remap[ids] = np.arange(len(ids))
        x = mask[fa_] & ~mask[fb_]
        touch[lab[remap[fb_[x]]]] = True
        x = mask[fb_] & ~mask[fa_]
        touch[lab[remap[fa_[x]]]] = True
        fill = (comp_area < params.min_region_area) & touch
        add = ids[fill[lab]] if len(ids) else ids
        add = add[normals[add, 2] < 0]                    # never fill upward faces
        mask[add] = True
        stats["n_mask_holes_filled"] = int(len(add))
        # 2. drop small patches
        ids, lab, comp_area = components(mask)
        small = comp_area[lab] < params.min_region_area
        mask[ids[small]] = False
        stats["n_speckle_faces_dropped"] = int(small.sum())
        stats["n_overhang_patches"] = int((comp_area >= params.min_region_area).sum())
    stats["face_overhang_area"] = float(areas[mask].sum())
    stats["n_overhang_faces"] = int(mask.sum())
    if mask.any():
        # directed occurrences of edges belonging to mask faces
        dmask = mask[face_of]
        occ = np.nonzero(dmask)[0]
        eid = inverse[occ]
        good = counts[eid] <= 2
        occ, eid = occ[good], eid[good]
        # for each edge, the "other" face occurrence (if any)
        order = np.argsort(inverse, kind="stable")
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
        first = order[starts[eid]]
        second_pos = np.minimum(starts[eid] + 1, len(order) - 1)
        second = np.where(counts[eid] == 2, order[second_pos], -1)
        other = np.where(first == occ, second, first)
        fa = face_of[occ]
        other_face = np.where(other >= 0, face_of[np.maximum(other, 0)], -1)
        # boundary of the patch: neighbour missing or not in mask
        is_bnd = (other_face < 0) | ~mask[np.maximum(other_face, 0)]
        occ, eid, fa, other_face, other = occ[is_bnd], eid[is_bnd], fa[is_bnd], other_face[is_bnd], other[is_bnd]
        u = v[ev[eid, 0]]
        w = v[ev[eid, 1]]
        opp_a = v[f[fa, (local[occ] + 2) % 3]]
        in_a = _inward_z(v, u, w, opp_a)
        has_b = other_face >= 0
        in_b = np.ones(len(eid))
        if has_b.any():
            ob = other[has_b]
            opp_b = v[f[face_of[ob], (local[ob] + 2) % 3]]
            in_b[has_b] = _inward_z(v, u[has_b], w[has_b], opp_b)
        # lower boundary: entering the overhang patch does not go down, and the
        # neighbour surface rises from the edge (or the mesh is open there)
        # and the edge is a crease (dihedral > HANGING_CREASE_DEG): on a smooth
        # surface the boundary of the overhang mask is not a physical edge
        crease = np.ones(len(eid), dtype=bool)
        crease[has_b] = (normals[fa[has_b]] * normals[other_face[has_b]]).sum(axis=1) \
            < math.cos(math.radians(HANGING_CREASE_DEG))
        hang = (in_a >= -0.1) & (in_b > 0.1) & crease
        hang_ev = ev[eid[hang]]
        hang_ev = np.unique(hang_ev, axis=0) if len(hang_ev) else hang_ev
        hanging_edges = [(v[p].copy(), v[q].copy()) for p, q in hang_ev]

        # ---- corners of flat overhangs ------------------------------------
        if len(hang_ev):
            deg = np.bincount(hang_ev.ravel(), minlength=len(v))
            flat = mask & (normals[:, 2] < -math.cos(math.radians(20.0)))
            mf = np.nonzero(mask)[0]
            ang = np.asarray(mesh.face_angles)[mf]          # (k,3) radians
            interior = np.bincount(f[mf].ravel(), weights=ang.ravel(), minlength=len(v))
            # every mask face around the vertex must be near horizontal
            n_mask = np.bincount(f[mf].ravel(), minlength=len(v))
            n_flat = np.bincount(f[flat].ravel(), minlength=len(v))
            cand = np.nonzero((deg >= 2) & (n_mask > 0) & (n_flat == n_mask)
                              & (interior < math.radians(150.0)))[0]
            corner_idx = cand
            corners = v[cand].copy()
    stats["n_hanging_edges"] = len(hanging_edges)
    stats["n_corners"] = int(len(corners))
    stats["n_low_points"] = int(len(low_idx))
    stats["hanging_edge_length"] = float(sum(np.linalg.norm(q - p) for p, q in hanging_edges))
    return mask, hanging_edges, corners, v[low_idx].copy(), corner_idx, low_idx


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def analyze(mesh: trimesh.Trimesh, params: SupportParams, progress: Progress) -> AnalysisResult:
    progress = progress or Progress()
    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    stats: dict = {"n_faces": int(len(mesh.faces)), "n_vertices": int(len(mesh.vertices))}
    h = float(params.layer_height)
    progress.update(0.0, "Preparing mesh")

    if len(mesh.faces) == 0:
        stats.update(n_layers=0, n_islands=0, n_regions=0, overhang_area=0.0, timings=timings)
        return AnalysisResult(h, np.zeros(0), overhang_face_mask=np.zeros(0, bool), stats=stats)

    zmax = float(mesh.vertices[:, 2].max())
    zmin = float(mesh.vertices[:, 2].min())
    stats["z_min"] = zmin
    if zmin < -1e-3:
        stats["warning_below_plate"] = True
    n_layers = max(0, int(math.ceil(zmax / h - 0.5)))
    layer_z = h * (np.arange(n_layers) + 0.5)
    stats["n_layers"] = n_layers

    # ---- slicer backend -----------------------------------------------------
    t = time.perf_counter()
    stats["watertight"] = bool(mesh.is_watertight)
    man, status = _to_manifold(mesh)
    if man is not None:
        stats["slicer"] = "manifold"
        slicer = None
    else:
        stats["slicer"] = "fallback"
        stats["manifold_status"] = status
        slicer = _FallbackSlicer(mesh, close_gap=max(1.0, 4 * params.line_width))
    timings["prepare"] = time.perf_counter() - t
    progress.update(0.05, "Slicing")

    # ---- slicing ------------------------------------------------------------
    t = time.perf_counter()
    layers: list[m3d.CrossSection] = []
    step = max(1, n_layers // 50)
    for i, z in enumerate(layer_z):
        if i % step == 0:
            progress.update(0.05 + 0.35 * i / max(1, n_layers), f"Slicing layer {i + 1}/{n_layers}")
        layers.append(man.slice(float(z)) if man is not None else slicer.slice(float(z)))
    if slicer is not None:
        stats["open_chains_dropped"] = slicer.open_dropped
    timings["slicing"] = time.perf_counter() - t

    # ---- layer comparison ---------------------------------------------------
    t = time.perf_counter()
    d, step_source = max_layer_step(params)
    stats["max_step"] = d
    stats["max_step_source"] = step_source
    stats["max_step_line_widths"] = d / params.line_width
    stats["effective_overhang_angle_deg"] = effective_overhang_angle_deg(params)
    min_area = params.min_region_area
    regions: list[SupportRegion] = []
    layer_polys: list = []
    prev_cs = _EMPTY_CS
    prev_tree = None
    # label of each component of the previous layer: -1 = grounded (connected to
    # the plate or to an island already reported), k >= 0 = pending island k
    # (started in the air but still smaller than min_region_area)
    prev_label = np.zeros(0, dtype=np.int64)
    pending_z: list[float] = []
    promoted: set[int] = set()
    for i, z in enumerate(layer_z):
        if i % step == 0:
            progress.update(0.40 + 0.40 * i / max(1, n_layers), f"Comparing layer {i + 1}/{n_layers}")
        cur = layers[i]
        comps = cs_components(cur)
        polys = [p for _, p in comps]
        layer_polys.append(_to_multipolygon(polys))
        if i == 0 or not comps:
            # first layer rests on the plate
            prev_cs = cur
            prev_tree = shapely.STRtree(polys) if polys else None
            prev_label = np.full(len(polys), -1, dtype=np.int64)
            continue
        # islands: components that do not touch the previous layer at all
        label = np.full(len(comps), -2, dtype=np.int64)       # -2 = new island
        if prev_tree is not None:
            qi, pi = prev_tree.query(polys, predicate="intersects")
            for a_, b_ in zip(qi, pi):
                lb = prev_label[b_]
                if lb == -1:
                    label[a_] = -1
                elif label[a_] != -1:
                    label[a_] = lb if label[a_] < 0 else min(label[a_], lb)
        support_cs = cur
        island_cs = []
        for k, (cs, poly) in enumerate(comps):
            if label[k] == -1:
                continue
            if label[k] == -2:
                pending_z.append(float(z))
                label[k] = len(pending_z) - 1
            island_cs.append(cs)
            if poly.area >= min_area:
                # z = first layer of the island (where it really starts)
                regions.append(SupportRegion("island", pending_z[label[k]], poly, float(poly.area)))
                promoted.add(int(label[k]))
                label[k] = -1
        if island_cs:
            support_cs = cur - m3d.CrossSection.batch_boolean(island_cs, m3d.OpType.Add) \
                if len(island_cs) > 1 else cur - island_cs[0]
        if not prev_cs.is_empty() and not support_cs.is_empty():
            unsup = support_cs - prev_cs.offset(d, m3d.JoinType.Miter, 2.0)
            if not unsup.is_empty() and unsup.area() >= min_area:
                for _, poly in cs_components(unsup):
                    if poly.area >= min_area:
                        regions.append(SupportRegion("overhang", float(z), poly, float(poly.area)))
        prev_cs = cur
        prev_tree = shapely.STRtree(polys)
        prev_label = label
    n_small_islands = len(pending_z) - len(promoted)
    del layers
    timings["layers"] = time.perf_counter() - t

    # ---- face based ---------------------------------------------------------
    progress.update(0.80, "Analysing faces")
    t = time.perf_counter()
    mask, hanging, corners, low_points, corner_idx, low_idx = _face_analysis(mesh, params, stats)
    timings["faces"] = time.perf_counter() - t
    progress.update(1.0, "Analysis done")

    stats["n_islands"] = sum(1 for r in regions if r.kind == "island")
    stats["n_regions"] = len(regions)
    stats["n_small_islands_dropped"] = n_small_islands
    stats["overhang_area"] = float(sum(r.area for r in regions if r.kind == "overhang"))
    stats["island_area"] = float(sum(r.area for r in regions if r.kind == "island"))
    stats["corner_vertex_ids"] = [int(x) for x in corner_idx]      # JSON friendly
    stats["low_point_vertex_ids"] = [int(x) for x in low_idx]
    timings["total"] = time.perf_counter() - t_all
    stats["timings"] = timings
    return AnalysisResult(layer_height=h, layer_z=layer_z, regions=regions,
                          overhang_face_mask=mask, hanging_edges=hanging, corners=corners,
                          low_points=low_points, layer_polygons=layer_polys, stats=stats)

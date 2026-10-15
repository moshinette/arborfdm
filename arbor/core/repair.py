"""Mesh diagnosis and conservative repair.

``check_mesh`` never modifies its input. ``repair_mesh`` works on a copy and returns
a new mesh together with a :class:`RepairReport` listing every action and a re-check.

All topology is computed with vectorised numpy / scipy (no networkx), so a 1M-face
mesh is checked in a few seconds.

Definitions used here
---------------------
* edge: unordered vertex pair; its *use count* is the number of faces containing it.
* boundary edge: used once. Boundary edges are grouped into connected loops = holes.
* non-manifold edge: used by 3 or more faces.
* winding inconsistency: a manifold edge whose two faces traverse it in the same
  direction (a correctly oriented closed surface traverses each edge once per direction).
* degenerate face: repeated vertex index or area <= ``area_eps`` (relative to bbox).
* duplicate face: same set of 3 vertex indices as an earlier face (any order).
* inverted: closed, consistently wound mesh with negative signed volume (normals inward).
* components: connected through shared vertices.

Not checked (documented limitation): self-intersections between triangles.
Hole filling and winding repair are our own vectorised code (trimesh.repair.fill_holes
and fix_winding go through networkx and were too slow on 1M faces).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
import trimesh


@dataclass
class MeshProblem:
    severity: str      # "error" | "warning" | "info"
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.message}"


@dataclass
class MeshReport:
    n_faces: int
    n_vertices: int
    watertight: bool
    winding_consistent: bool
    n_boundary_edges: int
    n_holes: int
    n_degenerate_faces: int
    n_duplicate_faces: int
    n_non_manifold_edges: int
    n_inconsistent_edges: int
    inverted: bool
    n_components: int
    bbox: np.ndarray                       # (2, 3)
    volume: Optional[float]                # only when watertight and consistently wound
    n_nonfinite_vertices: int = 0
    n_unreferenced_vertices: int = 0
    problems: list[MeshProblem] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True if there is no error-severity problem (printable solid)."""
        return not any(p.severity == "error" for p in self.problems)

    @property
    def size(self) -> np.ndarray:
        return self.bbox[1] - self.bbox[0]

    def summary(self) -> str:
        lines = [f"{self.n_faces} faces, {self.n_vertices} vertices, {self.n_components} component(s)"]
        lines += [str(p) for p in self.problems] or ["no problem found"]
        return "\n".join(lines)


@dataclass
class RepairReport:
    actions: list[str]
    before: MeshReport
    after: MeshReport
    success: bool
    counts: dict[str, int] = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.actions)

    def summary(self) -> str:
        head = "Repair succeeded" if self.success else "Repair FAILED"
        lines = [head] + [f"- {a}" for a in self.actions or ["nothing to do"]]
        lines += [f"! {r}" for r in self.failure_reasons]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# topology helpers
# ---------------------------------------------------------------------------
def _area_eps(vertices: np.ndarray) -> float:
    if len(vertices) == 0:
        return 0.0
    finite = vertices[np.all(np.isfinite(vertices), axis=1)]
    if len(finite) == 0:
        return 0.0
    ext = float(np.max(finite.max(0) - finite.min(0)))
    return (ext * 1e-7) ** 2


def _face_areas2(vertices, faces) -> np.ndarray:
    """Twice the triangle area."""
    if len(faces) == 0:
        return np.zeros(0)
    t = vertices[faces]
    return np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)


def _degenerate_mask(vertices, faces) -> np.ndarray:
    f = faces
    rep = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2])
    with np.errstate(invalid="ignore"):
        a2 = _face_areas2(vertices, faces)
    small = ~(a2 > 2 * _area_eps(vertices))       # NaN counts as degenerate
    return rep | small


def _duplicate_mask(faces) -> np.ndarray:
    """True for every face whose vertex set already appeared earlier."""
    if len(faces) == 0:
        return np.zeros(0, bool)
    s = np.sort(faces, axis=1).astype(np.int64)
    n = np.int64(int(s.max()) + 1)
    if n < 2_000_000:                          # n^3 fits in int64: single integer key
        key = (s[:, 0] * n + s[:, 1]) * n + s[:, 2]
        _, first = np.unique(key, return_index=True)
    else:
        _, first = np.unique(s, axis=0, return_index=True)
    mask = np.ones(len(faces), bool)
    mask[first] = False
    return mask


def _edge_stats(faces: np.ndarray, n_vertices: int):
    """Returns (unique undirected edges (E,2), use count (E,), inverse (3F,),
    directed edges (3F,2))."""
    directed = np.stack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=1).reshape(-1, 2)
    und = np.sort(directed, axis=1)
    key = und[:, 0].astype(np.int64) * np.int64(max(n_vertices, 1)) + und[:, 1]
    uk, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
    edges = np.stack([uk // max(n_vertices, 1), uk % max(n_vertices, 1)], axis=1)
    return edges, counts, inv.reshape(-1), directed


def _n_loops(edges: np.ndarray, n_vertices: int) -> int:
    if len(edges) == 0:
        return 0
    g = sp.coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(n_vertices, n_vertices))
    n, labels = connected_components(g, directed=False)
    return int(len(np.unique(labels[edges[:, 0]])))


def _signed_volume(vertices, faces) -> float:
    t = vertices[faces]
    return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def check_mesh(mesh: trimesh.Trimesh) -> MeshReport:
    """Diagnose ``mesh`` (not modified)."""
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64).reshape(-1, 3)
    nv, nf = len(v), len(f)
    problems: list[MeshProblem] = []

    finite_v = np.all(np.isfinite(v), axis=1) if nv else np.zeros(0, bool)
    n_nonfinite = int((~finite_v).sum())
    bbox = (np.vstack([v[finite_v].min(0), v[finite_v].max(0)]) if finite_v.any()
            else np.zeros((2, 3)))

    if nf == 0:
        problems.append(MeshProblem("error", "empty", "Mesh has no triangle."))
        return MeshReport(0, nv, False, False, 0, 0, 0, 0, 0, 0, False, 0, bbox, None,
                          n_nonfinite, nv, problems)
    if f.min() < 0 or f.max() >= nv:
        problems.append(MeshProblem("error", "bad_index", "Triangle indices out of range."))
        return MeshReport(nf, nv, False, False, 0, 0, 0, 0, 0, 0, False, 0, bbox, None,
                          n_nonfinite, 0, problems)

    degenerate = _degenerate_mask(v, f)
    duplicate = _duplicate_mask(f)
    n_deg, n_dup = int(degenerate.sum()), int(duplicate.sum())

    # topology on faces that are not index-degenerate (edge (a,a) is meaningless)
    idx_ok = (f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])
    ft = f[idx_ok]
    edges, counts, inv, directed = _edge_stats(ft, nv)
    boundary = edges[counts == 1]
    n_boundary = int(len(boundary))
    n_nonmanifold = int((counts > 2).sum())
    n_holes = _n_loops(boundary, nv)

    # winding: for edges used exactly twice, the two directed uses must be opposite
    two = counts[inv] == 2
    if two.any():
        d = directed[two]
        e_id = inv[two]
        forward = d[:, 0] < d[:, 1]
        fwd_count = np.bincount(e_id, weights=forward, minlength=len(edges))
        n_inconsistent = int(((counts == 2) & (fwd_count != 1)).sum())
    else:
        n_inconsistent = 0
    winding_ok = n_inconsistent == 0

    used = np.zeros(nv, bool)
    used[f.ravel()] = True
    n_unref = int((~used).sum())
    g = sp.coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(nv, nv)) if len(edges) \
        else sp.coo_matrix((nv, nv))
    _, labels = connected_components(g, directed=False)
    n_components = int(len(np.unique(labels[used])))

    watertight = n_boundary == 0 and n_nonmanifold == 0
    volume = None
    inverted = False
    if watertight and winding_ok and n_nonfinite == 0:
        volume = _signed_volume(v, ft)
        inverted = volume < 0

    # ---- problems (human readable) -----------------------------------------
    if n_nonfinite:
        problems.append(MeshProblem("error", "nonfinite", f"{n_nonfinite} vertices have NaN/inf coordinates."))
    if n_boundary:
        problems.append(MeshProblem("error", "holes",
                                    f"Mesh is open: {n_holes} hole(s), {n_boundary} boundary edges."))
    if n_nonmanifold:
        problems.append(MeshProblem("error", "non_manifold",
                                    f"{n_nonmanifold} non-manifold edges (shared by 3+ faces)."))
    if n_inconsistent:
        problems.append(MeshProblem("error", "winding",
                                    f"Inconsistent winding: {n_inconsistent} edges with flipped neighbour faces."))
    if inverted:
        problems.append(MeshProblem("error", "inverted", "Normals point inward (negative volume)."))
    if n_deg:
        problems.append(MeshProblem("warning", "degenerate", f"{n_deg} degenerate (zero-area) faces."))
    if n_dup:
        problems.append(MeshProblem("warning", "duplicate", f"{n_dup} duplicate faces."))
    if n_components > 1:
        problems.append(MeshProblem("info", "components",
                                    f"{n_components} separate shells (may overlap; slicers usually union them)."))
    if n_unref:
        problems.append(MeshProblem("info", "unreferenced", f"{n_unref} unused vertices."))
    if watertight and winding_ok and volume is not None and abs(volume) < 1e-9:
        problems.append(MeshProblem("error", "zero_volume", "Closed mesh with zero volume."))

    return MeshReport(nf, nv, watertight, winding_ok, n_boundary, n_holes, n_deg, n_dup,
                      n_nonmanifold, n_inconsistent, inverted, n_components, bbox, volume,
                      n_nonfinite, n_unref, problems)


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------
def _boundary_cycles(bdir: np.ndarray) -> list[list[int]]:
    """Split directed boundary edges into edge-disjoint SIMPLE cycles (vertex lists).
    Where the boundary touches itself (pinch vertex) the walk is cut into separate
    simple loops, so a fan never uses the same vertex twice. Open chains (not possible
    on a real boundary) are dropped."""
    out_edges: dict[int, list[int]] = {}
    for a, b in bdir.tolist():
        out_edges.setdefault(a, []).append(b)
    cycles = []
    for start in list(out_edges):
        while out_edges.get(start):
            path = [start]
            pos = {start: 0}
            cur = out_edges[start].pop()
            while True:
                if cur in pos:                       # closed a loop: cut it out
                    i = pos[cur]
                    loop = path[i:]
                    cycles.append(loop)
                    for x in loop[1:]:
                        del pos[x]
                    del path[i + 1:]
                    if cur == start and not out_edges.get(start):
                        break
                nxt = out_edges.get(cur)
                if not nxt:
                    break                            # open chain or finished
                if cur not in pos:
                    pos[cur] = len(path)
                    path.append(cur)
                cur = nxt.pop()
    return [c for c in cycles if len(c) >= 3]


def _fill_holes(v: np.ndarray, f: np.ndarray, max_hole_edges: int):
    """Close boundary loops of at most ``max_hole_edges`` edges.

    Boundary edges are walked into closed cycles. Triangles are oriented opposite to the
    boundary edges, so a consistently wound mesh stays consistent: 3-edge loops get one
    triangle, larger loops a fan around their centroid (one new vertex; fine for small,
    roughly planar holes, not for large non-convex ones). Returns
    (v, f, n_filled, n_skipped)."""
    nv = len(v)
    directed = np.stack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], axis=1).reshape(-1, 2)
    edges, counts, inv, _ = _edge_stats(f, nv)
    bdir = directed[counts[inv] == 1]   # boundary edges with the orientation of their face
    if len(bdir) == 0:
        return v, f, 0, 0
    new_v, new_f = [], []
    filled = skipped = 0
    next_v = nv
    for cyc in _boundary_cycles(bdir):
        if len(cyc) > max_hole_edges or len(cyc) < 3:
            skipped += 1
            continue
        if len(cyc) == 3:
            a, b, c = cyc
            new_f.append([c, b, a])
        else:
            new_v.append(v[cyc].mean(0))
            c = next_v
            next_v += 1
            for a, b in zip(cyc, cyc[1:] + cyc[:1]):
                new_f.append([b, a, c])
        filled += 1
    if new_v:
        v = np.vstack([v, np.asarray(new_v)])
    if new_f:
        f = np.vstack([f, np.asarray(new_f, dtype=np.int64)])
    return v, f, filled, skipped


def _fix_winding(v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, int]:
    """Make winding consistent per connected patch (BFS over manifold edges, scipy), then
    orient each closed patch outward (positive volume). Returns (faces, n_flipped).
    On non-orientable input the BFS result leaves some edges inconsistent: the caller's
    re-check reports it."""
    from scipy.sparse.csgraph import breadth_first_order

    nf = len(f)
    faces = f.copy()
    if nf == 0:
        return faces, 0
    edges, counts, inv, directed = _edge_stats(f, len(v))
    face_of = np.repeat(np.arange(nf), 3)
    m2 = counts[inv] == 2
    e_ids = inv[m2]
    order = np.argsort(e_ids, kind="stable")
    fa = face_of[m2][order].reshape(-1, 2)
    da = directed[m2][order].reshape(-1, 2, 2)
    same = da[:, 0, 0] == da[:, 1, 0]          # both faces traverse the edge the same way
    # graph weights: 1 = keep relative orientation, 2 = flip relative orientation
    w = np.where(same, 2.0, 1.0)
    g = sp.coo_matrix((np.r_[w, w], (np.r_[fa[:, 0], fa[:, 1]], np.r_[fa[:, 1], fa[:, 0]])),
                      shape=(nf, nf)).tocsr()
    g.sum_duplicates()
    flip = np.zeros(nf, bool)
    seen = np.zeros(nf, bool)
    n_comp, labels = connected_components(g, directed=False)
    for comp in range(n_comp):
        root = int(np.argmax(labels == comp))
        if seen[root]:
            continue
        bfs, pred = breadth_first_order(g, root, directed=False, return_predecessors=True)
        seen[bfs] = True
        if len(bfs) > 1:
            child = bfs[1:]
            parent = pred[child]
            rel = np.asarray(g[child, parent]).ravel() >= 2   # (may be 3 if both relations: non-orientable)
            # flip[x] = XOR of rel along the tree path root -> x, by pointer jumping
            # (O(n log depth), fully vectorised)
            anc = np.arange(nf)
            acc = np.zeros(nf, bool)
            anc[child] = parent
            acc[child] = rel
            anc[root] = root
            for _ in range(64):
                done = anc[bfs] == root
                if done.all():
                    break
                acc[bfs] = acc[bfs] ^ np.where(anc[bfs] == root, False, acc[anc[bfs]])
                anc[bfs] = anc[anc[bfs]]
            flip[bfs] = acc[bfs]
    # per patch choose the global orientation:
    #  * signed volume around the patch centroid when it is significant (closed or
    #    almost closed patch -> outward normals),
    #  * otherwise (flat / very open patch) the majority orientation of the input.
    for comp in range(n_comp):
        idx = np.nonzero(labels == comp)[0]
        cand = f[idx].copy()
        fl = flip[idx]
        cand[fl] = cand[fl][:, ::-1]
        pts = v[np.unique(cand)]
        cen = pts.mean(0)
        vol = _signed_volume(v - cen, cand)
        ext = float(np.max(pts.max(0) - pts.min(0))) if len(pts) else 0.0
        if abs(vol) > 1e-3 * ext ** 3:
            if vol < 0:
                flip[idx] = ~fl
        elif fl.sum() * 2 > len(idx):
            flip[idx] = ~fl
    faces[flip] = faces[flip][:, ::-1]
    n_flipped = int(np.sum(np.any(faces != f, axis=1)))
    return faces, n_flipped


def _manifold_roundtrip(v: np.ndarray, f: np.ndarray, union_shells: bool):
    """Pass the mesh through manifold3d. Returns (v, f, message) or (None, None, reason)."""
    import manifold3d as m3d
    mesh = m3d.Mesh(vert_properties=np.ascontiguousarray(v, np.float32),
                    tri_verts=np.ascontiguousarray(f, np.uint32))
    man = m3d.Manifold(mesh)
    st = man.status()
    if st != m3d.Error.NoError:
        return None, None, f"manifold3d rejected the mesh ({st.name})"
    if union_shells:
        parts = man.decompose()
        if len(parts) > 1:
            man = m3d.Manifold.batch_boolean(parts, m3d.OpType.Add)
    out = man.to_mesh()
    return (np.asarray(out.vert_properties[:, :3], np.float64),
            np.asarray(out.tri_verts, np.int64), "ok")


def repair_mesh(mesh: trimesh.Trimesh, *, fill_holes: bool = True, max_hole_edges: int = 64,
                fix_winding: bool = True, use_manifold: str = "auto",
                union_shells: bool = False) -> tuple[trimesh.Trimesh, RepairReport]:
    """Return a repaired COPY of ``mesh`` and a report. ``mesh`` is never modified.

    Steps (each only when needed, each reported):
      1. drop faces touching non-finite vertices
      2. drop degenerate faces (repeated index or ~zero area)
      3. drop duplicate faces (same vertex set; the first occurrence is kept)
      4. drop unreferenced vertices
      5. make winding consistent and orient closed shells outward
      6. close holes of at most ``max_hole_edges`` edges (larger ones are reported)
      7. manifold3d round trip: ``"auto"`` when ``union_shells`` is set and the mesh is
         closed, ``"always"`` to force it, ``"never"`` to skip. ``union_shells`` merges
         overlapping shells into one solid (removes internal faces).
    The result is re-checked; ``success`` means no error-severity problem remains.
    """
    before = check_mesh(mesh)
    v = np.array(mesh.vertices, dtype=np.float64, copy=True)
    f = np.array(mesh.faces, dtype=np.int64, copy=True).reshape(-1, 3)
    actions: list[str] = []
    counts: dict[str, int] = {}
    failures: list[str] = []

    if len(f) and (f.min() < 0 or f.max() >= len(v)):
        keep = np.all((f >= 0) & (f < len(v)), axis=1)
        counts["bad_index_faces_removed"] = int((~keep).sum())
        f = f[keep]
        actions.append(f"Removed {counts['bad_index_faces_removed']} faces with invalid indices.")

    finite = np.all(np.isfinite(v), axis=1)
    if not finite.all() and len(f):
        keep = finite[f].all(axis=1)
        counts["nonfinite_faces_removed"] = int((~keep).sum())
        f = f[keep]
        actions.append(f"Removed {counts['nonfinite_faces_removed']} faces with NaN/inf vertices.")

    if len(f):
        deg = _degenerate_mask(v, f)
        if deg.any():
            counts["degenerate_removed"] = int(deg.sum())
            f = f[~deg]
            actions.append(f"Removed {counts['degenerate_removed']} degenerate faces.")
    if len(f):
        dup = _duplicate_mask(f)
        if dup.any():
            counts["duplicates_removed"] = int(dup.sum())
            f = f[~dup]
            actions.append(f"Removed {counts['duplicates_removed']} duplicate faces.")

    used = np.zeros(len(v), bool)
    used[f.ravel()] = True
    if not used.all():
        remap = np.cumsum(used) - 1
        counts["unreferenced_vertices_removed"] = int((~used).sum())
        v, f = v[used], remap[f]
        actions.append(f"Removed {counts['unreferenced_vertices_removed']} unused vertices.")

    if len(f) == 0:
        failures.append("No valid triangle left after cleaning.")
        out = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), np.int64), process=False)
        after = check_mesh(out)
        return out, RepairReport(actions, before, after, False, counts, failures)

    def _check(vv, ff):
        return check_mesh(trimesh.Trimesh(vertices=vv, faces=ff, process=False, validate=False))

    # reuse the initial report when cleaning changed nothing (saves a full pass on big meshes)
    mid = before if not actions else _check(v, f)
    cur = mid
    if fix_winding and (mid.n_inconsistent_edges or mid.inverted or mid.n_boundary_edges):
        f2, n_flipped = _fix_winding(v, f)
        if n_flipped:
            f = f2
            counts["faces_flipped"] = n_flipped
            actions.append(f"Flipped {n_flipped} faces to make winding consistent and outward.")
            cur = _check(v, f)

    if fill_holes:
        if cur.n_boundary_edges:
            v, f, filled, skipped = _fill_holes(v, f, max_hole_edges)
            if filled:
                counts["holes_filled"] = filled
                actions.append(f"Filled {filled} hole(s).")
            if skipped:
                counts["holes_skipped"] = skipped
                failures.append(f"{skipped} hole(s) not filled (larger than {max_hole_edges} edges "
                                "or not a simple loop).")
            post = _check(v, f) if filled else None
            if filled and fix_winding and (not post.winding_consistent or post.inverted):
                f2, n_flipped = _fix_winding(v, f)
                if n_flipped:
                    f = f2
                    counts["faces_flipped"] = counts.get("faces_flipped", 0) + n_flipped
                    actions.append(f"Flipped {n_flipped} faces after hole filling.")
            if filled:
                cur = _check(v, f)

    run_m = use_manifold == "always" or (use_manifold == "auto" and union_shells)
    if run_m:
        if cur.watertight:
            mv, mf, msg = _manifold_roundtrip(v, f, union_shells)
            if mv is None:
                failures.append(msg)
            else:
                actions.append("Rebuilt through manifold3d" + (" (overlapping shells united)." if union_shells else "."))
                v, f = mv, mf
        else:
            failures.append("manifold3d round trip skipped: mesh is still open or non-manifold.")

    out = trimesh.Trimesh(vertices=v, faces=f, process=False, validate=False)
    after = cur if (cur.n_faces == len(f) and cur.n_vertices == len(v) and not run_m) else check_mesh(out)
    for p in after.problems:
        if p.severity == "error":
            failures.append(f"Remaining after repair: {p.message}")
    success = after.is_valid
    return out, RepairReport(actions, before, after, success, counts, failures)

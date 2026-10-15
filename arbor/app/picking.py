"""Fast ray picking on the displayed model (display/interaction only, no support logic).

trimesh's ``mesh.ray`` builds an R-tree on the UI thread the first time it is used (4.5 s on
1 M triangles) and then costs ~100 ms per ray, which froze the viewport on hover (QA-11).

MeshPicker is a two level bounding sphere hierarchy:
  * triangles are sorted along a Morton (Z-order) curve of their centroids, so consecutive
    triangles are spatially close,
  * they are grouped in blocks of BLOCK triangles; each block gets a bounding sphere,
  * a ray first tests the block spheres (a few thousand), then runs a vectorised
    Moller-Trumbore test on the triangles of the blocks it crosses only.

The structure is built once per mesh, from copies of the arrays, so it can be built in a
background thread (Viewport does so for large meshes) while the UI keeps the mesh.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

BLOCK = 128
SYNC_BUILD_MAX_FACES = 60_000      # below this, building takes a few ms: done immediately


def _morton_order(points: np.ndarray) -> np.ndarray:
    lo = points.min(axis=0)
    ext = np.maximum(points.max(axis=0) - lo, 1e-12)
    q = np.clip(((points - lo) / ext * 1023.0).astype(np.uint64), 0, 1023)

    def spread(x):
        x = (x | (x << np.uint64(16))) & np.uint64(0x030000FF)
        x = (x | (x << np.uint64(8))) & np.uint64(0x0300F00F)
        x = (x | (x << np.uint64(4))) & np.uint64(0x030C30C3)
        x = (x | (x << np.uint64(2))) & np.uint64(0x09249249)
        return x
    code = spread(q[:, 0]) | (spread(q[:, 1]) << np.uint64(1)) | (spread(q[:, 2]) << np.uint64(2))
    return np.argsort(code, kind="stable")


class MeshPicker:
    def __init__(self, vertices: np.ndarray, faces: np.ndarray):
        self.vertices = np.array(vertices, dtype=np.float64, copy=True)
        self.faces = np.array(faces, dtype=np.int64, copy=True)
        self.ready = False
        self.build_seconds = 0.0

    def build(self) -> "MeshPicker":
        import time
        t0 = time.perf_counter()
        V, F = self.vertices, self.faces
        n = len(F)
        if n == 0:
            self.ready = True
            return self
        tri = V[F]                                          # (n, 3, 3)
        order = _morton_order(tri.mean(axis=1))
        tri = tri[order]
        self.face_index = order
        self.v0 = tri[:, 0].copy()
        self.e1 = tri[:, 1] - tri[:, 0]
        self.e2 = tri[:, 2] - tri[:, 0]
        nrm = np.cross(self.e1, self.e2)
        ln = np.linalg.norm(nrm, axis=1)
        self.normals = nrm / np.where(ln > 0, ln, 1.0)[:, None]
        nb = (n + BLOCK - 1) // BLOCK
        pad = nb * BLOCK - n
        pts = tri.reshape(-1, 9)
        if pad:
            pts = np.vstack([pts, np.repeat(pts[-1:], pad, axis=0)])
        pts = pts.reshape(nb, BLOCK * 3, 3)
        lo, hi = pts.min(axis=1), pts.max(axis=1)
        self.block_center = (lo + hi) / 2
        self.block_radius = np.sqrt(((pts - self.block_center[:, None, :]) ** 2).sum(-1)).max(1)
        self.n = n
        self.build_seconds = time.perf_counter() - t0
        self.ready = True
        return self

    def intersect(self, origin, direction) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """All hits of the ray (t > 0): (locations (k, 3), distances (k,), face indices (k,)),
        sorted by distance."""
        o = np.asarray(origin, float)
        d = np.asarray(direction, float)
        d = d / (np.linalg.norm(d) or 1.0)
        empty = (np.zeros((0, 3)), np.zeros(0), np.zeros(0, dtype=np.int64))
        if not self.ready or getattr(self, "n", 0) == 0:
            return empty
        oc = self.block_center - o
        tp = oc @ d
        dist2 = np.einsum("ij,ij->i", oc, oc) - tp * tp
        r = self.block_radius * (1 + 1e-9) + 1e-9
        blocks = np.nonzero((dist2 <= r * r) & (tp + r >= 0))[0]
        if len(blocks) == 0:
            return empty
        idx = (blocks[:, None] * BLOCK + np.arange(BLOCK)[None, :]).ravel()
        idx = idx[idx < self.n]
        e1, e2, v0 = self.e1[idx], self.e2[idx], self.v0[idx]
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, p)
        scale = np.einsum("ij,ij->i", e1, e1) * np.einsum("ij,ij->i", e2, e2)
        ok = np.abs(det) > 1e-14 * np.sqrt(np.maximum(scale, 1e-300))
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = o - v0
        u = np.einsum("ij,ij->i", tv, p) * inv
        q = np.cross(tv, e1)
        v = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        eps = 1e-9
        hit = ok & (u >= -eps) & (v >= -eps) & (u + v <= 1 + eps) & (t > 0)
        if not hit.any():
            return empty
        t, sel = t[hit], idx[hit]
        k = np.argsort(t)
        t, sel = t[k], sel[k]
        return o + t[:, None] * d, t, self.face_index[sel]

    def face_normal(self, face: int) -> np.ndarray:
        """Normal of an original face index (winding order)."""
        pos = getattr(self, "_inverse", None)
        if pos is None:
            pos = np.empty_like(self.face_index)
            pos[self.face_index] = np.arange(len(self.face_index))
            self._inverse = pos
        return self.normals[pos[face]].copy()


class BackgroundPicker:
    """Builds a MeshPicker in a daemon thread. ``picker`` is None until ready."""

    def __init__(self, mesh) -> None:
        self.mesh = mesh
        self.picker: Optional[MeshPicker] = None
        self.error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        mp = MeshPicker(np.asarray(mesh.vertices), np.asarray(mesh.faces))
        if len(mp.faces) <= SYNC_BUILD_MAX_FACES:
            self.picker = mp.build()
        else:
            self._thread = threading.Thread(target=self._run, args=(mp,), daemon=True,
                                            name="arbor-picker-build")
            self._thread.start()

    def _run(self, mp: MeshPicker) -> None:
        try:
            self.picker = mp.build()
        except BaseException as exc:     # reported through .error, never raised in the thread
            self.error = exc

    @property
    def ready(self) -> bool:
        return self.picker is not None

    def wait(self, timeout: Optional[float] = None) -> Optional[MeshPicker]:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.picker

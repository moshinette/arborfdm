"""Pure geometric transforms. Every function returns a NEW mesh; inputs are untouched.

Conventions: millimetres, Z up, plate at z = 0. Euler angles are in degrees, extrinsic
X then Y then Z (i.e. R = Rz @ Ry @ Rx applied to column vectors).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import trimesh


# ---------------------------------------------------------------------------
# matrices
# ---------------------------------------------------------------------------
def rotation_matrix_xyz(rx: float, ry: float, rz: float) -> np.ndarray:
    """4x4 rotation from extrinsic X, Y, Z angles in degrees (R = Rz Ry Rx)."""
    ax, ay, az = np.radians([rx, ry, rz])
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    m = np.eye(4)
    m[:3, :3] = Rz @ Ry @ Rx
    return m


def euler_xyz_from_matrix(m: np.ndarray) -> tuple[float, float, float]:
    """Inverse of :func:`rotation_matrix_xyz` (degrees). Uses the 3x3 part only."""
    r = np.asarray(m, float)[:3, :3]
    sy = -r[2, 0]
    sy = max(-1.0, min(1.0, sy))
    ry = np.arcsin(sy)
    if abs(sy) < 1 - 1e-9:
        rx = np.arctan2(r[2, 1], r[2, 2])
        rz = np.arctan2(r[1, 0], r[0, 0])
    else:  # gimbal lock: set rz = 0
        rz = 0.0
        rx = np.arctan2(-r[1, 2], r[1, 1])
    return tuple(float(np.degrees(a)) for a in (rx, ry, rz))


def scale_matrix(factor) -> np.ndarray:
    f = np.broadcast_to(np.asarray(factor, float), (3,))
    if np.any(f <= 0) or not np.all(np.isfinite(f)):
        raise ValueError(f"Scale factor must be finite and > 0, got {factor}")
    return np.diag([f[0], f[1], f[2], 1.0])


def translation_matrix(t: Sequence[float]) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = np.asarray(t, float)
    return m


def scale_factor_for_size(mesh: trimesh.Trimesh, target_mm: float, axis: Optional[int] = None) -> float:
    """Uniform factor so that the largest bbox extent (or the extent along ``axis``,
    0=X 1=Y 2=Z) becomes ``target_mm``."""
    ext = mesh.bounds[1] - mesh.bounds[0]
    cur = float(ext.max() if axis is None else ext[axis])
    if cur <= 0 or target_mm <= 0:
        raise ValueError("Cannot scale a flat/empty mesh or to a non-positive size.")
    return target_mm / cur


# ---------------------------------------------------------------------------
# mesh operations
# ---------------------------------------------------------------------------
def apply_transform(mesh: trimesh.Trimesh, matrix: np.ndarray) -> trimesh.Trimesh:
    """New mesh = matrix applied to ``mesh``. Mirroring matrices reverse the triangle
    order so normals stay outward."""
    m = np.asarray(matrix, float)
    v = np.asarray(mesh.vertices, float)
    nv = v @ m[:3, :3].T + m[:3, 3]
    f = np.array(mesh.faces, dtype=np.int64, copy=True)
    if np.linalg.det(m[:3, :3]) < 0:
        f = f[:, ::-1].copy()
    return trimesh.Trimesh(vertices=nv, faces=f, process=False, validate=False)


def scale_uniform(mesh: trimesh.Trimesh, factor: Optional[float] = None, *,
                  target_size_mm: Optional[float] = None, axis: Optional[int] = None) -> trimesh.Trimesh:
    """Scale about the bbox centre, by ``factor`` or to ``target_size_mm``."""
    if (factor is None) == (target_size_mm is None):
        raise ValueError("Give exactly one of factor / target_size_mm")
    if factor is None:
        factor = scale_factor_for_size(mesh, target_size_mm, axis)
    c = mesh.bounds.mean(0)
    mat = translation_matrix(c) @ scale_matrix(factor) @ translation_matrix(-c)
    return apply_transform(mesh, mat)


def rotate(mesh: trimesh.Trimesh, rx: float = 0, ry: float = 0, rz: float = 0) -> trimesh.Trimesh:
    """Rotate about the bbox centre."""
    c = mesh.bounds.mean(0)
    mat = translation_matrix(c) @ rotation_matrix_xyz(rx, ry, rz) @ translation_matrix(-c)
    return apply_transform(mesh, mat)


def plate_matrix(mesh: trimesh.Trimesh) -> np.ndarray:
    """Translation putting min z at 0 and the XY bbox centre at the origin."""
    lo, hi = mesh.bounds
    c = (lo + hi) / 2
    return translation_matrix([-c[0], -c[1], -lo[2]])


def place_on_plate(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    return apply_transform(mesh, plate_matrix(mesh))


def rotation_aligning(a: Sequence[float], b: Sequence[float]) -> np.ndarray:
    """4x4 rotation taking unit direction ``a`` onto ``b`` (Rodrigues; handles the
    antiparallel case)."""
    a = np.asarray(a, float) / np.linalg.norm(a)
    b = np.asarray(b, float) / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    m = np.eye(4)
    if c > 1 - 1e-12:
        return m
    if c < -1 + 1e-12:
        axis = np.cross(a, [1.0, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        m[:3, :3] = 2 * np.outer(axis, axis) - np.eye(3)
        return m
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    m[:3, :3] = np.eye(3) + k + k @ k * (1.0 / (1.0 + c))
    return m


def lay_flat_matrix(mesh: trimesh.Trimesh, face_index: int) -> np.ndarray:
    """Rotation turning face ``face_index`` downward (normal -> -Z) followed by plate
    placement."""
    t = mesh.vertices[mesh.faces[face_index]]
    n = np.cross(t[1] - t[0], t[2] - t[0])
    if np.linalg.norm(n) == 0:
        raise ValueError(f"Face {face_index} is degenerate")
    rot = rotation_aligning(n, [0, 0, -1])
    rotated = apply_transform(mesh, rot)
    return plate_matrix(rotated) @ rot


def lay_flat_on_face(mesh: trimesh.Trimesh, face_index: int) -> trimesh.Trimesh:
    return apply_transform(mesh, lay_flat_matrix(mesh, face_index))


# ---------------------------------------------------------------------------
# ModelTransform (stored in project files)
# ---------------------------------------------------------------------------
@dataclass
class ModelTransform:
    """Transform from source file coordinates to plate coordinates.

    matrix = T(translation) @ R(rotation_deg) @ S(scale), rotation and scale are applied
    about the source origin. Use :meth:`placed_on_plate` to compute the translation that
    lays the result on the plate."""
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float = 1.0
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def to_matrix(self) -> np.ndarray:
        return (translation_matrix(self.translation) @ rotation_matrix_xyz(*self.rotation_deg)
                @ scale_matrix(self.scale))

    def apply(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        return apply_transform(mesh, self.to_matrix())

    def placed_on_plate(self, source_mesh: trimesh.Trimesh) -> "ModelTransform":
        """Same rotation/scale, translation chosen so the result sits on the plate,
        centred in XY."""
        base = ModelTransform(self.rotation_deg, self.scale, (0.0, 0.0, 0.0))
        moved = base.apply(source_mesh)
        t = plate_matrix(moved)[:3, 3]
        return ModelTransform(self.rotation_deg, self.scale, tuple(float(x) for x in t))

    @classmethod
    def from_matrix(cls, m: np.ndarray) -> "ModelTransform":
        """Decompose a rotation x uniform scale + translation matrix."""
        m = np.asarray(m, float)
        a = m[:3, :3]
        s = float(np.cbrt(np.linalg.det(a)))
        if s <= 0:
            raise ValueError("Matrix contains a mirror or is singular")
        r = a / s
        if not np.allclose(r @ r.T, np.eye(3), atol=1e-6):
            raise ValueError("Matrix is not rotation x uniform scale")
        return cls(euler_xyz_from_matrix(r), s, tuple(float(x) for x in m[:3, 3]))

    def to_dict(self) -> dict:
        return {"rotation_deg": [float(x) for x in self.rotation_deg],
                "scale": float(self.scale),
                "translation": [float(x) for x in self.translation]}

    @classmethod
    def from_dict(cls, d: dict) -> "ModelTransform":
        rot = tuple(float(x) for x in d.get("rotation_deg", (0, 0, 0)))
        tr = tuple(float(x) for x in d.get("translation", (0, 0, 0)))
        if len(rot) != 3 or len(tr) != 3:
            raise ValueError("rotation_deg and translation need 3 values")
        sc = float(d.get("scale", 1.0))
        if not sc > 0:
            raise ValueError("scale must be > 0")
        return cls(rot, sc, tr)

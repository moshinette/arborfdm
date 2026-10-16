"""Automatic orientation suggestions.

Candidates
----------
* the current orientation,
* the 6 axis-aligned orientations (each of +-X, +-Y, +-Z pointing down),
* the largest facets of the convex hull lying on the plate (these are the statically
  stable resting poses; trimesh.poses.compute_stable_poses is not used because it needs
  a watertight mesh and is slow on big meshes).
Candidates whose down direction is within 1 degree of another are merged.

Criteria (computed on the mesh rotated and placed on the plate, z >= 0)
-----------------------------------------------------------------------
A face needs support when its downward tilt from vertical exceeds ``overhang_angle_deg``
(same convention as SupportParams): with unit normal n, ``-n_z > sin(overhang_angle)``.
Faces whose centroid is within ``plate_eps`` mm of the plate are resting on it and are
not overhangs.

* ``overhang_area``      sum of areas of overhang faces (mm^2)
* ``overhang_moment``    sum(area * centroid height) (mm^3): overhang area weighted by
                         height above the plate
* ``support_volume``     sum(projected area * height) (mm^3): volume of vertical
                         columns from overhang faces to the plate. UPPER BOUND: model parts
                         below an overhang are not subtracted (tree supports also use less).
* ``height``             max z (mm): print time and wobble
* ``footprint``          area (mm^2) of the convex hull of vertices within ``plate_eps``
                         of the plate (contact with the plate)
* ``stability``          margin / centroid height: distance from the projected centroid
                         to the footprint edge divided by centroid height (tan of the
                         tipping angle); negative when the centroid falls outside the
                         footprint (needs supports or raft to stand).

Cost (lower is better), all terms dimensionless:
    cost = 2.0 * support_volume / V_ref
         + 0.5 * overhang_moment / V_ref
         + 1.0 * overhang_area   / A_total
         + 0.1 * height / L_ref
         + 0.3 * max(0, 0.2 - stability)          (penalise poses that tip easily)
         - 0.1 * min(footprint / A_ref, 1)        (small bonus for a solid base)
with V_ref = volume of the cube whose diagonal is L_ref (rotation invariant), A_total = total surface area, L_ref = bbox diagonal, A_ref = L_ref^2 / 4.
The weights are heuristic defaults (no print data yet) and are exposed in ``weights``.
Overlapping shells are not united first: their internal faces are counted (run
repair_mesh(union_shells=True) before if needed). Self-shadowing is ignored.
Reported metric (not part of the cost, does not change the ranking)
-------------------------------------------------------------------
``overhang_surface_area`` (mm^2) and ``overhang_surface_pct`` (%): area of the faces whose
downward tilt exceeds the engine's EFFECTIVE overhang angle (engine.analysis
.effective_overhang_angle_deg: step per layer capped at one line width), faces resting on
the plate (all vertices below one layer height) excluded, divided by the total area of the
mesh. Computed on every face of the rotated mesh (no sampling), identical for all
candidates. Same mask as analysis._face_analysis (-n_z > sin(angle) and not on plate).

Miniature specific concerns (faces, detail side) are NOT modelled: the user chooses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import trimesh
from scipy.spatial import ConvexHull, QhullError

from .transform import euler_xyz_from_matrix, rotation_aligning

DEFAULT_WEIGHTS = {
    "support_volume": 2.0,
    "overhang_moment": 0.5,
    "overhang_area": 1.0,
    "height": 0.1,
    "instability": 0.3,
    "footprint_bonus": 0.1,
}
MAX_FACES = 300_000

# Colour levels of the reported overhang surface (%), see overhang_level().
LEVEL_ABS_GOOD = 5.0       # <= 5 %: always "good" (nothing worth worrying about)
LEVEL_ABS_BAD = 40.0       # >= 40 %: always "bad"
LEVEL_FLAT_SPREAD = 5.0    # best-to-worst spread below this (points): relative ranking is noise
LEVEL_FLAT_GOOD = 10.0     # absolute thresholds used then: < 10 % good, < 25 % medium, else bad
LEVEL_FLAT_BAD = 25.0


def overhang_surface(mesh: trimesh.Trimesh, rot: np.ndarray, angle_deg: float,
                     layer_height: float) -> tuple[float, float]:
    """(overhang surface area in mm^2, total area in mm^2) of ``mesh`` rotated by ``rot``
    and placed on the plate. Same criterion as the engine's overhang face mask."""
    v = np.asarray(mesh.vertices, float)
    f = np.asarray(mesh.faces)
    r = rot[:3, :3]
    z = v @ r[2]
    z = z - z.min()
    nz = np.asarray(mesh.face_normals, float) @ r[2]
    areas = np.asarray(mesh.area_faces, float)
    mask = (-nz > np.sin(np.radians(angle_deg))) & ~(z[f].max(axis=1) < layer_height)
    return float(areas[mask].sum()), float(areas.sum())


def overhang_level(pct: float, all_pcts) -> str:
    """"good" / "medium" / "bad" for an overhang surface of ``pct`` % among the candidates
    ``all_pcts``. Relative first: the share of overhang depends on the model (a figurine
    with arms never gets near 0), so the colour follows the position between the best and
    the worst candidate (lower third good, middle third medium, upper third bad). Absolute
    guards keep it honest: <= 5 % is always good, >= 40 % always bad. When all candidates
    are within 5 points of each other, the ranking is not meaningful and absolute
    thresholds apply (< 10 % good, < 25 % medium, else bad)."""
    if pct <= LEVEL_ABS_GOOD:
        return "good"
    if pct >= LEVEL_ABS_BAD:
        return "bad"
    lo, hi = min(all_pcts), max(all_pcts)
    if hi - lo < LEVEL_FLAT_SPREAD:
        return "good" if pct < LEVEL_FLAT_GOOD else "medium" if pct < LEVEL_FLAT_BAD else "bad"
    t = (pct - lo) / (hi - lo)
    return "good" if t < 1 / 3 else "medium" if t < 2 / 3 else "bad"



@dataclass
class OrientationCandidate:
    rotation_deg: tuple[float, float, float]   # extrinsic XYZ, see transform.rotation_matrix_xyz
    matrix: np.ndarray                         # 4x4: rotation then plate placement
    cost: float
    criteria: dict[str, float]
    label: str
    down_direction: np.ndarray                 # source-frame direction that points to -Z
    terms: dict[str, float] = field(default_factory=dict)

    def describe(self) -> str:
        c = self.criteria
        return (f"{self.label}: cost {self.cost:.3f} | overhang {c['overhang_area']:.0f} mm2, "
                f"support ~{c['support_volume']:.0f} mm3, height {c['height']:.1f} mm, "
                f"footprint {c['footprint']:.0f} mm2, stability {c['stability']:.2f}"
                + (f", overhang surface {c['overhang_surface_pct']:.1f} % "
                   f"({c['overhang_surface_area']:.0f} mm2)" if "overhang_surface_pct" in c else ""))


def _sample_faces(mesh: trimesh.Trimesh, max_faces: int, seed: int = 0):
    tri = np.asarray(mesh.triangles, float)
    n = len(tri)
    if n <= max_faces:
        return tri, 1.0
    idx = np.random.default_rng(seed).choice(n, max_faces, replace=False)
    return tri[idx], n / max_faces


def _candidate_downs(vertices: np.ndarray, n_hull: int = 12) -> list[tuple[np.ndarray, str]]:
    out = [(np.array([0, 0, -1.0]), "current")]
    for axis, name in ((0, "X"), (1, "Y"), (2, "Z")):
        for sgn in (1, -1):
            d = np.zeros(3)
            d[axis] = sgn
            out.append((d, f"{'+' if sgn > 0 else '-'}{name} down"))
    try:
        hull = ConvexHull(vertices)
    except (QhullError, ValueError):
        return out
    eq = hull.equations                         # outward normal, offset
    tri = vertices[hull.simplices]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    # group coplanar hull triangles into facets by rounded normal
    key = np.round(eq[:, :3], 3)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    facet_area = np.bincount(inv, weights=area)
    facet_n = np.zeros((len(uniq), 3))
    np.add.at(facet_n, inv, eq[:, :3] * area[:, None])
    facet_n /= np.linalg.norm(facet_n, axis=1, keepdims=True) + 1e-300
    for i in np.argsort(-facet_area)[:n_hull]:
        n_ = facet_n[i]
        out.append((n_, f"hull facet {facet_area[i]:.0f} mm2 ({n_[0]:+.2f},{n_[1]:+.2f},{n_[2]:+.2f})"))
    return out


def _dedupe(cands, tol_deg: float = 1.0):
    kept = []
    c = np.cos(np.radians(tol_deg))
    for d, label in cands:
        d = d / np.linalg.norm(d)
        if all(np.dot(d, k[0]) < c for k in kept):
            kept.append((d, label))
    return kept


def evaluate_orientation(tri: np.ndarray, area_scale: float, rot: np.ndarray,
                         overhang_angle_deg: float, plate_eps: float = 0.2,
                         centroid: Optional[np.ndarray] = None) -> dict[str, float]:
    """Criteria for triangles ``tri`` (F,3,3) after rotation ``rot`` (4x4) and plate
    placement."""
    r = rot[:3, :3]
    t = tri @ r.T
    zmin = t[:, :, 2].min()
    t = t - np.array([0, 0, zmin])
    cr = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    a2 = np.linalg.norm(cr, axis=1)
    ok = a2 > 0
    area = 0.5 * a2 * area_scale
    nz = np.zeros(len(t))
    nz[ok] = cr[ok, 2] / a2[ok]
    cz = t[:, :, 2].mean(1)
    sin_oa = np.sin(np.radians(overhang_angle_deg))
    over = ok & (-nz > sin_oa) & (cz > plate_eps)
    ov_area = float(area[over].sum())
    moment = float((area[over] * cz[over]).sum())
    volume = float((area[over] * -nz[over] * cz[over]).sum())
    height = float(t[:, :, 2].max())

    pts = t.reshape(-1, 3)
    base = pts[pts[:, 2] <= plate_eps][:, :2]
    footprint, stability = 0.0, -1.0
    if centroid is None:
        cen = (t.mean(1) * area[:, None]).sum(0) / max(area.sum(), 1e-300)
    else:
        cen = centroid @ r.T - np.array([0, 0, zmin])
    if len(base) >= 3:
        try:
            h = ConvexHull(base)
            footprint = float(h.volume)
            # signed distance of centroid to hull edges (negative = outside)
            eq = h.equations
            margin = float(-(eq[:, :2] @ cen[:2] + eq[:, 2]).max())
            stability = margin / max(cen[2], 1e-6)
        except (QhullError, ValueError):
            pass
    return {"overhang_area": ov_area, "overhang_moment": moment, "support_volume": volume,
            "height": height, "footprint": footprint, "stability": float(stability)}


def cost_terms(c: dict[str, float], v_ref: float, a_total: float, l_ref: float,
               weights: dict[str, float]) -> dict[str, float]:
    a_ref = l_ref ** 2 / 4
    return {
        "support_volume": weights["support_volume"] * c["support_volume"] / v_ref,
        "overhang_moment": weights["overhang_moment"] * c["overhang_moment"] / v_ref,
        "overhang_area": weights["overhang_area"] * c["overhang_area"] / a_total,
        "height": weights["height"] * c["height"] / l_ref,
        "instability": weights["instability"] * max(0.0, 0.2 - c["stability"]),
        "footprint_bonus": -weights["footprint_bonus"] * min(c["footprint"] / a_ref, 1.0),
    }


def suggest_orientations(mesh: trimesh.Trimesh, overhang_angle_deg: float = 55.0, n: int = 6,
                         weights: Optional[dict[str, float]] = None,
                         max_faces: int = MAX_FACES, effective_angle_deg: Optional[float] = None,
                         layer_height: float = 0.2) -> list[OrientationCandidate]:
    """Return up to ``n`` candidate orientations sorted by increasing cost.
    ``effective_angle_deg`` (default: overhang_angle_deg) and ``layer_height`` define the
    reported overhang surface metric only (see module doc)."""
    eff = overhang_angle_deg if effective_angle_deg is None else effective_angle_deg
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    if len(mesh.faces) == 0:
        return []
    tri, area_scale = _sample_faces(mesh, max_faces)
    verts = np.asarray(mesh.vertices, float)
    hull_pts = verts if len(verts) <= 200_000 else verts[
        np.random.default_rng(1).choice(len(verts), 200_000, replace=False)]
    l_ref = float(np.linalg.norm(mesh.bounds[1] - mesh.bounds[0])) or 1.0
    v_ref = l_ref ** 3 / (3 * np.sqrt(3))      # volume of the cube with diagonal l_ref
    a_total = float(mesh.area) or 1.0
    centroid = None
    try:
        if mesh.is_watertight and mesh.volume > 0:
            centroid = np.asarray(mesh.center_mass, float)
    except Exception:
        centroid = None

    cands = []
    for d, label in _dedupe(_candidate_downs(hull_pts)):
        rot = rotation_aligning(d, [0, 0, -1])
        c = evaluate_orientation(tri, area_scale, rot, overhang_angle_deg, centroid=centroid)
        terms = cost_terms(c, v_ref, a_total, l_ref, w)
        s_area, s_total = overhang_surface(mesh, rot, eff, layer_height)
        c["overhang_surface_area"] = s_area
        c["overhang_surface_pct"] = 100.0 * s_area / max(s_total, 1e-300)
        moved = verts @ rot[:3, :3].T
        lo, hi = moved.min(0), moved.max(0)
        place = np.eye(4)
        place[:3, 3] = [-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]
        full = place @ rot
        cands.append(OrientationCandidate(
            rotation_deg=euler_xyz_from_matrix(rot), matrix=full, cost=float(sum(terms.values())),
            criteria=c, label=label, down_direction=d, terms=terms))
    cands.sort(key=lambda c: c.cost)
    return cands[:n]

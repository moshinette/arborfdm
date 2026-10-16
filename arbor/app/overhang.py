"""Overhang colouring of the model (display only, numpy, no Qt).

Angle convention: the same as SupportParams.overhang_angle_deg and the analysis stage,
measured from the vertical: 0 = vertical wall, 90 = flat ceiling, negative = facing up.
A face at angle a (a = asin(-n_z)) needs support when a > threshold; faces lying on the
plate (max z below one layer) never need it, as in arbor.engine.analysis.

Bands relative to the current threshold T:
  ok        a < T - NEAR_MARGIN_DEG               normal model colour
  near      T - NEAR_MARGIN_DEG <= a < T          yellow (close to the threshold)
  over      T <= a < S                            orange (needs support)
  severe    a >= S                                red (strong overhang, up to a ceiling)
S is the engine's severe angle (analysis.severe_overhang_angle_deg: moderate overhangs
between T and S get sparser contacts) when given, else T + (90 - T) / 2.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NEAR_MARGIN_DEG = 10.0

BAND_KEYS = ("ok", "near", "over", "severe")
BAND_HEX = {"near": "#ffd43b", "over": "#ff922b", "severe": "#f03e3e"}


def _hex_u8(h: str) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


@dataclass(frozen=True)
class Band:
    key: str
    lo: float          # inclusive, degrees from vertical
    hi: float          # exclusive (90 is a flat ceiling)
    label: str


def band_edges(threshold: float, severe: float | None = None) -> tuple[float, float, float]:
    t = float(np.clip(threshold, 0.0, 90.0))
    mid = t + (90.0 - t) / 2.0
    if severe is not None and t < float(severe) < 90.0:
        mid = float(severe)
    return (max(t - NEAR_MARGIN_DEG, -90.0), t, mid)


def bands(threshold: float, severe: float | None = None) -> list[Band]:
    near, t, mid = band_edges(threshold, severe)
    if severe is not None and mid == float(severe):
        over, strong = "moderate, fewer contacts", "severe, full support"
    else:
        over, strong = "needs support", "strong overhang"
    return [Band("ok", -90.0, near, f"below {near:.0f}°"),
            Band("near", near, t, f"{near:.0f} - {t:.0f}°  near threshold"),
            Band("over", t, mid, f"{t:.0f} - {mid:.0f}°  {over}"),
            Band("severe", mid, 90.0, f"{mid:.0f} - 90°  {strong}")]


def face_angles(normals: np.ndarray) -> np.ndarray:
    """Overhang angle of every face in degrees from vertical (float32, vectorised)."""
    nz = np.asarray(normals, dtype=np.float32).reshape(-1, 3)[:, 2]
    return np.degrees(np.arcsin(np.clip(-nz, -1.0, 1.0)))


def face_geometry(vertices: np.ndarray, faces: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(angle from vertical, top z, area) of every face, float32, one vectorised pass.
    Cheaper than trimesh.face_normals on large meshes (no float64 copies, no validation).
    Degenerate faces get angle -90 (never coloured)."""
    v = np.asarray(vertices, dtype=np.float32)
    f = np.asarray(faces)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    cross = np.cross(b - a, c - a)
    norm = np.sqrt(np.einsum("ij,ij->i", cross, cross))
    with np.errstate(invalid="ignore", divide="ignore"):
        nz = np.where(norm > 0, cross[:, 2] / norm, 1.0)
    angles = np.degrees(np.arcsin(np.clip(-nz, -1.0, 1.0)))
    top = np.maximum(np.maximum(a[:, 2], b[:, 2]), c[:, 2])
    return angles.astype(np.float32), top, 0.5 * norm


def face_top_z(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    z = np.asarray(vertices, dtype=np.float32)[:, 2]
    f = np.asarray(faces)
    return np.maximum(np.maximum(z[f[:, 0]], z[f[:, 1]]), z[f[:, 2]])


def classify(angles: np.ndarray, threshold: float, top_z: np.ndarray | None = None,
             plate_height: float = 0.0, severe: float | None = None) -> np.ndarray:
    """Band index (0 ok, 1 near, 2 over, 3 severe) of every face (uint8)."""
    edges = np.asarray(band_edges(threshold, severe), dtype=np.float32)
    cls = np.searchsorted(edges, np.asarray(angles, dtype=np.float32), side="right")
    cls = cls.astype(np.uint8)
    if top_z is not None and plate_height > 0:
        cls[np.asarray(top_z) < plate_height] = 0      # resting on the plate
    return cls


def palette_u8(base_rgba) -> np.ndarray:
    base = tuple(int(round(float(c) * 255)) for c in base_rgba[:4])
    return np.array([base] + [_hex_u8(BAND_HEX[k]) for k in BAND_KEYS[1:]], dtype=np.uint8)


def vertex_colors(cls: np.ndarray, base_rgba, dtype=np.uint8) -> np.ndarray:
    """Per-vertex colours for a non-indexed (3 vertices per face) draw: (3 N, 4)."""
    pal = palette_u8(base_rgba)
    if dtype != np.uint8:
        pal = pal.astype(np.float32) / 255.0
    return np.repeat(pal[cls], 3, axis=0)


def band_areas(cls: np.ndarray, areas: np.ndarray) -> np.ndarray:
    return np.bincount(cls, weights=np.asarray(areas, float), minlength=4)[:4]

"""Contact placement must follow the geometry, not the tessellation.

Covers the fixes in arbor/engine/contacts.py:
  * hanging edges are chained into polylines and sampled evenly (no longer one
    sample per mesh segment);
  * the residual fill (step 6) samples a global XY lattice lifted on the
    overhanging faces instead of face centroids;
and documents why sphere_on_plate (default fixture) has no surface beyond the
overhang angle.
"""
from __future__ import annotations

import math
from functools import lru_cache

import manifold3d as mf
import numpy as np
import pytest
import trimesh

from arbor.engine import analysis, contacts
from arbor.engine.contacts import _edge_polylines, _lattice_on_faces, _sample_polyline
from arbor.engine.params import load_profiles
from arbor.engine.types import ContactReason, Progress
from tests.models import synthetic as S


@lru_cache(maxsize=None)
def P(name="nozzle_04_normal"):
    return load_profiles()[name].params


def place(mesh, params):
    a = analysis.analyze(mesh, params, Progress())
    return a, contacts.place_contacts(mesh, a, params, [], Progress())


def small_shelf(refine_to: float | None = None) -> trimesh.Trimesh:
    m = mf.Manifold.batch_boolean([
        mf.Manifold.cube((3, 10, 12)),
        mf.Manifold.cube((5.5, 10, 1.5)).translate((2.5, 0, 8)),
    ], mf.OpType.Add)
    if refine_to:
        m = m.refine_to_length(refine_to)
    mg = m.to_mesh()
    return trimesh.Trimesh(np.asarray(mg.vert_properties)[:, :3], np.asarray(mg.tri_verts), process=True)


def sorted_positions(cs):
    x = np.array([c.position for c in cs]).reshape(-1, 3)
    return x[np.lexsort((x[:, 1], x[:, 0], x[:, 2]))]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def test_edge_polylines_independent_of_segment_count():
    # an L shaped edge (10 mm then 5 mm, 90 deg turn), coarse vs split in 0.1 mm pieces
    a, b, c = np.array([0., 0, 5]), np.array([10., 0, 5]), np.array([10., 5, 5])
    coarse = np.array([[a, b], [b, c]])
    t = np.linspace(0, 1, 101)
    fine = np.concatenate([
        np.stack([a + t[:-1, None] * (b - a), a + t[1:, None] * (b - a)], axis=1),
        np.stack([b + t[:-1, None] * (c - b), b + t[1:, None] * (c - b)], axis=1)])
    rng = np.random.default_rng(1)
    fine = fine[rng.permutation(len(fine))]                  # arbitrary order
    for E in (coarse, fine):
        pls = _edge_polylines(E)
        assert len(pls) == 2                                 # cut at the corner
        lengths = sorted(float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) for p in pls)
        assert lengths == pytest.approx([5.0, 10.0])
    s_c = np.concatenate([_sample_polyline(p, 2.0)[0] for p in _edge_polylines(coarse)])
    s_f = np.concatenate([_sample_polyline(p, 2.0)[0] for p in _edge_polylines(fine)])
    key = lambda x: x[np.lexsort((x[:, 1], x[:, 0]))]
    assert np.allclose(key(np.unique(s_c.round(6), axis=0)), key(np.unique(s_f.round(6), axis=0)))


def test_sample_polyline_spacing():
    pl = np.array([[0., 0, 0], [7.0, 0, 0]])
    pts, _, gap = _sample_polyline(pl, 2.0)
    assert len(pts) == 5 and gap == pytest.approx(1.75)      # ends included, gap <= step
    pts, _, _ = _sample_polyline(np.array([[0., 0, 0], [0.4, 0, 0]]), 2.0)
    assert len(pts) == 1 and pts[0, 0] == pytest.approx(0.2)  # short edge: its middle


def test_lattice_on_faces_independent_of_triangulation():
    coarse = trimesh.creation.box(extents=(10, 6, 2))
    fine = coarse.subdivide().subdivide().subdivide()
    out = []
    for m in (coarse, fine):
        down = np.nonzero(m.face_normals[:, 2] < -0.9)[0]
        pts, fid = _lattice_on_faces(np.asarray(m.vertices), np.asarray(m.faces), down, 0.5)
        assert np.allclose(pts[:, 2], -1.0)
        out.append(np.unique(pts.round(6), axis=0))
    assert np.array_equal(out[0], out[1])


# ---------------------------------------------------------------------------
# tessellation independence of the whole placement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("edge", [1.0, 0.4, 0.15])
def test_refined_shelf_same_contacts_as_coarse(edge):
    p = P()
    _, ref = place(small_shelf(), p)
    _, cs = place(small_shelf(edge), p)
    assert len(cs) == len(ref)
    a, b = sorted_positions(ref), sorted_positions(cs)
    assert np.abs(a - b).max() < 0.05
    assert [c.reason for c in sorted(cs, key=lambda c: c.id)] == \
        [c.reason for c in sorted(ref, key=lambda c: c.id)]


def test_tiny_triangles_positions_match_coarse():
    p = P()
    coarse = S.shelf(wall_h=12, shelf_z=8, shelf_len=5.0, shelf_t=1.5, width=10, wall_t=3)
    _, c1 = place(coarse, p)
    _, c2 = place(S.tiny_triangles(), p)
    assert len(c1) == len(c2)
    assert np.abs(sorted_positions(c1) - sorted_positions(c2)).max() < 0.05


@pytest.mark.parametrize("seg", [96, 256])
def test_sphere_real_overhang_consistent_across_tessellation(seg):
    """Sphere sunk 1 mm: the junction is at asin(0.9) = 64 deg > 55 deg, so the
    band above the plate overhangs and must get contacts, independently of seg."""
    p = P()
    _, cs = place(S.sphere_on_plate(sink=1.0, seg=seg), p)
    _, ref = place(S.sphere_on_plate(sink=1.0, seg=96), p)
    assert 8 <= len(cs) and abs(len(cs) - len(ref)) <= max(2, 0.2 * len(ref))
    x = sorted_positions(cs)
    # every contact lies on the overhanging band (z between the plate top and the
    # height where the sphere reaches 55 deg), with a downward normal and a reason
    z55 = 11.0 - 10 * math.sin(math.radians(55))
    assert (x[:, 2] >= 2.0 - 1e-6).all() and (x[:, 2] <= z55 + 0.2).all()
    for c in cs:
        assert c.normal[2] < -math.sin(math.radians(50))
        assert c.note and c.reason in (ContactReason.SURFACE, ContactReason.EDGE)


def test_sphere_sunk_2mm_is_below_overhang_angle():
    """A sphere sunk 2 mm (the former fixture default) needs no support: the sphere (r = 10)
    leaves the plate at 8 mm below its centre, i.e. at asin(0.8) = 53.1 deg from
    vertical, under the 55 deg threshold. Only facetting overshoot of the 96
    segment mesh (thin ring < one layer tall, < 0.2 mm2 in 20 separate faces)
    crosses 55 deg; the slicing step at the junction is 0.152 mm < 0.171 mm."""
    p = P()
    m = S.sphere_on_plate(sink=2.0)
    r, cz, plate = m.metadata["radius"], m.metadata["center_z"], m.metadata["plate"]
    tilt = math.degrees(math.asin((cz - plate) / r))
    assert tilt < p.overhang_angle_deg
    step = (math.sqrt(r * r - (cz - 2.22) ** 2) - math.sqrt(r * r - (cz - 2.10) ** 2))
    assert step < p.layer_height * math.tan(math.radians(p.overhang_angle_deg))
    a = analysis.analyze(m, p, Progress())
    assert a.regions == []
    over = (-m.face_normals[:, 2] > math.sin(math.radians(p.overhang_angle_deg))) \
        & (m.triangles_center[:, 2] > plate)
    assert m.area_faces[over].sum() < 0.25
    assert np.ptp(m.triangles_center[over, 2]) < p.layer_height


# ---------------------------------------------------------------------------
# QA-2: bounded memory with a tiny contact spacing
# ---------------------------------------------------------------------------
def test_cast_chunking_gives_same_hits(monkeypatch):
    m = S.figurine()
    caster = contacts.VerticalCaster(m)
    rng = np.random.default_rng(3)
    lo, hi = m.bounds
    pts = np.column_stack([rng.uniform(lo[0], hi[0], 3000), rng.uniform(lo[1], hi[1], 3000),
                           rng.uniform(lo[2], hi[2], 3000)])
    z0, f0 = caster.cast(pts)
    monkeypatch.setattr(contacts, "CAST_POINT_BLOCK", 97)
    monkeypatch.setattr(contacts, "CAST_MAX_PAIRS", 50)
    z1, f1 = caster.cast(pts)
    assert np.array_equal(f0, f1) and np.array_equal(np.isnan(z0), np.isnan(z1))
    assert np.array_equal(z0[~np.isnan(z0)], z1[~np.isnan(z1)])


def test_lattice_blocks_and_cap():
    m = trimesh.creation.box(extents=(10, 6, 2)).subdivide().subdivide()
    v, f = np.asarray(m.vertices), np.asarray(m.faces)
    down = np.nonzero(m.face_normals[:, 2] < -0.9)[0]
    p0, f0 = _lattice_on_faces(v, f, down, 0.25)
    p1, f1 = _lattice_on_faces(v, f, down, 0.25, block=5)
    assert np.array_equal(p0, p1) and np.array_equal(f0, f1)
    p2, _, g = _lattice_on_faces(v, f, down, 0.25, max_points=200, return_pitch=True)
    assert g > 0.25 and 0 < len(p2) <= 200


@pytest.mark.parametrize("spacing", [3.0, 0.4])
def test_fill_blocks_do_not_change_contacts(monkeypatch, spacing):
    p = P().updated(contact_spacing=spacing)
    m = S.figurine()
    _, ref = place(m, p)
    monkeypatch.setattr(contacts, "FILL_BLOCK", 7)
    monkeypatch.setattr(contacts, "CAST_POINT_BLOCK", 101)
    monkeypatch.setattr(contacts, "LATTICE_BLOCK", 1000)
    _, cs = place(m, p)
    assert [(c.id, c.reason, c.note) for c in cs] == [(c.id, c.reason, c.note) for c in ref]
    assert np.array_equal(np.array([c.position for c in cs]), np.array([c.position for c in ref]))


def test_min_spacing_contacts_peak_memory():
    """contact_spacing = 0.2 (minimum accepted by validate) on the 62k face figurine:
    measured ~470 MB peak RSS after the fix (> 4 GB, OOM, before)."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    code = textwrap.dedent("""
        import resource
        from tests.models import synthetic as S
        from arbor.engine import analysis, contacts
        from arbor.engine.params import load_profiles
        from arbor.engine.types import Progress
        p = load_profiles()['nozzle_04_normal'].params.updated(contact_spacing=0.2)
        m = S.figurine()
        a = analysis.analyze(m, p, Progress())
        cs = contacts.place_contacts(m, a, p, [], Progress())
        assert cs and all(c.note for c in cs)
        print('RSS_MB', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                         timeout=300, env={**__import__("os").environ, "PYTHONPATH": str(root)})
    assert out.returncode == 0, out.stderr[-500:]
    rss = float(out.stdout.split("RSS_MB")[1])
    assert rss < 1500, f"peak RSS {rss:.0f} MB"

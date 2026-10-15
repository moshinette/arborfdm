"""Tests of arbor.engine.analysis (meshes built inline)."""
import math
import threading
import time

import numpy as np
import pytest
import trimesh

from arbor.engine.analysis import analyze
from arbor.engine.params import SupportParams
from arbor.engine.types import Cancelled, Progress

P = SupportParams()


def box(ext, center):
    b = trimesh.creation.box(extents=ext)
    b.apply_translation(center)
    return b


def union(*meshes):
    return trimesh.boolean.union(list(meshes), engine="manifold")


def slope_block(angle_from_vertical_deg, height=10.0, width=10.0):
    """Wedge resting on the plate whose underside is tilted: its downward face makes
    `angle_from_vertical_deg` with the vertical (90 = flat ceiling)."""
    run = height * math.tan(math.radians(angle_from_vertical_deg))
    # profile in XZ: base (0,0)-(1,0), top at height, underside from (1,0) to (1+run, height)
    pts = np.array([[0, 0], [1, 0], [1 + run, height], [0, height]], float)
    from shapely.geometry import Polygon
    m = trimesh.creation.extrude_polygon(Polygon(pts), width)   # extruded along +Z
    # map (x, y, z) -> (x, z, y): profile y becomes Z, extrusion becomes Y
    m.apply_transform(np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float))
    m.fix_normals()
    return m


def test_cube_on_plate_needs_nothing():
    r = analyze(box([10, 10, 10], [0, 0, 5]), P, Progress())
    assert r.stats["n_islands"] == 0
    assert r.regions == []
    assert not r.overhang_face_mask.any()
    assert len(r.low_points) == 0
    assert r.stats["n_layers"] == round(10 / P.layer_height)
    assert np.allclose(r.layer_z[:2], [0.06, 0.18])


def test_floating_cube_is_island():
    r = analyze(box([10, 10, 10], [0, 0, 10]), P, Progress())
    isl = [x for x in r.regions if x.kind == "island"]
    assert len(isl) == 1
    assert abs(isl[0].area - 100) < 1
    assert abs(isl[0].z - 5.04) < 0.13
    assert r.overhang_face_mask.sum() == 2           # the two bottom triangles
    assert len(r.hanging_edges) == 4
    assert len(r.corners) == 4


def test_t_shape_shelf():
    T = union(box([4, 4, 10], [0, 0, 5]), box([20, 4, 2], [0, 0, 11]))
    r = analyze(T, P, Progress())
    assert r.stats["n_islands"] == 0
    oh = [x for x in r.regions if x.kind == "overhang"]
    # the flat ceiling appears in ONE layer, as two arms of 8 x 4 mm
    assert len({round(x.z, 3) for x in oh}) == 1
    assert abs(sum(x.area for x in oh) - 64) < 3
    assert len(r.corners) == 4
    assert np.allclose(sorted(np.abs(r.corners[:, 0])), [10, 10, 10, 10])
    # hanging edges are on the ceiling outline (z = 10), never on the walls
    for p, q in r.hanging_edges:
        assert abs(p[2] - 10) < 1e-6 and abs(q[2] - 10) < 1e-6
    # only the ceiling faces are flagged
    fz = T.vertices[T.faces][:, :, 2]
    assert np.allclose(fz[r.overhang_face_mask], 10)


def test_floating_sphere_island_and_low_point():
    s = trimesh.creation.icosphere(subdivisions=4, radius=5)
    s.apply_translation([0, 0, 10])
    r = analyze(s, P, Progress())
    assert r.stats["n_islands"] == 1
    assert len(r.low_points) == 1
    assert np.allclose(r.low_points[0], [0, 0, 5])


def test_sphere_on_plate_low_point_excluded():
    s = trimesh.creation.icosphere(subdivisions=3, radius=5)
    s.apply_translation([0, 0, 5])
    r = analyze(s, P, Progress())
    assert r.stats["n_islands"] == 0
    assert len(r.low_points) == 0                   # the bottom vertex rests on the plate
    assert r.overhang_face_mask.any()                # lower hemisphere overhangs


@pytest.mark.parametrize("angle,expect", [(30.0, False), (45.0, False), (60.0, True)])
def test_slope_vs_threshold_55(angle, expect):
    m = slope_block(angle)
    r = analyze(m, P, Progress())
    assert bool(r.overhang_face_mask.any()) == expect
    assert (r.stats["overhang_area"] > 0) == expect


def test_slope_45_with_threshold_30():
    m = slope_block(45.0)
    r = analyze(m, P.updated(overhang_angle_deg=30.0), Progress())
    assert r.overhang_face_mask.any()
    assert r.stats["overhang_area"] > 0
    # many thin strips, each small, not one big ceiling
    oh = [x for x in r.regions if x.kind == "overhang"]
    assert len(oh) > 10


def test_bridge():
    b = union(box([2, 4, 10], [-6, 0, 5]), box([2, 4, 10], [6, 0, 5]), box([14, 4, 2], [0, 0, 11]))
    r = analyze(b, P, Progress())
    oh = [x for x in r.regions if x.kind == "overhang"]
    assert r.stats["n_islands"] == 0
    assert abs(sum(x.area for x in oh) - 10 * 4) < 2
    assert len(r.hanging_edges) >= 2


def test_max_unsupported_overhang_param():
    # 1 mm step at z=5: with max_unsupported_overhang=2 it is not an overhang
    m = union(box([10, 10, 5], [0, 0, 2.5]), box([12, 12, 5], [0, 0, 7.5]))
    r1 = analyze(m, P, Progress())
    r2 = analyze(m, P.updated(max_unsupported_overhang=2.0), Progress())
    assert r1.stats["overhang_area"] > 30
    assert r2.stats["overhang_area"] == 0


def test_cavity_ceiling_detected():
    # closed internal cavity: its roof is an overhang inside the part
    outer = box([20, 20, 10], [0, 0, 5])
    inner = box([10, 10, 4], [0, 0, 5])
    m = trimesh.boolean.difference([outer, inner], engine="manifold")
    r = analyze(m, P, Progress())
    oh = [x for x in r.regions if x.kind == "overhang"]
    # hole shrunk by the tolerated step d on each side: (10 - 2d)^2 ~ 93 mm2
    assert any(88 < x.area <= 100 for x in oh)


def test_non_watertight_does_not_crash():
    m = box([10, 10, 10], [0, 0, 10])
    m.update_faces(np.arange(len(m.faces) - 1))     # punch a hole in the top
    r = analyze(m, P, Progress())
    assert r.stats["slicer"] == "fallback"
    assert r.stats["n_islands"] == 1
    assert r.stats["n_boundary_edges"] > 0


def test_soup_and_degenerate_faces_do_not_crash():
    rng = np.random.default_rng(1)
    v = rng.uniform(0, 10, (60, 3))
    f = rng.integers(0, 60, (80, 3))
    f[0] = [1, 1, 2]
    m = trimesh.Trimesh(v, f, process=False)
    r = analyze(m, P, Progress())
    assert "n_layers" in r.stats


def test_empty_mesh():
    r = analyze(trimesh.Trimesh(), P, Progress())
    assert r.stats["n_layers"] == 0


def test_progress_and_timings():
    calls = []
    r = analyze(box([10, 10, 10], [0, 0, 10]), P, Progress(lambda f, m: calls.append(f)))
    assert calls and calls[-1] == pytest.approx(1.0)
    assert all(b >= a - 1e-9 for a, b in zip(calls, calls[1:]))
    for k in ("slicing", "layers", "faces", "total"):
        assert k in r.stats["timings"]


def test_cancellation():
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        analyze(box([10, 10, 10], [0, 0, 10]), P, Progress(cancel_event=ev))


def test_cancellation_midway():
    ev = threading.Event()
    seen = []

    def cb(f, msg):
        seen.append(f)
        if f > 0.2:
            ev.set()
    s = trimesh.creation.icosphere(subdivisions=4, radius=10)
    s.apply_translation([0, 0, 12])
    with pytest.raises(Cancelled):
        analyze(s, P, Progress(cb, ev))
    assert max(seen) < 0.9


@pytest.mark.slow
def test_large_mesh_timing():
    rng = np.random.default_rng(0)
    a = trimesh.creation.icosphere(subdivisions=7, radius=15)     # 327 680 faces
    a.vertices += rng.normal(0, 0.03, a.vertices.shape)
    a.apply_translation([0, 0, 15])
    b = trimesh.creation.icosphere(subdivisions=7, radius=8)
    b.vertices += rng.normal(0, 0.03, b.vertices.shape)
    b.apply_translation([25, 0, 20])
    m = trimesh.util.concatenate([a, b])
    assert len(m.faces) >= 500_000
    t = time.perf_counter()
    r = analyze(m, P, Progress())
    dt = time.perf_counter() - t
    print(f"\n[analysis] {len(m.faces)} faces, {r.stats['n_layers']} layers: {dt:.2f} s "
          f"{ {k: round(v, 2) for k, v in r.stats['timings'].items()} }")
    assert r.stats["n_islands"] == 1                   # the floating small sphere
    assert len(r.low_points) == 1                      # its bottom, noise filtered out
    assert dt < 60

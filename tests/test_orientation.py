import time

import numpy as np
import pytest
import trimesh

from arbor.core.orientation import suggest_orientations
from arbor.core.transform import apply_transform, rotate, rotation_matrix_xyz

KEYS = {"overhang_area", "overhang_moment", "support_volume", "height", "footprint", "stability"}


def test_cube_needs_no_support():
    c = suggest_orientations(trimesh.creation.box((10, 10, 10)))
    assert c and KEYS <= set(c[0].criteria)
    assert c[0].criteria["overhang_area"] == pytest.approx(0, abs=1e-9)
    assert c[0].criteria["footprint"] == pytest.approx(100, rel=1e-6)


def test_tilted_cone_prefers_base_down():
    cone = rotate(trimesh.creation.cone(8, 20), 70, 0, 0)
    c = suggest_orientations(cone, n=6)
    assert len(c) <= 6
    assert [x.cost for x in c] == sorted(x.cost for x in c)
    best = c[0]
    placed = apply_transform(cone, best.matrix)
    assert np.isclose(placed.bounds[0, 2], 0, atol=1e-9)
    assert best.criteria["overhang_area"] < 1e-6
    assert best.criteria["height"] == pytest.approx(20, rel=1e-3)
    assert np.allclose(rotation_matrix_xyz(*best.rotation_deg)[:3, :3], best.matrix[:3, :3], atol=1e-9)


def test_t_shape_upright_beats_upside_down():
    stem = trimesh.creation.box((4, 4, 20))
    stem.apply_translation((0, 0, 10))
    bar = trimesh.creation.box((30, 4, 4))
    bar.apply_translation((0, 0, 22))
    t = trimesh.util.concatenate([stem, bar])
    from arbor.core.repair import repair_mesh
    t, rep = repair_mesh(t, union_shells=True)      # remove the internal stem/bar faces
    assert rep.success
    upside = apply_transform(t, rotation_matrix_xyz(180, 0, 0))     # bar on plate (T upside down)
    c = suggest_orientations(upside, n=10)
    by_label = {x.label: x for x in c}
    # bar on the plate (current) needs no support; stem-down needs support under the bar
    cur = by_label["current"]
    assert cur.criteria["overhang_area"] < 1e-6
    flipped = next(x for x in c if np.allclose(x.down_direction, [0, 0, 1]))
    assert flipped.criteria["overhang_area"] > 100
    assert cur.cost < flipped.cost
    assert flipped.criteria["support_volume"] > 0
    assert "+Z down" in flipped.label or "hull" in flipped.label


def test_subsampling_fast():
    s = trimesh.creation.icosphere(subdivisions=7, radius=20)      # 327,680 faces
    t0 = time.perf_counter()
    c = suggest_orientations(s, n=6)
    dt = time.perf_counter() - t0
    print(f"\n[orientation] {len(s.faces)} faces, {len(c)} candidates in {dt:.2f} s")
    assert c and dt < 30


def test_empty():
    assert suggest_orientations(trimesh.Trimesh()) == []

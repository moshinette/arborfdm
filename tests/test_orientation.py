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


# ---------------------------------------------------------------------------
# overhang surface metric (% of the mesh area beyond the effective overhang angle)
# ---------------------------------------------------------------------------
def _identity():
    return np.eye(4)


def test_surface_metric_cube_on_plate_is_zero():
    from arbor.core.orientation import overhang_surface
    a, tot = overhang_surface(trimesh.creation.box((10, 10, 10)), _identity(), 55.0, 0.2)
    assert a == pytest.approx(0, abs=1e-9) and tot == pytest.approx(600)
    c = suggest_orientations(trimesh.creation.box((10, 10, 10)))
    assert c[0].criteria["overhang_surface_pct"] == pytest.approx(0, abs=1e-9)
    assert c[0].criteria["overhang_surface_area"] == pytest.approx(0, abs=1e-9)


def test_surface_metric_cantilever_plate_analytic():
    from arbor.core.orientation import overhang_surface
    post = trimesh.creation.box((4, 4, 20))
    post.apply_translation([0, 0, 10])
    slab = trimesh.creation.box((20, 4, 2))          # underside 20 x 4, at z = 15..17
    slab.apply_translation([12, 0, 16])
    m = trimesh.util.concatenate([post, slab])
    a, tot = overhang_surface(m, _identity(), 55.0, 0.2)
    # underside of the slab (80 mm2) is horizontal: overhang; the post bottom is on the plate
    assert a == pytest.approx(80.0, rel=1e-6)
    assert tot == pytest.approx(m.area, rel=1e-9)
    assert 100 * a / tot == pytest.approx(100 * 80 / m.area)


def test_surface_metric_same_for_every_candidate_and_matches_engine_mask():
    from arbor.engine import analysis
    from arbor.engine.params import SupportParams
    from arbor.core.transform import apply_transform
    post = trimesh.creation.box((4, 4, 20))
    post.apply_translation([0, 0, 10])
    arm = trimesh.creation.cylinder(1.5, 14)         # tilted arm: faces at all angles
    arm.apply_transform(trimesh.transformations.rotation_matrix(np.radians(60), [0, 1, 0]))
    arm.apply_translation([5, 0, 14])
    m = trimesh.util.concatenate([post, arm])
    prm = SupportParams()
    eff = analysis.effective_overhang_angle_deg(prm)
    cands = suggest_orientations(m, overhang_angle_deg=prm.overhang_angle_deg, n=20,
                                 effective_angle_deg=eff, layer_height=prm.layer_height)
    assert len(cands) >= 3
    for c in cands:
        placed = apply_transform(m, c.matrix)
        stats: dict = {}
        analysis._face_analysis(placed, prm, stats)
        assert c.criteria["overhang_surface_area"] == pytest.approx(
            stats["face_overhang_area"], rel=1e-6, abs=1e-6)
        assert c.criteria["overhang_surface_pct"] == pytest.approx(
            100 * stats["face_overhang_area"] / placed.area, rel=1e-6, abs=1e-6)
    # the ranking is still the cost ranking
    assert [c.cost for c in cands] == sorted(c.cost for c in cands)


def test_overhang_level_relative_and_absolute():
    from arbor.core.orientation import overhang_level
    ps = [10.0, 20.0, 30.0, 40.0 - 1]
    assert overhang_level(10.0, ps) == "good" and overhang_level(20.0, ps) == "medium"
    assert overhang_level(39.0, ps) == "bad"
    assert overhang_level(3.0, [3.0, 60.0]) == "good" and overhang_level(45.0, [30, 45]) == "bad"
    # flat set (spread < 5 points): absolute thresholds
    assert overhang_level(12.0, [12.0, 14.0]) == "medium"
    assert overhang_level(30.0, [28.0, 30.0]) == "bad"

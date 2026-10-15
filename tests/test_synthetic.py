"""Validate the synthetic model generators themselves (no engine needed)."""
from __future__ import annotations

import numpy as np
import pytest

from tests.models import synthetic as S

SOLIDS = sorted(S.GENERATORS)
FLOATING = {"floating_cube", "floating_sphere"}


@pytest.mark.parametrize("name", SOLIDS)
def test_solid_is_watertight_single_body(name):
    m = S.GENERATORS[name]()
    assert m.is_watertight, name
    assert m.is_winding_consistent, name
    assert m.volume > 0, f"{name}: inward oriented (negative volume)"
    assert len(m.split(only_watertight=True)) == 1, f"{name}: not one connected body"
    assert len(m.faces) >= 12


@pytest.mark.parametrize("name", SOLIDS)
def test_solid_rests_on_plate_unless_floating(name):
    m = S.GENERATORS[name]()
    zmin = m.bounds[0, 2]
    if name in FLOATING:
        assert zmin > 1.0
    else:
        assert abs(zmin) < 1e-6, f"{name}: min z = {zmin}"


@pytest.mark.parametrize("name", SOLIDS)
def test_deterministic(name):
    a, b = S.GENERATORS[name](), S.GENERATORS[name]()
    assert len(a.faces) == len(b.faces)
    assert np.array_equal(a.vertices, b.vertices)


def test_cube_bounds_and_volume():
    m = S.cube(20)
    assert np.allclose(m.bounds, [[-10, -10, 0], [10, 10, 20]])
    assert m.volume == pytest.approx(8000)


def test_floating_cube_bounds():
    m = S.floating_cube(10, z=10)
    assert np.allclose(m.bounds, [[-5, -5, 10], [5, 5, 20]])
    assert m.metadata["island_z"] == 10


def test_floating_sphere_low_point():
    m = S.floating_sphere(5, 12)
    assert m.bounds[0, 2] == pytest.approx(7.0, abs=0.01)


@pytest.mark.parametrize("angle", [30, 45, 60, 75])
def test_inclined_underside_angle(angle):
    m = S.inclined_surface(angle)
    n = np.asarray(m.metadata["underside_normal"])
    # find the face(s) whose normal matches the advertised underside normal
    hits = np.where(m.face_normals @ n > 0.9999)[0]
    assert len(hits) > 0
    nz = m.face_normals[hits[0], 2]
    # angle from vertical of the surface = angle of the normal below horizontal
    measured = np.degrees(np.arcsin(-nz))
    assert measured == pytest.approx(angle, abs=0.01)


def test_inclined_rejects_bad_angle():
    with pytest.raises(ValueError):
        S.inclined_surface(90)


def test_shelf_geometry():
    m = S.shelf()
    z = m.metadata["shelf_bottom_z"]
    down = m.face_normals[:, 2] < -0.99
    zs = m.triangles_center[down, 2]
    assert set(np.round(zs, 6)) == {0.0, z}
    assert m.volume == pytest.approx(4 * 20 * 30 + 15.5 * 20 * 3 - 0.5 * 20 * 3, rel=1e-6)


def test_bridge_deck():
    m = S.bridge()
    down = m.face_normals[:, 2] < -0.99
    assert np.round(m.triangles_center[down, 2], 6).max() == m.metadata["deck_bottom_z"]


def test_suspended_arm_low_point():
    m = S.suspended_arm()
    lp = np.asarray(m.metadata["hand_low_point"])
    d = np.linalg.norm(m.vertices - lp, axis=1).min()
    assert d < 0.3


def test_figurine_face_count_and_features():
    m = S.figurine()
    assert 20_000 <= len(m.faces) <= 80_000
    assert m.bounds[1, 2] == pytest.approx(60, abs=0.1)
    for name, (lo, hi) in m.metadata["features"].items():
        lo, hi = np.asarray(lo), np.asarray(hi)
        inside = np.all((m.vertices >= lo - 0.5) & (m.vertices <= hi + 0.5), axis=1)
        assert inside.sum() > 0, f"figurine feature {name} has no geometry"


def test_figurine_has_overhangs():
    m = S.figurine()
    steep = (m.face_normals[:, 2] < -np.cos(np.radians(90 - 55))) & (m.triangles_center[:, 2] > 3.5)
    assert m.area_faces[steep].sum() > 50.0


def test_cavity_pocket_and_mouth():
    m = S.cavity()
    assert m.volume < 20 ** 3
    # ray up from below the plate at the centre enters the pocket, hits the ceiling at z=8
    loc, _, _ = m.ray.intersects_location([[0, 0, -1]], [[0, 0, 1]])
    assert np.isclose(np.sort(loc[:, 2])[0], m.metadata["pocket_ceiling_z"])


def test_tiny_triangles_are_tiny():
    m = S.tiny_triangles()
    assert len(m.faces) > 20_000
    assert np.median(m.edges_unique_length) < 0.3


def test_impossible_case_cap_enclosed():
    m = S.impossible_case()
    md = m.metadata
    assert md["cap_bottom_z"] < md["rim_z"]
    # straight down from under the cap (outside the stem) we hit the bowl floor, not the plate
    p = [[6.0, 0.0, md["cap_bottom_z"] - 0.01]]
    loc, _, _ = m.ray.intersects_location(p, [[0, 0, -1]])
    assert len(loc) > 0 and np.isclose(loc[:, 2].max(), md["bowl_floor_z"])


def test_close_contacts_spikes():
    m = S.close_contacts_case(gap=1.0)
    for t in m.metadata["spike_tips"]:
        assert np.linalg.norm(m.vertices - np.asarray(t), axis=1).min() < 1e-3


def test_crossing_case_arms():
    m = S.crossing_case()
    md = m.metadata
    assert md["arm_a_bottom_z"] > md["arm_b_bottom_z"]


# ---------------------------------------------------------------------------
# broken meshes
# ---------------------------------------------------------------------------
def test_broken_hole():
    m = S.broken_hole()
    assert not m.is_watertight
    assert len(m.faces) == 192 - m.metadata["removed"]
    assert m.metadata["removed"] >= 4


def test_broken_hole_is_simple_loop():
    import collections
    m = S.broken_hole()
    e = np.sort(m.edges, axis=1)
    b = [k for k, v in collections.Counter(map(tuple, e)).items() if v == 1]
    deg = collections.Counter(x for k in b for x in k)
    assert len(b) > 0 and set(deg.values()) == {2}


def test_broken_pinched_hole():
    m = S.broken_pinched_hole()
    assert not m.is_watertight


def test_broken_flipped():
    m = S.broken_flipped()
    assert not m.is_winding_consistent


def test_broken_degenerate():
    m = S.broken_degenerate()
    assert (m.area_faces < 1e-12).sum() == m.metadata["degenerate"]


def test_broken_nonmanifold():
    m = S.broken_nonmanifold()
    import collections
    cnt = collections.Counter(map(tuple, np.sort(m.edges, axis=1)))
    assert max(cnt.values()) >= 4


# ---------------------------------------------------------------------------
# large mesh
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target", [10_000, 100_000])
def test_large_mesh_face_count(target):
    m = S.large_mesh(target)
    assert 0.75 * target <= len(m.faces) <= 1.35 * target
    assert m.is_watertight


@pytest.mark.slow
def test_large_mesh_1m():
    m = S.large_mesh(1_000_000)
    assert 800_000 <= len(m.faces) <= 1_250_000
    assert m.is_watertight

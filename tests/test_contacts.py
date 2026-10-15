"""Tests of arbor.engine.contacts (meshes built inline)."""
import math
import threading
import time
from collections import Counter

import numpy as np
import pytest
import trimesh
from scipy.spatial import cKDTree

from arbor.engine.analysis import analyze
from arbor.engine.contacts import place_contacts
from arbor.engine.params import SupportParams
from arbor.engine.types import Cancelled, Contact, ContactReason, Progress

P = SupportParams()


def box(ext, center):
    b = trimesh.creation.box(extents=ext)
    b.apply_translation(center)
    return b


def union(*meshes):
    return trimesh.boolean.union(list(meshes), engine="manifold")


def run(mesh, params=P, manual=None):
    a = analyze(mesh, params, Progress())
    return place_contacts(mesh, a, params, manual or [], Progress()), a


def slope_block(angle_from_vertical_deg, height=10.0, width=10.0):
    from shapely.geometry import Polygon
    run_ = height * math.tan(math.radians(angle_from_vertical_deg))
    pts = np.array([[0, 0], [1, 0], [1 + run_, height], [0, height]], float)
    m = trimesh.creation.extrude_polygon(Polygon(pts), width)
    m.apply_transform(np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float))
    m.fix_normals()
    return m


def on_surface(mesh, contacts, tol=1e-3):
    pts = np.array([c.position for c in contacts])
    _, d, _ = trimesh.proximity.closest_point(mesh, pts)
    return np.all(d < tol)


def check_common(mesh, contacts, params=P):
    ids = [c.id for c in contacts]
    assert len(set(ids)) == len(ids)
    for c in contacts:
        assert c.note
        assert abs(np.linalg.norm(c.normal) - 1) < 1e-6
    auto = [c for c in contacts if not c.manual]
    if auto:
        assert on_surface(mesh, auto)
        pts = np.array([c.position for c in contacts])
        d, _ = cKDTree(pts).query(pts, k=2)
        assert d[:, 1].min() >= params.min_contact_distance - 1e-9


def test_cube_on_plate_no_contacts():
    cs, _ = run(box([10, 10, 10], [0, 0, 5]))
    assert cs == []


def test_t_shelf_contacts():
    T = union(box([4, 4, 10], [0, 0, 5]), box([20, 4, 2], [0, 0, 11]))
    cs, _ = run(T)
    check_common(T, cs)
    reasons = Counter(c.reason for c in cs)
    assert reasons[ContactReason.CORNER] == 4
    assert reasons[ContactReason.EDGE] >= 8
    pos = np.array([c.position for c in cs])
    # every contact is under the shelf ceiling (z = 10), none on the vertical walls
    assert np.allclose(pos[:, 2], 10, atol=1e-6)
    assert np.all(np.abs(pos[:, 0]) >= 2 - 1e-6)            # not above the pillar
    for c in cs:
        assert c.normal[2] < -0.99
    # corners are inset by the tip radius, within the face
    corners = pos[[c.reason == ContactReason.CORNER for c in cs]]
    assert np.all(np.abs(corners[:, 0]) <= 10) and np.all(np.abs(corners[:, 0]) > 9.5)
    # both arms covered
    assert (pos[:, 0] > 0).sum() >= 6 and (pos[:, 0] < 0).sum() >= 6


def test_floating_cube_island_contacts_cover_area():
    m = box([10, 10, 10], [0, 0, 10])
    cs, _ = run(m)
    check_common(m, cs)
    reasons = Counter(c.reason for c in cs)
    assert reasons[ContactReason.ISLAND] >= 1
    pos = np.array([c.position for c in cs])
    assert np.allclose(pos[:, 2], 5)
    # every point of the bottom is within contact_spacing of a contact
    X, Y = np.meshgrid(np.linspace(-5, 5, 41), np.linspace(-5, 5, 41))
    d, _ = cKDTree(pos[:, :2]).query(np.column_stack([X.ravel(), Y.ravel()]))
    assert d.max() <= P.contact_spacing


def test_big_island_gets_more_contacts():
    small = box([3, 3, 3], [0, 0, 10])
    big = box([30, 30, 3], [0, 0, 10])
    n_small = len(run(small)[0])
    n_big = len(run(big)[0])
    assert n_small >= 1
    assert n_big > 5 * n_small


def test_floating_sphere_island_and_low_point():
    s = trimesh.creation.icosphere(subdivisions=4, radius=5)
    s.apply_translation([0, 0, 10])
    cs, a = run(s)
    check_common(s, cs)
    isl = [c for c in cs if c.reason == ContactReason.ISLAND]
    assert len(isl) >= 1
    # the island contact sits on the lowest point, flagged as such
    lowest = min(isl, key=lambda c: c.position[2])
    assert np.allclose(lowest.position, [0, 0, 5])
    assert "low_point" in lowest.note
    assert lowest.normal[2] < -0.99
    # the rest of the lower hemisphere is covered by surface contacts
    assert any(c.reason == ContactReason.SURFACE for c in cs)


def test_low_point_reason_on_grounded_part():
    # pillar with a hanging "arm" tip (downward cone) attached to a shelf
    pillar = box([4, 4, 12], [0, 0, 6])
    shelf = box([16, 4, 2], [6, 0, 13])
    cone = trimesh.creation.cone(radius=1.5, height=3, sections=24)
    cone.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    cone.apply_translation([11, 0, 12.0 + 0.01])
    m = union(pillar, shelf, cone)
    cs, a = run(m)
    assert len(a.low_points) >= 1
    tip = [c for c in cs if np.allclose(c.position, [11, 0, 9.01], atol=1e-3)]
    assert tip and tip[0].reason in (ContactReason.LOW_POINT, ContactReason.ISLAND)


def test_slope_45():
    m = slope_block(45.0)
    cs55, _ = run(m, P)
    assert cs55 == []
    p30 = P.updated(overhang_angle_deg=30.0)
    cs30, a = run(m, p30)
    check_common(m, cs30, p30)
    assert len(cs30) > 0
    # on the sloped face only (normal = (sin45, 0, -cos45) pointing out & down)
    for c in cs30:
        assert c.normal[2] < -0.6
    # density bounded: slope area ~ 141 mm2, spacing 3 -> at most a few dozen contacts
    assert len(cs30) < 60


def test_bridge_contacts():
    b = union(box([2, 4, 10], [-6, 0, 5]), box([2, 4, 10], [6, 0, 5]), box([14, 4, 2], [0, 0, 11]))
    cs, _ = run(b)
    check_common(b, cs)
    pos = np.array([c.position for c in cs])
    assert len(cs) >= 4
    assert np.allclose(pos[:, 2], 10)
    assert np.all(np.abs(pos[:, 0]) <= 5 + 1e-6)              # between the pillars


def test_manual_contact_preserved_and_suppresses_auto():
    m = box([10, 10, 10], [0, 0, 10])
    man = Contact(id=42, position=np.array([0.0, 0.0, 5.0]), normal=np.array([0, 0, -1.0]),
                  reason=ContactReason.MANUAL, manual=True, tip_diameter=0.6, note="user")
    cs, _ = run(m, manual=[man])
    check_common(m, cs)
    kept = [c for c in cs if c.id == 42]
    assert len(kept) == 1 and kept[0] is man
    assert kept[0].tip_diameter == 0.6 and kept[0].note == "user"
    auto = [c for c in cs if not c.manual]
    assert auto and min(c.id for c in auto) > 42
    d = [np.linalg.norm(c.position - man.position) for c in auto]
    assert min(d) >= P.min_contact_distance


def test_manual_only():
    m = box([10, 10, 10], [0, 0, 10])
    man = Contact(id=3, position=np.array([1.0, 1.0, 5.0]), normal=np.array([0, 0, -1.0]),
                  reason=ContactReason.MANUAL, manual=True)
    cs, _ = run(m, P.updated(manual_only=True), manual=[man])
    assert cs == [man]


def test_density_increases_contacts():
    m = box([20, 20, 3], [0, 0, 10])
    n1 = len(run(m, P)[0])
    n2 = len(run(m, P.updated(contact_density=2.0))[0])
    assert n2 > 1.5 * n1


def test_contact_above_model_kept():
    # a shelf above a lower shelf: the upper contacts' downward ray hits the model
    m = union(box([4, 4, 20], [0, 0, 10]), box([12, 4, 2], [4, 0, 9]), box([12, 4, 2], [4, 0, 19]))
    cs, _ = run(m)
    zs = np.array([c.position[2] for c in cs])
    assert (np.abs(zs - 18) < 1e-6).sum() >= 3        # upper shelf contacts kept
    assert (np.abs(zs - 8) < 1e-6).sum() >= 3


def test_ids_deterministic():
    m = box([10, 10, 10], [0, 0, 10])
    a = [(c.id, tuple(np.round(c.position, 6))) for c in run(m)[0]]
    b = [(c.id, tuple(np.round(c.position, 6))) for c in run(m)[0]]
    assert a == b


def test_cancellation():
    m = box([10, 10, 10], [0, 0, 10])
    a = analyze(m, P, Progress())
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        place_contacts(m, a, P, [], Progress(cancel_event=ev))


@pytest.mark.slow
def test_large_mesh_contacts_timing():
    rng = np.random.default_rng(0)
    a = trimesh.creation.icosphere(subdivisions=7, radius=15)
    a.vertices += rng.normal(0, 0.03, a.vertices.shape)
    a.apply_translation([0, 0, 15])
    b = trimesh.creation.icosphere(subdivisions=7, radius=8)
    b.vertices += rng.normal(0, 0.03, b.vertices.shape)
    b.apply_translation([25, 0, 20])
    m = trimesh.util.concatenate([a, b])
    assert len(m.faces) >= 500_000
    t = time.perf_counter()
    an = analyze(m, P, Progress())
    ta = time.perf_counter() - t
    t = time.perf_counter()
    cs = place_contacts(m, an, P, [], Progress())
    tc = time.perf_counter() - t
    print(f"\n[contacts] {len(m.faces)} faces: analysis {ta:.2f} s, contacts {tc:.2f} s, "
          f"{len(cs)} contacts {dict(Counter(c.reason.value for c in cs))}")
    assert any(c.reason == ContactReason.ISLAND for c in cs)
    assert tc < 60

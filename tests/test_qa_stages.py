"""QA: analysis + contacts stages on synthetic models (independent of tree/build/verify)."""
from __future__ import annotations

import copy
from functools import lru_cache

import numpy as np
import pytest
import trimesh

from tests.models import synthetic as S

analysis = pytest.importorskip("arbor.engine.analysis")
contacts = pytest.importorskip("arbor.engine.contacts")
from arbor.engine.params import load_profiles  # noqa: E402
from arbor.engine.types import Cancelled, Contact, ContactReason, Progress  # noqa: E402


@lru_cache(maxsize=None)
def P(name="nozzle_04_normal"):
    return load_profiles()[name].params


def place(mesh, params, manual=()):
    a = analysis.analyze(mesh, params, Progress())
    return a, contacts.place_contacts(mesh, a, params, list(manual), Progress())


def pts(cs):
    return np.array([c.position for c in cs if c.enabled]).reshape(-1, 3)


def test_cube_no_contacts():
    a, cs = place(S.cube(), P())
    assert cs == [] and a.regions == []


@pytest.mark.parametrize("name", ["shelf", "bridge", "figurine", "suspended_arm", "floating_sphere",
                                  "impossible_case", "cavity", "crossing_case", "close_contacts_case",
                                  "tiny_triangles", "sphere_on_plate"])
def test_contacts_on_surface_unique_ids_and_spacing(name):
    m = S.GENERATORS[name]()
    p = P()
    _, cs = place(m, p)
    assert cs
    ids = [c.id for c in cs]
    assert len(set(ids)) == len(ids), "duplicate contact ids"
    x = pts(cs)
    _, d, _ = trimesh.proximity.closest_point(m, x)
    assert d.max() < 0.05, f"contact {d.max():.3f} mm off the surface"
    # normals: unit and pointing downward-ish (a support comes from below)
    n = np.array([c.normal for c in cs])
    assert np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-3)
    assert (n[:, 2] < 0.2).all(), f"contact with upward facing normal: {n[:, 2].max():.2f}"
    dd = np.linalg.norm(x[:, None] - x[None], axis=2) + np.eye(len(x)) * 1e9
    assert dd.min() >= p.min_contact_distance * 0.99, f"two contacts {dd.min():.3f} mm apart"
    assert all(c.note for c in cs), "contact without explanation"


def test_shelf_contacts_under_shelf_and_coverage():
    m = S.shelf()
    p = P()
    _, cs = place(m, p)
    x = pts(cs)
    assert np.allclose(x[:, 2], m.metadata["shelf_bottom_z"], atol=p.layer_height)
    lo, hi = np.asarray(m.metadata["shelf_box"])
    g = np.array([[a, b] for a in np.linspace(lo[0] + .3, hi[0] - .3, 20)
                  for b in np.linspace(lo[1] + .3, hi[1] - .3, 20)])
    gap = np.linalg.norm(g[:, None] - x[None, :, :2], axis=2).min(1).max()
    assert gap <= p.contact_spacing, f"largest uncovered distance {gap:.2f} mm > spacing {p.contact_spacing}"


def test_floating_cube_island():
    m = S.floating_cube()
    a, cs = place(m, P())
    assert any(r.kind == "island" for r in a.regions)
    assert any(c.reason == ContactReason.ISLAND for c in cs)


def test_floating_sphere_low_point_and_no_side_contacts():
    m = S.floating_sphere()
    p = P()
    _, cs = place(m, p)
    x = pts(cs)
    lp = np.asarray(m.metadata["low_point"])
    assert np.linalg.norm(x - lp, axis=1).min() < 0.5
    # with overhang angle 55 deg from vertical, faces need support only where the normal is
    # more than 35 deg below horizontal: on a sphere of radius r centred at zc that is
    # z < zc - r*sin(35 deg)
    zc, r = 12.0, 5.0
    zlim = zc - r * np.sin(np.radians(90 - p.overhang_angle_deg)) + p.layer_height
    assert x[:, 2].max() <= zlim, f"contact at z={x[:, 2].max():.2f} on a self supporting part (limit {zlim:.2f})"


def test_figurine_features():
    m = S.figurine()
    _, cs = place(m, P())
    x = pts(cs)
    f = m.metadata["features"]
    for k in ("right_arm", "left_arm", "chin", "sword", "cape_bottom"):
        lo, hi = np.asarray(f[k][0]) - 1.5, np.asarray(f[k][1]) + 1.5
        assert np.all((x >= lo) & (x <= hi), axis=1).any(), f"no contact on {k}"


@pytest.mark.parametrize("name", ["bridge", "shelf", "figurine", "floating_cube"])
@pytest.mark.parametrize("noz", ["04", "02"])
def test_density_monotonic(name, noz):
    m = S.GENERATORS[name]()
    n = [len(place(m, P(f"nozzle_{noz}_{d}"))[1]) for d in ("light", "normal", "dense")]
    assert n[0] < n[1] < n[2], n


def test_02_denser_than_04():
    m = S.bridge()
    assert len(place(m, P("nozzle_02_normal"))[1]) > len(place(m, P("nozzle_04_normal"))[1])


def test_scale_2x():
    m1 = S.shelf()
    m2 = S.shelf()
    m2.apply_scale(2)
    x1, x2 = pts(place(m1, P())[1]), pts(place(m2, P())[1])
    assert np.allclose(x2[:, 2], 40.0, atol=0.15)
    assert 2.5 * len(x1) <= len(x2) <= 6 * len(x1), (len(x1), len(x2))


@pytest.mark.parametrize("angle,needs", [(30, False), (50, False), (65, True), (80, True)])
def test_inclined_threshold(angle, needs):
    _, cs = place(S.inclined_surface(angle), P().updated(overhang_angle_deg=55))
    assert bool(cs) == needs, f"{angle} deg: {len(cs)} contacts"


def test_manual_contact_kept_and_not_duplicated():
    m = S.bridge()
    man = Contact(9999, np.array([12.0, 2.5, 15.0]), np.array([0, 0, -1.0]), ContactReason.MANUAL, manual=True)
    _, cs = place(m, P(), [man])
    kept = [c for c in cs if c.manual]
    assert len(kept) == 1 and kept[0].id == 9999
    assert np.allclose(kept[0].position, man.position)
    x = pts([c for c in cs if not c.manual])
    assert np.linalg.norm(x - man.position, axis=1).min() >= P().min_contact_distance * 0.99


def test_manual_only():
    man = Contact(3, np.array([12.0, 2.5, 15.0]), np.array([0, 0, -1.0]), ContactReason.MANUAL, manual=True)
    _, cs = place(S.bridge(), P().updated(manual_only=True), [man])
    assert [c.id for c in cs] == [3]


def test_manual_input_not_mutated():
    man = Contact(5, np.array([12.0, 2.5, 15.0]), np.array([0, 0, -1.0]), ContactReason.MANUAL, manual=True)
    before = copy.deepcopy(man)
    place(S.bridge(), P(), [man])
    assert man.to_dict() == before.to_dict()


def test_deterministic():
    m = S.figurine()
    a = pts(place(m, P())[1])
    b = pts(place(m, P())[1])
    assert np.array_equal(a, b)


def test_analysis_cancel():
    pr = Progress()
    pr.cancel()
    with pytest.raises(Cancelled):
        analysis.analyze(S.figurine(), P(), pr)


def test_analysis_progress_monotonic():
    seen = []
    analysis.analyze(S.figurine(), P(), Progress(lambda f, m: seen.append(f)))
    assert seen and all(b >= a for a, b in zip(seen, seen[1:])) and seen[-1] == pytest.approx(1.0)


def test_tiny_triangles_same_as_coarse():
    """Tessellation must not change where contacts go (same geometry, 50k vs 36 faces)."""
    coarse = S.shelf(wall_h=12, shelf_z=8, shelf_len=5.0, shelf_t=1.5, width=10, wall_t=3)
    fine = S.tiny_triangles()
    nc, nf = len(place(coarse, P())[1]), len(place(fine, P())[1])
    assert abs(nc - nf) <= max(2, 0.3 * nc), f"coarse {nc} vs fine {nf} contacts"


def test_empty_mesh():
    a, cs = place(trimesh.Trimesh(), P())
    assert cs == []


def test_model_below_plate_handled():
    m = S.cube()
    m.apply_translation((0, 0, -1))
    try:
        a, cs = place(m, P())
    except ValueError:
        return  # explicit refusal is acceptable
    # silently analysing a model below the plate is acceptable only if it is reported
    assert a.stats.get("z_min", 0) < 0

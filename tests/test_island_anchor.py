"""Island anchoring (contacts step 1b): islands that start in a point get a ring of thick
contacts around their lowest point; flat bottoms are left unchanged.

Real defect (docs/PHYSICAL_TESTS.md, essai 2): the hands of examples/figurine.stl (spheres
r 2.6 mm) were held by a single 0.26 mm tip during their first layers and printed as a
nest of strings."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import trimesh
from shapely.geometry import Point

from arbor.engine import pipeline
from arbor.engine.analysis import analyze
from arbor.engine.contacts import island_anchor_footprint, place_contacts
from arbor.engine.params import SupportParams, load_profiles
from arbor.engine.types import ContactReason, NodeKind, Progress

ROOT = Path(__file__).resolve().parent.parent
PROFILES = load_profiles()
P02 = PROFILES["nozzle_02_normal"].params
P04 = PROFILES["nozzle_04_normal"].params


def _place(mesh, params):
    a = analyze(mesh, params, Progress())
    return place_contacts(mesh, a, params, [], Progress()), a


def _hand(radius=2.6, z_bottom=10.0, subdivisions=4):
    s = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    s.apply_translation([0.0, 0.0, z_bottom + radius])
    return s


def _anchors(cs):
    return [c for c in cs if "island anchor:" in c.note]


def _main(cs):
    return min((c for c in cs if c.reason == ContactReason.ISLAND), key=lambda c: c.position[2])


@pytest.fixture(scope="module")
def figurine():
    return trimesh.load(ROOT / "examples" / "figurine.stl")


# ---------------------------------------------------------------------------
# parameters and profiles
# ---------------------------------------------------------------------------
def test_profiles_define_anchor_parameters():
    for key, prof in PROFILES.items():
        p = prof.params
        assert p.validate() == [], key
        lw = p.line_width
        assert p.island_anchor_height == pytest.approx(1.0), key
        # ~2.5 line widths: the slicer still extrudes the top layer of the tip (essai 2)
        assert 2.2 * lw <= p.island_tip_diameter <= 3.0 * lw, key
        assert p.island_tip_diameter > p.tip_diameter, key
        assert p.island_anchor_spacing >= p.min_contact_distance, key
    assert P02.island_tip_diameter == pytest.approx(0.6)
    assert P04.island_tip_diameter == pytest.approx(1.0)


def test_validate_anchor_parameters():
    assert any("island_anchor_height" in m for m in SupportParams(island_anchor_height=-1).validate())
    assert any("island_anchor_spacing" in m for m in SupportParams(island_anchor_spacing=0).validate())
    assert any("island_tip_diameter" in m for m in SupportParams(island_tip_diameter=0.1).validate())
    assert SupportParams(island_anchor_height=0).validate() == []


# ---------------------------------------------------------------------------
# floating sphere = a hand
# ---------------------------------------------------------------------------
def test_floating_sphere_gets_anchor_ring():
    s = _hand()
    cs, a = _place(s, P02)
    anchors = _anchors(cs)
    main = _main(cs)
    assert np.allclose(main.position, [0, 0, 10.0], atol=1e-6)
    assert len(anchors) == 6
    assert a.stats["contacts"]["anchored_islands"] == 1
    assert a.stats["contacts"]["anchor_contacts"] == 6
    for c in anchors + [main]:
        assert c.reason == ContactReason.ISLAND
        assert c.tip_diameter == pytest.approx(P02.island_tip_diameter)
    assert "island anchored" in main.note
    # ring around the lowest point: radius = island_anchor_spacing, every 60 deg sector
    xy = np.array([c.position[:2] for c in anchors])
    rho = np.linalg.norm(xy, axis=1)
    assert np.allclose(rho, P02.island_anchor_spacing, atol=1e-6)
    sectors = {int(math.floor((math.degrees(math.atan2(y, x)) + 30.0) % 360 / 60.0)) for x, y in xy}
    assert sectors == set(range(6))
    # on the bottom surface, within the anchor height above the first layer
    for c in anchors:
        assert 10.0 < c.position[2] <= 10.0 + P02.island_anchor_height
        d = np.linalg.norm(c.position - [0, 0, 12.6])
        assert d == pytest.approx(2.6, abs=0.02)
        assert c.normal[2] < -0.8
    # the anchors replace the regular contacts that were "covered" by the lowest one
    plain, _ = _place(s, P02.updated(island_anchor_height=0))
    assert not _anchors(plain)
    assert all(c.tip_diameter is None for c in plain)
    first_plain = min(c.position[2] for c in plain if c.reason != ContactReason.ISLAND)
    assert max(c.position[2] for c in anchors) < first_plain


def test_anchor_height_zero_disables():
    cs, _ = _place(_hand(), P02.updated(island_anchor_height=0))
    assert not _anchors(cs)
    assert _main(cs).tip_diameter is None


def test_anchor_ring_is_bounded_on_a_large_ball():
    # a big round bottom keeps the regular contacts above the ring: at most 7 thick tips
    cs, _ = _place(_hand(radius=5.0, z_bottom=5.0), SupportParams())
    thick = [c for c in cs if c.tip_diameter is not None]
    assert 1 < len(thick) <= 7
    assert any(c.reason == ContactReason.SURFACE for c in cs)


def test_small_island_ring_shrinks_to_fit():
    # 0.4 mm profile (ring 1.2 mm) on a smaller ball: at 1.2 mm the ring leaves the
    # overhanging cap (R x sin 35 deg = 1.15 mm), it is retried at 0.75 x
    cs, _ = _place(_hand(radius=2.0), P04)
    anchors = _anchors(cs)
    assert len(anchors) >= 3
    rho = np.linalg.norm([c.position[:2] for c in anchors], axis=1)
    assert np.allclose(rho, 0.75 * P04.island_anchor_spacing, atol=1e-6)


def test_tree_uses_thick_tip_and_wider_base():
    s = _hand()
    res = pipeline.generate(s, P02)
    assert not res.errors
    tips = {n.contact_id: n for n in res.tree.nodes.values() if n.kind == NodeKind.TIP}
    taper = 0.5 * (P02.tip_base_diameter - P02.tip_diameter)
    anchored = [c for c in res.contacts if c.tip_diameter is not None]
    assert len(anchored) == 7
    for c in anchored:
        t = tips[c.id]
        assert t.radius == pytest.approx(P02.island_tip_diameter / 2)
        base = res.tree.nodes[t.parent]
        assert base.radius == pytest.approx(P02.island_tip_diameter / 2 + taper)
    # the support mesh really carries the thick tip right under the lowest point
    sec = res.support_mesh.section(plane_origin=[0, 0, 10.0 - 0.1], plane_normal=[0, 0, 1])
    assert sec is not None
    pl, _ = sec.to_2D(to_2D=np.eye(4))
    widths = []
    for poly in pl.polygons_full:
        if poly.centroid.distance(Point(0.0, 0.0)) < 0.3:
            r = poly.minimum_rotated_rectangle
            xs = np.array(r.exterior.coords)
            widths.append(min(np.linalg.norm(xs[1] - xs[0]), np.linalg.norm(xs[2] - xs[1])))
    assert widths and max(widths) >= 0.9 * P02.island_tip_diameter
    # marks: 7 thick tips, counted in contact_area_mm2, not reported as dense marks
    assert res.stats["contact_area_mm2"] >= 7 * math.pi * (P02.island_tip_diameter / 2) ** 2 - 1e-6
    assert "hard_to_remove" not in res.stats["issues_by_code"]


# ---------------------------------------------------------------------------
# flat bottoms are unchanged
# ---------------------------------------------------------------------------
def test_flat_floating_box_unchanged():
    box = trimesh.creation.box([8, 6, 3])
    box.apply_translation([0, 0, 8])
    a_cs, a = _place(box, P02)
    b_cs, _ = _place(box, P02.updated(island_anchor_height=0))
    assert a.stats["contacts"]["anchored_islands"] == 0
    assert not _anchors(a_cs)
    assert all(c.tip_diameter is None for c in a_cs)
    assert [tuple(np.round(c.position, 9)) for c in a_cs] == [tuple(np.round(c.position, 9)) for c in b_cs]
    assert [c.note for c in a_cs] == [c.note for c in b_cs]


def test_footprint_of_flat_and_round_islands():
    box = trimesh.creation.box([8, 6, 3])
    box.apply_translation([0, 0, 8])
    a = analyze(box, P02, Progress())
    isl = [r for r in a.regions if r.kind == "island"]
    first, foot, z_top = island_anchor_footprint(a, isl[0], 1.0)
    assert first.area == pytest.approx(foot.area, rel=1e-3)     # flat: not anchored
    a = analyze(_hand(), P02, Progress())
    isl = [r for r in a.regions if r.kind == "island"]
    first, foot, z_top = island_anchor_footprint(a, isl[0], 1.0)
    assert first.area < 0.2 * foot.area
    assert z_top <= isl[0].z + 1.0 + 1e-9
    assert island_anchor_footprint(a, isl[0], 0.0) is None


# ---------------------------------------------------------------------------
# figurine (the real case)
# ---------------------------------------------------------------------------
HANDS = (np.array([-21.0, 0.0]), np.array([21.0, 0.0]))


def test_figurine_hands_anchored_rest_unchanged(figurine):
    p = P02.updated(raft_enabled=True)
    cs, a = _place(figurine, p)
    plain, _ = _place(figurine, p.updated(island_anchor_height=0))
    for hc in HANDS:
        near = [c for c in cs if np.linalg.norm(c.position[:2] - hc) < 1.5 and c.position[2] < 40.5]
        anchors = [c for c in near if "island anchor:" in c.note]
        assert len(anchors) == 6
        assert all(c.tip_diameter == pytest.approx(0.6) for c in near if c.reason == ContactReason.ISLAND)
        main = min(near, key=lambda c: c.position[2])
        assert main.position[2] == pytest.approx(39.4, abs=1e-3)
        assert main.tip_diameter == pytest.approx(0.6)
        # the first ring sits within 2-3 layers of the bottom of the hand
        assert all(c.position[2] - 39.4 <= 3 * p.layer_height for c in anchors)
        # footprint stops before the hand merges with the arm
        r = min((r for r in a.regions if r.kind == "island" and r.polygon.distance(Point(*hc)) < 0.5),
                key=lambda r: r.z)
        _, foot, z_top = island_anchor_footprint(a, r, p.island_anchor_height)
        assert z_top < 40.04
        assert foot.area < math.pi * 2.6 ** 2
    # away from the hands (torso, cape, base, most of the arms): same contacts, same tips;
    # within 5 mm the greedy coverage of the hand/arm junction shifts a few arm contacts
    def far(lst):
        return sorted(tuple(np.round(c.position, 6)) for c in lst
                      if min(np.linalg.norm(c.position[:2] - hc) for hc in HANDS) > 5.0)
    assert far(cs) == far(plain)
    assert all(c.tip_diameter is None for c in cs
               if min(np.linalg.norm(c.position[:2] - hc) for hc in HANDS) > 5.0)
    # the cape (flat island at z=18) is not anchored
    assert a.stats["contacts"]["anchored_islands"] == 2
    assert len(cs) - len(plain) <= 12

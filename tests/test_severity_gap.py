"""Overhang severity (moderate overhangs get sparser contacts) and the support
interface gap (edge / corner / surface tips stop one layer below the model).

Origin: 5th print (version 0.3.0, docs/PHYSICAL_TESTS.md, essai 5): every tip was fused
to the model, the marks were deep and one tip stayed welded to a hand."""
from __future__ import annotations

import math

import numpy as np
import pytest
import trimesh

from arbor.app import overhang as oh
from arbor.engine import analysis, contacts, pipeline
from arbor.engine.analysis import (SEVERE_STEP_LINE_WIDTHS, effective_overhang_angle_deg,
                                   max_layer_step, severe_layer_step, severe_overhang_angle_deg)
from arbor.engine.params import SupportParams, load_profiles
from arbor.engine.tree import INTERFACE_GAP_REASONS
from arbor.engine.types import ContactReason, NodeKind, Progress
from tests.models import synthetic as S

P02 = load_profiles()["nozzle_02_normal"].params
P04 = load_profiles()["nozzle_04_normal"].params


def _place(mesh, p):
    a = analysis.analyze(mesh, p, Progress())
    return contacts.place_contacts(mesh, a, p, [], Progress()), a


def _surface(cs):
    return [c for c in cs if c.reason == ContactReason.SURFACE]


# ---------------------------------------------------------------------------
# severity
# ---------------------------------------------------------------------------
def test_severe_angle_from_line_width_and_layer():
    for p in (P02, P04):
        d, _ = max_layer_step(p)
        assert severe_layer_step(p) == pytest.approx(max(d, SEVERE_STEP_LINE_WIDTHS * p.line_width))
        expect = math.degrees(math.atan2(SEVERE_STEP_LINE_WIDTHS * p.line_width, p.layer_height))
        assert severe_overhang_angle_deg(p) == pytest.approx(expect)
    assert severe_overhang_angle_deg(P02) == pytest.approx(64.1, abs=0.1)
    assert severe_overhang_angle_deg(P04) == pytest.approx(69.2, abs=0.1)
    # no moderate band when the distinction is off
    off = P02.updated(mild_overhang_spacing=1.0)
    assert severe_layer_step(off) == pytest.approx(max_layer_step(off)[0])
    assert severe_overhang_angle_deg(off) == pytest.approx(effective_overhang_angle_deg(off))


@pytest.mark.parametrize("angle,expected", [(60.0, "mild"), (78.0, "severe")])
def test_region_severity(angle, expected):
    m = S.inclined_surface(angle)
    a = analysis.analyze(m, P02, Progress())
    over = [r for r in a.regions if r.kind == "overhang"]
    assert over
    assert {r.severity for r in over} == {expected}
    # face mask agrees with the regions
    under = a.overhang_face_mask
    assert under.any()
    if expected == "mild":
        assert not a.severe_face_mask[under].any()
    else:
        assert a.severe_face_mask[under].all()


def test_moderate_slope_gets_sparser_contacts_and_stays_verified():
    m = S.inclined_surface(60.0)
    dense, _ = _place(m, P02.updated(mild_overhang_spacing=1.0))
    sparse, _ = _place(m, P02)
    assert len(_surface(sparse)) < 0.6 * len(_surface(dense))
    assert all("moderate overhang" in c.note for c in _surface(sparse))
    # still verified: no unsupported region, no error
    r = pipeline.generate(m, P02)
    assert not r.errors, [i.message for i in r.errors]
    assert "unsupported_region" not in {i.code for i in r.issues}


def test_severe_slope_unchanged():
    m = S.inclined_surface(78.0)
    a_cs, _ = _place(m, P02.updated(mild_overhang_spacing=1.0))
    b_cs, _ = _place(m, P02)
    pa = np.array(sorted(map(tuple, (c.position for c in a_cs))))
    pb = np.array(sorted(map(tuple, (c.position for c in b_cs))))
    assert pa.shape == pb.shape and np.allclose(pa, pb)
    assert not any("moderate overhang" in c.note for c in b_cs)


def test_islands_and_anchors_never_thinned():
    hand = S.floating_sphere(radius=2.6, z_center=12.6)
    a_cs, _ = _place(hand, P02.updated(mild_overhang_spacing=1.0))
    b_cs, _ = _place(hand, P02)
    isl = lambda cs: sorted(tuple(np.round(c.position, 6)) for c in cs if c.reason == ContactReason.ISLAND)
    assert isl(a_cs) == isl(b_cs) and len(isl(b_cs)) == 7


# ---------------------------------------------------------------------------
# interface gap
# ---------------------------------------------------------------------------
def _vertical_clearance(model, support, tip, contact_z, r):
    """Smallest vertical distance between the support top and the model above, sampled
    on the tip disc (as a slicer sees it)."""
    rs = trimesh.ray.ray_triangle.RayMeshIntersector(support)
    rm = trimesh.ray.ray_triangle.RayMeshIntersector(model)
    out = []
    ang = np.linspace(0, 2 * math.pi, 8, endpoint=False)
    for dx, dy in [(0.0, 0.0)] + [(0.9 * r * math.cos(t), 0.9 * r * math.sin(t)) for t in ang]:
        o = np.array([[tip[0] + dx, tip[1] + dy, contact_z - 3.0]])
        hs, _, _ = rs.intersects_location(o, [[0, 0, 1.0]])
        hm, _, _ = rm.intersects_location(o, [[0, 0, 1.0]])
        zm = hm[:, 2][hm[:, 2] > contact_z - 1.0] if len(hm) else []
        if not len(hs) or not len(zm):
            continue
        zs = hs[:, 2][hs[:, 2] < min(zm) + 0.3]
        if len(zs):
            out.append(min(zm) - zs.max())
    return min(out) if out else None


@pytest.mark.parametrize("model", ["shelf", "slope"])
def test_gapped_tips_leave_one_air_layer(model):
    m = S.shelf() if model == "shelf" else S.inclined_surface(70.0)
    r = pipeline.generate(m, P02)
    assert not r.errors, [i.message for i in r.errors]
    by_id = {c.id: c for c in r.contacts}
    gap = P02.interface_gap_layers * P02.layer_height
    n_gap = 0
    for n in r.tree.nodes.values():
        if n.kind != NodeKind.TIP:
            continue
        c = by_id[n.contact_id]
        cl = _vertical_clearance(m, r.support_mesh, n.position, c.position[2], n.radius)
        if cl is None:
            continue
        if c.id in r.tree.interface_gap_ids:
            assert c.reason in INTERFACE_GAP_REASONS
            n_gap += 1
            assert 0.9 * gap <= cl <= gap + n.radius + 0.05, (c.id, c.reason, cl)
        else:
            assert cl < 0.0, (c.id, c.reason, cl)          # welded: overlaps the model
    assert n_gap > 0
    assert r.stats["interface_gap_contacts"] > 0
    assert r.stats["welded_contact_area_mm2"] < r.stats["contact_area_mm2"]


def test_gap_off_restores_welded_tips():
    m = S.shelf()
    p = P02.updated(interface_gap_layers=0)
    r = pipeline.generate(m, p)
    pts = np.array([c.position for c in r.contacts])
    _, d, _ = trimesh.proximity.closest_point(r.support_mesh, pts)
    assert d.max() <= p.tip_penetration + 0.02
    assert r.stats["interface_gap_contacts"] == 0
    assert r.stats["welded_contact_area_mm2"] == pytest.approx(r.stats["contact_area_mm2"])


def test_island_tips_stay_welded():
    hand = S.floating_sphere(radius=2.6, z_center=12.6)
    r = pipeline.generate(hand, P02)
    isl = {c.id for c in r.contacts if c.reason == ContactReason.ISLAND}
    tips = [n for n in r.tree.nodes.values() if n.kind == NodeKind.TIP and n.contact_id in isl]
    assert len(tips) == 7
    _, d, _ = trimesh.proximity.closest_point(hand, np.array([n.position for n in tips]))
    assert d.max() <= P02.tip_penetration + 0.02


def test_low_contact_without_room_for_the_gap_is_still_supported():
    """A slope starting at the plate: the lowest contacts have less than one layer + the
    gap below them; they fall back to a welded short support instead of failing."""
    m = S.inclined_surface(78.0)
    r = pipeline.generate(m, P02.updated(raft_enabled=False))
    assert "unrouted_contact" not in {i.code for i in r.issues if i.severity == "error"}


def test_params_validation():
    assert SupportParams(interface_gap_layers=4).validate()
    assert SupportParams(interface_gap_layers=1.5).validate()
    assert not SupportParams(interface_gap_layers=2).validate()
    assert SupportParams(mild_overhang_spacing=0.5).validate()
    assert not SupportParams(mild_overhang_spacing=3.0).validate()


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------
def test_overhang_colours_follow_the_engine_severity():
    sev = severe_overhang_angle_deg(P02)
    assert oh.band_edges(55, sev) == (45.0, 55.0, pytest.approx(sev))
    labels = [b.label for b in oh.bands(55, sev)]
    assert "fewer contacts" in labels[2] and "full support" in labels[3]
    # default (no severe angle) unchanged
    assert oh.band_edges(55) == (45.0, 55.0, 72.5)
    cls = oh.classify(np.array([50.0, 60.0, 66.0]), 55, severe=sev)
    assert cls.tolist() == [1, 2, 3]

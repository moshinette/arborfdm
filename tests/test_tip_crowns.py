"""Tip crowns (docs/PHYSICAL_TESTS.md, essai 3; docs/ALGORITHM.md, section 2).

Real print, 0.2 mm nozzle: strings between the top of the arm columns and the sword /
hand. Measured in OrcaSlicer's G-code: 20 to 30 separate micro islands per layer over the
last 1.5 mm under the sword (one per tip), tip bases overlapping (0.82 mm apart for a
0.7 mm base), and two parallel rows of edge tips under a blade 0.4 to 3 mm wide.

Non regression:
  - active automatic contacts keep the effective spacing tip_base_diameter + line_width
    (island anchors excepted: deliberate cluster of thick tips);
  - a narrow part (blade) gets one central row of edge contacts, not two;
  - the coverage of the overhanging faces is kept (distance to the nearest contact).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh
from scipy.spatial import cKDTree

from arbor.core.mesh_io import load_mesh
from arbor.engine import verify as V
from arbor.engine.analysis import analyze
from arbor.engine.contacts import (COVER_FACTOR, _lattice_on_faces, effective_min_contact_distance,
                                   narrow_width_limit, place_contacts)
from arbor.engine.params import load_profiles
from arbor.engine.types import Contact, ContactReason, Progress

EX = Path(__file__).resolve().parent.parent / "examples"
PROFILES = load_profiles()


def _params(key):
    return PROFILES[key].params


def _run(mesh, params):
    a = analyze(mesh, params, Progress())
    return place_contacts(mesh, a, params, [], Progress()), a


def _blade(width: float, length: float = 16.0, thick: float = 0.6, z: float = 10.0):
    """A pillar holding a thin horizontal blade (sword without hilt) at height z."""
    a = trimesh.creation.box(extents=[4, 4, z + thick])
    a.apply_translation([0, 0, 0.5 * (z + thick)])
    b = trimesh.creation.box(extents=[length, width, thick])
    b.apply_translation([2 + 0.5 * length, 0, z + 0.5 * thick])
    return trimesh.boolean.union([a, b], engine="manifold")


def _coverage(mesh, analysis, contacts, select=None, mask=None):
    """Distances from a 0.1 mm lattice on the overhanging faces (or on mask) to the
    nearest contact."""
    ids = np.nonzero(analysis.overhang_face_mask if mask is None else mask)[0]
    S, _ = _lattice_on_faces(np.asarray(mesh.vertices), np.asarray(mesh.faces), ids, 0.1)
    S = S[S[:, 2] > analysis.layer_height]
    if select is not None:
        S = S[select(S)]
    d, _ = cKDTree(np.array([c.position for c in contacts])).query(S)
    return d


def _is_anchor_cluster(c: Contact) -> bool:
    return c.reason == ContactReason.ISLAND and (c.tip_diameter is not None or "island anchor" in c.note)


@pytest.fixture(scope="module")
def figurine():
    return load_mesh(EX / "figurine.stl").mesh


@pytest.fixture(scope="module")
def fig02(figurine):
    p = _params("nozzle_02_normal").updated(raft_enabled=True)
    contacts, a = _run(figurine, p)
    return p, contacts, a


# ---------------------------------------------------------------------------
def test_effective_spacing_values():
    for key, prof in PROFILES.items():
        p = prof.params
        d = effective_min_contact_distance(p)
        assert d >= p.min_contact_distance
        assert d >= p.tip_base_diameter + p.line_width - 1e-12
    assert effective_min_contact_distance(_params("nozzle_02_normal")) == pytest.approx(0.92)
    assert effective_min_contact_distance(_params("nozzle_04_normal")) == pytest.approx(1.42)


def test_figurine_active_tips_keep_effective_spacing(fig02):
    p, contacts, _ = fig02
    dmin = effective_min_contact_distance(p)
    act = [c for c in contacts if c.enabled and not _is_anchor_cluster(c)]
    P = np.array([c.position for c in act])
    d, _ = cKDTree(P).query(P, k=2)
    assert d[:, 1].min() >= dmin - 1e-9, f"two tips {d[:, 1].min():.3f} mm apart (min {dmin:.2f})"
    # the island anchor rings are still there (6 contacts per hand)
    anchors = [c for c in contacts if c.note.startswith("island anchor:")]
    assert len(anchors) == 12


def test_figurine_04_keeps_island_anchors(figurine):
    """0.4 profiles: anchor ring radius 1.2 mm < effective spacing 1.42 mm, the anchors
    keep min_contact_distance (deliberate cluster) and are not thinned out."""
    p = _params("nozzle_04_normal")
    contacts, _ = _run(figurine, p)
    assert len([c for c in contacts if c.note.startswith("island anchor:")]) == 12


def test_sword_side_less_crowded(fig02):
    """Sword side of the example figurine (x > 17): 40 contacts before the fix (6 pairs
    closer than 0.92 mm, 2.7 neighbours within 1.6 mm sharing layers per tip)."""
    _, contacts, _ = fig02
    P = np.array([c.position for c in contacts])
    R = P[P[:, 0] > 17]
    assert len(R) <= 34
    near = [sum(1 for j in cKDTree(R[:, :2]).query_ball_point(q[:2], 1.6) if abs(R[j, 2] - q[2]) < 1.2) - 1
            for q in R]
    assert np.mean(near) < 2.3


def test_figurine_coverage_kept(figurine, fig02):
    """Same coverage of the overhanging faces as before the fix (max 2.29 mm overall,
    1.42 mm on each arm side with the 0.2 Normal profile)."""
    p, contacts, a = fig02
    # severe overhangs keep that coverage; moderate ones (mild_overhang_spacing) may have
    # contacts up to mild_overhang_spacing times farther (measured: 1.57 mm on an arm side)
    sev = a.severe_face_mask
    mild = a.overhang_face_mask & ~sev
    assert _coverage(figurine, a, contacts, mask=sev).max() <= 2.30
    for sel in (lambda S: S[:, 0] > 17, lambda S: S[:, 0] < -17):
        assert _coverage(figurine, a, contacts, sel, mask=sev).max() <= 1.45
        if mild.any():
            assert _coverage(figurine, a, contacts, sel, mask=mild).max() <= 1.45 * p.mild_overhang_spacing
    assert _coverage(figurine, a, contacts).max() <= 2.30 * p.mild_overhang_spacing


@pytest.mark.parametrize("width", [0.6, 1.2, 1.8])
def test_narrow_blade_single_central_row(width):
    p = _params("nozzle_02_normal")
    assert width < narrow_width_limit(p)
    m = _blade(width)
    contacts, a = _run(m, p)
    blade = [c for c in contacts if c.position[0] > 2.3]
    edges = [c for c in blade if c.reason == ContactReason.EDGE]
    assert edges
    # one row on the centre line, not one row per side
    assert max(abs(c.position[1]) for c in edges) < 0.05
    # about length / edge spacing contacts, not twice as many
    assert len(blade) <= 16.0 / p.edge_spacing + 4
    assert all("centre line of a part" in c.note for c in edges)
    # coverage: every point of the blade underside within the surface coverage radius
    d = _coverage(m, a, contacts, lambda S: S[:, 0] > 2.3)
    assert d.max() <= COVER_FACTOR * p.contact_spacing / p.contact_density + 0.05
    # all contacts lie on the model
    _, dist, _ = trimesh.proximity.closest_point(m, np.array([c.position for c in blade]))
    assert dist.max() < 1e-3


def test_wide_blade_keeps_two_edge_rows():
    p = _params("nozzle_02_normal")
    w = 3.0
    assert w > narrow_width_limit(p)
    contacts, _ = _run(_blade(w), p)
    ys = [c.position[1] for c in contacts if c.reason == ContactReason.EDGE and c.position[0] > 2.3]
    assert min(ys) < -1.0 and max(ys) > 1.0


def test_narrow_limit_preserves_coverage_radius():
    for key, prof in PROFILES.items():
        p = prof.params
        r = COVER_FACTOR * p.contact_spacing / p.contact_density
        g = p.edge_spacing / p.contact_density
        w = narrow_width_limit(p)
        assert np.hypot(0.5 * w, 0.5 * g) <= r + 1e-9, key


def test_manual_contacts_too_close_uses_effective_spacing(fig02):
    p, contacts, _ = fig02
    base = np.array([0.0, 0.0, 30.0])
    man = [Contact(id=1000, position=base, normal=np.array([0, 0, -1.0]), reason=ContactReason.MANUAL,
                   manual=True),
           Contact(id=1001, position=base + [0.8, 0, 0], normal=np.array([0, 0, -1.0]),
                   reason=ContactReason.MANUAL, manual=True)]
    issues = []
    V._check_spacing(man, p, issues)
    assert [i.code for i in issues] == ["contacts_too_close"]   # 0.8 < 0.92, > 0.6

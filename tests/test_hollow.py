"""Hollow supports (hollow_supports): sealed voids in wide trunk sections only."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh
from scipy.spatial import cKDTree

from arbor.engine import pipeline
from arbor.engine.build import hollow_cores, hollow_min_radius
from arbor.engine.params import SupportParams, load_profiles
from arbor.engine.types import NodeKind
from tests.models import synthetic as S

PROFILES = load_profiles()
P02 = PROFILES["nozzle_02_normal"].params
P04 = PROFILES["nozzle_04_normal"].params


@pytest.fixture(scope="module")
def shelf():
    return S.shelf()


@pytest.fixture(scope="module")
def solid02(shelf):
    return pipeline.generate(shelf, P02)


@pytest.fixture(scope="module")
def hollow02(shelf):
    return pipeline.generate(shelf, P02.updated(hollow_supports=True))


def _shells(m):
    parts = m.split(only_watertight=False)
    return [q for q in parts if q.volume > 0], [q for q in parts if q.volume < 0]


def test_off_by_default_and_unchanged(shelf, solid02):
    assert SupportParams().hollow_supports is False
    assert all(not p.params.hollow_supports for p in PROFILES.values())
    assert P02.hollow_wall == pytest.approx(3 * P02.line_width)
    assert P04.hollow_wall == pytest.approx(3 * P04.line_width)
    again = pipeline.generate(shelf, P02.updated(hollow_supports=False))
    assert len(again.support_mesh.faces) == len(solid02.support_mesh.faces)
    assert again.support_mesh.volume == pytest.approx(solid02.support_mesh.volume, rel=1e-12)
    assert _shells(solid02.support_mesh)[1] == []


def test_hollow_trunks_are_sealed_and_lighter(solid02, hollow02):
    sm = hollow02.support_mesh
    assert sm.is_watertight and sm.is_winding_consistent
    outer, voids = _shells(sm)
    assert voids, "no void built"
    assert sm.volume < 0.95 * solid02.support_mesh.volume
    assert hollow02.stats["support_volume_mm3"] == pytest.approx(sm.volume, rel=1e-6)
    # no new problem compared with the solid supports (the void ceilings are bridges)
    codes = lambda r: sorted(i.code for i in r.issues if i.severity != "info")
    assert codes(hollow02) == codes(solid02)
    # walls: every void vertex at least ~hollow_wall from the outer surface
    shell = trimesh.util.concatenate(outer)
    for v in voids:
        d = trimesh.proximity.closest_point(shell, v.vertices)[1]
        assert d.min() > 0.85 * P02.hollow_wall


def test_tips_and_thin_parts_stay_solid(hollow02):
    t = hollow02.tree
    _, voids = _shells(hollow02.support_mesh)
    vv = np.vstack([v.vertices for v in voids])
    tips = np.array([n.position for n in t.nodes.values() if n.kind == NodeKind.TIP])
    assert cKDTree(tips).query(vv)[0].min() > P02.tip_length
    # each void lies along a section at least hollow_min_radius wide
    nodes = list(t.nodes.values())
    pos = np.array([n.position for n in nodes])
    rad = np.array([n.radius for n in nodes])
    _, idx = cKDTree(pos).query(vv, k=2)
    assert np.all(rad[idx].max(axis=1) >= hollow_min_radius(P02) - 1e-9)
    # nothing hollow in the feet discs / collars
    zb = t.z_base
    assert vv[:, 2].min() > zb + P02.foot_height + P02.collar_height


def test_no_void_when_sections_are_too_thin(shelf):
    # 0.4 mm profile: trunks 2.6 mm, wall 1.26 mm: a hole would be narrower than 2 lines
    r = pipeline.generate(shelf, P04.updated(hollow_supports=True))
    assert hollow_cores(r.tree, P04.updated(hollow_supports=True)) == []
    assert _shells(r.support_mesh)[1] == []


def test_hollow_validation():
    assert SupportParams(hollow_supports=True, hollow_wall=0.5).validate()   # < 2 x 0.42
    assert SupportParams(hollow_supports=False, hollow_wall=0.5).validate() == []

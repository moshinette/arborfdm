"""Regression tests for the tree router fixes (tree.py):

- ModelGrid.top_below never reports a "surface" beside a wall (point inside material);
- tips next to a wall / leg tilt away from it so their branch can reach the plate;
- node radii keep clearance in the slab below the node too (trunks passing over a low
  ledge or the figurine base disk do not dig into it);
- landing on the model never drops a segment through an intervening part.
"""
from __future__ import annotations

from functools import lru_cache

import manifold3d as mf
import numpy as np
import pytest

from arbor.engine import build, params, pipeline
from arbor.engine import tree as T
from arbor.engine.types import Contact, ContactReason, NodeKind, Progress, SupportTree
from tests.models import synthetic as S


@lru_cache(maxsize=None)
def prm():
    return params.load_profiles()["nozzle_04_normal"].params


_RES: dict = {}


def run(name):
    if name not in _RES:
        _RES[name] = pipeline.generate(S.GENERATORS[name]().copy(), prm())
    return _RES[name]


def overlap(support_mesh, solid: mf.Manifold) -> float:
    return (build.trimesh_to_manifold(support_mesh) ^ solid).volume()


def _router(mesh, contact_pos):
    c = Contact(0, np.asarray(contact_pos, float), np.array([0.0, 0.0, -1.0]), ContactReason.MANUAL)
    tree = SupportTree()
    T._init_tree_extras(tree, 0.0)
    r = T._Router(mesh, [c], prm(), tree, Progress())
    r._prepare()
    r.landed, r.fail_nodes = [], {}
    return r


# ---------------------------------------------------------------------------
def test_top_below_ignores_material_beside_point():
    m = S.shelf()                     # wall x 0..4 (z 0..30), shelf x 3.5..19, z 20..23
    g = T.ModelGrid(m, 0.25)
    assert g.top_below((2.0, 10.0), 15.0) is None        # inside the wall: no surface
    assert g.top_below((10.0, 10.0), 15.0) is None       # under the shelf: nothing below
    assert g.top_below((10.0, 10.0), 25.0) == pytest.approx(23.0, abs=1e-6)
    assert g.top_below((2.0, 10.0), 35.0) == pytest.approx(30.0, abs=1e-6)


def test_shelf_tips_near_wall_tilt_away_and_reach_plate():
    res = run("shelf")
    nodes = res.tree.nodes
    near = [n for n in nodes.values() if n.kind == NodeKind.TIP and n.position[0] < 5.0]
    assert near, "expected contacts close to the wall"
    def wall_dist(p):          # XY distance to the wall footprint x 0..4, y 0..20
        dx = max(0.0, p[0] - 4.0, -p[0])
        dy = max(0.0, p[1] - 20.0, -p[1])
        return float(np.hypot(dx, dy))

    for n in near:
        base = nodes[n.parent]
        assert wall_dist(base.position) > wall_dist(n.position) + 0.5, \
            "tip next to the wall not tilted away"
    assert res.tree.stats()["model_feet"] == 0
    assert all(not c.rests_on_model for c in res.contacts if c.enabled)


def test_crossing_case_trunks_do_not_dig_into_base_plate():
    res = run("crossing_case")
    base = mf.Manifold.cube((26.0, 16.0, 2.0)).translate((0.0, -2.0, 0.0))
    # was ~8 mm3 when node radii only respected the slab above the node
    assert overlap(res.support_mesh, base) < 2.0


def test_figurine_supports_do_not_cross_legs():
    res = run("figurine")
    for sx in (-1, 1):
        leg = mf.Manifold.cylinder(23.0, 2.5, 2.5, 64).translate((sx * 4.0, 0.0, 3.0))
        # was ~15 mm3 per leg: blocked nodes dropped a straight segment through the leg
        assert overlap(res.support_mesh, leg) < 0.5


def test_landing_path_rejects_drop_through_model():
    floor = S._box((-15, -15, 0), (15, 15, 3))
    bar = S._box((-5, -15, 8), (5, 15, 10))
    blocked = _router(S._to_trimesh(S._union([floor, bar])), (0.0, 0.0, 16.0))
    free = _router(S._to_trimesh(floor), (0.0, 0.0, 16.0))
    for r, expect in ((blocked, False), (free, True)):
        n = r.tree.add([0.0, 0.0, 14.0], 0.5, NodeKind.BRANCH)
        assert r._land_path_clear(n.id, np.array([0.0, 0.0]), 14.0, 3.0) is expect
    # a tilted landing beyond the bar still crosses it
    n = blocked.tree.add([0.0, 0.0, 14.0], 0.5, NodeKind.BRANCH)
    assert not blocked._land_path_clear(n.id, np.array([8.0, 0.0]), 14.0, 3.0)


def test_blocked_node_fails_instead_of_cutting_through():
    """A node boxed in above a bar with no clear landing spot is reported, not landed."""
    floor = S._box((-15, -15, 0), (15, 15, 3))
    bar = S._box((-12, -12, 8), (12, 12, 10))
    r = _router(S._to_trimesh(S._union([floor, bar])), (0.0, 0.0, 16.0))
    n = r.tree.add([0.0, 0.0, 10.5], 0.5, NodeKind.BRANCH)
    r._blocked(n.id, np.array([0.0, 0.0]), 10.5, 0.5)
    # lands on the bar top (z = 10) right below, never on the floor through the bar
    feet = [m for m in r.tree.nodes.values() if m.kind == NodeKind.MODEL_FOOT]
    assert len(feet) == 1 and feet[0].position[2] > 9.0
    n2 = r.tree.add([0.0, 0.0, 7.5], 0.5, NodeKind.BRANCH)   # under the bar
    r._blocked(n2.id, np.array([0.0, 0.0]), 7.5, 0.5)
    feet = [m for m in r.tree.nodes.values() if m.kind == NodeKind.MODEL_FOOT]
    assert len(feet) == 2 and min(f.position[2] for f in feet) < 3.5

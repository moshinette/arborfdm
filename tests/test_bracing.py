"""Braces that link isolated groups of tall trees (Essai 4, docs/PHYSICAL_TESTS.md).

A tall tree braced to nothing, or a narrow braced group, is linked to its nearest
neighbouring trunk (tree._Router._bridge_isolated) until its braced group is wide enough
for its height (tree.braced_group_stable); otherwise verify reports isolated_tall_tree.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
import trimesh

from arbor.engine import params, pipeline
from arbor.engine import tree as T
from arbor.engine import verify as V
from arbor.engine.types import Contact, ContactReason, NodeKind, Progress, SupportTree

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=None)
def _profiles():
    return params.load_profiles()


def prm(key="nozzle_02_normal", **over):
    import copy
    p = copy.deepcopy(_profiles()[key].params)
    for k, v in over.items():
        setattr(p, k, v)
    return p


def _scene(mesh, trunks, P, fork=True):
    """Router on `mesh` with hand-made trunks [(x, y, height)]: foot, a trunk node every
    mm and (fork) a short side branch at z = 2 so the regular pass skips the trunk, like
    the trees of the figurine arms."""
    tree = SupportTree()
    T._init_tree_extras(tree, 0.0)
    contacts = [Contact(i, np.array([x, y, h + 0.5]), np.array([0.0, 0.0, -1.0]),
                        ContactReason.ISLAND) for i, (x, y, h) in enumerate(trunks)]
    r = T._Router(mesh, contacts, P, tree, Progress())
    r._prepare()
    for i, (x, y, h) in enumerate(trunks):
        f = tree.add([x, y, 0.0], 1.4, NodeKind.FOOT)
        tree.foot_radius[f.id] = P.foot_diameter / 2
        par = f.id
        for z in np.arange(1.0, h + 1e-9, 1.0):
            par = tree.add([x, y, z], 1.3, NodeKind.TRUNK, par).id
            if fork and abs(z - 2.0) < 1e-9:
                b = tree.add([x + 0.6, y, 3.0], 0.5, NodeKind.BRANCH, par)
                tree.add([x + 0.9, y, 4.0], 0.15, NodeKind.TIP, b.id, contact_id=1000 + i)
        tree.add([x, y, h + 0.5], 0.15, NodeKind.TIP, par, contact_id=i)
    return r, tree


def _far_box():
    return trimesh.creation.box(extents=(4, 4, 45)).apply_translation((70.0, 0.0, 22.5))


def _unstable_tall(tree, P):
    return [g for g in T.braced_groups(tree, P) if g["tall"] and not g["stable"]]


def _assert_printable(braces, P, angle_print):
    for p0, p1, rb in braces:
        p0, p1 = np.asarray(p0), np.asarray(p1)
        v = p1 - p0
        assert v[2] > 0
        tilt = math.degrees(math.atan2(float(np.linalg.norm(v[:2])), float(v[2])))
        assert tilt <= angle_print + 1e-6
        assert 2 * rb >= P.brace_diameter - 1e-9
        assert np.linalg.norm(v) / (2 * rb) <= V.BRACE_SLENDER_MAX


def test_isolated_group_is_linked_on_several_levels():
    P = prm()
    trunks = [(0.0, 0.0, 40.0), (14.0, 0.0, 40.0), (7.0, 12.0, 40.0)]
    r, tree = _scene(_far_box(), trunks, P)
    assert len(_unstable_tall(tree, P)) == 3          # three lone 40 mm trees
    r._braces()
    assert tree.braces, "the isolated trees must be linked"
    assert _unstable_tall(tree, P) == []
    _assert_printable(tree.braces, P, T.printable_angle_deg(P))
    # tall trees: several levels, one every brace_spacing_z at least between them
    z0 = sorted(float(b[0][2]) for b in tree.braces)
    assert z0[-1] - z0[0] >= P.brace_spacing_z
    issues = []
    V._check_tree(tree, P, issues)
    assert not [i for i in issues if i.code == "isolated_tall_tree"]


def test_bridge_length_limit():
    """Up to BRIDGE_LENGTH_FACTOR x brace_max_length of horizontal run, no further."""
    P = prm()
    lim = T.BRIDGE_LENGTH_FACTOR * P.brace_max_length
    for gap, linked in ((lim - 1.0, True), (lim + 2.0, False)):
        r, tree = _scene(_far_box(), [(0.0, 0.0, 40.0), (gap, 0.0, 40.0)], P)
        r._braces()
        assert bool(tree.braces) == linked, gap
        for p0, p1, _ in tree.braces:
            assert np.linalg.norm(np.asarray(p1)[:2] - np.asarray(p0)[:2]) <= lim + 1e-9


def test_nothing_isolated_nothing_added(monkeypatch):
    """A wide group already braced by the regular pass: the bridging pass adds nothing."""
    P = prm()
    trunks = [(0.0, 0.0, 30.0), (10.0, 0.0, 30.0), (0.0, 10.0, 30.0), (10.0, 10.0, 30.0)]
    r, tree = _scene(_far_box(), trunks, P, fork=False)
    r._braces()
    with_bridge = [(tuple(a), tuple(b), rb) for a, b, rb in tree.braces]
    assert with_bridge and _unstable_tall(tree, P) == []
    r2, tree2 = _scene(_far_box(), trunks, P, fork=False)
    monkeypatch.setattr(T._Router, "_bridge_isolated", lambda self, ch, links: None)
    r2._braces()
    assert [(tuple(a), tuple(b), rb) for a, b, rb in tree2.braces] == with_bridge


def test_model_in_between_is_reported():
    """Two tall trees on either side of a wall: no brace may cross it, each tree is
    reported (isolated_tall_tree, at its foot)."""
    P = prm()
    wall = trimesh.creation.box(extents=(2.0, 60.0, 45.0)).apply_translation((0.0, 0.0, 22.5))
    trunks = [(-6.0, 0.0, 40.0), (6.0, 0.0, 40.0)]
    r, tree = _scene(wall, trunks, P)
    r._braces()
    assert tree.braces == []
    issues = []
    V._check_tree(tree, P, issues)
    iso = [i for i in issues if i.code == "isolated_tall_tree"]
    assert len(iso) == 2 and all(i.severity == "warning" for i in iso)
    xs = sorted(float(i.position[0]) for i in iso)
    assert xs == pytest.approx([-6.0, 6.0], abs=1e-6)
    assert "40.5 mm" in iso[0].message


@lru_cache(maxsize=None)
def _figurine(key):
    m = trimesh.load(ROOT / "examples" / "figurine.stl", force="mesh")
    return m, pipeline.generate(m.copy(), prm(key))


def _brace_clear_of_model(mesh, braces):
    pq = trimesh.proximity.ProximityQuery(mesh)
    for p0, p1, rb in braces:
        s = np.linspace(0.0, 1.0, 25)[:, None]
        pts = np.asarray(p0) + s * (np.asarray(p1) - np.asarray(p0))
        d = pq.signed_distance(pts)          # > 0 inside the model
        assert (d < -rb).all(), "brace crosses or touches the model"


def test_figurine_02_no_isolated_tall_group():
    """Essai 4 configuration: every tall tree of the arms belongs to a wide braced group."""
    P = prm("nozzle_02_normal")
    m, res = _figurine("nozzle_02_normal")
    assert _unstable_tall(res.tree, P) == []
    codes = {i.code for i in res.issues}
    assert "isolated_tall_tree" not in codes
    assert "support_floating_region" not in codes
    assert not [i for i in res.issues if i.code == "fragile_support" and "Brace" in i.message]
    _assert_printable(res.tree.braces, P, T.printable_angle_deg(P))
    _brace_clear_of_model(m, res.tree.braces)
    # the arm trees (x ~ 21 and x ~ -17) are both braced
    feet = [n for n in res.tree.nodes.values() if n.kind == NodeKind.FOOT]
    for side in (1, -1):
        arm = [f for f in feet if side * f.position[0] > 15]
        assert arm
        for f in arm:
            d = [min(np.linalg.norm(np.asarray(a)[:2] - f.position[:2]),
                     np.linalg.norm(np.asarray(b)[:2] - f.position[:2]))
                 for a, b, _ in res.tree.braces]
            assert min(d) < 6.0, f"arm tree at {f.position[:2]} not braced"


def test_figurine_04_remaining_groups_are_reported():
    P = prm("nozzle_04_normal")
    m, res = _figurine("nozzle_04_normal")
    left = _unstable_tall(res.tree, P)
    iso = [i for i in res.issues if i.code == "isolated_tall_tree"]
    assert len(iso) == len(left) <= 1
    assert "support_floating_region" not in {i.code for i in res.issues}
    _assert_printable(res.tree.braces, P, T.printable_angle_deg(P))
    _brace_clear_of_model(m, res.tree.braces)

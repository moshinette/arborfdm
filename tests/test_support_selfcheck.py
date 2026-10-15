"""The supports sliced alone, like a slicer does (verify: support_floating_region).

Supports are printed without supports of their own: every part of them must stand on the
layer below (support, model or plate) and never lean beyond the overhang angle. These
tests check the detector on hand-made faulty supports, the geometry fixes behind it
(printable branch angle, landing segments, joint skirts, widening limit, braces) and that
the example models produce no floating support region.
"""
from __future__ import annotations

import math
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
import trimesh

from arbor.engine import analysis, build, params, pipeline, verify
from arbor.engine import tree as T
from arbor.engine.types import Contact, ContactReason, NodeKind, Progress, SupportTree
from tests.models import synthetic as S

EX = Path(__file__).resolve().parent.parent / "examples"
CODE = "support_floating_region"


@lru_cache(maxsize=None)
def prm(profile: str = "nozzle_04_normal"):
    return params.load_profiles()[profile].params


def _columns_tree(z_top: float = 15.0, gap: float = 10.0) -> SupportTree:
    """Two vertical trunks on feet, built by hand (no routing)."""
    tree = SupportTree()
    T._init_tree_extras(tree, 0.0)
    for x in (0.0, gap):
        f = tree.add([x, 0.0, 0.0], 1.3, NodeKind.FOOT)
        tree.foot_radius[f.id] = 3.0
        below = f
        for z in np.arange(1.0, z_top + 1e-9, 1.0):
            below = tree.add([x, 0.0, z], 1.3, NodeKind.TRUNK, parent=below.id)
    return tree


def _far_model() -> trimesh.Trimesh:
    m = trimesh.creation.box([2.0, 2.0, 2.0])
    m.apply_translation([60.0, 60.0, 1.0])
    return m


def _issues(tree: SupportTree, P) -> list:
    sm = build.mesh_tree(tree, P)
    model = _far_model()
    an = analysis.analyze(model, P, Progress())
    return [i for i in verify.verify(model, an, [], tree, sm, P) if i.code == CODE]


# ---------------------------------------------------------------------------
# the detector on hand-made faulty supports
# ---------------------------------------------------------------------------
def test_clean_columns_have_no_floating_region():
    assert _issues(_columns_tree(), prm()) == []


def test_horizontal_brace_is_reported_as_overhang():
    P = prm()
    tree = _columns_tree()
    tree.braces.append((np.array([0.0, 0.0, 10.0]), np.array([10.0, 0.0, 10.0]), 0.4))
    found = _issues(tree, P)
    assert found, "a horizontal brace between two trunks must be flagged"
    assert all(i.severity == "warning" for i in found)
    z = [i.position[2] for i in found]
    assert min(z) == pytest.approx(9.6, abs=0.3)               # underside of the brace
    assert any(1.0 < i.position[0] < 9.0 for i in found)       # between the trunks
    # a brace at the printable angle is fine
    tree = _columns_tree()
    rise = 10.0 / math.tan(math.radians(T.routing_angle_deg(P)))
    tree.braces.append((np.array([0.0, 0.0, 2.0]), np.array([10.0, 0.0, 2.0 + rise]), 0.4))
    assert _issues(tree, P) == []


def test_branch_dipping_below_its_ends_is_an_island():
    """A V hanging between two trunks (a branch that goes down in a curve and comes back
    up) starts in the air at its lowest point: error with a 3D position."""
    P = prm()
    tree = _columns_tree()
    tree.braces.append((np.array([0.0, 0.0, 12.0]), np.array([5.0, 0.0, 7.0]), 0.5))
    tree.braces.append((np.array([5.0, 0.0, 7.0]), np.array([10.0, 0.0, 12.0]), 0.5))
    found = _issues(tree, P)
    errors = [i for i in found if i.severity == "error"]
    assert len(errors) == 1
    p = errors[0].position
    assert p[0] == pytest.approx(5.0, abs=0.8) and 6.0 < p[2] < 7.5


def test_support_resting_on_the_model_is_not_an_island():
    """A support standing on a model ledge is held by the model, not floating."""
    P = prm()
    ledge = trimesh.creation.box([6.0, 6.0, 1.0])
    ledge.apply_translation([0.0, 0.0, 5.5])                     # top at z = 6
    post = trimesh.creation.cylinder(0.6, 5.0, sections=16)
    post.apply_translation([0.0, 0.0, 6.0 + 2.5 - 0.05])         # 0.05 mm into the ledge
    regs = verify.support_layer_regions(post, P, 0.0, model_mesh=ledge)
    assert [r for r in regs if r["kind"] == "island"] == []
    # without the model under it, the same post floats
    regs = verify.support_layer_regions(post, P, 0.0, model_mesh=None)
    assert [r["kind"] for r in regs] == ["island"]


def test_raft_and_plate_hold_the_first_layer():
    P = prm().updated(raft_enabled=True)
    tree = _columns_tree()
    tree.z_base = P.raft_thickness
    for n in tree.nodes.values():
        if n.kind == NodeKind.FOOT:
            n.position[2] = P.raft_thickness
    assert _issues(tree, P) == []


# ---------------------------------------------------------------------------
# geometry fixes
# ---------------------------------------------------------------------------
def test_printable_angle_caps_branch_angle():
    P = prm()
    assert T.printable_angle_deg(P) == pytest.approx(P.overhang_angle_deg - T.PRINTABLE_MARGIN_DEG)
    assert T.routing_angle_deg(P) == P.branch_angle_deg              # 40 < 55 - margin
    Q = P.updated(branch_angle_deg=60.0)
    assert T.routing_angle_deg(Q) == pytest.approx(T.printable_angle_deg(Q))


def _max_segment_tilt(tree: SupportTree) -> float:
    worst = 0.0
    for n in tree.nodes.values():
        if n.parent is None or n.kind == NodeKind.TIP:
            continue
        d = n.position - tree.nodes[n.parent].position
        worst = max(worst, math.degrees(math.atan2(np.linalg.norm(d[:2]), d[2])))
    return worst


def test_steep_branch_angle_is_kept_printable():
    P = prm().updated(branch_angle_deg=60.0)
    r = pipeline.generate(S.shelf(), P)
    assert _max_segment_tilt(r.tree) <= T.printable_angle_deg(P) + 0.5
    for p0, p1, _ in r.tree.braces:
        d = np.asarray(p1) - np.asarray(p0)
        assert math.degrees(math.atan2(np.linalg.norm(d[:2]), d[2])) <= T.printable_angle_deg(P) + 0.5
    assert [i for i in r.issues if i.code == CODE] == []


def test_landing_segment_respects_the_angle():
    """The tilted part of a landing is node -> tip base; measuring the angle down to the
    surface let short drops lean ~75 deg (an almost flat branch in the air)."""
    floor = S._to_trimesh(S._box((-15, -15, 0), (15, 15, 3)))
    c = Contact(0, np.array([0.0, 0.0, 16.0]), np.array([0.0, 0.0, -1.0]), ContactReason.MANUAL)
    tree = SupportTree()
    T._init_tree_extras(tree, 0.0)
    r = T._Router(floor, [c], prm(), tree, Progress())
    r._prepare()
    r.landed, r.fail_nodes = [], {}
    z1 = 3.0 + prm().tip_length
    n = tree.add([0.0, 0.0, z1 + 0.5], 0.5, NodeKind.BRANCH)
    assert r._land_path_clear(n.id, np.array([0.0, 0.0]), z1 + 0.5, 3.0)
    assert not r._land_path_clear(n.id, np.array([1.0, 0.0]), z1 + 0.5, 3.0)   # 63 deg
    assert r._land_path_clear(n.id, np.array([0.6, 0.0]), z1 + 0.5, 3.0)       # 50 deg


def test_widening_limit_thins_the_upper_node_only():
    P = prm()
    tree = SupportTree()
    T._init_tree_extras(tree, 0.0)
    foot = tree.add([0.0, 0.0, 0.0], 1.3, NodeKind.FOOT)
    low = tree.add([0.0, 0.0, 1.0], 0.5, NodeKind.BRANCH, parent=foot.id)   # squeezed node
    up = tree.add([0.8, 0.0, 2.0], 1.3, NodeKind.TRUNK, parent=low.id)
    tip = tree.add([0.8, 0.0, 3.6], 0.2, NodeKind.TIP, parent=up.id)
    r = T._Router.__new__(T._Router)
    r.tree, r.P, r.angle_print = tree, P, T.printable_angle_deg(P)
    r._limit_widening()
    theta = math.atan2(0.8, 1.0)
    allowed = (0.5 - T.JOINT_INSET + math.hypot(0.8, 1.0) * math.sin(math.radians(r.angle_print) - theta)) / build.joint_radius_factors(P)[1]
    assert low.radius == 0.5 and up.radius == pytest.approx(allowed)
    assert tip.radius == 0.2


@pytest.mark.parametrize("factor", [1.15, 1.5])
def test_junction_joint_bottom_is_printable(factor):
    """A junction sphere wider than the branch below gets a skirt buried in the branch."""
    P = prm().updated(junction_radius_factor=factor)
    tree = SupportTree()
    T._init_tree_extras(tree, 0.0)
    foot = tree.add([0.0, 0.0, 0.0], 1.3, NodeKind.FOOT)
    prev = foot
    t = math.tan(math.radians(40.0))
    for k in range(1, 9):                                    # 40 deg trunk
        prev = tree.add([k * t, 0.0, float(k)], 1.3, NodeKind.TRUNK, parent=prev.id)
    j = prev
    for dx in (-2.0, 2.0):                                   # two branches above it
        b = tree.add([j.position[0] + dx, 0.0, j.position[2] + 3.0], 0.8, NodeKind.BRANCH, parent=j.id)
        tree.add([b.position[0], 0.0, b.position[2] + 1.6], 0.2, NodeKind.TIP, parent=b.id)
    sm = build.mesh_tree(tree, P)
    regs = verify.support_layer_regions(sm, P.updated(min_region_area=0.02), 0.0)
    assert regs == [], [(r["kind"], r["z"], r["area"]) for r in regs]


def test_ball_tips_are_printable():
    P = prm().updated(tip_shape="ball")
    r = pipeline.generate(S.bridge(), P)
    assert [i for i in r.issues if i.code == CODE] == []


# ---------------------------------------------------------------------------
# example models: no floating support region
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _figurine_stl():
    return trimesh.load(EX / "figurine.stl")


@pytest.mark.parametrize("profile,over", [
    ("nozzle_04_normal", {}),
    ("nozzle_04_normal", dict(raft_enabled=True)),
    ("nozzle_04_normal", dict(braces_enabled=False)),
    ("nozzle_02_normal", {}),
    ("nozzle_02_normal", dict(raft_enabled=True)),
])
def test_figurine_supports_have_no_floating_region(profile, over):
    r = pipeline.generate(_figurine_stl().copy(), prm(profile).updated(**over))
    found = [i for i in r.issues if i.code == CODE]
    assert found == [], [(i.severity, i.message) for i in found]
    # every support body has a real volume (no zero-volume sliver from the joints)
    assert min(abs(c.volume) for c in r.support_mesh.split(only_watertight=False)) > 1e-3


@pytest.mark.parametrize("name", ["shelf", "bridge", "suspended_arm", "crossing_case",
                                  "impossible_case", "figurine", "cavity", "inclined_surface"])
def test_synthetic_supports_have_no_floating_region(name):
    for profile in ("nozzle_04_normal", "nozzle_02_normal"):
        r = pipeline.generate(S.GENERATORS[name](), prm(profile))
        found = [i for i in r.issues if i.code == CODE]
        assert found == [], (profile, [(i.severity, i.message) for i in found])


def test_selfcheck_is_fast_on_the_figurine():
    """The layer check reuses the model slices of the analysis and slices the supports
    once; it must stay a fraction of the generation time."""
    P = prm()
    r = pipeline.generate(_figurine_stl().copy(), P)
    t = time.perf_counter()
    verify.support_layer_regions(r.support_mesh, P, r.tree.z_base, _figurine_stl(),
                                 r.analysis.layer_polygons)
    dt = time.perf_counter() - t
    assert dt < max(2.0, 0.6 * sum(r.timings.values()))

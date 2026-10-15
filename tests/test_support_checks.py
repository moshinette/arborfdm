"""fragile_support / hard_to_remove checks and GenerationResult.stats.

Hand-made trees that must trigger each rule (and close negatives that must not), the
example models with the default "normal" profiles (no finding except the measured,
justified ones listed in EXPECTED) and the consistency of the statistics.
"""
from __future__ import annotations

import collections
import math
from functools import lru_cache
from pathlib import Path

import manifold3d as mf
import numpy as np
import pytest
import trimesh

from arbor.engine import analysis, build, params, pipeline, verify
from arbor.engine import tree as T
from arbor.engine.types import (AnalysisResult, GenerationResult, Issue, NodeKind, Progress,
                                SupportTree)

EX = Path(__file__).resolve().parent.parent / "examples"
CHECKS = ("fragile_support", "hard_to_remove")


@lru_cache(maxsize=None)
def prm(profile: str = "nozzle_04_normal"):
    return params.load_profiles()[profile].params


def _box(lo, hi) -> mf.Manifold:
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    return mf.Manifold.cube(tuple(hi - lo)).translate(tuple(lo))


def _mesh(m: mf.Manifold) -> trimesh.Trimesh:
    return build.manifold_to_trimesh(m)


def _far_model() -> trimesh.Trimesh:
    return _mesh(_box((60, 60, 0), (62, 62, 2)))


def _new_tree(z_base: float = 0.0) -> SupportTree:
    tree = SupportTree()
    T._init_tree_extras(tree, z_base)
    return tree


def _column(tree, xy, z0, z1, r, root_kind=NodeKind.FOOT, root_r=None, foot_r=3.0, cid=None):
    """Vertical column from a root at z0 to a tip at z1 (nodes every mm)."""
    root = tree.add([xy[0], xy[1], z0], root_r if root_r is not None else r, root_kind)
    if root_kind == NodeKind.FOOT:
        tree.foot_radius[root.id] = foot_r
    below = root
    zs = list(np.arange(z0 + 1.0, z1 - 0.5, 1.0))
    for z in zs:
        below = tree.add([xy[0], xy[1], z], r, NodeKind.TRUNK, parent=below.id)
    tree.add([xy[0], xy[1], z1], 0.2, NodeKind.TIP, parent=below.id,
             contact_id=cid if cid is not None else root.id)
    return root


def _codes(issues, code):
    return [i for i in issues if i.code == code]


def _run_checks(tree, P, model=None):
    issues: list[Issue] = []
    verify._check_fragile(tree, P, issues)
    verify._check_removal(model if model is not None else _far_model(), tree, P, issues)
    return issues


# ---------------------------------------------------------------------------
# fragile_support
# ---------------------------------------------------------------------------
def test_thin_tall_branch_is_fragile():
    """0.4 nozzle: 3 line widths = 1.26 mm. A 0.8 mm column 12 mm tall: lever / d = 14 > 10."""
    P = prm()
    tree = _new_tree()
    _column(tree, (0, 0), 0.0, 12.0, 0.4)
    found = _codes(_run_checks(tree, P), "fragile_support")
    assert len(found) == 1
    assert found[0].severity == "warning" and found[0].position is not None
    assert "thinner than 3 line widths" in found[0].message
    assert found[0].contact_id is not None


def test_short_thin_branch_is_not_fragile():
    P = prm()
    tree = _new_tree()
    _column(tree, (0, 0), 0.0, 8.0, 0.4)       # (8 - 1.8) / 0.8 = 7.75 <= 10
    assert _codes(_run_checks(tree, P), "fragile_support") == []


def test_thick_column_slenderness_limit():
    """A 2.0 mm trunk: 60 mm tall is beyond 25 diameters, 45 mm is not."""
    P = prm()
    tall = _new_tree()
    _column(tall, (0, 0), 0.0, 60.0, 1.0)
    found = _codes(_run_checks(tall, P), "fragile_support")
    assert len(found) == 1 and "slenderness" in found[0].message
    ok = _new_tree()
    _column(ok, (0, 0), 0.0, 45.0, 1.0)
    assert _codes(_run_checks(ok, P), "fragile_support") == []


def test_thin_limit_follows_the_line_width():
    """The same 1.0 mm, 11 mm column: thin (< 3 x 0.42) for the 0.4 nozzle, thick for the
    0.2 nozzle (3 x 0.22 = 0.66 mm)."""
    tree = _new_tree()
    _column(tree, (0, 0), 0.0, 11.0, 0.5)       # (11 - 1.8) / 1.0 = 9.2 ... 0.4: limit 10
    tree2 = _new_tree()
    _column(tree2, (0, 0), 0.0, 14.0, 0.5)      # 12.2: fragile for 0.4, fine for 0.2 (25)
    assert _codes(_run_checks(tree, prm("nozzle_04_normal")), "fragile_support") == []
    assert len(_codes(_run_checks(tree2, prm("nozzle_04_normal")), "fragile_support")) == 1
    assert _codes(_run_checks(tree2, prm("nozzle_02_normal")), "fragile_support") == []


def test_tall_column_on_a_model_pad_is_fragile():
    P = prm("nozzle_02_normal")
    model = _mesh(_box((-10, -10, 0), (10, 10, 3)))
    tall = _new_tree()
    _column(tall, (0, 0), 3.0, 13.0, 0.45, root_kind=NodeKind.MODEL_FOOT, root_r=0.12)
    found = _codes(_run_checks(tall, P, model), "fragile_support")
    assert len(found) == 1 and "pad" in found[0].message
    short = _new_tree()
    _column(short, (0, 0), 3.0, 6.0, 0.45, root_kind=NodeKind.MODEL_FOOT, root_r=0.12)
    assert _codes(_run_checks(short, P, model), "fragile_support") == []   # 3 / 0.24 = 12.5


def test_neighbouring_fragile_trees_are_grouped():
    P = prm()
    tree = _new_tree()
    for x in (0.0, 3.0, 6.0):
        _column(tree, (x, 0), 0.0, 12.0, 0.4)
    _column(tree, (60.0, 0), 0.0, 12.0, 0.4)
    found = _codes(_run_checks(tree, P), "fragile_support")
    assert len(found) == 2
    assert any("3 supports like this nearby" in i.message for i in found)


def test_first_print_brace_is_fragile_and_current_brace_is_not():
    """First print test: a 0.5 mm x 14 mm brace was judged too fragile (now 0.9 mm)."""
    P = prm("nozzle_02_normal")
    for d, expect in ((0.5, 1), (0.9, 0)):
        tree = _new_tree()
        _column(tree, (0, 0), 0.0, 20.0, 1.2)
        _column(tree, (10, 0), 0.0, 20.0, 1.2)
        p0 = np.array([0.0, 0.0, 4.0])
        p1 = np.array([10.0, 0.0, 4.0 + math.sqrt(14.0 ** 2 - 10.0 ** 2)])
        tree.braces.append((p0, p1, d / 2))
        found = _codes(_run_checks(tree, P), "fragile_support")
        assert len(found) == expect, (d, [i.message for i in found])
        if expect:
            assert "Brace" in found[0].message


def test_section_slenderness_values():
    P = prm()
    tree = _new_tree()
    _column(tree, (0, 0), 0.0, 21.8, 1.0)
    recs = verify.section_slenderness(tree, P)
    worst = max(recs, key=lambda r: r["slenderness"])
    # the section above the collar: lever 21.8 - (0.6 + 1.2) = 20 mm, d = 2 mm
    assert worst["lever"] == pytest.approx(20.0)
    assert worst["slenderness"] == pytest.approx(10.0)
    assert worst["limit"] == verify.SLENDER_MAX


# ---------------------------------------------------------------------------
# hard_to_remove
# ---------------------------------------------------------------------------
def test_support_in_a_closed_void_is_hard_to_remove():
    P = prm()
    model = _mesh(_box((-15, -15, 0), (15, 15, 20)) - _box((-10, -10, 3), (10, 10, 15)))
    tree = _new_tree()
    _column(tree, (0, 0), 3.0, 15.0, 0.7, root_kind=NodeKind.MODEL_FOOT, root_r=0.6)
    found = _codes(_run_checks(tree, P, model), "hard_to_remove")
    assert len(found) == 1 and "enclosed" in found[0].message
    assert found[0].contact_id is not None


def test_support_wedged_between_two_walls():
    P = prm()
    base = _box((-10, -10, 0), (10, 10, 3))
    narrow = _mesh(base + _box((-3, -10, 3), (-1.0, 10, 12)) + _box((1.0, -10, 3), (3, 10, 12)))
    wide = _mesh(base + _box((-8, -10, 3), (-4, 10, 12)) + _box((4, -10, 3), (8, 10, 12)))
    for model, expect in ((narrow, 1), (wide, 0)):
        tree = _new_tree()
        _column(tree, (0, 0), 3.0, 8.0, 0.5, root_kind=NodeKind.MODEL_FOOT, root_r=0.5)
        found = _codes(_run_checks(tree, P, model), "hard_to_remove")
        assert len(found) == expect, [i.message for i in found]
        if expect:
            assert "wedged" in found[0].message


def test_open_support_on_the_model_is_not_hard_to_remove():
    P = prm()
    model = _mesh(_box((-10, -10, 0), (10, 10, 3)))
    tree = _new_tree()
    _column(tree, (0, 0), 3.0, 8.0, 0.5, root_kind=NodeKind.MODEL_FOOT, root_r=0.5)
    assert _codes(_run_checks(tree, P, model), "hard_to_remove") == []


def _tips_tree(spacing: float, n: int, tip_r: float) -> SupportTree:
    tree = _new_tree()
    for i in range(n):
        for j in range(n):
            _column(tree, (i * spacing, j * spacing), 0.0, 5.0, 0.6)
    for node in tree.nodes.values():
        if node.kind == NodeKind.TIP:
            node.radius = tip_r
    return tree


def test_dense_contacts_leave_marks():
    """0.6 mm tips 0.7 mm apart: their marks cover most of the area."""
    P = prm()
    found = _codes(_run_checks(_tips_tree(0.7, 4, 0.3), P), "hard_to_remove")
    assert len(found) == 1 and "marks cover" in found[0].message
    assert found[0].contact_id is not None
    # same tips at the Normal profile spacing: no finding
    assert _codes(_run_checks(_tips_tree(3.0, 4, 0.3), P), "hard_to_remove") == []


def test_checks_run_inside_verify():
    """The checks are part of verify.verify (and therefore of every generation)."""
    P = prm()
    tree = _new_tree()
    _column(tree, (0, 0), 0.0, 12.0, 0.4)
    model = _far_model()
    an = analysis.analyze(model, P, Progress())
    issues = verify.verify(model, an, [], tree, build.mesh_tree(tree, P), P)
    assert len(_codes(issues, "fragile_support")) == 1


# ---------------------------------------------------------------------------
# example models with the default profiles
# ---------------------------------------------------------------------------
EXAMPLES = ["bridge", "cavity", "crossing_case", "cube", "figurine", "floating_cube",
            "impossible_case", "inclined_60deg", "shelf", "sphere_on_plate", "suspended_arm"]
# Findings measured on the examples (docs/ALGORITHM.md, section 5). They are true positives:
#  crossing_case (0.2): 12 mm columns standing on the base plate of the model on 0.24 mm pads
#  figurine (0.2): a 22 mm column standing on the socle on a 0.24 mm pad
#  impossible_case: 9 mm columns standing on the bowl floor (0.24 / 0.42 mm pads), in a bowl
#    whose only opening is a 3 mm ring above them
#  cavity: supports standing on the floor of the 3 mm high mouth slot, deep inside it
EXPECTED = {
    ("nozzle_02_normal", "crossing_case"): {"fragile_support"},
    ("nozzle_02_normal", "figurine"): {"fragile_support"},
    ("nozzle_02_normal", "impossible_case"): {"fragile_support", "hard_to_remove"},
    ("nozzle_04_normal", "impossible_case"): {"fragile_support", "hard_to_remove"},
    ("nozzle_02_normal", "cavity"): {"hard_to_remove"},
    ("nozzle_04_normal", "cavity"): {"hard_to_remove"},
}


@lru_cache(maxsize=None)
def _generate(profile: str, name: str, raft: bool = False) -> tuple:
    """(params, light result): the analysis and the meshes are dropped from the cached
    results (22 generations, the figurine ones are large)."""
    mesh = trimesh.load(EX / f"{name}.stl")
    P = prm(profile).updated(raft_enabled=raft)
    r = pipeline.generate(mesh, P)
    r.analysis = None
    r.support_mesh = r.raft_mesh = None
    return P, r


@lru_cache(maxsize=4)
def _generate_full(profile: str, name: str, raft: bool = False) -> tuple:
    mesh = trimesh.load(EX / f"{name}.stl")
    P = prm(profile).updated(raft_enabled=raft)
    return mesh, P, pipeline.generate(mesh, P)


@pytest.mark.parametrize("profile", ["nozzle_04_normal", "nozzle_02_normal"])
@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_have_only_the_measured_findings(profile, name):
    P, r = _generate(profile, name)
    found = [i for i in r.issues if i.code in CHECKS]
    assert {i.code for i in found} == EXPECTED.get((profile, name), set()), \
        [(i.code, i.message) for i in found]
    for i in found:
        assert i.severity == "warning"
        # on the examples only supports standing on the model are concerned: no plate tree,
        # no brace, no dense contacts
        assert "standing on the model" in i.message, i.message


def test_example_sections_keep_a_margin():
    """Measured: the plate trees of the examples stay below 95 % of the section limits."""
    for profile in ("nozzle_04_normal", "nozzle_02_normal"):
        for name in EXAMPLES:
            P, r = _generate(profile, name)
            for rec in verify.section_slenderness(r.tree, P):
                if rec["kind"] == "section" and \
                        r.tree.nodes[rec["root"]].kind == NodeKind.FOOT:
                    assert rec["slenderness"] <= 0.95 * rec["limit"], (profile, name, rec)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------
STAT_KEYS = ("support_volume_mm3", "support_mass_g", "contact_area_mm2", "model_contacts",
             "max_tree_height_mm", "max_slenderness")


@pytest.mark.parametrize("name,profile,raft", [("crossing_case", "nozzle_02_normal", False),
                                               ("suspended_arm", "nozzle_04_normal", True),
                                               ("cavity", "nozzle_04_normal", False)])
def test_stats_are_consistent(name, profile, raft):
    mesh, P, r = _generate_full(profile, name, raft)
    s = r.stats
    for k in STAT_KEYS:
        assert k in s and isinstance(s[k], (int, float)) and s[k] >= 0, k
    assert s["support_volume_mm3"] == pytest.approx(r.support_mesh.volume)
    assert s["support_volume_mm3"] > 0
    assert s["support_mass_g"] == pytest.approx(r.support_mesh.volume * 1.24e-3)
    if raft:
        assert s["raft_volume_mm3"] == pytest.approx(r.raft_mesh.volume) and s["raft_volume_mm3"] > 0
        assert s["raft_mass_g"] == pytest.approx(r.raft_mesh.volume * 1.24e-3)
    else:
        assert s["raft_volume_mm3"] == 0.0
    # contacts on the model, counted from the contacts and the parameters (not the tree code)
    routed = [c for c in r.contacts if c.enabled and c.id not in r.tree.failed]
    pads = [n for n in r.tree.nodes.values() if n.kind == NodeKind.MODEL_FOOT]
    assert s["tip_contacts"] == len(routed)
    assert s["model_feet"] == len(pads)
    assert s["model_contacts"] == len(routed) + len(pads)
    area = sum(math.pi * ((c.tip_diameter or P.tip_diameter) / 2) ** 2 for c in routed)
    area += sum(math.pi * n.radius ** 2 for n in pads)
    assert s["contact_area_mm2"] == pytest.approx(area, rel=1e-6)
    # tree height, from the mesh: the tallest tree reaches the top of the supports
    top = r.support_mesh.bounds[1, 2]
    lowest_root = min(float(n.position[2]) for n in r.tree.roots())
    assert 0 < s["max_tree_height_mm"] <= top - lowest_root + 1e-6
    if not pads:
        assert s["max_tree_height_mm"] == pytest.approx(top - r.tree.z_base, abs=0.2)
    recs = verify.section_slenderness(r.tree, P)
    assert s["max_slenderness"] == pytest.approx(max(q["slenderness"] for q in recs))
    assert s["max_fragility"] == pytest.approx(max(q["slenderness"] / q["limit"] for q in recs))
    assert s["issues_by_code"] == dict(collections.Counter(i.code for i in r.issues))


def test_stats_flag_the_pad_column_of_the_crossing_case():
    """crossing_case, 0.2 nozzle: two 12.1 mm columns on 0.24 mm pads (measured)."""
    P, r = _generate("nozzle_02_normal", "crossing_case")
    assert r.stats["max_slenderness"] == pytest.approx(12.1 / 0.24, rel=0.02)
    assert r.stats["max_fragility"] > 1.0
    assert r.stats["issues_by_code"].get("fragile_support", 0) >= 1


def test_stats_of_an_empty_generation():
    P, r = _generate("nozzle_04_normal", "cube")
    s = r.stats
    assert s["support_volume_mm3"] == 0.0 and s["support_mass_g"] == 0.0
    assert s["model_contacts"] == 0 and s["contact_area_mm2"] == 0.0
    assert s["max_tree_height_mm"] == 0.0 and s["max_slenderness"] == 0.0


def test_generation_result_stats_default():
    """Existing code building GenerationResult positionally keeps working."""
    r = GenerationResult([], SupportTree(), None, None, [], AnalysisResult(0.1, np.zeros(0)), {})
    assert r.stats == {}
    r2 = GenerationResult([], SupportTree(), None, None, [], AnalysisResult(0.1, np.zeros(0)))
    assert r2.stats == {} and r2.stats is not r.stats


def test_denser_profile_has_more_marks():
    """The stats separate configurations: Dense touches the model more than Light."""
    mesh = trimesh.load(EX / "shelf.stl")
    light = pipeline.generate(mesh, prm("nozzle_04_light")).stats
    dense = pipeline.generate(mesh, prm("nozzle_04_dense")).stats
    assert dense["model_contacts"] > light["model_contacts"]
    assert dense["contact_area_mm2"] > light["contact_area_mm2"]
    assert dense["support_volume_mm3"] > light["support_volume_mm3"]


def test_checks_survive_a_malformed_tree():
    """A node whose parent is missing (verify reports disconnected_branch) must not crash
    the new checks nor the statistics."""
    P = prm()
    tree = _new_tree()
    _column(tree, (0, 0), 0.0, 12.0, 0.4)
    orphan = tree.add([20.0, 0.0, 5.0], 0.5, NodeKind.BRANCH, parent=9999)
    tree.add([20.0, 0.0, 6.0], 0.2, NodeKind.TIP, parent=orphan.id, contact_id=77)
    issues = _run_checks(tree, P)
    assert len(_codes(issues, "fragile_support")) == 1
    s = verify.support_stats(_far_model(), [], tree, None, None, issues, P)
    assert s["model_contacts"] == 2 and s["support_volume_mm3"] == 0.0

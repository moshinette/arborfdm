"""Raft coverage (feet / footprint / bed), raft on by default, model lift above the raft
(placement.plan_lift) and the frame bookkeeping of a lifted generation."""
from __future__ import annotations

import json
import zipfile

import manifold3d as mf
import numpy as np
import pytest
import trimesh
from shapely.geometry import MultiPoint, Point

from arbor.engine import pipeline, placement
from arbor.engine.build import raft_polygon, trimesh_to_manifold
from arbor.engine.params import SupportParams, load_profiles
from arbor.engine.types import NodeKind
from tests.models import synthetic as S

P02 = load_profiles()["nozzle_02_normal"].params
G = P02.raft_release_gap          # 1 layer between raft top and model bottom


def _union(*ms):
    return trimesh.Trimesh(*_mf_mesh(mf.Manifold.batch_boolean([trimesh_to_manifold(m) for m in ms],
                                                               mf.OpType.Add)), process=False)


def _mf_mesh(m):
    mm = m.to_mesh()
    return np.asarray(mm.vert_properties, float)[:, :3], np.asarray(mm.tri_verts, np.int64)


def ball_on_plate():
    """Sphere touching the plate at one point: its bottom is a curved overhang that
    starts right on the plate."""
    m = S.floating_sphere()
    m.apply_translation((0, 0, -m.bounds[0][2]))
    return m


def low_arm(base: float):
    """Column of base x base mm with an arm whose underside is 1.8 mm above the plate
    (less than a full tip + foot in 0.2 mm profiles)."""
    col = trimesh.creation.box((base, base, 15))
    col.apply_translation((0, 0, 7.5))
    arm = trimesh.creation.box((12, 3, 2))
    arm.apply_translation((base / 2 + 6 - 0.5, 0, 2.8))
    return _union(col, arm)


def lying_cylinder():
    c = trimesh.creation.cylinder(radius=4, height=20, sections=64)
    c.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    c.apply_translation((0, 0, -c.bounds[0][2]))
    return c


def _codes(r, sev=None):
    return [i.code for i in r.issues if sev is None or i.severity == sev]


@pytest.fixture(scope="module")
def figurine():
    return S.figurine()


@pytest.fixture(scope="module")
def fig_footprint(figurine):
    return pipeline.generate(figurine, P02)


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------
def test_raft_on_by_default_everywhere():
    d = SupportParams()
    assert d.raft_enabled and d.raft_coverage == "footprint" and d.raft_lift_auto
    assert not d.hollow_supports
    for key, prof in load_profiles().items():
        assert prof.params.raft_enabled, key
        assert prof.params.raft_coverage == "footprint", key
        assert prof.params.validate() == [], key


def test_validation_of_new_params():
    assert any("raft_coverage" in m for m in SupportParams(raft_coverage="plate").validate())
    assert any("raft_lift" in m for m in SupportParams(raft_lift=6.0).validate())
    assert any("raft_lift" in m for m in SupportParams(raft_lift=-1.0).validate())
    assert SupportParams(raft_coverage="bed", raft_lift=5.0).validate() == []


# ---------------------------------------------------------------------------
# coverage modes
# ---------------------------------------------------------------------------
def test_footprint_raft_is_one_plate_under_model_and_feet(figurine, fig_footprint):
    r, p = fig_footprint, P02
    assert r.model_lift == 0.0 and r.plate_z == pytest.approx(-p.raft_thickness - G)
    assert r.errors == []
    raft = r.raft_mesh
    assert raft is not None and raft.is_watertight
    assert len(raft.split(only_watertight=False)) == 1                 # one plate
    assert raft.bounds[:, 2] == pytest.approx([-p.raft_thickness - G, -G], abs=1e-6)
    assert r.support_mesh.bounds[0, 2] == pytest.approx(-G, abs=1e-3)  # supports on the raft top
    poly = raft_polygon(r.tree, p).buffer(1e-3)
    base = MultiPoint([tuple(v[:2]) for v in figurine.vertices[figurine.vertices[:, 2] < p.layer_height]])
    assert poly.contains(base.convex_hull.buffer(p.raft_margin - 0.01))  # the model rests on it
    feet = [n for n in r.tree.nodes.values() if n.kind == NodeKind.FOOT]
    assert feet and all(poly.contains(Point(n.position[:2]).buffer(p.raft_margin - 0.01)) for n in feet)
    # nothing of the raft inside the model (it is under it)
    inter = trimesh_to_manifold(figurine) ^ trimesh_to_manifold(raft)
    assert abs(inter.volume()) < 1e-6


def test_bed_raft_is_the_bounding_rectangle_plus_margin(figurine, fig_footprint):
    p = P02.updated(raft_coverage="bed")
    r = pipeline.generate(figurine, p)
    poly = raft_polygon(r.tree, p)
    x0, y0, x1, y1 = poly.bounds
    assert poly.area == pytest.approx((x1 - x0) * (y1 - y0), rel=1e-6)   # a rectangle
    mb = figurine.bounds
    assert x0 <= mb[0, 0] - p.raft_margin + 1e-6 and x1 >= mb[1, 0] + p.raft_margin - 1e-6
    assert y0 <= mb[0, 1] - p.raft_margin + 1e-6 and y1 >= mb[1, 1] + p.raft_margin - 1e-6
    # never the whole build plate: just the scene + margin
    assert (x1 - x0) < mb[1, 0] - mb[0, 0] + 2 * p.raft_margin + 2 * p.foot_diameter
    assert r.raft_mesh.volume > fig_footprint.raft_mesh.volume
    assert r.errors == []


def test_raft_volume_ordering_feet_footprint_bed(figurine, fig_footprint):
    feet = pipeline.generate(figurine, P02.updated(raft_coverage="feet"))
    bed = pipeline.generate(figurine, P02.updated(raft_coverage="bed"))
    v = [x.stats["raft_volume_mm3"] for x in (feet, fig_footprint, bed)]
    assert 0 < v[0] < v[1] < v[2]
    assert feet.plate_z == 0.0 and feet.raft_mesh.bounds[0, 2] == pytest.approx(0.0)


def test_footprint_raft_without_supports_is_a_raft_under_the_model():
    m = S.cube()
    r = pipeline.generate(m, P02)
    assert r.raft_mesh is not None
    assert r.raft_mesh.bounds[0, :2] == pytest.approx(m.bounds[0, :2] - P02.raft_margin, abs=0.05)


# ---------------------------------------------------------------------------
# lift planning
# ---------------------------------------------------------------------------
def test_plan_lift_cases(figurine):
    clear = placement.lift_clearance(P02)
    assert clear == pytest.approx(P02.tip_length + P02.branch_min_diameter / 2 + 0.1
                                  + placement.LIFT_EXTRA_LAYERS * P02.layer_height)
    ball = placement.plan_lift(ball_on_plate(), P02)
    assert not ball.refused and ball.lift == pytest.approx(clear, abs=0.02)
    arm = placement.plan_lift(low_arm(3.0), P02)
    assert not arm.refused and 0 < arm.lift <= 5.0
    big = placement.plan_lift(low_arm(10.0), P02)
    assert big.refused and big.lift == 0.0 and "flat base" in big.reason
    cyl = placement.plan_lift(lying_cylinder(), P02)        # curved, long contact line
    assert cyl.refused and cyl.lift == 0.0
    fig = placement.plan_lift(figurine, P02)
    assert fig.lift == 0.0 and not fig.refused               # overhangs are high above the socle
    # floating model: already far from the plate, nothing to do
    assert placement.plan_lift(S.floating_sphere(), P02).lift == 0.0
    # raft under the feet only / no raft: the model stays on the plate
    assert placement.plan_lift(ball_on_plate(), P02.updated(raft_coverage="feet")).lift == 0.0
    assert placement.plan_lift(ball_on_plate(), P02.updated(raft_enabled=False)).lift == 0.0
    # manual
    man = placement.plan_lift(figurine, P02.updated(raft_lift_auto=False, raft_lift=2.0))
    assert man.lift == 2.0 and man.mode == "manual"


# ---------------------------------------------------------------------------
# lifted generation: one frame for supports, raft, contacts, analysis, issues
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ball_lifted():
    m = ball_on_plate()
    return m, pipeline.generate(m, P02)


def test_lifted_ball_is_supported_from_below(ball_lifted):
    m, r = ball_lifted
    L = r.model_lift
    assert L == pytest.approx(placement.lift_clearance(P02), abs=0.02)
    assert r.plate_z == pytest.approx(-(P02.raft_thickness + G + L))
    assert "model_lifted" in _codes(r, "info")
    assert r.errors == [] and not r.tree.failed
    # the model is not moved: everything else is placed under it
    assert r.raft_mesh.bounds[:, 2] == pytest.approx([-L - G - P02.raft_thickness, -L - G], abs=1e-6)
    assert r.support_mesh.bounds[0, 2] == pytest.approx(-L - G, abs=1e-3)
    assert r.support_mesh.is_watertight
    # contacts on the (unmoved) model surface, bottom included
    pos = np.array([c.position for c in r.contacts])
    d = trimesh.proximity.closest_point(m, pos)[1]
    assert d.max() < 1e-3
    assert pos[:, 2].min() < 0.2                              # the lowest point is supported
    tips = np.array([n.position for n in r.tree.nodes.values() if n.kind == NodeKind.TIP])
    assert trimesh.proximity.closest_point(m, tips)[1].max() < 0.2
    # analysis back in the model frame: the bottom island starts at the model's bottom
    isl = [g for g in r.analysis.regions if g.kind == "island"]
    assert isl and min(g.z for g in isl) == pytest.approx(0.0, abs=P02.layer_height)
    assert r.analysis.stats["model_lift"] == L
    # support penetration into the model stays the tips' allowance
    assert "model_collision" not in _codes(r)


def test_lifted_without_lift_gives_short_or_failed_supports(ball_lifted):
    m, r = ball_lifted
    flat = pipeline.generate(m, P02.updated(raft_lift_auto=False))
    assert flat.model_lift == 0.0
    # without the lift the bottom gets almost no support material (short cones)
    assert flat.stats["support_volume_mm3"] < 0.2 * r.stats["support_volume_mm3"]


def test_low_arm_lifted_and_big_base_refused():
    r = pipeline.generate(low_arm(3.0), P02)
    assert r.model_lift > 0 and r.errors == []
    bottom = [c for c in r.contacts if c.position[2] < 0.1]
    assert bottom                                            # the column's bottom is supported
    big = pipeline.generate(low_arm(10.0), P02)
    assert big.model_lift == 0.0
    assert "raft_lift_refused" in _codes(big, "warning")
    assert big.support_mesh.bounds[0, 2] == pytest.approx(-G, abs=1e-3)


def test_manual_lift_supports_the_flat_bottom():
    m = S.suspended_arm()
    r = pipeline.generate(m, P02.updated(raft_lift_auto=False, raft_lift=2.0))
    assert r.model_lift == 2.0 and r.errors == []
    under = [c for c in r.contacts if c.position[2] < 0.1]
    assert len(under) >= 4


def test_lift_reuses_analysis_and_restores_given_contacts(ball_lifted):
    m, r = ball_lifted
    # branches only regeneration with the result's contacts (user frame)
    cs = r.contacts
    before = [c.position.copy() for c in cs]
    r2 = pipeline.generate(m, P02, analysis=r.analysis, contacts=cs)
    assert r2.model_lift == r.model_lift
    assert all(np.array_equal(c.position, b) for c, b in zip(cs, before))   # restored exactly
    assert r2.support_mesh.volume == pytest.approx(r.support_mesh.volume, rel=1e-6)
    # an analysis computed for another placement is not reused
    flat = pipeline.generate(m, P02.updated(raft_lift_auto=False))
    r3 = pipeline.generate(m, P02, analysis=flat.analysis)
    assert r3.analysis.stats["model_lift"] == r.model_lift
    assert len(r3.contacts) == len(r.contacts)


def test_lifted_export_keeps_alignment(ball_lifted, tmp_path):
    from arbor.io.export import export_3mf, validate_export
    m, r = ball_lifted
    path = tmp_path / "ball.3mf"
    er = export_3mf(path, m, r.support_mesh, r.raft_mesh, P02, model_name="ball", layout="separate")
    assert validate_export(path) == []
    # the model does not touch the plate: written as one object, shifted as a whole
    # model on the raft: one object with separate parts (export.py, docs/SLICERS.md)
    assert er.objects == ["ball", "ball supports", "ball raft"]
    assert er.offset[2] == pytest.approx(P02.raft_thickness + G + r.model_lift, abs=1e-6)
    g = list(trimesh.load(path).dump(concatenate=False))
    assert len(g) == 3 and min(x.bounds[0, 2] for x in g) == pytest.approx(0.0, abs=1e-5)
    assert g[0].bounds[1, 2] == pytest.approx(m.bounds[1, 2] + er.offset[2], abs=1e-4)


# ---------------------------------------------------------------------------
# saved projects keep their behaviour
# ---------------------------------------------------------------------------
def test_legacy_project_keeps_feet_raft(tmp_path):
    from arbor.core.project import Project
    profs = load_profiles()
    p = Project(profs)
    p.set_model(trimesh.creation.box((10, 10, 10)), name="box")
    p.set_param("raft_enabled", False)
    path = p.save(tmp_path / "new")
    q = Project.load(path, profs)
    assert q.params.raft_enabled is False and q.params.raft_coverage == "footprint"
    # a v0.1 file: no raft_coverage / raft_lift keys, raft on under the feet
    data = p.to_dict()
    for k in ("raft_coverage", "raft_lift", "raft_lift_auto", "hollow_supports", "hollow_wall"):
        data["params"].pop(k)
    data["params"]["raft_enabled"] = True
    old = tmp_path / "old.arbor"
    with zipfile.ZipFile(path) as zi, zipfile.ZipFile(old, "w") as zo:
        for n in zi.namelist():
            zo.writestr(n, json.dumps(data) if n == "project.json" else zi.read(n))
    q = Project.load(old, profs)
    assert q.params.raft_enabled is True and q.params.raft_coverage == "feet"
    off = dict(data)
    off["params"] = dict(data["params"], raft_enabled=False)
    with zipfile.ZipFile(path) as zi, zipfile.ZipFile(tmp_path / "old_off.arbor", "w") as zo:
        for n in zi.namelist():
            zo.writestr(n, json.dumps(off) if n == "project.json" else zi.read(n))
    assert Project.load(tmp_path / "old_off.arbor", profs).params.raft_enabled is False


# ---------------------------------------------------------------------------
# release gap between raft and model
# ---------------------------------------------------------------------------
def test_release_gap_defaults_and_validation():
    profs = load_profiles()
    for key, prof in profs.items():
        assert prof.params.raft_release_gap == pytest.approx(prof.params.layer_height), key
    assert any("raft_release_gap" in m for m in SupportParams(raft_release_gap=-0.1).validate())
    assert any("raft_release_gap" in m for m in SupportParams(raft_release_gap=0.6).validate())


def test_release_gap_keeps_model_unsupported_and_tips_on_model(figurine, fig_footprint):
    r = fig_footprint
    # the gap is under the whole footprint: the socle is not supported, it is printed
    # over the raft like a support interface
    assert not any(c.position[2] < P02.layer_height for c in r.contacts)
    assert r.raft_mesh.bounds[1, 2] == pytest.approx(-G, abs=1e-6)
    tips = np.array([n.position for n in r.tree.nodes.values() if n.kind == NodeKind.TIP])
    # tips touch the model or stop one support interface gap (+ rim clearance) below it
    lim = P02.interface_gap_layers * P02.layer_height + 0.5 * P02.tip_diameter + 0.1
    assert trimesh.proximity.closest_point(figurine, tips)[1].max() < lim
    welded = pipeline.generate(figurine, P02.updated(raft_release_gap=0.0))
    assert welded.raft_mesh.bounds[1, 2] == pytest.approx(0.0, abs=1e-6)
    assert welded.plate_z == pytest.approx(-P02.raft_thickness)
    assert len(welded.contacts) == len(r.contacts)


def test_feet_raft_reports_contacts_below_the_raft_top():
    """Raft under the feet only (model on the plate): a contact lower than the raft top
    + one layer has no room for a support; it is reported with the reason (it never
    becomes a contact without tip and without explanation)."""
    m = S.inclined_surface()
    p = load_profiles()["nozzle_04_normal"].params.updated(raft_coverage="feet")
    r = pipeline.generate(m, p)
    tip_cids = {n.contact_id for n in r.tree.nodes.values() if n.contact_id is not None}
    missing = [c for c in r.contacts if c.enabled and c.id not in tip_cids]
    msgs = {i.contact_id: i.message for i in r.issues if i.code == "unrouted_contact"}
    assert all(c.id in msgs for c in missing)
    low = [c for c in missing if c.position[2] < p.raft_thickness + p.layer_height]
    assert all("below the raft top" in msgs[c.id] for c in low)
    # the default raft under the model supports them
    r2 = pipeline.generate(m, load_profiles()["nozzle_04_normal"].params)
    assert "unrouted_contact" not in _codes(r2)

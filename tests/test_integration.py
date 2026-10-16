"""End-to-end tests through arbor.engine.pipeline.generate on synthetic models.

These tests assert geometric properties of the result, not only "it did not crash".
Run with:  python -m pytest tests/test_integration.py -q
"""
from __future__ import annotations

import copy
import threading
import time
from functools import lru_cache

import manifold3d as mf
import numpy as np
import pytest
import trimesh

from tests.models import synthetic as S

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
PLATE_EPS = 0.3        # a support foot is "on the plate" if its lowest point is below this
SURFACE_EPS = 0.05     # contacts must lie on the model surface within this distance


def _engine():
    # imported lazily so a missing module shows up as a clear per-test failure
    from arbor.engine import pipeline, params, types  # noqa: F401
    return pipeline, params, types


@lru_cache(maxsize=None)
def profiles():
    _, params, _ = _engine()
    return params.load_profiles()


def prm(profile: str = "nozzle_04_normal", **over):
    p = profiles()[profile].params
    return p.updated(**over) if over else p


@lru_cache(maxsize=None)
def model(name: str, *args):
    gen = {**S.GENERATORS, **S.BROKEN_GENERATORS}[name]
    return gen(*args)


_RESULTS: dict = {}


def run(name: str, profile: str = "nozzle_04_normal", **over):
    """Cached generate() on a synthetic model (results are treated as read only)."""
    key = (name, profile, tuple(sorted(over.items())))
    if key not in _RESULTS:
        pipeline, _, _ = _engine()
        _RESULTS[key] = pipeline.generate(model(name).copy(), prm(profile, **over))
    return _RESULTS[key]


def active_contacts(res):
    return [c for c in res.contacts if c.enabled]


def contact_points(res) -> np.ndarray:
    pts = [c.position for c in active_contacts(res)]
    return np.asarray(pts, float).reshape(-1, 3)


def is_empty_mesh(m) -> bool:
    return m is None or len(getattr(m, "faces", [])) == 0


def to_manifold(m: trimesh.Trimesh) -> mf.Manifold:
    mesh = mf.Mesh(vert_properties=np.ascontiguousarray(m.vertices, dtype=np.float32),
                   tri_verts=np.ascontiguousarray(m.faces, dtype=np.uint32))
    out = mf.Manifold(mesh)
    assert out.status() == mf.Error.NoError, f"mesh is not a valid manifold: {out.status()}"
    return out


def intersection_volume(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    return (to_manifold(a) ^ to_manifold(b)).volume()


def penetration_budget(res, params) -> float:
    """Allowed support/model overlap: each tip may sink tip_penetration into the model with
    at most its base diameter, plus a generous discretisation allowance per contact."""
    n = len(active_contacts(res))
    r = params.tip_base_diameter / 2
    per_tip = np.pi * r * r * (params.tip_penetration + 0.15)
    return n * per_tip + 0.5


def codes(res, severity=None):
    return [i.code for i in res.issues if severity is None or i.severity == severity]


def in_box(p, box, margin=0.0) -> np.ndarray:
    lo, hi = np.asarray(box[0]) - margin, np.asarray(box[1]) + margin
    p = np.atleast_2d(p)
    return np.all((p >= lo) & (p <= hi), axis=1)


def grounded_components(res) -> list[str]:
    """Return a description of every support component that neither reaches the plate nor
    contains a model foot (i.e. floats in the air)."""
    _, _, types = _engine()
    sm = res.support_mesh
    if is_empty_mesh(sm):
        return []
    model_feet = np.array([n.position for n in res.tree.nodes.values()
                           if n.kind == types.NodeKind.MODEL_FOOT]).reshape(-1, 3)
    bad = []
    for comp in sm.split(only_watertight=False):
        lo, hi = comp.bounds
        if lo[2] <= PLATE_EPS:
            continue
        if len(model_feet) and in_box(model_feet, (lo, hi), margin=1.0).any():
            continue
        bad.append(f"component bounds {np.round(lo, 2).tolist()} .. {np.round(hi, 2).tolist()}")
    return bad


# ---------------------------------------------------------------------------
# generic properties on several models
# ---------------------------------------------------------------------------
GENERIC_MODELS = ["shelf", "bridge", "floating_cube", "suspended_arm", "sphere_on_plate",
                  "inclined_surface", "crossing_case", "close_contacts_case", "cavity"]


@pytest.mark.parametrize("name", GENERIC_MODELS)
def test_generic_result_properties(name):
    res = run(name)
    m = model(name)
    p = prm()
    assert len(active_contacts(res)) > 0, f"{name}: needs support but got no contact"

    # contacts on the model surface, unit normals
    pts = contact_points(res)
    _, dist, _ = trimesh.proximity.closest_point(m, pts)
    assert dist.max() < SURFACE_EPS, f"{name}: contact {dist.argmax()} is {dist.max():.3f} mm off the surface"
    for c in active_contacts(res):
        assert np.linalg.norm(c.normal) == pytest.approx(1.0, abs=1e-3)
        assert c.note or c.reason is not None

    # support mesh: watertight, positive volume, not below the plate
    sm = res.support_mesh
    assert not is_empty_mesh(sm), f"{name}: contacts but empty support mesh"
    assert sm.is_watertight, f"{name}: support mesh not watertight"
    assert sm.volume > 0
    # not below the support base (plate, or raft top: below z = 0 when the model rests
    # on the raft, v0.2 default)
    zb = res.tree.z_base
    assert sm.bounds[0, 2] >= zb - 1e-3, f"{name}: support below its base (min z {sm.bounds[0, 2]} < {zb})"

    # every component grounded
    bad = grounded_components(res)
    assert not bad, f"{name}: floating support components: {bad}"

    # every enabled contact has a tip in the tree
    tip_cids = {n.contact_id for n in res.tree.nodes.values() if n.contact_id is not None}
    missing = [c.id for c in active_contacts(res) if c.id not in tip_cids]
    assert not missing, f"{name}: contacts without tree tip: {missing}"

    # tree sanity: parents exist, no cycles, parents never above children
    nodes = res.tree.nodes
    for n in nodes.values():
        seen = set()
        cur = n
        while cur.parent is not None:
            assert cur.parent in nodes, f"{name}: dangling parent {cur.parent}"
            assert cur.id not in seen, f"{name}: cycle in tree at node {cur.id}"
            seen.add(cur.id)
            par = nodes[cur.parent]
            assert par.position[2] <= cur.position[2] + 1e-6, \
                f"{name}: node {par.id} (parent) above child {cur.id}"
            cur = par

    # support does not eat the model
    vol = intersection_volume(sm, m)
    budget = penetration_budget(res, p)
    assert vol <= budget, f"{name}: support/model overlap {vol:.3f} mm3 > budget {budget:.3f} mm3"

    # no unsupported region left
    assert "unsupported_region" not in codes(res, "error"), \
        f"{name}: {[i.message for i in res.errors]}"

    # timings reported for every stage
    assert {"analysis", "contacts", "tree", "mesh", "verify"} <= set(res.timings)


# ---------------------------------------------------------------------------
# specific models
# ---------------------------------------------------------------------------
def test_cube_needs_no_support():
    res = run("cube")
    assert len(active_contacts(res)) == 0, [c.to_dict() for c in res.contacts]
    sm = res.support_mesh
    assert is_empty_mesh(sm) or abs(sm.volume) < 1e-6
    assert not res.errors, [i.message for i in res.errors]


def test_shelf_contacts_only_under_shelf():
    res = run("shelf")
    m = model("shelf")
    z = m.metadata["shelf_bottom_z"]
    pts = contact_points(res)
    assert len(pts) >= 4, "a 15 x 20 mm flat shelf needs several contacts"
    p = prm()
    assert np.all(np.abs(pts[:, 2] - z) <= p.layer_height + 0.05), \
        f"contact z values {np.unique(np.round(pts[:, 2], 3))} not at shelf bottom {z}"
    lo, hi = m.metadata["shelf_box"]
    assert in_box(pts, (lo, hi), margin=0.1).all(), "contact outside the shelf underside"
    # coverage: no point of the underside is far from a contact
    xs = np.linspace(lo[0] + 0.5, hi[0] - 0.5, 15)
    ys = np.linspace(lo[1] + 0.5, hi[1] - 0.5, 20)
    grid = np.array([[x, y] for x in xs for y in ys])
    d = np.linalg.norm(grid[:, None, :] - pts[None, :, :2], axis=2).min(axis=1)
    assert d.max() <= p.contact_spacing * 1.25, f"uncovered shelf area: {d.max():.2f} mm from a contact"
    # the shelf has nothing below it but the plate: no foot on the model
    assert res.tree.stats()["model_feet"] == 0
    assert res.support_mesh.is_watertight


def test_floating_cube_island_contacts():
    _, _, types = _engine()
    res = run("floating_cube")
    z = model("floating_cube").metadata["island_z"]
    isl = [c for c in active_contacts(res) if c.reason == types.ContactReason.ISLAND]
    assert isl, f"no ISLAND contact; reasons = {[c.reason for c in res.contacts]}"
    assert all(abs(c.position[2] - z) < 0.2 for c in isl)
    assert any(r.kind == "island" for r in res.analysis.regions)


def test_floating_sphere_low_point():
    res = run("floating_sphere")
    lp = np.asarray(model("floating_sphere").metadata["low_point"])
    pts = contact_points(res)
    assert len(pts) > 0
    assert np.linalg.norm(pts - lp, axis=1).min() < 1.0, "lowest point of a floating sphere not supported"


def test_suspended_arm_hand_supported():
    res = run("suspended_arm")
    lp = np.asarray(model("suspended_arm").metadata["hand_low_point"])
    pts = contact_points(res)
    assert np.linalg.norm(pts - lp, axis=1).min() < 1.5


@pytest.mark.parametrize("angle,needs", [(30, False), (70, True)])
def test_inclined_surface_threshold(angle, needs):
    """overhang_angle_deg = 55 (from vertical): 30 deg slope prints alone, 70 deg needs support."""
    pipeline, _, _ = _engine()
    res = pipeline.generate(S.inclined_surface(angle), prm(overhang_angle_deg=55.0))
    n = len(active_contacts(res))
    if needs:
        assert n > 0
    else:
        assert n == 0, f"{n} contacts on a {angle} deg slope (threshold 55)"


def test_figurine():
    res = run("figurine")
    m = model("figurine")
    p = prm()
    pts = contact_points(res)
    feats = m.metadata["features"]
    for f in ("right_arm", "left_arm", "chin", "sword"):
        assert in_box(pts, feats[f], margin=1.5).any(), f"no contact on figurine {f}"
    assert "unsupported_region" not in codes(res, "error"), [i.message for i in res.errors]
    assert res.support_mesh.is_watertight
    vol = intersection_volume(res.support_mesh, m)
    budget = penetration_budget(res, p)
    assert vol <= budget, f"support/model overlap {vol:.3f} mm3 > budget {budget:.3f}"
    bad = grounded_components(res)
    assert not bad, bad


def _tip_diameters(res):
    _, _, types = _engine()
    return np.array([2 * n.radius for n in res.tree.nodes.values() if n.kind == types.NodeKind.TIP])


@pytest.mark.parametrize("profile", ["nozzle_04_normal", "nozzle_02_normal", "nozzle_04_dense"])
def test_tip_diameter_respects_profile(profile):
    res = run("figurine", profile)
    p = prm(profile)
    d = _tip_diameters(res)
    assert len(d) > 0
    allowed = [p.tip_diameter, p.on_model_tip_diameter] + \
              [c.tip_diameter for c in res.contacts if c.tip_diameter]
    err = np.min(np.abs(d[:, None] - np.asarray(allowed)[None, :]), axis=1)
    assert err.max() < 0.02, f"tip diameters {np.unique(np.round(d, 3))} vs profile {allowed}"


def test_nozzle_02_vs_04():
    r2, r4 = run("bridge", "nozzle_02_normal"), run("bridge", "nozzle_04_normal")
    d2, d4 = np.median(_tip_diameters(r2)), np.median(_tip_diameters(r4))
    assert d2 < d4, f"0.2 tips {d2} not finer than 0.4 tips {d4}"
    n2, n4 = len(active_contacts(r2)), len(active_contacts(r4))
    assert n2 > n4, f"0.2 profile ({n2} contacts) should be denser than 0.4 ({n4})"


@pytest.mark.parametrize("name", ["bridge", "shelf", "figurine"])
@pytest.mark.parametrize("nozzle", ["04", "02"])
def test_density_monotonic(name, nozzle):
    n = [len(active_contacts(run(name, f"nozzle_{nozzle}_{d}"))) for d in ("light", "normal", "dense")]
    assert n[0] <= n[1] <= n[2] and n[0] < n[2], f"{name} light/normal/dense = {n}"


def test_impossible_case_reported_when_on_model_forbidden():
    res = run("impossible_case", allow_on_model=False)
    md = model("impossible_case").metadata
    cap_issues = [i for i in res.issues if i.severity in ("error", "warning")]
    assert cap_issues, "cap cannot be supported from the plate, but no error/warning was reported"
    # no support may stand on the model
    assert res.tree.stats()["model_feet"] == 0
    # and if supports were built anyway they must not pass through the bowl floor
    sm = res.support_mesh
    if not is_empty_mesh(sm):
        vol = intersection_volume(sm, model("impossible_case"))
        assert vol <= penetration_budget(res, prm()), f"supports cut through the bowl ({vol:.2f} mm3)"
    with_pos = [i for i in cap_issues if i.position is not None]
    if with_pos:
        assert any(np.hypot(*np.asarray(i.position)[:2]) <= md["cap_radius"] + 1.0 for i in with_pos)


def test_impossible_case_model_feet_when_allowed():
    res = run("impossible_case", allow_on_model=True)
    st = res.tree.stats()
    assert st["model_feet"] > 0, f"expected supports standing on the bowl floor, stats={st}"
    assert any(c.rests_on_model for c in res.contacts)
    assert "unsupported_region" not in codes(res, "error")
    md = model("impossible_case").metadata
    _, _, types = _engine()
    for n in res.tree.nodes.values():
        if n.kind == types.NodeKind.MODEL_FOOT:
            assert abs(n.position[2] - md["bowl_floor_z"]) < 0.5, f"model foot at z={n.position[2]}"
    assert not grounded_components(res)


# ---------------------------------------------------------------------------
# editing and regeneration
# ---------------------------------------------------------------------------
def _manual_contact(types, cid, pos, normal=(0, 0, -1)):
    return types.Contact(id=cid, position=np.asarray(pos, float), normal=np.asarray(normal, float),
                         reason=types.ContactReason.MANUAL, manual=True)


def test_regenerate_with_edited_contacts():
    pipeline, _, types = _engine()
    m = model("bridge")
    p = prm()
    r1 = run("bridge")
    edited = [copy.deepcopy(c) for c in r1.contacts]
    removed = edited.pop(0)
    new_id = max(c.id for c in r1.contacts) + 1
    span = m.metadata["span_x"]
    man = _manual_contact(types, new_id, (0.5 * (span[0] + span[1]) + 0.3, 2.6, m.metadata["deck_bottom_z"]))
    edited.append(man)
    r2 = pipeline.generate(m.copy(), p, analysis=r1.analysis, contacts=edited)
    ids = {c.id for c in r2.contacts}
    assert new_id in ids and removed.id not in ids
    assert any(c.manual for c in r2.contacts)
    tip_cids = {n.contact_id for n in r2.tree.nodes.values() if n.contact_id is not None}
    assert new_id in tip_cids, "manual contact has no tip"
    assert removed.id not in tip_cids, "removed contact still has a tip"
    assert len(r2.contacts) == len(r1.contacts)


def test_disabled_contact_gets_no_tip():
    pipeline, _, _ = _engine()
    r1 = run("bridge")
    edited = [copy.deepcopy(c) for c in r1.contacts]
    edited[0].enabled = False
    r2 = pipeline.generate(model("bridge").copy(), prm(), analysis=r1.analysis, contacts=edited)
    tip_cids = {n.contact_id for n in r2.tree.nodes.values() if n.contact_id is not None}
    assert edited[0].id not in tip_cids


def test_manual_contacts_survive_automatic_placement():
    pipeline, _, types = _engine()
    m = model("bridge")
    span = m.metadata["span_x"]
    man = _manual_contact(types, 10_000, (span[0] + 1.1, 1.3, m.metadata["deck_bottom_z"]))
    res = pipeline.generate(m.copy(), prm(), manual_contacts=[man])
    kept = [c for c in res.contacts if c.manual]
    assert len(kept) == 1
    assert np.allclose(kept[0].position, man.position, atol=1e-6)
    # automatic contacts must not be merged onto/over the manual one
    auto = contact_points(res)[[not c.manual for c in active_contacts(res)]]
    assert len(auto) > 0


def test_manual_only():
    pipeline, _, types = _engine()
    m = model("bridge")
    man = _manual_contact(types, 7, (15.0, 2.5, m.metadata["deck_bottom_z"]))
    res = pipeline.generate(m.copy(), prm(manual_only=True), manual_contacts=[man])
    assert [c.id for c in active_contacts(res)] == [7]


# ---------------------------------------------------------------------------
# progress and cancellation
# ---------------------------------------------------------------------------
def test_cancel_before_start():
    pipeline, _, types = _engine()
    pr = types.Progress()
    pr.cancel()
    with pytest.raises(types.Cancelled):
        pipeline.generate(model("shelf").copy(), prm(), progress=pr)


def test_progress_monotonic_and_cancel_midway():
    pipeline, _, types = _engine()
    seen = []
    pipeline.generate(model("figurine").copy(), prm(),
                      progress=types.Progress(lambda f, msg: seen.append(f)))
    assert seen, "no progress reported"
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert all(b >= a - 1e-9 for a, b in zip(seen, seen[1:])), "progress went backwards"
    assert seen[-1] == pytest.approx(1.0)
    assert len(seen) >= 5, "too few progress updates for a responsive UI"

    ev = threading.Event()

    def cb(f, msg):
        if f >= 0.4:
            ev.set()

    with pytest.raises(types.Cancelled):
        pipeline.generate(model("figurine").copy(), prm(), progress=types.Progress(cb, ev))


def test_cancel_from_other_thread_is_prompt():
    pipeline, _, types = _engine()
    m = S.large_mesh(300_000)
    pr = types.Progress()
    out = {}

    def work():
        t = time.perf_counter()
        try:
            pipeline.generate(m, prm(), progress=pr)
            out["result"] = "finished"
        except types.Cancelled:
            out["result"] = "cancelled"
        out["t"] = time.perf_counter() - t

    th = threading.Thread(target=work)
    th.start()
    time.sleep(0.5)
    t_cancel = time.perf_counter()
    pr.cancel()
    th.join(60)
    assert not th.is_alive()
    if out["result"] == "cancelled":
        assert time.perf_counter() - t_cancel < 3.0, "cancellation took more than 3 s"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def _scene_geoms(loaded):
    return loaded.dump() if isinstance(loaded, trimesh.Scene) else [loaded]


def _check_3mf_package(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert "3D/3dmodel.model" in names
        assert "[Content_Types].xml" in names
        assert "_rels/.rels" in names
        xml = z.read("3D/3dmodel.model").decode()
        assert 'unit="millimeter"' in xml


def test_export_3mf_without_engine(tmp_path):
    """export_3mf alone, on a model with negative XY (figurine) plus a fake support."""
    from arbor.io.export import export_3mf, validate_export
    m = model("figurine")
    sup = S.cube(4)
    sup.apply_translation((10, -10, 0))
    path = tmp_path / "fig.3mf"
    er = export_3mf(path, m, sup)
    _check_3mf_package(path)
    assert validate_export(path) == []
    geoms = _scene_geoms(trimesh.load(str(path)))
    assert len(geoms) == 2
    vols = sorted(g.volume for g in geoms)
    assert vols == pytest.approx(sorted([m.volume, sup.volume]), rel=1e-4)
    # whatever offset is applied, it is reported and applied to every object identically
    off = np.asarray(er.offset)
    big = max(geoms, key=lambda g: g.volume)
    assert big.bounds == pytest.approx(m.bounds + off, abs=1e-3)
    assert big.bounds[0, 2] == pytest.approx(0.0, abs=1e-4)


def test_export_stl_without_engine(tmp_path):
    from arbor.io.export import export_stl
    a, b = model("shelf"), S.floating_cube()
    path = export_stl([a, b], tmp_path / "x.stl")
    loaded = trimesh.load(str(path))
    assert sum(g.volume for g in _scene_geoms(loaded)) == pytest.approx(a.volume + b.volume, rel=1e-4)
    assert loaded.bounds == pytest.approx(np.array([np.minimum(a.bounds[0], b.bounds[0]),
                                                    np.maximum(a.bounds[1], b.bounds[1])]), abs=1e-3)


def test_export_rejects_empty_model(tmp_path):
    from arbor.io.export import export_3mf
    with pytest.raises(ValueError):
        export_3mf(tmp_path / "e.3mf", trimesh.Trimesh())


def test_export_3mf_roundtrip(tmp_path):
    from arbor.io.export import export_3mf, validate_export
    res = run("shelf")
    m = model("shelf")
    path = tmp_path / "out.3mf"
    export_3mf(path, m, res.support_mesh, res.raft_mesh, prm())
    _check_3mf_package(path)
    assert validate_export(path) == []
    geoms = _scene_geoms(trimesh.load(str(path)))
    assert len(geoms) >= 2, "model and supports must be separate objects in the 3MF"
    for g in geoms:
        assert g.is_watertight
    # v0.2: raft on by default, the model rests on it (raft below z = 0, the export
    # shifts the scene up by the raft thickness)
    raft_v = res.raft_mesh.volume if res.raft_mesh is not None else 0.0
    expected = m.volume + res.support_mesh.volume + raft_v
    assert sum(g.volume for g in geoms) == pytest.approx(expected, rel=1e-3)
    allb = np.vstack([g.bounds for g in geoms])
    lo = min(float(x.bounds[0, 2]) for x in (m, res.support_mesh, res.raft_mesh) if x is not None)
    assert allb[:, 2].min() == pytest.approx(0.0, abs=1e-3)
    assert allb[:, 2].max() == pytest.approx(m.bounds[1, 2] - lo, abs=1e-3)


def test_export_stl_roundtrip(tmp_path):
    from arbor.io.export import export_stl
    res = run("shelf")
    m = model("shelf")
    path = export_stl([m, res.support_mesh], tmp_path / "out.stl")
    loaded = trimesh.load(str(path))
    assert sum(g.volume for g in _scene_geoms(loaded)) == pytest.approx(
        m.volume + res.support_mesh.volume, rel=1e-3)


# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------
def test_units_scale_2x():
    pipeline, _, _ = _engine()
    m1 = model("shelf").copy()
    m2 = model("shelf").copy()
    m2.apply_scale(2.0)
    r1 = pipeline.generate(m1, prm())
    r2 = pipeline.generate(m2, prm())
    z1 = m1.metadata["shelf_bottom_z"]
    p1, p2 = contact_points(r1), contact_points(r2)
    assert np.allclose(p1[:, 2], z1, atol=0.2)
    assert np.allclose(p2[:, 2], 2 * z1, atol=0.2), "contacts did not follow a 2x scale in z"
    lo, hi = np.asarray(m1.metadata["shelf_box"])
    assert in_box(p2, (2 * lo, 2 * hi), margin=0.1).all()
    # spacing is in mm, so the 4x larger area needs clearly more contacts (about 4x)
    assert 2.5 * len(p1) <= len(p2) <= 6 * len(p1), f"{len(p1)} -> {len(p2)} contacts"
    # tip size is absolute (mm), not relative to model size
    assert np.median(_tip_diameters(r1)) == pytest.approx(np.median(_tip_diameters(r2)), abs=1e-6)


def test_units_low_point_scales():
    pipeline, _, _ = _engine()
    m = model("floating_sphere").copy()
    m.apply_scale(2.0)
    res = pipeline.generate(m, prm())
    lp = 2 * np.asarray(model("floating_sphere").metadata["low_point"])
    assert np.linalg.norm(contact_points(res) - lp, axis=1).min() < 1.0


# ---------------------------------------------------------------------------
# robustness on tricky inputs
# ---------------------------------------------------------------------------
def test_tiny_triangles():
    res = run("tiny_triangles")
    z = model("tiny_triangles").metadata["shelf_bottom_z"]
    pts = contact_points(res)
    assert len(pts) > 0
    assert np.all(np.abs(pts[:, 2] - z) < 0.2), "tiny triangles created spurious contacts"


def test_close_contacts_are_not_duplicated():
    res = run("close_contacts_case")
    pts = contact_points(res)
    d = np.linalg.norm(pts[:, None] - pts[None], axis=2) + np.eye(len(pts)) * 1e9
    assert d.min() >= prm().min_contact_distance - 1e-6, f"contacts {d.min():.3f} mm apart"
    for t in model("close_contacts_case").metadata["spike_tips"]:
        assert np.linalg.norm(pts - np.asarray(t), axis=1).min() < 1.2, f"spike tip {t} unsupported"


def test_crossing_case_supports_do_not_cross_arms():
    res = run("crossing_case")
    md = model("crossing_case").metadata
    pts = contact_points(res)
    assert in_box(pts, md["arm_a_box"], 0.2).any() and in_box(pts, md["arm_b_box"], 0.2).any()
    # supports for arm A (higher) must not pass through arm B
    vol = intersection_volume(res.support_mesh, model("crossing_case"))
    assert vol <= penetration_budget(res, prm())


@pytest.mark.slow
def test_large_mesh_runs_in_reasonable_time():
    pipeline, _, _ = _engine()
    m = S.large_mesh(1_000_000)
    t = time.perf_counter()
    res = pipeline.generate(m, prm())
    dt = time.perf_counter() - t
    assert res.support_mesh.is_watertight
    assert dt < 300, f"1M faces took {dt:.0f} s"

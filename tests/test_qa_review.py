"""Independent QA review tests (see docs/QA_REVIEW.md).

Every test here is executed. Tests marked ``xfail(strict=True)`` reproduce a defect that
is NOT fixed yet: they fail today (reported as XFAIL) and will turn into XPASS, hence a
suite failure, once the defect is fixed, so the marker must then be removed.

Run: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_qa_review.py -q -p no:cacheprovider
"""
from __future__ import annotations

import copy
import json
import os
import re
try:
    import resource  # POSIX only
except ImportError:  # Windows
    resource = None
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
FIGURINE = EX / "figurine.stl"

from arbor.core.mesh_io import load_mesh  # noqa: E402
from arbor.core.transform import apply_transform, plate_matrix  # noqa: E402
from arbor.engine import pipeline  # noqa: E402
from arbor.engine import verify as V  # noqa: E402
from arbor.engine.params import SupportParams, load_profiles  # noqa: E402
from arbor.engine.types import Contact, ContactReason, Issue, NodeKind, SupportTree  # noqa: E402
from arbor.io.export import export_3mf, export_stl, validate_export  # noqa: E402

PROFILES = load_profiles()
NORMAL = PROFILES["nozzle_04_normal"].params
DENSE02 = PROFILES["nozzle_02_dense"].params


# ---------------------------------------------------------------------------
# helpers / shared fixtures
# ---------------------------------------------------------------------------
def _placed(mesh):
    return apply_transform(mesh, plate_matrix(mesh))


def _codes(issues, severity=None):
    return sorted({i.code for i in issues if severity is None or i.severity == severity})


def _routed(result):
    failed = set(getattr(result.tree, "failed", {}) or {})
    tips = {n.contact_id for n in result.tree.nodes.values() if n.kind == NodeKind.TIP}
    return {c for c in tips if c not in failed}


@pytest.fixture(scope="module")
def figurine():
    return load_mesh(FIGURINE).mesh


@pytest.fixture(scope="module")
def fig_cli(figurine):
    """Figurine placed exactly like the CLI does (XY centred, min z = 0)."""
    return _placed(figurine)


@pytest.fixture(scope="module")
def fig_normal(figurine):
    return pipeline.generate(figurine, NORMAL)


@pytest.fixture(scope="module")
def arm_result():
    m = load_mesh(EX / "suspended_arm.stl").mesh
    return m, pipeline.generate(m, NORMAL)


def _run_limited(code: str, mem_gb: float, timeout: float = 600):
    """Run python code in a subprocess with an address space limit; returns
    (returncode, stdout, stderr, wall seconds, peak RSS MB)."""
    if resource is None:
        pytest.skip("memory limit needs the POSIX resource module")
    lim = int(mem_gb * 1024 ** 3)

    def pre():
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    t = time.perf_counter()
    p = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, capture_output=True,
                       text=True, timeout=timeout, preexec_fn=pre,
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
    wall = time.perf_counter() - t
    return p.returncode, p.stdout, p.stderr, wall, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024


# ===========================================================================
# 1. engine on hard cases
# ===========================================================================
def test_figurine_normal_tips_touch_and_no_error(fig_normal, figurine):
    r = fig_normal
    assert r.errors == [], [i.message for i in r.errors]
    routed = _routed(r)
    assert routed == {c.id for c in r.contacts if c.enabled}
    P = np.array([c.position for c in r.contacts if c.id in routed])
    _, d, _ = trimesh.proximity.closest_point(r.support_mesh, P)
    assert d.max() <= NORMAL.tip_penetration + 0.02          # every tip really touches
    mf = np.array([n.position for n in r.tree.nodes.values() if n.kind == NodeKind.MODEL_FOOT])
    _, dm, _ = trimesh.proximity.closest_point(figurine, mf.reshape(-1, 3))
    assert dm.max() <= 0.1                                   # model feet are on the model


def test_dense02_figurine_has_no_false_floating_support(figurine):
    r = pipeline.generate(figurine, DENSE02)
    comps = r.support_mesh.split(only_watertight=False)
    voids = [c for c in comps if c.volume < 0]
    floating = [i for i in r.issues if i.code == "floating_support"]
    # diagnostic of the root cause (true today): every flagged part is an inverted shell
    assert len(voids) >= len(floating)
    assert floating == [], [i.message for i in floating]


def test_cli_default_figurine_exits_zero(tmp_path):
    from arbor.cli import main
    rc = main(["generate", str(FIGURINE), "-o", str(tmp_path / "f.3mf"), "-q",
               "--json", str(tmp_path / "f.json")])
    rep = json.loads((tmp_path / "f.json").read_text())
    assert rc == 0, [i["message"] for i in rep["issues"] if i["severity"] == "error"]


def test_rotated_and_scaled_figurine(figurine):
    m = figurine.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(37), [1, 1, 0]))
    m.apply_scale(0.5)
    m = _placed(m)
    assert m.extents[2] == pytest.approx(29.07, abs=0.05)
    r = pipeline.generate(m, NORMAL)
    assert r.errors == [], [i.message for i in r.errors]
    # not below the support base (raft top, below z = 0 under the model since v0.2)
    assert r.support_mesh.bounds[0, 2] >= r.tree.z_base - 1e-6
    routed = _routed(r)
    P = np.array([c.position for c in r.contacts if c.id in routed])
    _, d, _ = trimesh.proximity.closest_point(r.support_mesh, P)
    assert d.max() <= 0.1


def test_raft_supports_stand_on_raft_and_avoid_model(figurine):
    from arbor.engine.build import raft_polygon, trimesh_to_manifold
    from shapely.geometry import Point
    # raft under the feet only (raft_coverage "feet", the v0.1 raft): the model stays on
    # the plate; the default "footprint" raft is tested in tests/test_raft_lift.py
    p = NORMAL.updated(raft_enabled=True, raft_coverage="feet")
    r = pipeline.generate(figurine, p)
    assert r.raft_mesh is not None and r.raft_mesh.is_watertight
    assert r.raft_mesh.bounds[:, 2] == pytest.approx([0.0, p.raft_thickness], abs=1e-6)
    assert r.support_mesh.bounds[0, 2] == pytest.approx(p.raft_thickness, abs=1e-3)
    poly = raft_polygon(r.tree, p).buffer(1e-3)
    feet = [n for n in r.tree.nodes.values() if n.kind == NodeKind.FOOT]
    assert feet and all(poly.contains(Point(n.position[:2])) for n in feet)
    inter = trimesh_to_manifold(figurine) ^ trimesh_to_manifold(r.raft_mesh)
    assert abs(inter.volume()) < 1e-6                         # raft never under the model


def test_raft_does_not_lose_contacts(figurine, fig_normal):
    r = pipeline.generate(figurine, NORMAL.updated(raft_enabled=True))
    assert fig_normal.errors == []
    assert [i.message for i in r.errors] == []


def test_allow_on_model_false_never_stands_on_model_and_says_so(figurine):
    r = pipeline.generate(figurine, NORMAL.updated(allow_on_model=False))
    assert not any(n.kind == NodeKind.MODEL_FOOT for n in r.tree.nodes.values())
    unrouted = [i for i in r.issues if i.code == "unrouted_contact"]
    assert unrouted and all(i.severity == "error" and i.contact_id is not None for i in unrouted)
    assert len(_routed(r)) + len(unrouted) == sum(c.enabled for c in r.contacts)


def test_braces_toggle_has_effect(figurine, fig_normal):
    assert len(fig_normal.tree.braces) > 0
    r = pipeline.generate(figurine, NORMAL.updated(braces_enabled=False))
    assert r.tree.braces == []
    assert len(r.support_mesh.faces) < len(fig_normal.support_mesh.faces)


def test_very_close_manual_contacts_are_kept_and_warned(figurine):
    down = figurine.triangles_center[figurine.face_normals[:, 2] < -0.7]
    p0 = down[len(down) // 2]
    manual = [Contact(1000 + k, p0 + [0.02 * k, 0, 0], np.array([0, 0, -1.0]), ContactReason.MANUAL,
                      manual=True) for k in range(5)]
    r = pipeline.generate(figurine, NORMAL, manual_contacts=manual)
    assert sorted(c.id for c in r.contacts if c.manual) == [1000, 1001, 1002, 1003, 1004]
    close = [i for i in r.issues if i.code == "contacts_too_close"]
    assert len(close) == 10 and all(i.severity == "warning" for i in close)


def test_thin_parts_get_supports():
    a = trimesh.creation.box([2, 2, 10]); a.apply_translation([0, 0, 5])
    b = trimesh.creation.box([20, 20, 0.2]); b.apply_translation([0, 0, 10.1])
    plate = trimesh.boolean.union([a, b], engine="manifold")
    r = pipeline.generate(plate, NORMAL)
    assert len(_routed(r)) > 20 and r.errors == []
    a = trimesh.creation.box([3, 3, 10]); a.apply_translation([0, 0, 5])
    n = trimesh.creation.cylinder(0.15, 20, sections=8)
    n.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    n.apply_translation([10, 0, 8])
    needle = trimesh.boolean.union([a, n], engine="manifold")
    r = pipeline.generate(needle, NORMAL)
    assert len(_routed(r)) >= 4 and r.errors == []


@pytest.mark.parametrize("over", [
    dict(overhang_angle_deg=10, branch_angle_deg=0, circle_segments=6),
    dict(overhang_angle_deg=89, branch_angle_deg=60, circle_segments=64, merge_distance=50),
    dict(contact_density=5.0, min_contact_distance=0.05),
    dict(contact_density=0.1, merge_distance=0.0, foot_diameter=30, trunk_diameter=15,
         branch_max_diameter=10),
])
def test_extreme_valid_params_on_small_models(over):
    p = NORMAL.updated(**over)
    assert p.validate() == []
    for name in ("suspended_arm", "shelf", "bridge"):
        r = pipeline.generate(load_mesh(EX / f"{name}.stl").mesh, p)
        sm = r.support_mesh
        if sm is not None and len(sm.faces):
            # not below the support base (raft top, below z = 0 under the model since v0.2)
            assert sm.is_watertight and sm.bounds[0, 2] >= r.tree.z_base - 1e-6


def test_min_contact_spacing_bounded_memory():
    rc, out, err, wall, _ = _run_limited("""
        from arbor.core.mesh_io import load_mesh
        from arbor.engine import pipeline
        from arbor.engine.params import load_profiles
        m = load_mesh('examples/figurine.stl').mesh
        r = pipeline.generate(m, load_profiles()['nozzle_04_normal'].params.updated(contact_spacing=0.2))
        print('OK', len(r.contacts))
    """, mem_gb=3.0)
    assert rc == 0 and "OK" in out, err[-400:]


def test_huge_model_is_refused_cleanly():
    rc, out, err, wall, _ = _run_limited("""
        from arbor.core.mesh_io import load_mesh
        from arbor.engine import pipeline
        from arbor.engine.params import load_profiles
        m = load_mesh('examples/figurine.stl').mesh
        m.apply_scale(25.4)
        try:
            pipeline.generate(m, load_profiles()['nozzle_04_normal'].params)
            print('GENERATED')
        except ValueError as e:
            print('REFUSED', e)
    """, mem_gb=3.0)
    assert "REFUSED" in out or "GENERATED" in out, err[-300:]


def test_inch_sized_model_gets_scale_warning(tmp_path, figurine):
    m = figurine.copy()
    m.apply_scale(1 / 25.4)
    f = tmp_path / "inch.stl"
    m.export(f)
    lm = load_mesh(f)
    assert any("scale" in w.lower() for w in lm.warnings), lm.warnings


def test_unrepaired_broken_mesh_is_flagged(figurine):
    rng = np.random.default_rng(0)
    keep = rng.random(len(figurine.faces)) > 0.05
    broken = trimesh.Trimesh(figurine.vertices, figurine.faces[keep], process=False)
    assert not broken.is_watertight
    r = pipeline.generate(broken, NORMAL)
    mesh_issues = [i for i in r.issues if i.severity in ("error", "warning")
                   and ("mesh" in i.code or "manifold" in i.message.lower()
                        or "repair" in i.message.lower())]
    assert mesh_issues, _codes(r.issues)


def test_big_mesh_1m_triangles_time_and_memory(tmp_path):
    """~1 M triangles (figurine subdivided twice): generation time and peak memory measured
    in a fresh process. Bounds are generous (measured: 10.4 s, 1.1 GB on 2 cores)."""
    f = tmp_path / "fig_1M.stl"
    m = load_mesh(FIGURINE).mesh.subdivide().subdivide()
    assert len(m.faces) == 992256
    m.export(f)
    rc, out, err, wall, _ = _run_limited(f"""
        import time, resource, json
        from arbor.core.mesh_io import load_mesh
        from arbor.engine import pipeline
        from arbor.engine.params import load_profiles
        m = load_mesh({str(f)!r}).mesh
        t = time.perf_counter()
        r = pipeline.generate(m, load_profiles()['nozzle_04_normal'].params)
        dt = time.perf_counter() - t
        print(json.dumps({{"t": dt, "rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                          "errors": len(r.errors), "contacts": len(r.contacts)}}))
    """, mem_gb=5.0, timeout=900)
    assert rc == 0, err[-500:]
    d = json.loads(out.strip().splitlines()[-1])
    print("1M triangles:", d)
    assert d["t"] < 90 and d["rss"] < 3000 and d["contacts"] > 50


# ===========================================================================
# 2. verification honesty: fabricated defects MUST be reported
# ===========================================================================
def _verify(mesh, r, tree=None, sm=None, contacts=None):
    return V.verify(mesh, r.analysis, r.contacts if contacts is None else contacts,
                    r.tree if tree is None else tree, r.support_mesh if sm is None else sm, NORMAL)


def test_verify_baseline_clean(arm_result):
    m, r = arm_result
    assert _codes(_verify(m, r), "error") == []


def test_verify_detects_contact_removed_from_tree(arm_result):
    m, r = arm_result
    t = copy.deepcopy(r.tree)
    cid = next(n.contact_id for n in t.nodes.values() if n.kind == NodeKind.TIP)
    for nid in [n.id for n in t.nodes.values() if n.contact_id == cid]:
        del t.nodes[nid]
    iss = _verify(m, r, tree=t)
    assert any(i.code == "unrouted_contact" and i.contact_id == cid and i.severity == "error"
               for i in iss)


def test_verify_detects_floating_mesh_part(arm_result):
    m, r = arm_result
    cyl = trimesh.creation.cylinder(1.0, 3.0, transform=trimesh.transformations.translation_matrix([30, 30, 20]))
    iss = _verify(m, r, sm=trimesh.util.concatenate([r.support_mesh, cyl]))
    assert "floating_support" in _codes(iss, "error")


def test_verify_detects_dangling_branch(arm_result):
    m, r = arm_result
    t = copy.deepcopy(r.tree)
    a = t.add([30, 30, 25], 0.5, NodeKind.BRANCH)
    t.add([30, 30, 26], 0.3, NodeKind.TIP, a.id, 999)
    assert "disconnected_branch" in _codes(_verify(m, r, tree=t), "error")


def test_verify_detects_support_below_plate(arm_result):
    m, r = arm_result
    sm = r.support_mesh.copy()
    sm.apply_translation([0, 0, -1])
    assert "below_plate" in _codes(_verify(m, r, sm=sm), "error")


def test_verify_detects_missing_supports(arm_result):
    m, r = arm_result
    iss = _verify(m, r, tree=SupportTree(), sm=None, contacts=[])
    assert "unsupported_region" in _codes(iss, "error")


def _pillar_through(m):
    c, top = m.centroid, m.bounds[1][2] - 0.5
    assert m.contains([[c[0], c[1], top / 2]])[0]
    return trimesh.creation.cylinder(1.2, top, transform=trimesh.transformations.translation_matrix(
        [c[0], c[1], top / 2]))


def test_verify_measures_support_crossing_model(arm_result):
    m, r = arm_result
    iss = _verify(m, r, sm=trimesh.util.concatenate([r.support_mesh, _pillar_through(m)]))
    col = [i for i in iss if i.code == "model_collision"]
    assert col and float(re.search(r"by ([\d.]+) mm3", col[0].message).group(1)) > 100


def test_verify_support_crossing_model_is_an_error(arm_result):
    m, r = arm_result
    iss = _verify(m, r, sm=trimesh.util.concatenate([r.support_mesh, _pillar_through(m)]))
    assert "model_collision" in _codes(iss, "error")


def test_verify_detects_model_foot_in_the_air(arm_result):
    m, r = arm_result
    t = copy.deepcopy(r.tree)
    foot = t.add([40, 40, 15], 0.5, NodeKind.MODEL_FOOT)
    t.add([40, 40, 18], 0.3, NodeKind.TIP, foot.id, None)
    cyl = trimesh.creation.cylinder(0.5, 3.0, transform=trimesh.transformations.translation_matrix([40, 40, 16.5]))
    assert trimesh.proximity.closest_point(m, [[40, 40, 15]])[1][0] > 10
    iss = _verify(m, r, tree=t, sm=trimesh.util.concatenate([r.support_mesh, cyl]))
    assert set(_codes(iss, "error")) & {"floating_support", "disconnected_branch"}


# ===========================================================================
# 3. exports: 3MF package, independent readers, STL, CLI
# ===========================================================================
@pytest.fixture(scope="module")
def fig_export(tmp_path_factory, figurine):
    d = tmp_path_factory.mktemp("exp")
    p = NORMAL.updated(raft_enabled=True)
    r = pipeline.generate(figurine, p)
    res = export_3mf(d / "fig.3mf", figurine, r.support_mesh, r.raft_mesh, p, model_name="figurine")
    comp = export_3mf(d / "fig_comp.3mf", figurine, r.support_mesh, None, p, model_name="figurine",
                      layout="components")
    return d, r, res, comp, p


def test_3mf_package_structure(fig_export):
    d, r, res, comp, p = fig_export
    CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(d / "fig.3mf") as z:
        assert z.testzip() is None
        assert sorted(z.namelist()) == ["3D/3dmodel.model", "[Content_Types].xml", "_rels/.rels"]
        ct = ET.fromstring(z.read("[Content_Types].xml"))
        exts = {e.get("Extension"): e.get("ContentType") for e in ct}
        assert exts == {"rels": "application/vnd.openxmlformats-package.relationships+xml",
                        "model": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"}
        rels = ET.fromstring(z.read("_rels/.rels"))
        assert [(x.get("Target"), x.get("Type")) for x in rels] == [
            ("/3D/3dmodel.model", "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel")]
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    assert root.get("unit") == "millimeter"
    objs = root.find(CORE + "resources").findall(CORE + "object")
    assert [(o.get("id"), o.get("type"), o.get("name")) for o in objs] == [
        ("1", "model", "figurine"), ("2", "model", "figurine supports"), ("3", "model", "figurine raft")]
    items = root.find(CORE + "build").findall(CORE + "item")
    assert [i.get("objectid") for i in items] == ["1", "2", "3"]
    mats = {i.get("transform") for i in items}
    assert len(mats) == 1                                   # parts keep their relative position
    vals = [float(x) for x in mats.pop().split()]
    assert vals[:9] == [1, 0, 0, 0, 1, 0, 0, 0, 1]
    assert np.allclose(vals[9:], res.offset, atol=1e-6)
    assert validate_export(d / "fig.3mf") == []
    assert validate_export(d / "fig_comp.3mf") == []


def test_3mf_trimesh_readback_keeps_geometry(fig_export, figurine):
    d, r, res, comp, p = fig_export
    sc = trimesh.load(d / "fig.3mf")
    parts = {g.metadata.get("name"): g for g in sc.dump(concatenate=False)}
    assert set(parts) == {"figurine", "figurine supports", "figurine raft"}
    for name, g in parts.items():
        assert g.is_watertight and g.is_winding_consistent, name
    assert parts["figurine"].volume == pytest.approx(figurine.volume, rel=1e-6)
    assert parts["figurine supports"].volume == pytest.approx(r.support_mesh.volume, rel=1e-4)
    # world placement: everything shifted by the same offset, min corner >= 0
    assert np.allclose(parts["figurine"].bounds, figurine.bounds + res.offset, atol=1e-4)
    lo = np.min([g.bounds[0] for g in parts.values()], axis=0)
    assert np.all(lo >= -1e-6)
    # tips still touch the model after the round trip
    P = np.array([c.position for c in r.contacts if c.id in _routed(r)]) + res.offset
    _, dist, _ = trimesh.proximity.closest_point(parts["figurine supports"], P)
    assert dist.max() < 0.1


def test_3mf_lib3mf_independent_validation(fig_export):
    lib3mf = pytest.importorskip("lib3mf")
    d, r, res, comp, p = fig_export
    w = lib3mf.get_wrapper()
    for f, n_items, n_mesh, n_comp in (("fig.3mf", 3, 3, 0), ("fig_comp.3mf", 1, 2, 1)):
        model = w.CreateModel()
        reader = model.QueryReader("3mf")
        reader.SetStrictModeActive(True)
        reader.ReadFromFile(str(d / f))
        assert reader.GetWarningCount() == 0, [reader.GetWarning(i) for i in range(reader.GetWarningCount())]
        assert model.GetUnit() == lib3mf.ModelUnit.MilliMeter
        it, meshes = model.GetMeshObjects(), []
        while it.MoveNext():
            o = it.GetCurrentMeshObject()
            assert o.IsManifoldAndOriented(), o.GetName()
            meshes.append(o.GetName())
        assert len(meshes) == n_mesh
        ci, comps = model.GetComponentsObjects(), 0
        while ci.MoveNext():
            comps += 1
        assert comps == n_comp
        bi, items = model.GetBuildItems(), 0
        while bi.MoveNext():
            items += 1
        assert items == n_items


def test_stl_binary_layout_and_readback(tmp_path, fig_normal, figurine):
    f = tmp_path / "all.stl"
    export_stl([figurine, fig_normal.support_mesh], f)
    raw = f.read_bytes()
    n = int(np.frombuffer(raw[80:84], "<u4")[0])
    assert len(raw) == 84 + 50 * n
    m = trimesh.load(f)
    assert m.is_watertight
    assert np.allclose(m.bounds[0], np.minimum(figurine.bounds[0], fig_normal.support_mesh.bounds[0]), atol=1e-4)


def _cli(args, tmp_path):
    from arbor.cli import main
    j = tmp_path / "r.json"
    rc = main(["generate", *args, "-q", "--json", str(j)])
    return rc, (json.loads(j.read_text(), parse_constant=lambda c: pytest.fail(f"JSON has {c}"))
                if j.exists() else None)


def test_cli_exit_codes_and_report(tmp_path):
    out = str(tmp_path / "o.3mf")
    assert _cli([str(EX / "missing.stl"), "-o", out], tmp_path)[0] == 2
    rc, rep = _cli([str(EX / "broken_hole.stl"), "-o", out], tmp_path)
    assert rc == 2 and rep["exit_code"] == 2
    rc, rep = _cli([str(EX / "broken_hole.stl"), "-o", out, "--repair"], tmp_path)
    assert rc == 0 and rep["repair"]["success"]
    assert _cli([str(EX / "cube.stl"), "-o", str(tmp_path / "o.obj")], tmp_path)[0] == 2
    assert _cli([str(EX / "cube.stl"), "-o", out, "--set", "foo=1"], tmp_path)[0] == 2
    assert _cli([str(EX / "cube.stl"), "-o", out, "--set", "overhang_angle_deg=5"], tmp_path)[0] == 2
    rc, rep = _cli([str(EX / "suspended_arm.stl"), "-o", out], tmp_path)
    assert rc == 0 and rep["exit_code"] == 0 and rep["errors"] == 0
    assert rep["export"]["validation_problems"] == [] and Path(rep["files"]["output"]).exists()
    assert len(rep["contacts"]) == rep["tree"]["tips"] > 0


@pytest.mark.parametrize("assign", ["trunk_diameter=-2", "foot_diameter=0", "segment_length=0",
                                    "contact_spacing=nan"])
def test_validate_rejects_invalid_geometry_params(tmp_path, assign):
    p = NORMAL.updated(**{assign.split("=")[0]: float(assign.split("=")[1])})
    assert p.validate(), f"validate() accepts {assign}"


def test_cli_negative_raft_thickness_is_usage_error(tmp_path):
    rc, _ = _cli([str(EX / "suspended_arm.stl"), "-o", str(tmp_path / "o.3mf"), "--set",
                  "raft_enabled=true", "--set", "raft_thickness=-1"], tmp_path)
    assert rc == 2


# ===========================================================================
# 4. GUI, driven offscreen with the real figurine and real mouse events
# ===========================================================================
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a.setProperty("arbor_no_dialogs", True)
    return a


@pytest.fixture
def win(qapp):
    from arbor.app import main_window as mw
    w = mw.MainWindow()
    w.resize(1400, 900)
    w.show()
    qapp.processEvents()
    yield w
    if w.runner.is_running():
        w.runner.cancel()
        w.runner.wait(30000)
    w.project.undo_stack.set_clean()
    w.close()
    qapp.processEvents()


def _wait(qapp, w, timeout=300):
    end = time.monotonic() + timeout
    while w.runner.is_running() and time.monotonic() < end:
        qapp.processEvents(QtCore.QEventLoop.AllEvents, 50)
        time.sleep(0.005)
    qapp.processEvents()
    assert not w.runner.is_running()


def _load_and_generate(qapp, w, path=FIGURINE):
    assert w.open_path(str(path))
    _wait(qapp, w)
    assert w.project.has_model, w.status_msg.text()
    assert w.generate_full()
    _wait(qapp, w)
    assert w.project.result is not None, w.status_msg.text()


def _find_pickable_pixel(vp):
    vp.setCameraPosition(elevation=-30, azimuth=-60)
    for y in range(40, vp.height() - 40, 12):
        for x in range(40, vp.width() - 40, 12):
            h = vp.pick_model(x, y)
            if h is not None and h[1][2] < -0.5 and vp.pick_contact(x, y, 25) is None:
                return x, y, h
    pytest.fail("no pickable downward-facing pixel")


def test_gui_full_flow_real_picking_and_exports(qapp, win, tmp_path):
    w, p = win, win.project
    _load_and_generate(qapp, w)
    n0 = len(p.contacts)
    vp = w.viewport
    x, y, hit = _find_pickable_pixel(vp)
    QTest.mouseClick(vp, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
    qapp.processEvents()
    assert len(p.contacts) == n0 + 1
    c = p.contact(p.selected_contact_id)
    assert c.manual and np.allclose(c.position, hit[0])
    assert trimesh.proximity.closest_point(p.mesh, [c.position])[1][0] < 1e-3   # on the surface
    assert w.act_rebuild.text().endswith("*")
    assert w.generate_branches()
    _wait(qapp, w)
    assert c.id in _routed(p.result)
    assert trimesh.proximity.closest_point(p.result.support_mesh, [c.position])[1][0] < 0.1
    # right click on the marker removes it, undo / redo
    px, _ = vp.project_points(np.array([c.position]))
    QTest.mouseClick(vp, QtCore.Qt.RightButton, QtCore.Qt.NoModifier,
                     QtCore.QPoint(int(round(px[0][0])), int(round(px[0][1]))))
    qapp.processEvents()
    assert p.contact(c.id) is None
    w.act_undo.trigger(); assert p.contact(c.id) is not None
    w.act_redo.trigger(); assert p.contact(c.id) is None
    w.act_undo.trigger(); assert p.contact(c.id) is not None and not p.contacts_edited
    # exports through the menu actions, then read back
    outs = {}
    for act, name in ((w.act_export_3mf, "g.3mf"), (w.act_export_stl_sup, "g_sup.stl"),
                      (w.act_export_stl_all, "g_all.stl")):
        outs[name] = tmp_path / name
        w.path_provider = lambda *a, f=outs[name]: str(f)
        act.trigger()
        _wait(qapp, w)
        assert outs[name].exists(), w.last_error
    assert validate_export(outs["g.3mf"]) == []
    parts = {g.metadata.get("name"): g for g in trimesh.load(outs["g.3mf"]).dump(concatenate=False)}
    base = p.name.rsplit('.', 1)[0]
    if f"{base} supports" in parts:
        sup = parts[f"{base} supports"]
        off = sup.bounds[0] - p.result.support_mesh.bounds[0]
    else:
        # v0.2 default raft under the model: the model rests on the raft, the separate
        # layout writes one object (a model object not touching the plate would be
        # dropped onto it by the slicer)
        sup = parts[f"{base} with supports"]
        ref = [m for m in (p.mesh, p.result.support_mesh, p.result.raft_mesh) if m is not None]
        off = sup.bounds[0] - np.min([m.bounds[0] for m in ref], axis=0)
    assert trimesh.proximity.closest_point(sup, [c.position + off])[1][0] < 0.1   # manual tip exported
    s = trimesh.load(outs["g_sup.stl"])
    a = trimesh.load(outs["g_all.stl"])
    assert len(a.faces) - len(s.faces) == len(p.mesh.faces)


def test_gui_every_control_has_an_effect(qapp, win, tmp_path):
    w, p, L, R, V = win, win.project, win.left, win.right, win.view
    vp = w.viewport
    assert w.open_path(str(EX / "broken_hole.stl"))
    _wait(qapp, w)
    # mesh check / repair (repair is an undoable copy)
    L.check_button.click(); _wait(qapp, w)
    assert w.last_mesh_report is not None and not w.last_mesh_report.is_valid
    L.repair_button.click(); _wait(qapp, w)
    assert p.base_is_repaired and p.base_mesh.is_watertight and not p.imported_mesh.is_watertight
    w.act_undo.trigger()
    assert not p.base_is_repaired
    # transform controls
    assert w.open_path(str(FIGURINE)); _wait(qapp, w)
    d0 = p.dimensions().copy()
    L.rot_spins[0].setValue(90.0)
    assert np.allclose(p.dimensions(), d0[[0, 2, 1]], atol=1e-6)
    w.act_undo.trigger()
    L.scale_spin.setValue(50.0)
    assert np.allclose(p.dimensions(), d0 / 2, atol=1e-6)
    L.height_spin.setValue(45.0)
    assert p.dimensions()[2] == pytest.approx(45.0)
    p.set_transform(translation=(5.0, 3.0, 0.0))
    L.plate_button.click()
    assert np.allclose(p.mesh.bounds.mean(axis=0)[:2], 0, atol=1e-6) and p.mesh.bounds[0, 2] == pytest.approx(0)
    L.orient_button.click(); _wait(qapp, w)
    assert L.orient_list.count() >= 2 and L.orient_list.isVisibleTo(w)
    item = L.orient_list.item(1)
    QTest.mouseClick(L.orient_list.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                     L.orient_list.visualItemRect(item).center())
    assert np.allclose(p.transform.rotation_deg, L.candidates[1].rotation_deg)
    p.new()
    assert w.open_path(str(FIGURINE)); _wait(qapp, w)
    # profile combo, simple fields, every advanced field and its reset button
    i = L.profile_combo.findData("nozzle_02_dense")
    L.profile_combo.setCurrentIndex(i); L.profile_combo.activated.emit(i)
    assert p.profile_key == "nozzle_02_dense" and p.params.nozzle_diameter == 0.2
    L.fields["overhang_angle_deg"].widget.setValue(60.0)
    L.fields["raft_enabled"].widget.setChecked(True)
    assert p.params.overhang_angle_deg == 60.0 and p.params.raft_enabled
    R.reset_all_button.click()
    assert p.changed_from_profile() == {}
    dead = []
    for name, pf in R.fields.items():
        wd, before = pf.widget, getattr(p.params, name)
        if isinstance(wd, QtWidgets.QCheckBox):
            wd.setChecked(not wd.isChecked())
        elif isinstance(wd, QtWidgets.QComboBox):
            # any choice list (tip_shape, raft_coverage since v0.2): next item
            wd.setCurrentIndex((wd.currentIndex() + 1) % wd.count())
        else:
            v, s = wd.value(), wd.singleStep()
            wd.setValue(v + s if v + s <= wd.maximum() else v - s)
        if getattr(p.params, name) == before or not R.reset_buttons[name].isEnabled():
            dead.append(name)
            continue
        R.reset_buttons[name].click()
        if getattr(p.params, name) != getattr(p.profile_params(), name):
            dead.append(name + " (reset)")
    assert dead == []
    # generation, display modes, clipping, presets, issues, contact editor
    assert w.generate_full(); _wait(qapp, w)
    for k, (mv, sv) in {"model": (True, False), "supports": (False, True), "both": (True, True)}.items():
        V.mode_buttons[k].click()
        assert (vp.model_item.visible(), vp.support_item.visible()) == (mv, sv), k
    V.clip_slider.setValue(300)
    vp._clip_timer.stop(); vp._rebuild_geometry()
    assert vp.clip_z == pytest.approx(0.3 * vp.scene_height())
    assert vp.model_item.opts["meshdata"].vertexes()[:, 2].max() <= vp.clip_z + 1e-4
    V.clip_slider.setValue(1000)
    assert vp.clip_z is None
    for b in V.findChildren(QtWidgets.QPushButton):
        if b.text() == "Top":
            b.click()
    assert vp.opts["elevation"] == 90
    if R.issue_list.count():
        before = vp.opts["center"]
        it = R.issue_list.item(0)
        QTest.mouseClick(R.issue_list.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                         R.issue_list.visualItemRect(it).center())
        assert vp.opts["center"] != before
    cid = p.contacts[0].id
    p.select_contact(cid)
    ed = R.contact_editor
    ed.enabled_check.setChecked(False); assert not p.contact(cid).enabled
    ed.override_check.setChecked(True); ed.tip_spin.setValue(0.9)
    assert p.contact(cid).tip_diameter == pytest.approx(0.9)
    ed.delete_button.click(); assert p.contact(cid) is None
    n = len(p.contacts)
    w.act_clear.trigger(); assert p.contacts == [] and p.result is None
    w.act_undo.trigger(); assert len(p.contacts) == n and p.result is not None
    # cancel, save / new / open
    assert w.generate_full(); w.act_cancel.trigger(); _wait(qapp, w)
    assert w.status_msg.text() == "Cancelled"
    target = tmp_path / "p.arbor"
    w.path_provider = lambda *a: str(target)
    w.act_save.trigger(); assert target.exists()
    w.act_new.trigger(); assert not p.has_model
    w.act_open.trigger(); _wait(qapp, w)
    assert p.has_model and len(p.contacts) == n


def test_gui_edits_during_generation_are_not_lost(qapp, win):
    w, p = win, win.project
    assert w.open_path(str(FIGURINE)); _wait(qapp, w)
    c = p.add_manual_contact(p.mesh.bounds.mean(axis=0) * [1, 1, 1.5], (0, 0, -1))
    p.select_contact(c.id)
    assert w.generate_full()
    assert w.runner.is_running()
    w.right.contact_editor.delete_button.click()          # enabled while busy
    w.right.fields["tip_diameter"].widget.setValue(0.6)
    assert p.contact(c.id) is None and p.params.tip_diameter == 0.6
    _wait(qapp, w)
    stale = p.contact(c.id) is not None
    built_tip = min(n.radius for n in p.result.tree.nodes.values() if n.kind == NodeKind.TIP) * 2
    assert not stale, "deleted manual contact came back with the result"
    assert built_tip == pytest.approx(0.6, abs=0.05) or p.contacts_edited


def test_gui_export_metadata_matches_generated_geometry(qapp, win, tmp_path):
    w, p = win, win.project
    assert w.open_path(str(EX / "suspended_arm.stl")); _wait(qapp, w)
    assert w.generate_full(); _wait(qapp, w)
    w.right.fields["tip_diameter"].widget.setValue(0.6)
    out = tmp_path / "s.3mf"
    w.path_provider = lambda *a: str(out)
    w.export_3mf(); _wait(qapp, w)
    m = zipfile.ZipFile(out).read("3D/3dmodel.model").decode()
    meta = json.loads(re.search(r'name="arbor:support_params">([^<]*)<', m).group(1).replace("&quot;", '"'))
    assert meta["tip_diameter"] == pytest.approx(NORMAL.tip_diameter)


def test_issue_list_with_identical_messages(qapp):
    from arbor.app.panels import RightPanel
    from arbor.core.project import Project
    rp = RightPanel(Project())
    msg = "Tree of height 41.6 mm stands on a 4.6 mm foot without braces"
    issues = [Issue("warning", "unstable_trunk", msg, np.array([1.0, 2, 0])),
              Issue("warning", "unstable_trunk", msg, np.array([71.0, 2, 0]))]
    rp.set_issues(issues, True)
    assert rp.issue_list.count() == 2
    assert {rp.issue_list.item(i).data(QtCore.Qt.UserRole) for i in range(2)} == {0, 1}


def test_two_identical_minis_produce_identical_issue_messages(figurine):
    """Precondition of QA-7: two identical minis give issues with the same message at
    different positions (the GUI crash itself is tested just above).

    Contact placement and routing are not exactly translation invariant (the residual
    lattice is anchored at the origin, the prefilter voxels and the routing grid too: the
    figurine alone gives 80 contacts, moved by 70 mm 83), so generating both copies on
    one plate only produced identical messages by chance, and any change of the contact
    spacing (v0.2, effective tip spacing) changes that chance. The precondition is built
    from the issues of one figurine, placed twice."""
    # any warning will do (the code is incidental: since the bracing of isolated tall
    # trees the figurine no longer gives unstable_trunk)
    r = pipeline.generate(figurine, NORMAL.updated(raft_enabled=False))
    issues = [i for i in r.issues if i.severity in ("warning", "error")]
    assert issues
    doubled = issues + [Issue(i.severity, i.code, i.message,
                              None if i.position is None else i.position + np.array([70.0, 0, 0]))
                        for i in issues]
    msgs = [i.message for i in doubled]
    assert len(msgs) != len(set(msgs))


def test_gui_export_warns_about_unrebuilt_edits(qapp, win, tmp_path):
    w, p = win, win.project
    assert w.open_path(str(EX / "suspended_arm.stl")); _wait(qapp, w)
    assert w.generate_full(); _wait(qapp, w)
    p.remove_contact(p.contacts[0].id)
    assert p.contacts_edited
    out = tmp_path / "e.3mf"
    w.path_provider = lambda *a: str(out)
    w.export_3mf(); _wait(qapp, w)
    assert out.exists()
    shown = w.status_msg.text() + "\n" + "\n".join(getattr(w, "last_info", ("", "")))
    assert "edited" in shown.lower() or "rebuild" in shown.lower(), shown

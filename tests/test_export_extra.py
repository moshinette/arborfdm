"""Weld at write precision (arbor.io.export.weld_for_export / weld_for_stl).

Reproduces what manifold3d returns for unions: zero-area sliver triangles whose distinct
vertices share one float position. Index topology is closed, but a reader that merges
vertices by position (trimesh.load, slicers) sees non-manifold edges."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh

from arbor.core.mesh_io import read_3mf
from arbor.core.repair import check_mesh
from arbor.io.export import (_closed_consistent, export_3mf, export_stl, validate_export,
                             weld_for_export, weld_for_stl)


def _box_with_sliver():
    """Closed box where one edge midpoint is duplicated: vertex a and a' have identical
    coordinates, linked by two zero-area triangles. Closed and consistently wound by
    index, non-manifold once merged by position without dropping the slivers."""
    box = trimesh.creation.box((10, 10, 10))
    box.apply_translation((5, 5, 5))
    v = np.asarray(box.vertices, float)
    f = np.asarray(box.faces, np.int64)
    # split the edge (i, j) shared by two faces with a midpoint, duplicated
    edges = {}
    for fi, tri in enumerate(f):
        for k in range(3):
            e = (tri[k], tri[(k + 1) % 3])
            edges[e] = fi
    i, j = next(e for e in edges if (e[1], e[0]) in edges)
    f1, f2 = edges[(i, j)], edges[(j, i)]
    m = (v[i] + v[j]) / 2
    a, b = len(v), len(v) + 1               # same position, different index
    v = np.vstack([v, m, m])
    faces = [t for n, t in enumerate(f.tolist()) if n not in (f1, f2)]
    t1, t2 = f[f1].tolist(), f[f2].tolist()
    k1 = [(t1[k], t1[(k + 1) % 3]) for k in range(3)].index((i, j))
    o1 = t1[(k1 + 2) % 3]
    k2 = [(t2[k], t2[(k + 1) % 3]) for k in range(3)].index((j, i))
    o2 = t2[(k2 + 2) % 3]
    # face 1 (i -> j): split with a ; face 2 (j -> i): split with b
    faces += [[i, a, o1], [a, j, o1], [j, b, o2], [b, i, o2]]
    # zero-area sliver pair closing the gap between a and b along the edge
    faces += [[a, i, b], [b, j, a]]
    mesh = trimesh.Trimesh(vertices=v, faces=np.array(faces), process=False, validate=False)
    assert _closed_consistent(np.asarray(mesh.faces))
    return mesh


def test_sliver_fixture_breaks_position_merge():
    mesh = _box_with_sliver()
    assert check_mesh(mesh).watertight                  # index topology is closed
    merged = trimesh.Trimesh(mesh.vertices, mesh.faces)  # merge by position
    assert not merged.is_watertight


def test_weld_for_export_fixes_and_reports():
    mesh = _box_with_sliver()
    before_v = mesh.vertices.copy()
    txt, v, f, rep = weld_for_export(mesh, "box")
    assert rep.applied and rep.vertices_merged == 1 and rep.faces_removed == 2
    assert "box" in rep.describe()
    assert np.array_equal(mesh.vertices, before_v)        # input untouched
    w = trimesh.Trimesh(v, f, process=False)
    assert _closed_consistent(f) and w.volume == pytest.approx(1000.0, rel=1e-9)
    assert len(txt) == len(v)


def test_export_3mf_welds_and_reader_sees_watertight(tmp_path):
    mesh = _box_with_sliver()
    p = tmp_path / "s.3mf"
    er = export_3mf(p, mesh, model_name="box")
    assert validate_export(p) == []
    assert len(er.notes) == 1 and "merged 1 vertices" in er.notes[0]
    g = list(trimesh.load(str(p)).geometry.values())[0]
    assert g.is_watertight and g.volume == pytest.approx(1000.0, rel=1e-6)
    assert check_mesh(read_3mf(p).parts[0].mesh).watertight


def test_export_3mf_weld_disabled_keeps_problem(tmp_path):
    p = tmp_path / "raw.3mf"
    er = export_3mf(p, _box_with_sliver(), model_name="box", weld=False)
    assert er.notes == []
    g = list(trimesh.load(str(p)).geometry.values())[0]
    assert not g.is_watertight                           # the reason the weld exists


def test_weld_refuses_when_it_would_create_nonmanifold():
    """Two cubes touching along an edge, kept as separate vertices: welding would make a
    non-manifold edge, so the mesh must be written unchanged with an explanation."""
    a = trimesh.creation.box((1, 1, 1))
    b = trimesh.creation.box((1, 1, 1))
    b.apply_translation((1, 1, 0))
    m = trimesh.Trimesh(np.vstack([a.vertices, b.vertices]),
                        np.vstack([a.faces, b.faces + len(a.vertices)]), process=False)
    txt, v, f, rep = weld_for_export(m, "touch")
    assert not rep.applied and "non-manifold" in rep.reason
    assert len(v) == len(m.vertices) and np.array_equal(f, m.faces)


def test_weld_skips_open_mesh():
    m = _box_with_sliver()
    open_mesh = trimesh.Trimesh(m.vertices, m.faces[1:], process=False)
    _, v, f, rep = weld_for_export(open_mesh, "open")
    assert not rep.applied and "not closed" in rep.reason
    assert len(f) == len(open_mesh.faces)


def test_no_coincident_vertices_is_noop(tmp_path):
    box = trimesh.creation.box((5, 5, 5))
    _, v, f, rep = weld_for_export(box)
    assert not rep.applied and np.array_equal(f, box.faces)
    er = export_3mf(tmp_path / "b.3mf", box)
    assert er.notes == []


def test_export_stl_weld(tmp_path):
    mesh = _box_with_sliver()
    w, rep = weld_for_stl(mesh, "box")
    assert rep.applied and len(w.faces) == len(mesh.faces) - 2
    notes = []
    p = export_stl([mesh], tmp_path / "w.stl", notes=notes, names=["box"])
    assert len(notes) == 1 and notes[0].startswith("box:")
    assert trimesh.load(str(p)).is_watertight
    p2 = export_stl(mesh, tmp_path / "raw.stl", weld=False)
    assert not trimesh.load(str(p2)).is_watertight


def test_weld_on_real_support_mesh(tmp_path):
    """End to end on the engine output (shelf), the case that failed before the weld."""
    from arbor.engine import pipeline
    from arbor.engine.params import load_profiles
    from tests.models import synthetic as S
    m = S.shelf()
    res = pipeline.generate(m, load_profiles()["nozzle_04_normal"].params)
    sup = res.support_mesh
    p = tmp_path / "shelf.3mf"
    er = export_3mf(p, m, sup)
    assert validate_export(p) == []
    geoms = list(trimesh.load(str(p)).geometry.values())
    assert all(g.is_watertight for g in geoms)
    assert sum(g.volume for g in geoms) == pytest.approx(m.volume + sup.volume, rel=1e-6)


# ---------------------------------------------------------------------------
# layout="separate" (OrcaSlicer / Bambu Studio: separate objects, each on the plate)
# ---------------------------------------------------------------------------
import xml.etree.ElementTree as ET
import zipfile

from arbor.io.export import BBS_MODEL_CONFIG

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def _scene(on_model_shell=True):
    """Model box 0..10 on the plate, raft 0..0.8, a support trunk standing on the raft
    and touching the model, and (optionally) a small support shell standing on the
    model (its foot at z = 4, on a ledge)."""
    model = trimesh.creation.box((10, 10, 10))
    model.apply_translation((0, 0, 5))
    raft = trimesh.creation.box((20, 20, 0.8))
    raft.apply_translation((0, 0, 0.4))
    trunk = trimesh.creation.cylinder(1.0, 6.0, sections=16)
    trunk.apply_translation((8, 0, 3.8))              # 0.8 .. 6.8, beside the model
    sup = [trunk]
    if on_model_shell:
        twig = trimesh.creation.cylinder(0.5, 3.0, sections=12)
        twig.apply_translation((-8, 0, 5.5))          # 4 .. 7: floats above plate and raft
        sup.append(twig)
    s = trimesh.util.concatenate(sup)
    return model, s, raft


def _objects(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
        names = z.namelist()
        cfg = z.read(BBS_MODEL_CONFIG) if BBS_MODEL_CONFIG in names else None
        ct = z.read("[Content_Types].xml").decode()
    objs = {o.get("id"): o.get("name") for o in root.iter(CORE + "object")}
    items = [i.get("objectid") for i in root.iter(CORE + "item")]
    return objs, items, cfg, names, ct


def test_separate_layout_every_object_on_plate(tmp_path):
    model, sup, raft = _scene()
    p = tmp_path / "sep.3mf"
    er = export_3mf(p, model, sup, raft, model_name="fig", layout="separate")
    assert validate_export(p) == []
    assert er.layout == "separate" and er.objects == ["fig", "fig supports + raft"]
    objs, items, cfg, names, ct = _objects(p)
    assert len(objs) == 2 and items == ["1", "2"]
    assert cfg is None and "config" not in ct          # no Orca metadata unless asked
    assert any("1 of 2 support shells stand only on the model" in n for n in er.notes)
    # read back: two parts, both resting on the plate (z min = 0 after the offset)
    parts = {pt.name: pt.mesh for pt in read_3mf(p).parts}
    assert set(parts) == {"fig", "fig supports + raft"}
    for name, m in parts.items():
        assert m.bounds[0][2] == pytest.approx(0.0, abs=1e-6), name
    # no triangle lost or changed: volumes and bounds of the whole scene
    assert sum(abs(m.volume) for m in parts.values()) == pytest.approx(
        model.volume + sup.volume + raft.volume, rel=1e-6)
    # the shell standing on the model went with the model; trunk + raft with supports
    assert parts["fig"].bounds[0][0] == pytest.approx(-8.5 + er.offset[0], abs=1e-6)   # twig
    assert parts["fig"].bounds[0][2] == pytest.approx(0.0, abs=1e-6)
    assert parts["fig supports + raft"].bounds[0][0] == pytest.approx(-10 + er.offset[0], abs=1e-6)
    # trimesh read-back (merges by position): every object watertight
    geoms = list(trimesh.load(str(p)).geometry.values())
    assert len(geoms) == 2 and all(g.is_watertight for g in geoms)


def test_separate_layout_layer_heights_metadata(tmp_path):
    model, sup, raft = _scene()
    p = tmp_path / "lh.3mf"
    er = export_3mf(p, model, sup, raft, model_name="fig", layout="separate",
                    layer_heights={"model": 0.12, "supports": 0.24})
    assert validate_export(p) == []
    objs, items, cfg, names, ct = _objects(p)
    assert names == ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", BBS_MODEL_CONFIG]
    assert 'Extension="config"' in ct
    root = ET.fromstring(cfg)
    got = {}
    for o in root.findall("object"):
        md = {m.get("key"): m.get("value") for m in o.findall("metadata")}
        part = o.find("part")
        assert part.get("id") == o.get("id") and part.get("subtype") == "normal_part"
        assert objs[o.get("id")] == md["name"]          # ids match the 3dmodel objects
        assert md["extruder"] == "1"
        got[md["name"]] = float(md["layer_height"])
    assert got == {"fig": 0.12, "fig supports + raft": 0.24}
    assert any("per-object layer height" in n for n in er.notes)
    # only one key: the other object gets no layer_height
    p2 = tmp_path / "lh1.3mf"
    export_3mf(p2, model, sup, raft, model_name="fig", layout="separate",
               layer_heights={"supports": 0.3})
    root = ET.fromstring(_objects(p2)[2])
    lhs = {o.find("metadata").get("value"): [m.get("value") for m in o.findall("metadata")
                                             if m.get("key") == "layer_height"]
           for o in root.findall("object")}
    assert lhs == {"fig": [], "fig supports + raft": ["0.3"]}


def test_separate_layout_lib3mf_strict(tmp_path):
    lib3mf = pytest.importorskip("lib3mf")
    model, sup, raft = _scene()
    p = tmp_path / "lh.3mf"
    export_3mf(p, model, sup, raft, model_name="fig", layout="separate",
               layer_heights={"model": 0.12, "supports": 0.24})
    w = lib3mf.get_wrapper()
    m = w.CreateModel()
    r = m.QueryReader("3mf")
    r.SetStrictModeActive(True)
    r.ReadFromFile(str(p))
    assert r.GetWarningCount() == 0, [r.GetWarning(i) for i in range(r.GetWarningCount())]
    it, got = m.GetMeshObjects(), {}
    while it.MoveNext():
        o = it.GetCurrentMeshObject()
        assert o.IsManifoldAndOriented(), o.GetName()
        got[o.GetName()] = o.GetTriangleCount()
    assert set(got) == {"fig", "fig supports + raft"}
    bi, n = m.GetBuildItems(), 0
    while bi.MoveNext():
        n += 1
    assert n == 2


def test_separate_layout_without_raft_or_supports(tmp_path):
    model, sup, raft = _scene(on_model_shell=False)
    sup.apply_translation((0, 0, -0.8))                # trunk now stands on the plate
    er = export_3mf(tmp_path / "a.3mf", model, sup, None, model_name="m", layout="separate")
    assert er.objects == ["m", "m supports"] and er.notes == []
    er = export_3mf(tmp_path / "b.3mf", model, None, None, model_name="m", layout="separate")
    assert er.objects == ["m"]
    er = export_3mf(tmp_path / "c.3mf", model, None, raft, model_name="m", layout="separate")
    assert er.objects == ["m", "m raft"]
    for f in ("a", "b", "c"):
        assert validate_export(tmp_path / f"{f}.3mf") == []


def test_separate_layout_floating_model_becomes_one_object(tmp_path):
    """A model lifted above the plate (resting only on its supports) cannot be a
    separate object: it would be dropped (alignment lost) or refused by OrcaSlicer
    (empty first layer). Everything is written as one object, with a note."""
    model, sup, raft = _scene(on_model_shell=False)
    model.apply_translation((0, 0, 2.0))
    er = export_3mf(tmp_path / "f.3mf", model, sup, raft, model_name="m", layout="separate",
                    layer_heights={"model": 0.1, "supports": 0.2})
    assert er.objects == ["m with supports"]
    assert any("does not reach the plate" in n for n in er.notes)
    root = ET.fromstring(_objects(tmp_path / "f.3mf")[2])
    (o,) = root.findall("object")
    assert {m.get("key"): m.get("value") for m in o.findall("metadata")}["layer_height"] == "0.1"


def test_layer_heights_need_separate_layout(tmp_path):
    model, sup, raft = _scene()
    with pytest.raises(ValueError, match="separate"):
        export_3mf(tmp_path / "x.3mf", model, sup, raft, layer_heights={"model": 0.1})
    with pytest.raises(ValueError, match="unknown key"):
        export_3mf(tmp_path / "x.3mf", model, sup, raft, layout="separate", layer_heights={"raft": 0.1})
    with pytest.raises(ValueError, match=r"\(0, 2\]"):
        export_3mf(tmp_path / "x.3mf", model, sup, raft, layout="separate", layer_heights={"model": 0})
    with pytest.raises(ValueError, match="layout"):
        export_3mf(tmp_path / "x.3mf", model, layout="orca")
    assert not (tmp_path / "x.3mf").exists()


def test_separate_layout_on_engine_output(tmp_path):
    """Real engine output with a raft (shelf): every object on the plate, volumes kept."""
    from arbor.engine import pipeline
    from arbor.engine.params import load_profiles
    from tests.models import synthetic as S
    m = S.shelf()
    params = load_profiles()["nozzle_04_normal"].params.updated(raft_enabled=True)
    res = pipeline.generate(m, params)
    assert res.raft_mesh is not None and len(res.raft_mesh.faces)
    p = tmp_path / "shelf_sep.3mf"
    er = export_3mf(p, m, res.support_mesh, res.raft_mesh, params, model_name="shelf",
                    layout="separate", layer_heights={"model": 0.12, "supports": 0.24})
    assert validate_export(p) == []
    parts = read_3mf(p).parts
    assert 1 <= len(parts) <= 2
    for pt in parts:
        assert pt.mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-6), pt.name
        assert check_mesh(pt.mesh).watertight, pt.name
    total = m.volume + res.support_mesh.volume + res.raft_mesh.volume
    assert sum(abs(pt.mesh.volume) for pt in parts) == pytest.approx(total, rel=1e-5)


def test_light_slicer_supports_only_on_supports_object(tmp_path):
    """'Lighter supports' settings go on the supports object only, never on the model."""
    import xml.etree.ElementTree as ET
    from arbor.engine import pipeline
    from arbor.engine.params import load_profiles
    from tests.models import synthetic as S
    m = S.shelf()
    # coverage "feet": the model stands on the plate, so model and supports can be two
    # objects (with the model on the raft everything is one object, see the test below)
    p = load_profiles()["nozzle_04_normal"].params.updated(light_slicer_supports=True,
                                                           raft_coverage="feet")
    res = pipeline.generate(m, p)
    path = tmp_path / "light.3mf"
    er = export_3mf(path, m, res.support_mesh, res.raft_mesh, p, model_name="shelf",
                    layout="separate")
    assert any("Lighter supports" in n for n in er.notes)
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Metadata/model_settings.config"))
    per_obj = {o.find("metadata[@key='name']").get("value"):
               {md.get("key"): md.get("value") for md in o.findall("metadata")}
               for o in root.findall("object")}
    sup = [v for k, v in per_obj.items() if "supports" in k]
    mod = [v for k, v in per_obj.items() if "supports" not in k]
    assert sup and sup[0]["sparse_infill_density"] == "10%" and sup[0]["wall_loops"] == "2"
    assert mod and "sparse_infill_density" not in mod[0]
    # off by default: no per-object file at all for a plain export
    p0 = load_profiles()["nozzle_04_normal"].params
    export_3mf(tmp_path / "plain.3mf", m, res.support_mesh, res.raft_mesh, p0, layout="separate")
    with zipfile.ZipFile(tmp_path / "plain.3mf") as z:
        assert "Metadata/model_settings.config" not in z.namelist()


def test_light_supports_reported_when_model_rests_on_raft(tmp_path):
    """Model on the raft (default coverage): one object, so the lighter-supports settings
    cannot be applied; the export says so instead of ignoring them silently."""
    from arbor.engine import pipeline
    from arbor.engine.params import load_profiles
    from tests.models import synthetic as S
    m = S.shelf()
    p = load_profiles()["nozzle_04_normal"].params.updated(light_slicer_supports=True)
    assert p.raft_enabled and p.raft_coverage == "footprint"
    res = pipeline.generate(m, p)
    er = export_3mf(tmp_path / "one.3mf", m, res.support_mesh, res.raft_mesh, p,
                    model_name="shelf", layout="separate")
    assert any("Lighter supports not applied" in n for n in er.notes), er.notes

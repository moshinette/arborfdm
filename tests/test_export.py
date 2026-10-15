import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh

from arbor.core.mesh_io import CORE_NS, load_mesh, read_3mf
from arbor.core.repair import check_mesh
from arbor.engine.params import SupportParams
from arbor.io.export import export_3mf, export_stl, read_3mf_back, validate_export


def _scene():
    model = trimesh.creation.icosphere(subdivisions=3, radius=8)
    model.apply_translation((0, 0, 14))
    sup = trimesh.creation.cylinder(radius=1.0, height=5.5, sections=12)
    sup.apply_translation((0, 0, 2.75))
    raft = trimesh.creation.box((20, 20, 0.8))
    raft.apply_translation((0, 0, -0.4))
    return model, sup, raft


def _volumes(scene):
    return sorted(round(float(g.volume), 4) for g in scene.geometry.values())


@pytest.mark.parametrize("layout", ["objects", "components"])
def test_3mf_roundtrip(tmp_path, layout):
    model, sup, raft = _scene()
    p = tmp_path / f"out_{layout}.3mf"
    res = export_3mf(p, model, sup, raft, params=SupportParams(), model_name="Orc", layout=layout)
    assert res.objects == ["Orc", "Orc supports", "Orc raft"]
    assert validate_export(p) == []

    # 1) trimesh reader
    sc = trimesh.load(p)
    assert isinstance(sc, trimesh.Scene)
    expected_bounds = np.vstack([np.min([m.bounds[0] for m in (model, sup, raft)], 0),
                                 np.max([m.bounds[1] for m in (model, sup, raft)], 0)]) + res.offset
    assert np.allclose(sc.bounds, expected_bounds, atol=1e-5)
    assert _volumes(sc) == pytest.approx(sorted(round(float(m.volume), 4) for m in (model, sup, raft)), rel=1e-5)
    assert np.all(sc.bounds[0] >= -1e-9)        # positive octant

    # 2) our reader
    loaded, content = read_3mf_back(p)
    assert loaded.units_detected == "millimeter"
    assert [x.name for x in content.parts] == ["Orc", "Orc supports", "Orc raft"]
    assert content.metadata["Application"] == "ArborFDM"
    assert json.loads(content.metadata["arbor:support_params"])["tip_diameter"] == SupportParams().tip_diameter
    for part, orig in zip(content.parts, (model, sup, raft)):
        r = check_mesh(part.mesh)
        assert r.watertight and r.volume == pytest.approx(orig.volume, rel=1e-5)
        assert np.allclose(part.mesh.bounds, orig.bounds + res.offset, atol=1e-5)
    assert np.allclose(loaded.mesh.bounds, expected_bounds, atol=1e-5)


def test_3mf_structure(tmp_path):
    model, sup, _ = _scene()
    p = tmp_path / "s.3mf"
    export_3mf(p, model, sup, None)
    with zipfile.ZipFile(p) as z:
        assert set(z.namelist()) == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    assert root.get("unit") == "millimeter"
    objs = root.findall(f"{{{CORE_NS}}}resources/{{{CORE_NS}}}object")
    assert len(objs) == 2 and all(o.get("type") == "model" for o in objs)
    assert len(root.findall(f"{{{CORE_NS}}}build/{{{CORE_NS}}}item")) == 2


def test_model_only_and_input_unchanged(tmp_path):
    model, _, _ = _scene()
    v0 = model.vertices.copy()
    p = tmp_path / "m.3mf"
    res = export_3mf(p, model, None, None)
    assert res.objects == ["model"]
    assert np.array_equal(model.vertices, v0)
    assert validate_export(p) == []
    assert len(load_mesh(p).mesh.faces) == len(model.faces)


def test_export_errors(tmp_path):
    with pytest.raises(ValueError):
        export_3mf(tmp_path / "e.3mf", trimesh.Trimesh(), None, None)
    with pytest.raises(ValueError):
        export_3mf(tmp_path / "e.3mf", _scene()[0], layout="bad")


def test_validate_detects_problems(tmp_path):
    p = tmp_path / "bad.3mf"
    p.write_bytes(b"not a zip")
    assert validate_export(p)
    p2 = tmp_path / "bad2.3mf"
    with zipfile.ZipFile(p2, "w") as z:
        z.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
        z.writestr("_rels/.rels",
                   "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
                   "<Relationship Target='/3D/3dmodel.model' Id='r' "
                   "Type='http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel'/></Relationships>")
        z.writestr("3D/3dmodel.model",
                   f"<model unit='inch' xmlns='{CORE_NS}'><resources><object id='1' type='model'><mesh>"
                   "<vertices><vertex x='0' y='0' z='0'/><vertex x='1' y='0' z='0'/></vertices>"
                   "<triangles><triangle v1='0' v2='1' v3='5'/></triangles></mesh></object></resources>"
                   "<build><item objectid='9'/></build></model>")
    probs = validate_export(p2)
    text = " | ".join(probs)
    assert "content type" in text
    assert "unit" in text
    assert "out of range" in text
    assert "unknown object" in text


def test_stl_export(tmp_path):
    model, sup, raft = _scene()
    p = tmp_path / "all.stl"
    export_stl([model, sup, raft], p)
    assert p.stat().st_size == 84 + 50 * (len(model.faces) + len(sup.faces) + len(raft.faces))
    m = load_mesh(p)
    r = check_mesh(m.mesh)
    assert r.n_components == 3 and r.watertight
    assert r.volume == pytest.approx(model.volume + sup.volume + raft.volume, rel=1e-4)
    t = trimesh.load(p)
    assert np.allclose(t.bounds, m.mesh.bounds)
    with pytest.raises(ValueError):
        export_stl([], tmp_path / "x.stl")

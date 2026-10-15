import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import trimesh

from arbor.core.mesh_io import (MeshLoadError, format_3mf_matrix, load_mesh,
                                merge_identical_vertices, parse_3mf_matrix)
from arbor.core.repair import check_mesh

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def _box(extents=(10, 20, 30)):
    return trimesh.creation.box(extents=extents)


def test_stl_binary_roundtrip(tmp_path):
    b = _box()
    p = tmp_path / "b.stl"
    p.write_bytes(trimesh.exchange.stl.export_stl(b))
    m = load_mesh(p)
    assert m.file_format == "stl_binary"
    assert len(m.mesh.faces) == 12 and len(m.mesh.vertices) == 8
    assert m.units_detected is None
    assert any("millimetres assumed" in w for w in m.warnings)
    r = check_mesh(m.mesh)
    assert r.watertight and r.winding_consistent
    assert r.volume == pytest.approx(6000, rel=1e-6)


def test_stl_ascii_roundtrip(tmp_path):
    b = _box()
    p = tmp_path / "a.stl"
    p.write_text(trimesh.exchange.stl.export_stl_ascii(b))
    m = load_mesh(p)
    assert m.file_format == "stl_ascii"
    assert len(m.mesh.vertices) == 8
    assert check_mesh(m.mesh).volume == pytest.approx(6000, rel=1e-5)


def test_stl_binary_starting_with_solid(tmp_path):
    b = _box()
    data = bytearray(trimesh.exchange.stl.export_stl(b))
    data[:80] = b"solid but actually binary".ljust(80, b" ")
    p = tmp_path / "tricky.stl"
    p.write_bytes(bytes(data))
    m = load_mesh(p)
    assert m.file_format == "stl_binary" and len(m.mesh.faces) == 12


def test_obj_roundtrip(tmp_path):
    b = _box()
    p = tmp_path / "b.obj"
    p.write_text(trimesh.exchange.obj.export_obj(b))
    m = load_mesh(p)
    assert len(m.mesh.faces) == 12
    assert check_mesh(m.mesh).volume == pytest.approx(6000, rel=1e-6)


def test_no_silent_repair(tmp_path):
    """A broken STL must load as is (no face removed, no hole filled)."""
    b = _box()
    faces = np.vstack([b.faces[1:], b.faces[:1], b.faces[:1]])   # remove nothing, add duplicate
    faces = faces[1:]                                              # drop one face -> hole
    broken = trimesh.Trimesh(b.vertices, faces, process=False)
    p = tmp_path / "broken.stl"
    p.write_bytes(trimesh.exchange.stl.export_stl(broken))
    m = load_mesh(p)
    assert len(m.mesh.faces) == len(faces)
    r = check_mesh(m.mesh)
    assert not r.watertight and r.n_duplicate_faces == 1


def test_merge_identical_vertices_lossless():
    tri = np.random.default_rng(0).random((50, 3, 3))
    v = tri.reshape(-1, 3)
    f = np.arange(len(v)).reshape(-1, 3)
    v2 = np.vstack([v, v[:10]])
    nv, nf = merge_identical_vertices(v2, f)
    assert np.array_equal(nv[nf], tri)


def _write_3mf(path, model_xml):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="r0" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model_xml)


def _mesh_xml(m):
    vs = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in m.vertices)
    ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in m.faces)
    return f"<mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh>"


def test_matrix_roundtrip():
    m = np.eye(4)
    m[:3, :3] = trimesh.transformations.rotation_matrix(0.3, [1, 2, 3])[:3, :3] * 2
    m[:3, 3] = [1, 2, 3]
    assert np.allclose(parse_3mf_matrix(format_3mf_matrix(m)), m)
    # spec example: translation in last row
    t = parse_3mf_matrix("1 0 0 0 1 0 0 0 1 5 6 7")
    assert np.allclose(t[:3, 3], [5, 6, 7])


def test_3mf_inch_with_transforms_and_components(tmp_path):
    cube = trimesh.creation.box(extents=(1, 1, 1))     # 1 inch cube
    xml = (f'<?xml version="1.0"?><model unit="inch" xmlns="{CORE}">'
           f'<metadata name="Title">Inch test</metadata><resources>'
           f'<object id="1" name="cubeA" type="model">{_mesh_xml(cube)}</object>'
           f'<object id="2" name="asm"><components>'
           f'<component objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 2"/></components></object>'
           f'</resources><build>'
           f'<item objectid="1" transform="2 0 0 0 2 0 0 0 2 10 0 0"/>'
           f'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
           f'</build></model>')
    p = tmp_path / "inch.3mf"
    _write_3mf(p, xml)
    m = load_mesh(p)
    assert m.units_detected == "inch"
    assert m.object_names == ["cubeA", "cubeA"]
    assert m.name == "Inch test"
    assert any("inch" in w for w in m.warnings)
    r = check_mesh(m.mesh)
    # 2x scaled cube (8 in^3) + unit cube (1 in^3), in mm^3
    assert r.volume == pytest.approx(9 * 25.4 ** 3, rel=1e-6)
    assert r.n_components == 2
    assert np.allclose(r.bbox[0], np.array([-0.5, -1, -1]) * 25.4)
    assert np.allclose(r.bbox[1], np.array([11, 1, 2.5]) * 25.4)


def test_3mf_mirror_transform_keeps_outward_normals(tmp_path):
    cube = _box((1, 2, 3))
    xml = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}"><resources>'
           f'<object id="1" type="model">{_mesh_xml(cube)}</object></resources>'
           f'<build><item objectid="1" transform="-1 0 0 0 1 0 0 0 1 0 0 0"/></build></model>')
    p = tmp_path / "mirror.3mf"
    _write_3mf(p, xml)
    m = load_mesh(p)
    assert check_mesh(m.mesh).volume == pytest.approx(6.0)


@pytest.mark.parametrize("content,kind", [
    (b"", "empty"),
    (b"not a mesh at all", "garbage"),
])
def test_bad_files(tmp_path, content, kind):
    for ext in (".stl", ".3mf", ".obj"):
        p = tmp_path / f"{kind}{ext}"
        p.write_bytes(content)
        with pytest.raises(MeshLoadError):
            load_mesh(p)


def test_missing_and_unsupported(tmp_path):
    with pytest.raises(MeshLoadError):
        load_mesh(tmp_path / "nope.stl")
    p = tmp_path / "x.ply"
    p.write_text("ply")
    with pytest.raises(MeshLoadError):
        load_mesh(p)


def test_truncated_binary_stl(tmp_path):
    data = trimesh.exchange.stl.export_stl(_box())
    p = tmp_path / "trunc.stl"
    p.write_bytes(data[:-30])
    with pytest.raises(MeshLoadError):
        load_mesh(p)


def test_3mf_bad_index_and_no_build(tmp_path):
    p = tmp_path / "badidx.3mf"
    _write_3mf(p, f'<model xmlns="{CORE}"><resources><object id="1"><mesh><vertices>'
                  '<vertex x="0" y="0" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/>'
                  '</triangles></mesh></object></resources><build><item objectid="1"/></build></model>')
    with pytest.raises(MeshLoadError, match="out of range"):
        load_mesh(p)
    p2 = tmp_path / "nobuild.3mf"
    _write_3mf(p2, f'<model xmlns="{CORE}"><resources></resources><build></build></model>')
    with pytest.raises(MeshLoadError):
        load_mesh(p2)


def test_scale_warning_small_model(tmp_path):
    p = tmp_path / "tiny.stl"
    p.write_bytes(trimesh.exchange.stl.export_stl(_box((0.5, 0.5, 0.5))))
    m = load_mesh(p)
    assert any("Check the scale" in w for w in m.warnings)


@pytest.mark.slow
def test_large_mesh_load_and_check(tmp_path):
    s = trimesh.creation.icosphere(subdivisions=8, radius=30)     # 1,310,720 faces
    p = tmp_path / "big.stl"
    p.write_bytes(trimesh.exchange.stl.export_stl(s))
    t0 = time.perf_counter()
    m = load_mesh(p)
    t1 = time.perf_counter()
    r = check_mesh(m.mesh)
    t2 = time.perf_counter()
    print(f"\n[large mesh] {len(m.mesh.faces)} faces: load {t1 - t0:.2f} s, check {t2 - t1:.2f} s")
    assert len(m.mesh.faces) == len(s.faces)
    assert r.watertight and r.winding_consistent and r.n_components == 1

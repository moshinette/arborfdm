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

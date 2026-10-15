import numpy as np
import pytest
import trimesh

from arbor.core.repair import check_mesh, repair_mesh


def _raw(v, f):
    return trimesh.Trimesh(vertices=np.asarray(v, float), faces=np.asarray(f, np.int64),
                           process=False, validate=False)


def _sphere():
    s = trimesh.creation.icosphere(subdivisions=2, radius=10)
    return _raw(s.vertices, s.faces)


def test_clean_mesh():
    r = check_mesh(_sphere())
    assert r.watertight and r.winding_consistent and not r.inverted
    assert r.n_boundary_edges == r.n_holes == r.n_degenerate_faces == r.n_duplicate_faces == 0
    assert r.n_non_manifold_edges == 0 and r.n_components == 1
    assert r.is_valid and r.volume > 0
    assert r.bbox.shape == (2, 3)


def test_check_does_not_mutate():
    s = _sphere()
    f0 = s.faces.copy()
    s2 = _raw(s.vertices, np.vstack([s.faces[3:], s.faces[:1]]))
    v0 = s2.vertices.copy()
    check_mesh(s2)
    repair_mesh(s2)
    assert np.array_equal(s2.vertices, v0) and len(s2.faces) == len(f0) - 2
    assert np.array_equal(s.faces, f0)


def test_holes_detected_and_filled():
    s = _sphere()
    # remove two separated faces -> two triangular holes
    adj = set(np.unique(s.faces[0]))
    far = next(i for i in range(len(s.faces)) if not adj & set(s.faces[i]))
    keep = np.ones(len(s.faces), bool)
    keep[[0, far]] = False
    broken = _raw(s.vertices, s.faces[keep])
    r = check_mesh(broken)
    assert not r.watertight and r.n_holes == 2 and r.n_boundary_edges == 6
    assert r.volume is None and not r.is_valid
    fixed, rep = repair_mesh(broken)
    assert rep.success, rep.summary()
    assert rep.counts["holes_filled"] == 2
    assert rep.after.watertight and rep.after.volume == pytest.approx(check_mesh(s).volume, rel=1e-9)
    assert fixed is not broken


def test_large_hole_fan_fill():
    s = _sphere()
    top = s.vertices[s.faces].mean(1)[:, 2] > 8.5      # cap of several faces
    broken = _raw(s.vertices, s.faces[~top])
    r = check_mesh(broken)
    assert r.n_holes == 1 and r.n_boundary_edges > 3
    fixed, rep = repair_mesh(broken)
    assert rep.success and rep.after.watertight and rep.after.winding_consistent
    assert rep.after.volume > 0
    # refuse to fill when hole larger than limit
    _, rep2 = repair_mesh(broken, max_hole_edges=3)
    assert not rep2.success and rep2.counts.get("holes_skipped") == 1
    assert any("not filled" in m for m in rep2.failure_reasons)


def test_flipped_faces():
    s = _sphere()
    f = s.faces.copy()
    f[:10] = f[:10, ::-1]
    broken = _raw(s.vertices, f)
    r = check_mesh(broken)
    assert not r.winding_consistent and r.n_inconsistent_edges > 0
    assert any(p.code == "winding" for p in r.problems)
    fixed, rep = repair_mesh(broken)
    assert rep.success and rep.counts["faces_flipped"] == 10
    assert rep.after.winding_consistent and rep.after.volume > 0


def test_inverted_mesh():
    s = _sphere()
    inv = _raw(s.vertices, s.faces[:, ::-1])
    r = check_mesh(inv)
    assert r.inverted and r.winding_consistent and r.volume < 0
    fixed, rep = repair_mesh(inv)
    assert rep.success and not rep.after.inverted and rep.after.volume > 0


def test_degenerate_and_duplicate_faces():
    s = _sphere()
    a, b, c = s.faces[0]
    extra = np.array([[a, a, b],                  # repeated index
                      [a, b, c], [c, a, b]])      # duplicates (any rotation)
    v = np.vstack([s.vertices, s.vertices[[a, b]].mean(0)])
    collinear = [[a, b, len(v) - 1]]              # zero area
    broken = _raw(v, np.vstack([s.faces, extra, collinear]))
    r = check_mesh(broken)
    assert r.n_degenerate_faces == 2
    assert r.n_duplicate_faces == 2
    fixed, rep = repair_mesh(broken)
    assert rep.counts["degenerate_removed"] == 2 and rep.counts["duplicates_removed"] == 2
    assert rep.success, rep.summary()
    after = check_mesh(fixed)
    assert after.n_degenerate_faces == 0 and after.n_duplicate_faces == 0 and after.watertight
    assert len(fixed.faces) == len(s.faces)


def test_non_manifold_reported():
    s = _sphere()
    a, b = s.faces[0][:2]
    v = np.vstack([s.vertices, [[30, 30, 30]]])
    f = np.vstack([s.faces, [[a, b, len(v) - 1]]])      # fin on an existing edge
    r = check_mesh(_raw(v, f))
    assert r.n_non_manifold_edges == 1 and not r.watertight
    _, rep = repair_mesh(_raw(v, f))
    assert not rep.success           # we do not pretend to fix this
    assert any("non-manifold" in m for m in rep.failure_reasons)


def test_components_and_manifold_union():
    a = trimesh.creation.box((10, 10, 10))
    b = trimesh.creation.box((10, 10, 10))
    b.apply_translation((5, 0, 0))
    m = _raw(np.vstack([a.vertices, b.vertices]), np.vstack([a.faces, b.faces + 8]))
    r = check_mesh(m)
    assert r.n_components == 2 and r.watertight
    fixed, rep = repair_mesh(m, union_shells=True)
    assert rep.success
    assert check_mesh(fixed).n_components == 1
    assert check_mesh(fixed).volume == pytest.approx(1500, rel=1e-5)


def test_empty_mesh():
    r = check_mesh(_raw(np.zeros((0, 3)), np.zeros((0, 3))))
    assert not r.is_valid and r.problems[0].code == "empty"
    _, rep = repair_mesh(_raw(np.zeros((0, 3)), np.zeros((0, 3))))
    assert not rep.success


def test_clean_mesh_repair_is_noop():
    s = _sphere()
    fixed, rep = repair_mesh(s)
    assert rep.success and not rep.changed
    assert np.array_equal(fixed.faces, s.faces) and np.array_equal(fixed.vertices, s.vertices)


def test_pinched_hole_and_mixed_flips():
    """Removing adjacent faces leaves a boundary that touches itself at a vertex."""
    s = _sphere()
    f = s.faces.copy()
    f[:100] = f[:100, ::-1]
    broken = _raw(s.vertices, f[5:])
    fixed, rep = repair_mesh(broken)
    assert rep.success, rep.summary()
    assert rep.after.watertight and rep.after.winding_consistent and rep.after.volume > 0
    assert check_mesh(fixed).n_non_manifold_edges == 0
    assert rep.counts["faces_flipped"] <= 100


@pytest.mark.slow
def test_large_mesh_repair_timing():
    import time
    s = trimesh.creation.icosphere(subdivisions=8, radius=30)       # 1,310,720 faces
    f = s.faces.copy()
    f[:1000] = f[:1000, ::-1]
    broken = _raw(s.vertices, f[5:])
    t0 = time.perf_counter()
    fixed, rep = repair_mesh(broken)
    dt = time.perf_counter() - t0
    print(f"\n[large repair] {len(broken.faces)} faces: {dt:.2f} s, {rep.actions}")
    assert rep.success and rep.after.volume == pytest.approx(s.volume, rel=1e-4)

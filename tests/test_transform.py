import numpy as np
import pytest
import trimesh

from arbor.core.repair import check_mesh
from arbor.core.transform import (ModelTransform, apply_transform, euler_xyz_from_matrix,
                                  lay_flat_on_face, place_on_plate, rotate,
                                  rotation_matrix_xyz, scale_matrix, scale_uniform)


def _box():
    return trimesh.creation.box((10, 20, 30))


def test_rotation_basic():
    r = rotation_matrix_xyz(0, 0, 90)
    assert np.allclose(r[:3, :3] @ [1, 0, 0], [0, 1, 0])
    r = rotation_matrix_xyz(90, 0, 0)
    assert np.allclose(r[:3, :3] @ [0, 1, 0], [0, 0, 1])
    # order: X first, then Z
    r = rotation_matrix_xyz(90, 0, 90)
    assert np.allclose(r[:3, :3] @ [0, 1, 0], rotation_matrix_xyz(0, 0, 90)[:3, :3] @ [0, 0, 1])


@pytest.mark.parametrize("angles", [(10, 20, 30), (-45, 80, 170), (0, 0, 0), (90, 0, -90)])
def test_euler_roundtrip(angles):
    m = rotation_matrix_xyz(*angles)
    back = euler_xyz_from_matrix(m)
    assert np.allclose(rotation_matrix_xyz(*back), m, atol=1e-9)


def test_apply_transform_is_pure_and_mirror_safe():
    b = _box()
    v0 = b.vertices.copy()
    m = apply_transform(b, np.diag([-1, 1, 1, 1]))
    assert np.array_equal(b.vertices, v0)
    assert check_mesh(m).volume == pytest.approx(6000)


def test_scale():
    b = _box()
    s = scale_uniform(b, 2.0)
    assert np.allclose(s.bounds[1] - s.bounds[0], [20, 40, 60])
    assert np.allclose(s.bounds.mean(0), b.bounds.mean(0))
    t = scale_uniform(b, target_size_mm=60)
    assert np.isclose((t.bounds[1] - t.bounds[0]).max(), 60)
    t = scale_uniform(b, target_size_mm=5, axis=0)
    assert np.isclose(t.bounds[1, 0] - t.bounds[0, 0], 5)
    with pytest.raises(ValueError):
        scale_matrix(0)
    with pytest.raises(ValueError):
        scale_uniform(b)


def test_place_on_plate():
    b = _box()
    b.apply_translation((7, -3, 12))
    p = place_on_plate(b)
    assert np.isclose(p.bounds[0, 2], 0)
    assert np.allclose(p.bounds.mean(0)[:2], 0)


def test_rotate_about_center():
    b = _box()
    r = rotate(b, 0, 0, 90)
    assert np.allclose(r.bounds[1] - r.bounds[0], [20, 10, 30])


def test_lay_flat_on_face():
    b = trimesh.creation.box((10, 20, 30))
    b = apply_transform(b, rotation_matrix_xyz(23, 41, 7))
    # find a face whose normal is the box's original +X
    fi = int(np.argmax(b.face_normals @ (rotation_matrix_xyz(23, 41, 7)[:3, :3] @ [1, 0, 0])))
    flat = lay_flat_on_face(b, fi)
    assert np.isclose(flat.bounds[0, 2], 0, atol=1e-9)
    n = flat.face_normals[fi]
    assert np.allclose(n, [0, 0, -1], atol=1e-9)
    assert np.isclose(flat.bounds[1, 2], 10, atol=1e-6)


def test_model_transform():
    t = ModelTransform((10, 20, 30), 1.5, (1, 2, 3))
    d = t.to_dict()
    t2 = ModelTransform.from_dict(d)
    assert np.allclose(t2.to_matrix(), t.to_matrix())
    t3 = ModelTransform.from_matrix(t.to_matrix())
    assert np.allclose(t3.to_matrix(), t.to_matrix())
    b = _box()
    placed = t.placed_on_plate(b).apply(b)
    assert np.isclose(placed.bounds[0, 2], 0) and np.allclose(placed.bounds.mean(0)[:2], 0)
    with pytest.raises(ValueError):
        ModelTransform.from_dict({"scale": -1})

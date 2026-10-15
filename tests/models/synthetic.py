"""Deterministic synthetic models for ArborFDM tests and benchmarks.

Every generator returns a trimesh.Trimesh in millimetres, Z up. Unless the model is
intentionally floating it rests on the build plate (min z == 0). Solids are built with
manifold3d booleans so they are watertight and consistently oriented. The broken_* models
are the exception: they carry deliberate defects.

Useful reference values (shelf bottom height, feature boxes, ...) are stored in
mesh.metadata so the tests never hard-code geometry twice.
"""
from __future__ import annotations

import math

import manifold3d as mf
import numpy as np
import trimesh

__all__ = [
    "cube", "sphere_on_plate", "floating_cube", "floating_sphere", "inclined_surface",
    "shelf", "bridge", "suspended_arm", "figurine", "cavity", "tiny_triangles",
    "broken_hole", "broken_pinched_hole", "broken_flipped", "broken_degenerate", "broken_nonmanifold",
    "large_mesh", "crossing_case", "close_contacts_case", "impossible_case",
    "refined", "GENERATORS", "BROKEN_GENERATORS",
]

Box = tuple[tuple[float, float, float], tuple[float, float, float]]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _box(lo, hi) -> mf.Manifold:
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    return mf.Manifold.cube(tuple(hi - lo)).translate(tuple(lo))


def _cyl_z(center_xy, z0, z1, r, seg=48, r_top=None) -> mf.Manifold:
    c = mf.Manifold.cylinder(z1 - z0, r, r if r_top is None else r_top, seg)
    return c.translate((center_xy[0], center_xy[1], z0))


def _cyl_x(x0, x1, y, z, r, seg=48) -> mf.Manifold:
    # cylinder built along +z, rotated +90 deg about y maps +z to +x
    return mf.Manifold.cylinder(x1 - x0, r, r, seg).rotate((0, 90, 0)).translate((x0, y, z))


def _cyl_y(y0, y1, x, z, r, seg=48) -> mf.Manifold:
    # rotate -90 deg about x maps +z to +y
    return mf.Manifold.cylinder(y1 - y0, r, r, seg).rotate((-90, 0, 0)).translate((x, y0, z))


def _sphere(c, r, seg=64) -> mf.Manifold:
    return mf.Manifold.sphere(r, seg).translate(tuple(c))


def _prism_xz(poly_xz, y0, y1) -> mf.Manifold:
    """Polygon given in the XZ plane, extruded along Y from y0 to y1."""
    cs = mf.CrossSection([np.asarray(poly_xz, float)])
    m = mf.Manifold.extrude(cs, y1 - y0)
    # (u, v, w) -> (u, -w, v): proper rotation (det = +1)
    mat = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0]], float)
    return m.transform(mat).translate((0, y1, 0))


def _prism_yz(poly_yz, x0, x1) -> mf.Manifold:
    """Polygon given in the YZ plane, extruded along X from x0 to x1."""
    cs = mf.CrossSection([np.asarray(poly_yz, float)])
    m = mf.Manifold.extrude(cs, x1 - x0)
    # (u, v, w) -> (w, u, v): cyclic permutation (det = +1)
    mat = np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], float)
    return m.transform(mat).translate((x0, 0, 0))


def _union(parts) -> mf.Manifold:
    return mf.Manifold.batch_boolean(list(parts), mf.OpType.Add)


def _to_trimesh(m: mf.Manifold, **meta) -> trimesh.Trimesh:
    if m.status() != mf.Error.NoError:
        raise RuntimeError(f"manifold error: {m.status()}")
    mg = m.to_mesh()
    v = np.asarray(mg.vert_properties, dtype=np.float64)[:, :3]
    f = np.asarray(mg.tri_verts, dtype=np.int64)
    t = trimesh.Trimesh(v, f, process=True)
    t.metadata.update(meta)
    return t


def refined(m: mf.Manifold, n_faces: int) -> mf.Manifold:
    """Refine a manifold so it has roughly n_faces triangles (never coarsens)."""
    if m.num_tri() >= n_faces:
        return m
    area = m.surface_area()
    # an equilateral triangle of edge l has area sqrt(3)/4 l^2
    length = math.sqrt(area / (0.433 * n_faces))
    out = m.refine_to_length(length)
    # one correction step (refinement of curved, already dense parts is not linear)
    ratio = out.num_tri() / n_faces
    if not 0.8 <= ratio <= 1.25:
        out = m.refine_to_length(length * math.sqrt(ratio))
    return out


# ---------------------------------------------------------------------------
# simple solids
# ---------------------------------------------------------------------------
def cube(size: float = 20.0) -> trimesh.Trimesh:
    """Cube centred on the origin in XY, resting on the plate. Needs no support."""
    h = size / 2
    return _to_trimesh(_box((-h, -h, 0), (h, h, size)), kind="cube", size=size)


def sphere_on_plate(radius: float = 10.0, plate: float = 2.0, sink: float = 2.0,
                    seg: int = 96) -> trimesh.Trimesh:
    """A sphere sitting on a thin square plate (30 % sunk into it).

    The lower hemisphere outside the plate contact circle overhangs.
    """
    w = radius * 1.5
    cz = plate + radius - sink
    m = _union([_box((-w, -w, 0), (w, w, plate)), _sphere((0, 0, cz), radius, seg)])
    return _to_trimesh(m, kind="sphere_on_plate", radius=radius, center_z=cz, plate=plate)


def floating_cube(size: float = 10.0, z: float = 10.0) -> trimesh.Trimesh:
    """Cube floating above the plate: a pure island (min z == z)."""
    h = size / 2
    return _to_trimesh(_box((-h, -h, z), (h, h, z + size)), kind="floating_cube",
                       island_z=z, size=size)


def floating_sphere(radius: float = 5.0, z_center: float = 12.0, seg: int = 64) -> trimesh.Trimesh:
    """Sphere floating above the plate: island reduced to a single low point."""
    return _to_trimesh(_sphere((0, 0, z_center), radius, seg), kind="floating_sphere",
                       low_point=(0.0, 0.0, z_center - radius))


def inclined_surface(angle_deg: float = 60.0, length: float = 20.0, depth: float = 10.0,
                     thickness: float = 4.0, foot: float = 6.0) -> trimesh.Trimesh:
    """Wedge rising from a foot block, with a flat sloped underside.

    angle_deg is the angle of the underside measured from the vertical (same convention as
    SupportParams.overhang_angle_deg): 0 = vertical wall, 90 = flat ceiling.
    The underside starts at the plate (x = 0, z = 0) and rises towards +x.
    """
    if not 5.0 <= angle_deg <= 85.0:
        raise ValueError("angle_deg must be in [5, 85]")
    a = math.radians(angle_deg)
    dx, dz = length * math.sin(a), length * math.cos(a)
    top = dz + thickness
    poly = [(-foot, 0.0), (0.0, 0.0), (dx, dz), (dx, top), (-foot, top)]
    m = _prism_xz(poly, -depth / 2, depth / 2)
    n = np.array([math.cos(a), 0.0, -math.sin(a)])  # outward normal of the underside
    return _to_trimesh(m, kind="inclined_surface", angle_deg=angle_deg,
                       underside_normal=tuple(n), underside_x=(0.0, dx), underside_z=(0.0, dz))


def shelf(wall_h: float = 30.0, shelf_z: float = 20.0, shelf_len: float = 15.0,
          shelf_t: float = 3.0, width: float = 20.0, wall_t: float = 4.0) -> trimesh.Trimesh:
    """Vertical wall with a horizontal shelf sticking out (+x). Shelf underside at shelf_z."""
    m = _union([
        _box((0, 0, 0), (wall_t, width, wall_h)),
        _box((wall_t - 0.5, 0, shelf_z), (wall_t + shelf_len, width, shelf_z + shelf_t)),
    ])
    return _to_trimesh(m, kind="shelf", shelf_bottom_z=shelf_z,
                       shelf_box=((wall_t, 0.0, shelf_z), (wall_t + shelf_len, width, shelf_z + shelf_t)),
                       wall_box=((0.0, 0.0, 0.0), (wall_t, width, wall_h)))


def bridge(span: float = 20.0, pillar: float = 5.0, height: float = 15.0,
           deck_t: float = 3.0, depth: float = 5.0) -> trimesh.Trimesh:
    """Two pillars and a deck. The deck underside between the pillars is a flat overhang."""
    x1 = pillar + span
    m = _union([
        _box((0, 0, 0), (pillar, depth, height)),
        _box((x1, 0, 0), (x1 + pillar, depth, height)),
        _box((0, 0, height), (x1 + pillar, depth, height + deck_t)),
    ])
    return _to_trimesh(m, kind="bridge", deck_bottom_z=height,
                       span_x=(pillar, x1))


def suspended_arm(body_r: float = 5.0, body_h: float = 30.0, arm_z: float = 22.0,
                  arm_len: float = 18.0, arm_r: float = 1.5, hand_r: float = 3.0) -> trimesh.Trimesh:
    """Cylindrical body, horizontal arm (+x) ending in a hanging hand sphere."""
    hx = body_r + arm_len
    hz = arm_z - 1.5
    m = _union([
        _cyl_z((0, 0), 0, body_h, body_r, 64),
        _cyl_x(body_r - 1, hx, 0, arm_z, arm_r, 32),
        _sphere((hx, 0, hz), hand_r, 48),
    ])
    return _to_trimesh(m, kind="suspended_arm", hand_low_point=(hx, 0.0, hz - hand_r),
                       arm_box=((body_r, -arm_r, arm_z - arm_r), (hx, arm_r, arm_z + arm_r)))


# ---------------------------------------------------------------------------
# procedural humanoid
# ---------------------------------------------------------------------------
def _figurine_manifold(seg: int = 64) -> tuple[mf.Manifold, dict]:
    parts = []
    # base disk
    parts.append(_cyl_z((0, 0), 0, 3, 15, seg * 2))
    # legs
    for sx in (-1, 1):
        parts.append(_cyl_z((sx * 4, 0), 2.5, 26, 2.5, seg))
    # torso: hull of hips and shoulders
    torso = mf.Manifold.batch_hull([_box((-6.5, -3, 25), (6.5, 3, 25.5)),
                                    _box((-8, -4, 44.5), (8, 4, 45))])
    parts.append(torso)
    # neck + head (chin overhang: head sphere wider than the neck)
    parts.append(_cyl_z((0, 0), 44.5, 50, 2.2, seg))
    head_c, head_r = (0.0, 0.0, 54.0), 6.0
    parts.append(_sphere(head_c, head_r, seg * 2))
    # arms out (T pose) and hands
    arm_z, arm_r = 42.0, 2.0
    for sx in (-1, 1):
        x0, x1 = (7.0, 20.0) if sx > 0 else (-20.0, -7.0)
        parts.append(_cyl_x(x0, x1, 0, arm_z, arm_r, seg))
        parts.append(_sphere((sx * 21, 0, arm_z), 2.6, seg))
    # sword held in the right hand, pointing forward (+y), flat blade
    parts.append(_cyl_y(-3, 3, 21, arm_z, 0.9, 24))             # grip through the hand
    parts.append(_box((18.5, 2.5, 41.0), (23.5, 3.5, 43.0)))     # cross guard
    blade = mf.Manifold.batch_hull([_box((19.5, 3.4, 41.6), (22.5, 3.5, 42.4)),
                                    _box((20.9, 21.9, 41.9), (21.1, 22.0, 42.1))])
    parts.append(blade)
    # cape: slab behind the torso, flaring backwards, free hanging bottom edge
    cape = _prism_yz([(-2.5, 46.0), (-4.5, 46.0), (-10.0, 18.0), (-8.0, 18.0)], -9.0, 9.0)
    parts.append(cape)
    m = _union(parts)
    features = {
        "right_arm": ((7.0, -2.0, 40.0), (23.6, 2.0, 44.0)),
        "left_arm": ((-23.6, -2.0, 40.0), (-7.0, 2.0, 44.0)),
        "chin": ((-6.0, -6.0, 48.0), (6.0, 6.0, 52.0)),
        "sword": ((19.5, 3.5, 41.0), (22.5, 22.0, 42.5)),
        "cape_bottom": ((-9.0, -10.0, 18.0), (9.0, -8.5, 19.0)),
    }
    return m, features


def figurine(n_faces: int = 40_000) -> trimesh.Trimesh:
    """Procedural humanoid (~20k-80k faces): base disk, legs, torso, T-pose arms, head with
    chin overhang, sword pointing forward and a cape with a hanging bottom edge."""
    m, features = _figurine_manifold()
    m = refined(m, n_faces)
    return _to_trimesh(m, kind="figurine", features=features)


def cavity(size: float = 20.0) -> trimesh.Trimesh:
    """Cube with a downward open pocket (arch) and a mouth slot in a side wall.

    pocket: cylinder open to the plate, flat ceiling at pocket_ceiling_z (reachable from plate)
    mouth: horizontal slot in the +x wall, its ceiling at mouth_ceiling_z can only be supported
           from the slot floor (on the model).
    """
    h = size / 2
    body = _box((-h, -h, 0), (h, h, size))
    pocket = _cyl_z((0, 0), -1, 8, 5, 64)
    mouth = _box((h - 6, -4, 12), (h + 1, 4, 15))
    m = body - pocket - mouth
    return _to_trimesh(m, kind="cavity", pocket_ceiling_z=8.0, mouth_ceiling_z=15.0,
                       mouth_floor_z=12.0, mouth_box=((h - 6, -4, 12), (h, 4, 15)))


def tiny_triangles(edge: float = 0.15) -> trimesh.Trimesh:
    """Small shelf (wall + ledge) densely subdivided into tiny triangles (edge ~ 0.15 mm)."""
    m = _union([_box((0, 0, 0), (3, 10, 12)), _box((2.5, 0, 8), (8, 10, 9.5))])
    m = m.refine_to_length(edge)
    return _to_trimesh(m, kind="tiny_triangles", shelf_bottom_z=8.0, edge=edge)


# ---------------------------------------------------------------------------
# broken meshes (deliberate defects, built with plain trimesh, process=False)
# ---------------------------------------------------------------------------
def _base_box_mesh() -> trimesh.Trimesh:
    b = trimesh.creation.box(extents=(20, 20, 20))
    b = b.subdivide().subdivide()
    b.apply_translation((0, 0, 10))
    return trimesh.Trimesh(b.vertices.copy(), b.faces.copy(), process=False)


def broken_hole() -> trimesh.Trimesh:
    """Box with the fan of faces around one top vertex removed (one simple hole loop)."""
    b = _base_box_mesh()
    top_v = np.where(np.isclose(b.vertices[:, 2], 20.0))[0]
    centre = top_v[np.argmin(np.linalg.norm(b.vertices[top_v, :2], axis=1))]
    fan = np.where((b.faces == centre).any(axis=1))[0]
    keep = np.setdiff1d(np.arange(len(b.faces)), fan)
    out = trimesh.Trimesh(b.vertices, b.faces[keep], process=False)
    out.metadata.update(kind="broken_hole", defect="hole", removed=int(len(fan)))
    return out


def broken_pinched_hole(n_removed: int = 6) -> trimesh.Trimesh:
    """Box with a few non contiguous top faces removed: the boundary is a single loop that
    touches itself at two vertices (figure-eight like, not a simple cycle)."""
    b = _base_box_mesh()
    top = np.where(b.face_normals[:, 2] > 0.9)[0][:n_removed]
    keep = np.setdiff1d(np.arange(len(b.faces)), top)
    out = trimesh.Trimesh(b.vertices, b.faces[keep], process=False)
    out.metadata.update(kind="broken_pinched_hole", defect="pinched_hole", removed=int(n_removed))
    return out


def broken_flipped(n_flipped: int = 10) -> trimesh.Trimesh:
    """Box with some faces wound the wrong way."""
    b = _base_box_mesh()
    f = b.faces.copy()
    idx = np.arange(0, len(f), max(1, len(f) // n_flipped))[:n_flipped]
    f[idx] = f[idx][:, ::-1]
    out = trimesh.Trimesh(b.vertices, f, process=False)
    out.metadata.update(kind="broken_flipped", defect="flipped", flipped=idx.tolist())
    return out


def broken_degenerate(n_degen: int = 8) -> trimesh.Trimesh:
    """Box plus zero area faces (repeated vertex and collinear triplets)."""
    b = _base_box_mesh()
    f = b.faces
    extra = []
    for i in range(n_degen):
        a, c = f[i, 0], f[i, 1]
        extra.append([a, a, c] if i % 2 == 0 else [a, c, c])
    out = trimesh.Trimesh(b.vertices, np.vstack([f, np.asarray(extra)]), process=False)
    out.metadata.update(kind="broken_degenerate", defect="degenerate", degenerate=int(n_degen))
    return out


def broken_nonmanifold() -> trimesh.Trimesh:
    """Two boxes sharing exactly one edge (that edge has four incident faces)."""
    a = trimesh.creation.box(extents=(10, 10, 10))
    a.apply_translation((5, 5, 5))
    b = trimesh.creation.box(extents=(10, 10, 10))
    b.apply_translation((15, 15, 5))  # touches a along the line x=10, y=10
    m = trimesh.util.concatenate([a, b])
    m.merge_vertices()
    out = trimesh.Trimesh(m.vertices, m.faces, process=False)
    out.metadata.update(kind="broken_nonmanifold", defect="nonmanifold_edge")
    return out


# ---------------------------------------------------------------------------
# scale and routing stress cases
# ---------------------------------------------------------------------------
def large_mesh(n_faces: int = 1_000_000) -> trimesh.Trimesh:
    """The figurine refined to roughly n_faces triangles (default ~1M)."""
    m, features = _figurine_manifold(seg=16 if n_faces < 20_000 else 64)
    m = refined(m, n_faces)
    return _to_trimesh(m, kind="large_mesh", features=features, target_faces=n_faces)


def crossing_case() -> trimesh.Trimesh:
    """Two arms at different heights pointing at each other from opposite pillars, offset in
    y. Branches merging towards a common trunk would naturally cross."""
    m = _union([
        _box((0, -2, 0), (26, 14, 2)),          # common base plate
        _box((0, 0, 0), (4, 4, 24)),
        _box((0, 0, 20), (22, 4, 23)),          # arm A, z 20..23, towards +x
        _box((22, 8, 0), (26, 12, 18)),
        _box((4, 8, 14), (26, 12, 17)),          # arm B, z 14..17, towards -x
    ])
    return _to_trimesh(m, kind="crossing_case", arm_a_bottom_z=20.0, arm_b_bottom_z=14.0,
                       arm_a_box=((4.0, 0.0, 20.0), (22.0, 4.0, 23.0)),
                       arm_b_box=((4.0, 8.0, 14.0), (22.0, 12.0, 17.0)))


def close_contacts_case(gap: float = 1.0) -> trimesh.Trimesh:
    """A beam on a pillar with two downward pointing spikes whose tips are `gap` apart."""
    parts = [_box((0, -2, 0), (4, 2, 23)), _box((0, -2, 20), (16, 2, 23))]
    tips = []
    for x in (12.0, 12.0 + gap):
        parts.append(_cyl_z((x, 0), 16.5, 20.2, 0.0, 24, r_top=0.4))
        tips.append((x, 0.0, 16.5))
    return _to_trimesh(_union(parts), kind="close_contacts_case", spike_tips=tips, gap=gap)


def impossible_case() -> trimesh.Trimesh:
    """A mushroom cap standing inside a deep bowl. The cap underside (z = 12) is lower than
    the bowl rim (z = 18) and fully surrounded by the bowl: any support must stand on the
    bowl floor (the model). With allow_on_model = False it cannot be supported."""
    outer = _box((-15, -15, 0), (15, 15, 18))
    inner = _box((-12, -12, 3), (12, 12, 19))
    bowl = outer - inner
    m = _union([bowl, _cyl_z((0, 0), 2.5, 12.5, 2.0, 48), _cyl_z((0, 0), 12, 15, 9.0, 96)])
    return _to_trimesh(m, kind="impossible_case", cap_bottom_z=12.0, bowl_floor_z=3.0,
                       rim_z=18.0, cap_radius=9.0)


GENERATORS = {
    "cube": cube,
    "sphere_on_plate": sphere_on_plate,
    "floating_cube": floating_cube,
    "floating_sphere": floating_sphere,
    "inclined_surface": inclined_surface,
    "shelf": shelf,
    "bridge": bridge,
    "suspended_arm": suspended_arm,
    "figurine": figurine,
    "cavity": cavity,
    "tiny_triangles": tiny_triangles,
    "crossing_case": crossing_case,
    "close_contacts_case": close_contacts_case,
    "impossible_case": impossible_case,
}

BROKEN_GENERATORS = {
    "broken_hole": broken_hole,
    "broken_pinched_hole": broken_pinched_hole,
    "broken_flipped": broken_flipped,
    "broken_degenerate": broken_degenerate,
    "broken_nonmanifold": broken_nonmanifold,
}

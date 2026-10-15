"""Mesh import: STL (ASCII and binary), OBJ, 3MF.

Policy (see .claude/agents/mesh-engineer.md): no silent geometry change.
  * Files are loaded with ``process=False``: trimesh does not drop, reorder or repair
    anything.
  * The only post-processing is merging vertices whose coordinates are *bit-identical*
    (STL stores 3 independent vertices per triangle). This is lossless: every triangle
    keeps exactly the same corner coordinates, only the index buffer changes. It is
    required for any topological check (edges, holes, manifoldness).
  * Nothing is fixed here. Use ``arbor.core.repair`` for diagnosis / repair.

3MF is parsed by our own reader (zipfile + xml.etree): all build items are merged with
their transforms (item transform x nested component transforms), the ``<model unit>``
is converted to millimetres, object names are reported.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
START_PART_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

# 3MF Core spec: unit attribute, conversion factor to millimetres
UNIT_TO_MM = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}

SUPPORTED_EXTENSIONS = (".stl", ".obj", ".3mf")
_MAX_COMPONENT_DEPTH = 32


class MeshLoadError(Exception):
    """Raised when a file cannot be read or contains no usable triangles."""


@dataclass
class LoadedModel:
    mesh: trimesh.Trimesh                # merged geometry, millimetres
    name: str
    source_path: Path
    units_detected: Optional[str]       # 3MF unit attribute, None for STL / OBJ
    warnings: list[str] = field(default_factory=list)
    object_names: list[str] = field(default_factory=list)
    file_format: str = ""


@dataclass
class ThreeMFPart:
    """One build item (or component leaf) of a 3MF file, already in world mm."""
    name: str
    object_id: str
    mesh: trimesh.Trimesh
    object_type: str = "model"


@dataclass
class ThreeMFContent:
    parts: list[ThreeMFPart]
    unit: str
    metadata: dict[str, str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def merge_identical_vertices(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices with bit-identical coordinates (lossless). Unused vertices are kept
    only if referenced; order of first appearance is not guaranteed."""
    v = np.ascontiguousarray(vertices, dtype=np.float64)
    if len(v) == 0:
        return v.reshape(0, 3), np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    # +0.0 normalises -0.0 so that both compare identical
    v = v + 0.0
    view = v.view(np.dtype((np.void, v.dtype.itemsize * 3))).ravel()
    _, first, inverse = np.unique(view, return_index=True, return_inverse=True)
    new_v = v[first]
    new_f = inverse.reshape(-1)[np.asarray(faces, dtype=np.int64)]
    return new_v, new_f.reshape(-1, 3)


def _make_mesh(vertices, faces) -> trimesh.Trimesh:
    v, f = merge_identical_vertices(np.asarray(vertices, float).reshape(-1, 3),
                                    np.asarray(faces, np.int64).reshape(-1, 3))
    return trimesh.Trimesh(vertices=v, faces=f, process=False, validate=False)


def _unit_warnings(mesh: trimesh.Trimesh, declared: Optional[str]) -> list[str]:
    w = []
    if len(mesh.vertices) == 0:
        return w
    ext = float(np.max(mesh.bounds[1] - mesh.bounds[0]))
    if declared is None:
        w.append("File format carries no unit: millimetres assumed.")
        if ext < 5.0:
            w.append(f"Model is only {ext:.3g} mm large: it may have been saved in "
                     "centimetres, inches or metres. Check the scale.")
        elif ext > 2000.0:
            w.append(f"Model is {ext:.0f} mm large: it may have been saved in microns. "
                     "Check the scale.")
    return w


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def parse_3mf_matrix(text: Optional[str]) -> np.ndarray:
    """3MF ST_Matrix3D 'm00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32' (row-vector
    convention, translation in the last row) -> 4x4 column-vector matrix."""
    if text is None or not text.strip():
        return np.eye(4)
    vals = [float(x) for x in text.split()]
    if len(vals) != 12:
        raise MeshLoadError(f"Invalid 3MF transform (expected 12 numbers): {text!r}")
    m = np.array(vals).reshape(4, 3)       # rows: x-axis image, y, z, translation
    out = np.eye(4)
    out[:3, :3] = m[:3, :3].T
    out[:3, 3] = m[3]
    if not np.all(np.isfinite(out)):
        raise MeshLoadError(f"Non-finite 3MF transform: {text!r}")
    return out


def format_3mf_matrix(mat: np.ndarray) -> str:
    """Inverse of :func:`parse_3mf_matrix`."""
    mat = np.asarray(mat, float)
    rows = np.vstack([mat[:3, :3].T, mat[:3, 3]])
    return " ".join(f"{x:.9g}" for x in rows.ravel())


def _find_root_model(zf: zipfile.ZipFile) -> str:
    names = zf.namelist()
    lower = {n.lower(): n for n in names}
    if "_rels/.rels" in lower:
        try:
            rels = ET.fromstring(zf.read(lower["_rels/.rels"]))
            for r in rels:
                if r.get("Type") == START_PART_REL:
                    target = r.get("Target", "").lstrip("/")
                    if target.lower() in lower:
                        return lower[target.lower()]
        except ET.ParseError:
            pass
    for n in names:
        if n.lower().endswith(".model") and n.lower().startswith("3d/"):
            return n
    raise MeshLoadError("No 3D model part found in the 3MF package.")


def _parse_mesh_element(mesh_el) -> tuple[np.ndarray, np.ndarray]:
    vs, ts = None, None
    for child in mesh_el:
        t = _local(child.tag)
        if t == "vertices":
            vs = child
        elif t == "triangles":
            ts = child
    if vs is None or ts is None:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    try:
        v = np.array([(e.get("x"), e.get("y"), e.get("z")) for e in vs], dtype=np.float64)
        f = np.array([(e.get("v1"), e.get("v2"), e.get("v3")) for e in ts], dtype=np.int64)
    except (TypeError, ValueError) as exc:
        raise MeshLoadError(f"Malformed vertex/triangle in 3MF mesh: {exc}") from exc
    return v.reshape(-1, 3), f.reshape(-1, 3)


def read_3mf(path: str | Path) -> ThreeMFContent:
    """Read every build item of a 3MF file into world-space parts (millimetres)."""
    path = Path(path)
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise MeshLoadError(f"{path.name}: not a valid 3MF (zip) file: {exc}") from exc
    with zf:
        root_name = _find_root_model(zf)
        try:
            root = ET.fromstring(zf.read(root_name))
        except ET.ParseError as exc:
            raise MeshLoadError(f"{path.name}: invalid XML in {root_name}: {exc}") from exc

    if _local(root.tag) != "model":
        raise MeshLoadError(f"{path.name}: root element is <{_local(root.tag)}>, expected <model>.")
    if _ns(root.tag) != CORE_NS:
        warnings.append(f"Unexpected 3MF namespace {_ns(root.tag)!r}.")
    unit = root.get("unit", "millimeter")
    if unit not in UNIT_TO_MM:
        raise MeshLoadError(f"{path.name}: unknown 3MF unit {unit!r}.")
    scale = UNIT_TO_MM[unit]
    req = root.get("requiredextensions")
    if req:
        warnings.append(f"File declares required extensions ({req}); only core geometry is read.")

    metadata: dict[str, str] = {}
    objects: dict[str, dict] = {}
    build_items = []
    for el in root:
        t = _local(el.tag)
        if t == "metadata" and el.get("name"):
            metadata[el.get("name")] = (el.text or "").strip()
        elif t == "resources":
            for obj in el:
                if _local(obj.tag) != "object":
                    continue
                oid = obj.get("id")
                rec = {"name": obj.get("name") or f"object_{oid}",
                       "type": obj.get("type", "model"), "mesh": None, "components": []}
                for c in obj:
                    ct = _local(c.tag)
                    if ct == "mesh":
                        rec["mesh"] = _parse_mesh_element(c)
                    elif ct == "components":
                        for comp in c:
                            if _local(comp.tag) != "component":
                                continue
                            if comp.get(f"{{{PRODUCTION_NS}}}path"):
                                warnings.append("Component referencing another model part "
                                                "(production extension) is not supported; skipped.")
                                continue
                            rec["components"].append(
                                (comp.get("objectid"), parse_3mf_matrix(comp.get("transform"))))
                objects[oid] = rec
        elif t == "build":
            for it in el:
                if _local(it.tag) == "item":
                    if it.get(f"{{{PRODUCTION_NS}}}path"):
                        warnings.append("Build item referencing another model part is not supported; skipped.")
                        continue
                    build_items.append((it.get("objectid"), parse_3mf_matrix(it.get("transform"))))

    if not build_items:
        raise MeshLoadError(f"{path.name}: 3MF has no build item.")

    unit_mat = np.diag([scale, scale, scale, 1.0])
    parts: list[ThreeMFPart] = []

    def emit(oid: str, mat: np.ndarray, top_name: str, depth: int):
        if depth > _MAX_COMPONENT_DEPTH:
            raise MeshLoadError("3MF component nesting too deep (cycle?).")
        if oid not in objects:
            raise MeshLoadError(f"{path.name}: build item / component references unknown object id {oid!r}.")
        rec = objects[oid]
        if rec["mesh"] is not None:
            v, f = rec["mesh"]
            if len(f):
                if f.min() < 0 or f.max() >= len(v):
                    raise MeshLoadError(f"{path.name}: object {rec['name']!r} has triangle indices out of range.")
                world = unit_mat @ mat
                vh = np.c_[v, np.ones(len(v))] @ world.T
                ff = f
                if np.linalg.det(world[:3, :3]) < 0:
                    ff = f[:, ::-1]
                    warnings.append(f"Object {rec['name']!r} has a mirroring transform; "
                                    "triangle order reversed to keep outward normals.")
                parts.append(ThreeMFPart(rec["name"], oid, _make_mesh(vh[:, :3], ff), rec["type"]))
            else:
                warnings.append(f"Object {rec['name']!r} has an empty mesh.")
        for cid, cmat in rec["components"]:
            emit(cid, mat @ cmat, top_name, depth + 1)

    for oid, mat in build_items:
        emit(oid, mat, objects.get(oid, {}).get("name", ""), 0)

    other_types = sorted({p.object_type for p in parts if p.object_type not in ("model", "")})
    if other_types:
        warnings.append(f"File contains objects of type {other_types}; they are included.")
    return ThreeMFContent(parts, unit, metadata, warnings)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def _sniff_stl_ascii(path: Path) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(512)
    if not head.lstrip().lower().startswith(b"solid"):
        return False
    size = path.stat().st_size
    if size >= 84:
        n = int.from_bytes(head[80:84], "little") if len(head) >= 84 else -1
        if 84 + 50 * n == size:          # consistent binary header -> binary
            return False
    return b"facet" in head or size < 512


def load_mesh(path: str | Path) -> LoadedModel:
    """Load an STL / OBJ / 3MF file into a single merged mesh in millimetres."""
    path = Path(path)
    if not path.exists():
        raise MeshLoadError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise MeshLoadError(f"{path.name}: file is empty.")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise MeshLoadError(f"{path.name}: unsupported format {ext!r} (expected STL, OBJ or 3MF).")

    warnings: list[str] = []
    names: list[str] = []
    units: Optional[str] = None

    if ext == ".3mf":
        content = read_3mf(path)
        warnings += content.warnings
        units = content.unit
        if not content.parts:
            raise MeshLoadError(f"{path.name}: 3MF contains no geometry.")
        names = [p.name for p in content.parts]
        vs, fs, off = [], [], 0
        for p in content.parts:
            vs.append(p.mesh.vertices)
            fs.append(p.mesh.faces + off)
            off += len(p.mesh.vertices)
        mesh = _make_mesh(np.vstack(vs), np.vstack(fs))
        if units != "millimeter":
            warnings.append(f"3MF unit is {units!r}: converted to millimetres (x{UNIT_TO_MM[units]:g}).")
        name = content.metadata.get("Title") or (names[0] if len(set(names)) == 1 else path.stem)
        fmt = "3mf"
    else:
        file_type = "stl" if ext == ".stl" else "obj"
        fmt = file_type
        if ext == ".stl":
            fmt = "stl_ascii" if _sniff_stl_ascii(path) else "stl_binary"
            if fmt == "stl_binary":
                size = path.stat().st_size
                with open(path, "rb") as fh:
                    fh.seek(80)
                    n = int.from_bytes(fh.read(4), "little")
                if 84 + 50 * n != size:
                    raise MeshLoadError(f"{path.name}: truncated or corrupted binary STL "
                                        f"(header says {n} triangles, size {size} bytes).")
        try:
            loaded = trimesh.load(str(path), file_type=file_type, process=False, force="mesh")
        except Exception as exc:  # trimesh raises many different types
            raise MeshLoadError(f"{path.name}: cannot parse {file_type.upper()}: {exc}") from exc
        if isinstance(loaded, trimesh.Scene):
            geoms = list(loaded.dump()) if hasattr(loaded, "dump") else list(loaded.geometry.values())
            if not geoms:
                raise MeshLoadError(f"{path.name}: no geometry.")
            loaded = trimesh.util.concatenate(geoms)
        if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
            raise MeshLoadError(f"{path.name}: file contains no triangles.")
        if ext == ".obj":
            n_poly = loaded.metadata.get("face_count_quad", 0) if isinstance(loaded.metadata, dict) else 0
            if n_poly:
                warnings.append(f"{n_poly} polygons were triangulated.")
        mesh = _make_mesh(loaded.vertices, loaded.faces)
        name = path.stem
        names = [name]

    if len(mesh.faces) == 0:
        raise MeshLoadError(f"{path.name}: file contains no triangles.")
    if not np.all(np.isfinite(mesh.vertices)):
        warnings.append("Some vertices have non-finite coordinates (NaN/inf).")
    warnings += _unit_warnings(mesh, units)
    return LoadedModel(mesh=mesh, name=name, source_path=path, units_detected=units,
                       warnings=warnings, object_names=names, file_format=fmt)

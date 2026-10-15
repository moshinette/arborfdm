"""Export: binary STL and hand-written 3MF (Core spec 1.x), plus read-back validation.

3MF layout (see docs/research/3mf_export.md for the reasoning and sources):
  * one ``<object type="model">`` per part (model, supports, raft) with a ``name``
    attribute. ``type="support"`` is NOT used: the PrusaSlicer-family importer discards
    objects of that type.
  * ``layout="objects"`` (default): one build item per part, so slicers list them as
    separate objects.
    ``layout="components"``: one extra parent object made of components referencing the
    parts, and a single build item: the parts keep their relative position as one
    assembly.
  * unit="millimeter"; the whole scene is shifted in XY (build item transform) so that
    it lies in the positive octant as recommended by the spec; the shift is returned.
  * Metadata Application=ArborFDM (+ Title, CreationDate) and namespaced
    ``arbor:`` metadata with the support parameters as JSON.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union
from xml.sax.saxutils import escape, quoteattr
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

from arbor.core.mesh_io import (CORE_NS, REL_NS, START_PART_REL, UNIT_TO_MM, MeshLoadError,
                                format_3mf_matrix, load_mesh, read_3mf)

ARBOR_NS = "http://arborfdm.org/3mf/2026"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MODEL_CT = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
RELS_CT = "application/vnd.openxmlformats-package.relationships+xml"
MODEL_PATH = "3D/3dmodel.model"

MeshLike = Union[trimesh.Trimesh, Sequence[trimesh.Trimesh]]


@dataclass
class ExportResult:
    path: Path
    offset: np.ndarray                 # translation applied by the build items (mm)
    objects: list[str] = field(default_factory=list)
    n_triangles: int = 0
    layout: str = "objects"


# ---------------------------------------------------------------------------
# STL
# ---------------------------------------------------------------------------
def _concat(meshes: MeshLike) -> trimesh.Trimesh:
    if isinstance(meshes, trimesh.Trimesh):
        return meshes
    meshes = [m for m in meshes if m is not None and len(m.faces)]
    if not meshes:
        raise ValueError("Nothing to export (no triangles).")
    vs, fs, off = [], [], 0
    for m in meshes:
        vs.append(np.asarray(m.vertices, float))
        fs.append(np.asarray(m.faces, np.int64) + off)
        off += len(m.vertices)
    return trimesh.Trimesh(vertices=np.vstack(vs), faces=np.vstack(fs), process=False, validate=False)


def export_stl(mesh_or_list: MeshLike, path: str | Path) -> Path:
    """Write a binary STL (several meshes are concatenated, not united)."""
    path = Path(path)
    m = _concat(mesh_or_list)
    if len(m.faces) == 0:
        raise ValueError("Nothing to export (no triangles).")
    tri = np.asarray(m.vertices, np.float64)[m.faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    rec = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"] = n
    rec["v"] = tri
    header = b"ArborFDM binary STL, units mm".ljust(80, b" ")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(np.uint32(len(tri)).tobytes())
        fh.write(rec.tobytes())
    return path


# ---------------------------------------------------------------------------
# 3MF
# ---------------------------------------------------------------------------
def _mesh_xml(mesh: trimesh.Trimesh) -> str:
    # 6 decimals in mm = 1 nm resolution
    v = np.asarray(mesh.vertices, np.float64)
    f = np.asarray(mesh.faces, np.int64)
    xs = np.char.mod('<vertex x="%.6f" ', v[:, 0])
    ys = np.char.mod('y="%.6f" ', v[:, 1])
    zs = np.char.mod('z="%.6f"/>', v[:, 2])
    vert = "".join(np.char.add(np.char.add(xs, ys), zs).tolist())
    a = np.char.mod('<triangle v1="%d" ', f[:, 0])
    b = np.char.mod('v2="%d" ', f[:, 1])
    c = np.char.mod('v3="%d"/>', f[:, 2])
    tris = "".join(np.char.add(np.char.add(a, b), c).tolist())
    return f"<mesh><vertices>{vert}</vertices><triangles>{tris}</triangles></mesh>"


def _content_types() -> bytes:
    root = ET.Element("Types", xmlns=CT_NS)
    ET.SubElement(root, "Default", Extension="rels", ContentType=RELS_CT)
    ET.SubElement(root, "Default", Extension="model", ContentType=MODEL_CT)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _rels() -> bytes:
    root = ET.Element("Relationships", xmlns=REL_NS)
    ET.SubElement(root, "Relationship", Target="/" + MODEL_PATH, Id="rel0", Type=START_PART_REL)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_3mf(path: str | Path, model: trimesh.Trimesh, supports: Optional[trimesh.Trimesh] = None,
               raft: Optional[trimesh.Trimesh] = None, params=None, model_name: str = "model",
               *, layout: str = "objects", positive_octant: bool = True,
               title: Optional[str] = None) -> ExportResult:
    """Write model (+ supports, + raft) to a 3MF package. Input meshes are in plate
    coordinates (mm, z up) and are not modified."""
    if layout not in ("objects", "components"):
        raise ValueError("layout must be 'objects' or 'components'")
    path = Path(path)
    parts: list[tuple[str, trimesh.Trimesh]] = []
    if model is None or len(model.faces) == 0:
        raise ValueError("The model mesh is empty.")
    parts.append((model_name, model))
    if supports is not None and len(supports.faces):
        parts.append((f"{model_name} supports", supports))
    if raft is not None and len(raft.faces):
        parts.append((f"{model_name} raft", raft))
    for name, m in parts:
        if not np.all(np.isfinite(m.vertices)):
            raise ValueError(f"{name}: non-finite vertex coordinates.")

    lo = np.min([m.bounds[0] for _, m in parts], axis=0)
    offset = np.zeros(3)
    if positive_octant:
        offset[:2] = np.maximum(-lo[:2], 0.0)
        offset[2] = max(-lo[2], 0.0)
    tmat = np.eye(4)
    tmat[:3, 3] = offset

    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:arbor="{ARBOR_NS}">\n')
    out.write('<metadata name="Application">ArborFDM</metadata>\n')
    out.write(f'<metadata name="Title">{escape(title or model_name)}</metadata>\n')
    out.write(f'<metadata name="CreationDate">{now}</metadata>\n')
    if params is not None:
        pdict = params.to_dict() if hasattr(params, "to_dict") else dict(params)
        out.write(f'<metadata name="arbor:support_params">{escape(json.dumps(pdict, sort_keys=True))}</metadata>\n')
    out.write("<resources>\n")
    names = []
    for i, (name, m) in enumerate(parts, start=1):
        names.append(name)
        out.write(f'<object id="{i}" type="model" name={quoteattr(name)}>')
        out.write(_mesh_xml(m))
        out.write("</object>\n")
    if layout == "components":
        pid = len(parts) + 1
        out.write(f'<object id="{pid}" type="model" name={quoteattr(model_name + " assembly")}><components>')
        for i in range(1, len(parts) + 1):
            out.write(f'<component objectid="{i}"/>')
        out.write("</components></object>\n")
    out.write("</resources>\n<build>\n")
    tr = format_3mf_matrix(tmat)
    if layout == "components":
        out.write(f'<item objectid="{len(parts) + 1}" transform="{tr}"/>\n')
    else:
        for i in range(1, len(parts) + 1):
            out.write(f'<item objectid="{i}" transform="{tr}"/>\n')
    out.write("</build>\n</model>\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types())
        zf.writestr("_rels/.rels", _rels())
        zf.writestr(MODEL_PATH, out.getvalue().encode("utf-8"))
    tmp.replace(path)
    return ExportResult(path, offset, names, int(sum(len(m.faces) for _, m in parts)), layout)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def read_3mf_back(path: str | Path):
    """Re-read an exported 3MF with our own reader: returns (LoadedModel, ThreeMFContent)."""
    return load_mesh(path), read_3mf(path)


def validate_export(path: str | Path) -> list[str]:
    """Structural checks on a 3MF package. Returns a list of problems (empty = OK)."""
    path = Path(path)
    problems: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return [f"not a valid zip: {exc}"]
    with zf:
        bad = zf.testzip()
        if bad:
            problems.append(f"corrupted zip member {bad}")
        names = zf.namelist()
        for required in ("[Content_Types].xml", "_rels/.rels"):
            if required not in names:
                problems.append(f"missing {required}")
        if "[Content_Types].xml" in names:
            try:
                ct = ET.fromstring(zf.read("[Content_Types].xml"))
                exts = {d.get("Extension", "").lower(): d.get("ContentType") for d in ct
                        if d.tag.endswith("Default")}
                if exts.get("model") != MODEL_CT:
                    problems.append("[Content_Types].xml has no content type for .model")
                if exts.get("rels") != RELS_CT:
                    problems.append("[Content_Types].xml has no content type for .rels")
            except ET.ParseError as exc:
                problems.append(f"[Content_Types].xml invalid XML: {exc}")
        target = None
        if "_rels/.rels" in names:
            try:
                rels = ET.fromstring(zf.read("_rels/.rels"))
                for r in rels:
                    if r.get("Type") == START_PART_REL:
                        target = r.get("Target", "").lstrip("/")
                if target is None:
                    problems.append("no 3D model StartPart relationship")
                elif target not in names:
                    problems.append(f"StartPart target {target} missing from package")
            except ET.ParseError as exc:
                problems.append(f"_rels/.rels invalid XML: {exc}")
        if target is None or target not in names:
            return problems
        try:
            root = ET.fromstring(zf.read(target))
        except ET.ParseError as exc:
            return problems + [f"{target}: invalid XML: {exc}"]

    q = lambda t: f"{{{CORE_NS}}}{t}"
    if root.tag != q("model"):
        problems.append(f"root element is {root.tag}, expected core <model>")
        return problems
    unit = root.get("unit", "millimeter")
    if unit not in UNIT_TO_MM:
        problems.append(f"invalid unit {unit!r}")
    elif unit != "millimeter":
        problems.append(f"unit is {unit!r}, ArborFDM exports millimetres")
    res = root.find(q("resources"))
    build = root.find(q("build"))
    if res is None:
        problems.append("missing <resources>")
        return problems
    if build is None:
        problems.append("missing <build>")
    ids: dict[str, ET.Element] = {}
    for obj in res.findall(q("object")):
        oid = obj.get("id")
        if not oid or not oid.isdigit():
            problems.append(f"object with invalid id {oid!r}")
            continue
        if oid in ids:
            problems.append(f"duplicate object id {oid}")
        ids[oid] = obj
        otype = obj.get("type", "model")
        if otype not in ("model", "solidsupport", "support", "surface", "other"):
            problems.append(f"object {oid}: invalid type {otype!r}")
        mesh = obj.find(q("mesh"))
        comps = obj.find(q("components"))
        if (mesh is None) == (comps is None):
            problems.append(f"object {oid}: must contain exactly one of <mesh> or <components>")
            continue
        if mesh is not None:
            vs = mesh.find(q("vertices"))
            ts = mesh.find(q("triangles"))
            nv = len(vs) if vs is not None else 0
            if nv == 0 or ts is None or len(ts) == 0:
                problems.append(f"object {oid}: empty mesh")
                continue
            try:
                vv = np.array([[float(e.get(k)) for k in "xyz"] for e in vs])
                tt = np.array([[int(e.get(k)) for k in ("v1", "v2", "v3")] for e in ts])
            except (TypeError, ValueError):
                problems.append(f"object {oid}: malformed vertex/triangle attributes")
                continue
            if not np.all(np.isfinite(vv)):
                problems.append(f"object {oid}: non-finite vertex")
            if tt.min() < 0 or tt.max() >= nv:
                problems.append(f"object {oid}: triangle index out of range")
            if np.any((tt[:, 0] == tt[:, 1]) | (tt[:, 1] == tt[:, 2]) | (tt[:, 0] == tt[:, 2])):
                problems.append(f"object {oid}: triangle with repeated vertex index (forbidden by spec)")
            if otype == "model" and len(tt) < 4:
                problems.append(f"object {oid}: type model needs at least 4 triangles")
        else:
            for c in comps.findall(q("component")):
                if c.get("objectid") not in ids:
                    problems.append(f"object {oid}: component references undefined/later object {c.get('objectid')}")
    if build is not None:
        items = build.findall(q("item"))
        if not items:
            problems.append("build has no item")
        for it in items:
            if it.get("objectid") not in ids:
                problems.append(f"build item references unknown object {it.get('objectid')}")
            tr = it.get("transform")
            if tr is not None:
                vals = tr.split()
                if len(vals) != 12:
                    problems.append("build item transform must have 12 values")
                else:
                    m = np.array([float(x) for x in vals]).reshape(4, 3)
                    if abs(np.linalg.det(m[:3])) < 1e-12:
                        problems.append("build item transform is singular")
    if not problems:
        try:
            read_3mf(path)
        except MeshLoadError as exc:
            problems.append(f"reader failed: {exc}")
    return problems

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
  * Coordinates are written with 6 decimals (1 nm). Before writing, each part is
    *welded* (:func:`weld_for_export`): vertices closer than WELD_EPS = 1e-5 mm on the
    written values become one vertex (Core 4.1.3: "a producer SHOULD collapse vertices
    that are very closely proximal") and the triangles that collapse (repeated index,
    forbidden by Core 4.1.4.1) are dropped. This follows Core 4.1.3 ("SHOULD NOT include duplicate
    vertices unless coalescing duplicates would create non-manifold edges"): the weld is
    only kept if the result is still closed, manifold and consistently wound, otherwise
    the part is written unchanged and a note is returned. Every action is reported in
    ``ExportResult.notes``; the in-memory meshes are never modified.
    Why: manifold3d (support union) returns zero-area slivers whose distinct vertices
    share the same float32 position. Index topology is closed, but any consumer that
    merges vertices by position (trimesh.load, most slicers) sees non-manifold edges.
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
    notes: list[str] = field(default_factory=list)   # weld actions / warnings per part


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


def export_stl(mesh_or_list: MeshLike, path: str | Path, *, weld: bool = True,
               notes: Optional[list] = None, names: Optional[Sequence[str]] = None) -> Path:
    """Write a binary STL (several meshes are concatenated, not united).

    ``weld``: each mesh is first welded at float32 precision (see :func:`weld_for_stl`)
    so that zero-area slivers do not become non-manifold edges once a reader merges
    vertices by position. Actions are appended to ``notes`` if given."""
    path = Path(path)
    meshes = [mesh_or_list] if isinstance(mesh_or_list, trimesh.Trimesh) else list(mesh_or_list)
    if weld:
        welded = []
        for i, mm in enumerate(meshes):
            if mm is None or len(mm.faces) == 0:
                continue
            label = names[i] if names is not None and i < len(names) else f"STL part {i + 1}"
            w, rep = weld_for_stl(mm, label)
            if notes is not None and (rep.applied or not rep.reason.startswith("no vertices")):
                notes.append(rep.describe())
            welded.append(w)
        meshes = welded
    if not meshes:
        raise ValueError("Nothing to export (no triangles).")
    m = _concat(meshes)
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
COORD_FMT = "%.6f"     # 6 decimals in mm = 1 nm resolution
WELD_EPS = 1e-5        # mm: vertices closer than this (on written values) are one vertex


@dataclass
class WeldReport:
    name: str
    vertices_merged: int = 0
    faces_removed: int = 0
    max_shift: float = 0.0     # mm, largest vertex displacement caused by the weld
    applied: bool = False
    reason: str = ""

    def describe(self) -> str:
        if self.applied:
            return (f"{self.name}: merged {self.vertices_merged} vertices closer than "
                    f"{WELD_EPS:g} mm (max shift {self.max_shift:.1e} mm) and removed "
                    f"{self.faces_removed} collapsed zero-area triangles / zero-volume fins; "
                    "mesh still closed and manifold.")
        return f"{self.name}: written unchanged ({self.reason})."


def _closed_consistent(faces: np.ndarray) -> bool:
    """Every undirected edge used exactly twice, once in each direction."""
    if len(faces) == 0:
        return False
    d = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    und = np.sort(d, axis=1)
    _, inv, cnt = np.unique(und, axis=0, return_inverse=True, return_counts=True)
    if np.any(cnt != 2):
        return False
    fwd = np.bincount(inv.ravel(), weights=(d[:, 0] < d[:, 1]), minlength=len(cnt))
    return bool(np.all(fwd == 1))


def _written_values(v: np.ndarray) -> np.ndarray:
    """Coordinates exactly as a reader will parse them back from the 3MF (6 decimals),
    with -0.0 normalised to 0.0 (a tiny negative value would otherwise be written
    "-0.000000", a different string for the same number)."""
    txt = np.char.mod(COORD_FMT, np.asarray(v, np.float64))
    return txt.astype(np.float64) + 0.0


def _format_vertices(v: np.ndarray) -> np.ndarray:
    """(n, 3) array of the exact strings written to the file."""
    return np.char.mod(COORD_FMT, _written_values(v))


def _clusters(wv: np.ndarray, eps: float) -> tuple[np.ndarray, np.ndarray]:
    """Single-linkage clusters of points closer than ``eps``. Returns (first, inv): the
    lowest index of each cluster (its representative) and the cluster of each point.
    Points of different clusters are more than ``eps`` apart, so no reader merging by
    position with a smaller tolerance can merge two representatives."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    n = len(wv)
    pairs = cKDTree(wv).query_pairs(eps, output_type="ndarray")
    if len(pairs) == 0:
        idx = np.arange(n)
        return idx, idx
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, labels = connected_components(g, directed=False)
    # relabel by first appearance so that representatives keep the original order
    _, first, inv = np.unique(labels, return_index=True, return_inverse=True)
    order = np.argsort(first)
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    return first[order], rank[inv.ravel()]


def _weld_core(wv: np.ndarray, f: np.ndarray, rep: WeldReport, what: str
               ) -> tuple[Optional[np.ndarray], np.ndarray]:
    """Weld the vertices ``wv`` (written values) closer than WELD_EPS. Returns (source
    vertex indices, new faces), or (None, original faces) when nothing is merged;
    ``rep`` is filled in."""
    first, inv = _clusters(wv, WELD_EPS)
    n_coinc = len(wv) - len(first)
    if n_coinc == 0:
        rep.reason = f"no vertices closer than {WELD_EPS:g} mm at {what}"
        return None, f
    if not _closed_consistent(f):
        rep.reason = (f"{n_coinc} vertices closer than {WELD_EPS:g} mm at {what}; input mesh is "
                      "not closed/consistently wound, so it is not welded")
        return None, f
    nf = inv[f]
    ok = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
    n_collapsed = int((~ok).sum())
    nf = nf[ok]
    # Collapsing a sliver can leave two triangles on the same 3 vertices with opposite
    # orientation (a zero-thickness fin, zero volume). Remove such pairs only.
    n_fins = 0
    if len(nf):
        srt = np.sort(nf, axis=1)
        _, finv, fcnt = np.unique(srt, axis=0, return_inverse=True, return_counts=True)
        finv = finv.ravel()
        if np.any(fcnt > 1):
            r = np.argmin(nf, axis=1)          # rotate so the smallest index is first
            rolled = np.stack([nf[np.arange(len(nf)), (r + k) % 3] for k in range(3)], axis=1)
            positive = rolled[:, 1] < rolled[:, 2]
            pos_cnt = np.bincount(finv, weights=positive, minlength=len(fcnt))
            fin_group = (fcnt == 2) & (pos_cnt == 1)
            drop = fin_group[finv]
            n_fins = int(drop.sum())
            nf = nf[~drop]
    used = np.zeros(len(first), bool)
    used[nf.ravel()] = True
    new_f = (np.cumsum(used) - 1)[nf]
    if not _closed_consistent(new_f):
        rep.reason = (f"{n_coinc} vertices closer than {WELD_EPS:g} mm at {what}, but merging them "
                      "would create open or non-manifold edges (touching geometry); readers that "
                      "merge by position may report non-manifold edges")
        return None, f
    src = first[used]
    rep.vertices_merged = int(len(wv) - len(src))
    rep.faces_removed = n_collapsed + n_fins
    rep.max_shift = float(np.max(np.linalg.norm(wv - wv[first[inv]], axis=1)))
    rep.applied = True
    return src, new_f


def weld_for_export(mesh: trimesh.Trimesh, name: str = "mesh"
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, WeldReport]:
    """Return (vertex strings, vertices, faces, report) to write in a 3MF for ``mesh``.

    Vertices closer than WELD_EPS (on the values actually written) are merged into their
    cluster's first vertex; triangles that then repeat an index, and opposite-facing
    duplicate pairs (zero-volume fins), are dropped. Applied only when the input is
    closed and consistently wound and the result still is, so no non-manifold edge can be
    created (3MF Core 4.1.3). The input mesh is not modified."""
    v = np.asarray(mesh.vertices, np.float64)
    f = np.asarray(mesh.faces, np.int64).reshape(-1, 3)
    rep = WeldReport(name)
    if len(v) == 0 or len(f) == 0:
        rep.reason = "empty"
        return np.char.mod(COORD_FMT, v.reshape(-1, 3)), v, f, rep
    wv = _written_values(v)
    txt = np.char.mod(COORD_FMT, wv)
    src, nf = _weld_core(wv, f, rep, "write precision")
    if src is None:
        return txt, v, f, rep
    return txt[src], v[src], nf, rep


def weld_for_stl(mesh: trimesh.Trimesh, name: str = "mesh") -> tuple[trimesh.Trimesh, WeldReport]:
    """Same as :func:`weld_for_export` on float32 values (what a binary STL stores)."""
    v = np.asarray(mesh.vertices, np.float64)
    f = np.asarray(mesh.faces, np.int64).reshape(-1, 3)
    rep = WeldReport(name)
    if len(v) == 0 or len(f) == 0:
        rep.reason = "empty"
        return mesh, rep
    wv = v.astype(np.float32).astype(np.float64) + 0.0
    src, nf = _weld_core(wv, f, rep, "float32 precision")
    if src is None:
        return mesh, rep
    return trimesh.Trimesh(vertices=v[src], faces=nf, process=False, validate=False), rep


def _mesh_xml_from(txt: np.ndarray, f: np.ndarray) -> str:
    xs = np.char.add(np.char.add('<vertex x="', txt[:, 0]), '" ')
    ys = np.char.add(np.char.add('y="', txt[:, 1]), '" ')
    zs = np.char.add(np.char.add('z="', txt[:, 2]), '"/>')
    vert = "".join(np.char.add(np.char.add(xs, ys), zs).tolist())
    a = np.char.mod('<triangle v1="%d" ', f[:, 0])
    b = np.char.mod('v2="%d" ', f[:, 1])
    c = np.char.mod('v3="%d"/>', f[:, 2])
    tris = "".join(np.char.add(np.char.add(a, b), c).tolist())
    return f"<mesh><vertices>{vert}</vertices><triangles>{tris}</triangles></mesh>"


def _mesh_xml(mesh: trimesh.Trimesh) -> str:
    """Mesh element without welding (kept for callers that need the raw mesh)."""
    return _mesh_xml_from(_format_vertices(mesh.vertices), np.asarray(mesh.faces, np.int64))


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
               title: Optional[str] = None, weld: bool = True) -> ExportResult:
    """Write model (+ supports, + raft) to a 3MF package. Input meshes are in plate
    coordinates (mm, z up) and are not modified. ``weld``: see :func:`weld_for_export`
    (actions reported in ``ExportResult.notes``)."""
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
    notes: list[str] = []
    n_tri = 0
    for i, (name, m) in enumerate(parts, start=1):
        names.append(name)
        txt, _wv, wf, rep = weld_for_export(m, name) if weld else (
            _format_vertices(m.vertices), None, np.asarray(m.faces, np.int64), WeldReport(name))
        if rep.applied or (weld and rep.reason and not rep.reason.startswith("no vertices")):
            notes.append(rep.describe())
        n_tri += len(wf)
        out.write(f'<object id="{i}" type="model" name={quoteattr(name)}>')
        out.write(_mesh_xml_from(txt, wf))
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
    return ExportResult(path, offset, names, int(n_tri), layout, notes)


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

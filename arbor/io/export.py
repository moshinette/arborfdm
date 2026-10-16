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
    ``layout="separate"``: separate objects that each rest on the plate (z min = plate),
    so that slicers which drop every object to the bed (PrusaSlicer, OrcaSlicer, Bambu
    Studio) keep the alignment and OrcaSlicer does not merge them. The raft is written in
    the same object as the supports (several shells in one mesh): alone, the supports
    start on top of the raft and would float. Support shells standing only on the model
    are written in the model object (see :func:`_separate_groups`). Optionally (``layer_heights``) a
    ``Metadata/model_settings.config`` gives each object its own ``layer_height`` for
    OrcaSlicer / Bambu Studio (ignored by other consumers). See docs/SLICERS.md.
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
import re
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


def _content_types(extra: Sequence[tuple[str, str]] = ()) -> bytes:
    root = ET.Element("Types", xmlns=CT_NS)
    ET.SubElement(root, "Default", Extension="rels", ContentType=RELS_CT)
    ET.SubElement(root, "Default", Extension="model", ContentType=MODEL_CT)
    for ext, ct in extra:
        ET.SubElement(root, "Default", Extension=ext, ContentType=ct)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


LAYOUTS = ("objects", "components", "separate")
PLATE_TOL = 1e-3       # mm: an object "rests on the plate" if its z min is within this
# OrcaSlicer / Bambu Studio per-object settings (read by bbs_3mf.cpp for any 3MF, not
# only for their own projects; see docs/SLICERS.md).
BBS_MODEL_CONFIG = "Metadata/model_settings.config"
CONFIG_CT = "application/xml"


# "Lighter supports" (SupportParams.light_slicer_supports): written on the supports object only.
# Measured with OrcaSlicer 2.4.2 CLI, A1 mini 0.2, figurine supports: -31 % filament and time
# vs the slicer defaults. minimum_sparse_infill_area = 0 is required: otherwise Orca fills
# every section smaller than 15 mm2 (all branches) solid and the other two have no effect.
LIGHT_SUPPORT_SETTINGS = {"wall_loops": 2, "sparse_infill_density": "10%",
                          "minimum_sparse_infill_area": 0}


def _model_settings_config(objects: Sequence[tuple],
                           extra: Optional[dict[str, str]] = None) -> bytes:
    """Metadata/model_settings.config declaring each (object id, name, layer height),
    plus the same ``extra`` key/value settings on every object.
    The part id is the object id: each object is a single <mesh> object."""
    root = ET.Element("config")
    for oid, name, lh, *own in objects:
        own_settings = own[0] if own else {}
        o = ET.SubElement(root, "object", id=str(oid))
        ET.SubElement(o, "metadata", key="name", value=name)
        ET.SubElement(o, "metadata", key="extruder", value="1")
        if lh is not None:
            ET.SubElement(o, "metadata", key="layer_height", value=f"{lh:g}")
        for k, v in {**(extra or {}), **own_settings}.items():
            ET.SubElement(o, "metadata", key=k, value=v)
        p = ET.SubElement(o, "part", id=str(oid), subtype="normal_part")
        ET.SubElement(p, "metadata", key="name", value=name)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _check_layer_heights(layer_heights) -> dict[str, float]:
    if not layer_heights:
        return {}
    out = {}
    for k, v in dict(layer_heights).items():
        if k not in ("model", "supports"):
            raise ValueError(f"layer_heights: unknown key {k!r} (expected 'model' and/or 'supports')")
        if v is None:
            continue
        v = float(v)
        if not (np.isfinite(v) and 0.0 < v <= 2.0):
            raise ValueError(f"layer_heights[{k!r}] must be in (0, 2] mm, got {v}")
        out[k] = v
    return out


_SETTING_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED_SETTINGS = ("name", "extruder", "layer_height", "module")


def _check_object_settings(object_settings) -> dict[str, str]:
    """Validate per-object OrcaSlicer / Bambu Studio settings: {key: value} with plain
    config keys; values are written as text (bools as 0/1)."""
    if not object_settings:
        return {}
    out = {}
    for k, v in dict(object_settings).items():
        if not isinstance(k, str) or not _SETTING_KEY.match(k):
            raise ValueError(f"object_settings: invalid key {k!r}")
        if k in _RESERVED_SETTINGS:
            raise ValueError(f"object_settings: {k!r} is managed by the exporter "
                             "(use layer_heights for the layer height)")
        if isinstance(v, bool):
            v = "1" if v else "0"
        elif isinstance(v, (int, float)) and np.isfinite(v):
            v = f"{v:g}"
        elif not isinstance(v, str):
            raise ValueError(f"object_settings[{k!r}]: unsupported value {v!r}")
        out[k] = v
    return out


def _rels() -> bytes:
    root = ET.Element("Relationships", xmlns=REL_NS)
    ET.SubElement(root, "Relationship", Target="/" + MODEL_PATH, Id="rel0", Type=START_PART_REL)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_3mf(path: str | Path, model: trimesh.Trimesh, supports: Optional[trimesh.Trimesh] = None,
               raft: Optional[trimesh.Trimesh] = None, params=None, model_name: str = "model",
               *, layout: str = "objects", positive_octant: bool = True,
               title: Optional[str] = None, weld: bool = True,
               layer_heights: Optional[dict] = None,
               object_settings: Optional[dict] = None) -> ExportResult:
    """Write model (+ supports, + raft) to a 3MF package. Input meshes are in plate
    coordinates (mm, z up) and are not modified. ``weld``: see :func:`weld_for_export`
    (actions reported in ``ExportResult.notes``).

    ``layout``: ``"objects"`` (default), ``"components"`` or ``"separate"`` (see the
    module docstring). ``layer_heights`` (only with ``"separate"``): ``{"model": mm,
    "supports": mm}``, written as per-object ``layer_height`` for OrcaSlicer / Bambu
    Studio in ``Metadata/model_settings.config``; either key may be omitted.
    ``object_settings`` (only with ``"separate"``): other per-object settings written on
    every object of that file, e.g. ``{"enable_support": False}`` (OrcaSlicer applies
    them to the object configuration, also for a 3MF that is not an Orca project; see
    docs/SLICERS.md)."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
    lh = _check_layer_heights(layer_heights)
    extra = _check_object_settings(object_settings)
    if (lh or extra) and layout != "separate":
        raise ValueError("layer_heights / object_settings need layout='separate' (otherwise "
                         "slicers merge or drop the objects and the per-object settings break "
                         "the alignment)")
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
    notes: list[str] = []

    # objects to write: (object name, role, [(part name, mesh), ...]); one <mesh> each
    if layout == "separate":
        groups = _separate_groups(
            model_name, model, supports if supports is not None and len(supports.faces) else None,
            raft if raft is not None and len(raft.faces) else None, float(lo[2]), notes)
    else:
        groups = [(name, "model" if i == 0 else "supports", [(name, m)])
                  for i, (name, m) in enumerate(parts)]

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
    n_tri = 0
    for i, (oname, _role, members) in enumerate(groups, start=1):
        names.append(oname)
        txts, fs, nv = [], [], 0
        for pname, m in members:
            txt, _wv, wf, rep = weld_for_export(m, pname) if weld else (
                _format_vertices(m.vertices), None, np.asarray(m.faces, np.int64), WeldReport(pname))
            if rep.applied or (weld and rep.reason and not rep.reason.startswith("no vertices")):
                notes.append(rep.describe())
            txts.append(txt)
            fs.append(np.asarray(wf, np.int64) + nv)
            nv += len(txt)
        wf = np.vstack(fs)
        n_tri += len(wf)
        out.write(f'<object id="{i}" type="model" name={quoteattr(oname)}>')
        out.write(_mesh_xml_from(np.vstack(txts), wf))
        out.write("</object>\n")
    if layout == "components":
        pid = len(groups) + 1
        out.write(f'<object id="{pid}" type="model" name={quoteattr(model_name + " assembly")}><components>')
        for i in range(1, len(groups) + 1):
            out.write(f'<component objectid="{i}"/>')
        out.write("</components></object>\n")
    out.write("</resources>\n<build>\n")
    tr = format_3mf_matrix(tmat)
    if layout == "components":
        out.write(f'<item objectid="{len(groups) + 1}" transform="{tr}"/>\n')
    else:
        for i in range(1, len(groups) + 1):
            out.write(f'<item objectid="{i}" transform="{tr}"/>\n')
    out.write("</build>\n</model>\n")

    extra_files: dict[str, bytes] = {}
    sup_extra: dict[str, str] = {}
    if (layout == "separate" and getattr(params, "light_slicer_supports", False)
            and any(role == "supports" for _n, role, _m in groups)):
        sup_extra = _check_object_settings(LIGHT_SUPPORT_SETTINGS)
        notes.append("Lighter supports (OrcaSlicer / Bambu Studio, supports object only): "
                     + ", ".join(f"{k} = {v}" for k, v in sup_extra.items()) + ".")
    if (layout == "separate" and getattr(params, "light_slicer_supports", False)
            and not any(role == "supports" for _n, role, _m in groups)):
        notes.append("Lighter supports not applied: the model rests on the raft, so model, "
                     "supports and raft are one object (use raft coverage 'feet' to keep the "
                     "supports a separate object).")
    if lh or extra or sup_extra:
        extra_files[BBS_MODEL_CONFIG] = _model_settings_config(
            [(i, oname, lh.get(role), sup_extra if role == "supports" else {})
             for i, (oname, role, _m) in enumerate(groups, start=1)],
            extra)
    if lh:
        notes.append("OrcaSlicer / Bambu Studio per-object layer height: " + ", ".join(
            f"{oname} = {lh[role]:g} mm" for oname, role, _m in groups if role in lh)
            + " (Metadata/model_settings.config; other slicers use their own setting).")
    if extra:
        notes.append("OrcaSlicer / Bambu Studio per-object settings on every object: " + ", ".join(
            f"{k} = {v}" for k, v in extra.items()) + " (Metadata/model_settings.config).")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    _content_types([("config", CONFIG_CT)] if extra_files else ()))
        zf.writestr("_rels/.rels", _rels())
        zf.writestr(MODEL_PATH, out.getvalue().encode("utf-8"))
        for name, data in extra_files.items():
            zf.writestr(name, data)
    tmp.replace(path)
    return ExportResult(path, offset, names, int(n_tri), layout, notes)


def _shell_labels(mesh: trimesh.Trimesh) -> np.ndarray:
    """Connected component (shell) of every face, by shared edges (vertex indices)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    f = np.asarray(mesh.faces, np.int64)
    n = len(f)
    e = np.sort(np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    fid = np.tile(np.arange(n), 3)
    _, inv = np.unique(e, axis=0, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    a, b = fid[order][:-1], fid[order][1:]
    same = inv[order][:-1] == inv[order][1:]
    g = coo_matrix((np.ones(int(same.sum())), (a[same], b[same])), shape=(n, n))
    return connected_components(g, directed=False)[1]


def _submesh(mesh: trimesh.Trimesh, face_mask: np.ndarray) -> trimesh.Trimesh:
    f = np.asarray(mesh.faces, np.int64)[face_mask]
    used, nf = np.unique(f, return_inverse=True)
    return trimesh.Trimesh(np.asarray(mesh.vertices)[used], nf.reshape(-1, 3), process=False, validate=False)


def _separate_groups(model_name: str, model: trimesh.Trimesh, supports: Optional[trimesh.Trimesh],
                     raft: Optional[trimesh.Trimesh], plate_z: float, notes: list[str]
                     ) -> list[tuple[str, str, list[tuple[str, trimesh.Trimesh]]]]:
    """Objects (name, role, [(part name, mesh)]) for ``layout="separate"``.

    Every object must rest on the plate: OrcaSlicer / Bambu Studio (and PrusaSlicer)
    drop each object of a non-project 3MF to the bed, and an object kept in the air has
    an empty first layer, which OrcaSlicer refuses to slice (docs/SLICERS.md). So:

    * the raft and the support shells that stand on the plate or on the raft form the
      supports object;
    * support shells standing only on the model (their foot is on the model) go with
      the model: alone they would leave empty layers in the supports object;
    * if the model itself does not reach the plate, everything is one object.
    Every move is reported in ``notes``; no triangle is changed."""
    sup_name = f"{model_name} supports"
    model_members = [(model_name, model)]
    sup_members: list[tuple[str, trimesh.Trimesh]] = []
    ground = plate_z + PLATE_TOL
    base_top = max(ground, float(raft.bounds[1][2]) + PLATE_TOL) if raft is not None else ground
    if supports is not None:
        labels = _shell_labels(supports)
        n_shells = int(labels.max()) + 1
        zmin = np.full(n_shells, np.inf)
        tri_z = np.asarray(supports.vertices)[np.asarray(supports.faces)][:, :, 2].min(axis=1)
        np.minimum.at(zmin, labels, tri_z)
        grounded = zmin <= base_top
        on_plate = grounded[labels]
        if on_plate.all():
            sup_members.append((sup_name, supports))
        else:
            n_up = int((~grounded).sum())
            if on_plate.any():
                sup_members.append((sup_name, _submesh(supports, on_plate)))
            model_members.append((f"{sup_name} on model", _submesh(supports, ~on_plate)))
            notes.append(f"separate layout: {n_up} of {n_shells} support shells stand only on the "
                         f"model (lowest foot {zmin[~grounded].min() - plate_z:.2f} mm above the "
                         f"plate); they are written in the '{model_name}' object, otherwise the "
                         "supports object would have empty layers.")
    if raft is not None:
        sup_members.append((f"{model_name} raft", raft))
    sname = (f"{model_name} supports + raft" if raft is not None and len(sup_members) > 1
             else sup_members[0][0] if sup_members else sup_name)
    groups = [(model_name, "model", model_members)]
    if sup_members:
        groups.append((sname, "supports", sup_members))
    lowest = lambda members: min(float(m.bounds[0][2]) for _, m in members)
    floating = [g for g in groups if lowest(g[2]) > ground]
    if floating and len(groups) == 2:
        notes.append("separate layout: " + ", ".join(
            f"'{g[0]}' does not reach the plate (z min {lowest(g[2]) - plate_z:.3f} mm)" for g in floating)
            + "; everything is written as one object to keep the alignment.")
        groups = [(f"{model_name} with supports", "model", groups[0][2] + groups[1][2])]
    return groups


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

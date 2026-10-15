"""Project document of ArborFDM (Qt free).

A project holds:
  * the imported mesh (never modified) and the working base mesh (the imported one, or a
    repaired copy: repairs are undoable and never touch the imported mesh),
  * a ModelTransform and the resulting world mesh (transformed, standing on the plate z = 0),
  * the support parameters and the profile they come from,
  * the contacts (automatic + manual) and the last GenerationResult,
  * an undo stack of command objects.

Coordinates of contacts are world coordinates (mm, Z up, plate at z = 0). When the transform
changes, manual contacts follow the model (mapped with the delta matrix), automatic contacts
and the generation result are dropped (they must be regenerated).

Change notification is a plain list of callbacks: ``project.listeners.append(fn)``;
``fn(event: str)`` is called with one of EVENTS.
"""
from __future__ import annotations

import copy
import io
import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import trimesh

from arbor.engine.params import Profile, SupportParams, load_profiles
from arbor.engine.types import AnalysisResult, Contact, ContactReason, GenerationResult

FILE_VERSION = 1
PROJECT_SUFFIX = ".arbor"
DEFAULT_PROFILE = "nozzle_04_normal"

# Events sent to listeners
EVENTS = ("mesh", "transform", "params", "contacts", "result", "selection", "undo", "dirty")

# Parameters that influence the analysis stage (if they are unchanged, analysis is reused)
DETECTION_FIELDS = ("layer_height", "overhang_angle_deg", "min_region_area",
                    "max_unsupported_overhang", "nozzle_diameter", "line_width")


def _transform_module():
    from arbor.core import transform as _t   # imported lazily: module written in parallel
    return _t


def _new_transform():
    return _transform_module().ModelTransform()


# ---------------------------------------------------------------------------
# Undo stack
# ---------------------------------------------------------------------------
class Command:
    """Base class of undoable commands. Subclasses implement redo() and undo()."""
    text = "command"

    def redo(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def undo(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def merge_with(self, other: "Command") -> bool:
        """Try to absorb a following command (e.g. successive edits of one value)."""
        return False


class UndoStack:
    def __init__(self) -> None:
        self._commands: list[Command] = []
        self._index = 0          # number of applied commands
        self._clean_index = 0
        self.listeners: list[Callable[[], None]] = []

    # -- state ---------------------------------------------------------------
    def can_undo(self) -> bool:
        return self._index > 0

    def can_redo(self) -> bool:
        return self._index < len(self._commands)

    def undo_text(self) -> str:
        return self._commands[self._index - 1].text if self.can_undo() else ""

    def redo_text(self) -> str:
        return self._commands[self._index].text if self.can_redo() else ""

    def __len__(self) -> int:
        return len(self._commands)

    @property
    def index(self) -> int:
        return self._index

    def is_clean(self) -> bool:
        return self._index == self._clean_index

    def set_clean(self) -> None:
        self._clean_index = self._index
        self._notify()

    def clear(self) -> None:
        self._commands.clear()
        self._index = self._clean_index = 0
        self._notify()

    # -- operations ----------------------------------------------------------
    def push(self, cmd: Command, merge: bool = True) -> None:
        """Apply cmd (calls cmd.redo()) and record it."""
        cmd.redo()
        del self._commands[self._index:]
        if self._clean_index > self._index:
            self._clean_index = -1          # clean state is no longer reachable
        if (merge and self._commands and self._index != self._clean_index
                and self._commands[-1].merge_with(cmd)):
            pass
        else:
            self._commands.append(cmd)
            self._index += 1
        self._notify()

    def undo(self) -> bool:
        if not self.can_undo():
            return False
        self._index -= 1
        self._commands[self._index].undo()
        self._notify()
        return True

    def redo(self) -> bool:
        if not self.can_redo():
            return False
        self._commands[self._index].redo()
        self._index += 1
        self._notify()
        return True

    def _notify(self) -> None:
        for fn in list(self.listeners):
            fn()


# ---------------------------------------------------------------------------
# Generation request
# ---------------------------------------------------------------------------
@dataclass
class GenerationRequest:
    """Everything needed to call pipeline.generate(**request.kwargs, progress=...)."""
    mode: str                      # "full" | "branches"
    kwargs: dict[str, Any]
    mesh_version: int
    analysis_key: tuple
    contacts_version: int
    params: Optional[SupportParams] = None          # snapshot used to build the geometry
    contacts_before: list = field(default_factory=list)   # contacts when the run started


@dataclass
class ApplyReport:
    """What happened when a result was applied (edits made while it was computing)."""
    applied: bool = True
    kept_edits: list[str] = field(default_factory=list)      # user edits merged into it
    lost_edits: list[str] = field(default_factory=list)      # edits that could not be mapped
    changed_params: dict = field(default_factory=dict)       # {name: (used, current)}

    @property
    def stale(self) -> bool:
        return bool(self.kept_edits or self.lost_edits or self.changed_params)


@dataclass
class ProjectState:
    """Snapshot of the contact related state (used by commands)."""
    contacts: list[Contact]
    result: Optional[GenerationResult]
    contacts_edited: bool
    result_params: Optional[SupportParams] = None   # parameters the result was built with


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------
class Project:
    def __init__(self, profiles: Optional[dict[str, Profile]] = None) -> None:
        self.profiles: dict[str, Profile] = profiles if profiles is not None else load_profiles()
        self.listeners: list[Callable[[str], None]] = []
        self.undo_stack = UndoStack()
        self.undo_stack.listeners.append(lambda: self._emit("undo"))
        self.path: Optional[Path] = None
        self._reset_document()

    # -- document lifecycle --------------------------------------------------
    def _reset_document(self) -> None:
        self.name: str = ""
        self.source_path: Optional[str] = None
        self.imported_mesh: Optional[trimesh.Trimesh] = None   # never modified
        self.base_mesh: Optional[trimesh.Trimesh] = None       # imported or repaired copy
        self.base_is_repaired = False
        self.load_warnings: list[str] = []
        self.transform = _new_transform()
        self.mesh: Optional[trimesh.Trimesh] = None            # world mesh (on the plate)
        self.world_matrix = np.eye(4)
        self.mesh_version = 0
        key = DEFAULT_PROFILE if DEFAULT_PROFILE in self.profiles else next(iter(self.profiles), "")
        self.profile_key: str = key
        self.params: SupportParams = (copy.deepcopy(self.profiles[key].params)
                                      if key else SupportParams())
        self.contacts: list[Contact] = []
        self.result: Optional[GenerationResult] = None
        self.result_params: Optional[SupportParams] = None   # params used to build result
        self.contacts_edited = False       # contacts changed by hand since last generation
        self.last_apply: Optional[ApplyReport] = None
        self._mesh_reports: dict[int, tuple] = {}   # id(base mesh) -> (mesh, MeshReport)
        self.contacts_version = 0
        self.selected_contact_id: Optional[int] = None
        self._analysis_cache: Optional[tuple[tuple, AnalysisResult]] = None

    def new(self) -> None:
        self._reset_document()
        self.path = None
        self.undo_stack.clear()
        for ev in ("mesh", "transform", "params", "contacts", "result", "selection", "dirty"):
            self._emit(ev)

    @property
    def dirty(self) -> bool:
        return not self.undo_stack.is_clean()

    @property
    def has_model(self) -> bool:
        return self.mesh is not None

    def _emit(self, event: str) -> None:
        for fn in list(self.listeners):
            fn(event)
        if event == "undo":
            for fn in list(self.listeners):
                fn("dirty")

    # -- model ---------------------------------------------------------------
    def set_model(self, mesh: trimesh.Trimesh, name: str = "", source_path: Optional[str] = None,
                  warnings: Optional[list[str]] = None, transform=None) -> None:
        """Start a new document around this mesh (not undoable: it is a new document)."""
        self._reset_document()
        self.imported_mesh = mesh.copy()
        self.base_mesh = mesh.copy()
        self.name = name or "model"
        self.source_path = source_path
        self.load_warnings = list(warnings or [])
        if transform is not None:
            self.transform = transform
        else:
            self.transform = self.transform.placed_on_plate(self.base_mesh)
        self._rebuild_world_mesh()
        self.undo_stack.clear()
        self.path = None
        for ev in ("mesh", "transform", "params", "contacts", "result", "selection", "dirty"):
            self._emit(ev)

    def _rebuild_world_mesh(self) -> None:
        if self.base_mesh is None:
            self.mesh = None
            self.world_matrix = np.eye(4)
            return
        m = np.asarray(self.transform.to_matrix(), float)
        mesh = self.base_mesh.copy()
        mesh.apply_transform(m)
        dz = -float(mesh.bounds[0][2])            # the engine needs the model on z = 0
        drop = np.eye(4)
        drop[2, 3] = dz
        mesh.apply_transform(drop)
        self.world_matrix = drop @ m
        self.mesh = mesh
        self.mesh_version += 1

    def dimensions(self) -> np.ndarray:
        """Real size of the world mesh (mm)."""
        if self.mesh is None:
            return np.zeros(3)
        return np.asarray(self.mesh.extents, float)

    def base_dimensions(self) -> np.ndarray:
        if self.base_mesh is None:
            return np.zeros(3)
        return np.asarray(self.base_mesh.extents, float)

    # -- transform -----------------------------------------------------------
    def set_transform(self, transform=None, *, text: str = "Transform", **changes) -> None:
        """Undoable transform change. Pass a ModelTransform or field changes
        (rotation_deg=(x, y, z), scale=..., translation=(x, y, z))."""
        if self.base_mesh is None:
            return
        if transform is None:
            transform = copy.deepcopy(self.transform)
            for k, v in changes.items():
                setattr(transform, k, v)
        if _transform_equal(transform, self.transform):
            return
        self.undo_stack.push(SetTransformCommand(self, copy.deepcopy(self.transform),
                                                 copy.deepcopy(transform), text))

    def place_on_plate(self) -> None:
        """Center the model on the plate (XY bbox centre at the origin, resting on z = 0)."""
        if self.base_mesh is None:
            return
        self.set_transform(self.transform.placed_on_plate(self.base_mesh), text="Place on plate")

    def apply_orientation(self, rotation_deg, text: str = "Apply orientation") -> None:
        """Use this rotation (source frame, e.g. an OrientationCandidate) and re-center."""
        t = copy.deepcopy(self.transform)
        t.rotation_deg = tuple(float(v) for v in rotation_deg)
        self.set_transform(t.placed_on_plate(self.base_mesh), text=text)

    def set_rotation(self, rotation_deg) -> None:
        """Rotation about the model, the result is re-centred on the plate."""
        if self.base_mesh is None:
            return
        t = copy.deepcopy(self.transform)
        t.rotation_deg = tuple(float(v) for v in rotation_deg)
        self.set_transform(t.placed_on_plate(self.base_mesh), text="Rotate")

    def set_scale(self, scale: float) -> None:
        if self.base_mesh is None or not scale > 0:
            return
        t = copy.deepcopy(self.transform)
        t.scale = float(scale)
        self.set_transform(t.placed_on_plate(self.base_mesh), text="Scale")

    def set_scale_for_height(self, height_mm: float) -> None:
        """Choose the uniform scale giving this world height (Z extent)."""
        if self.mesh is None or height_mm <= 0:
            return
        current = float(self.mesh.extents[2])
        if current <= 0:
            return
        self.set_scale(_scalar_scale(self.transform.scale) * height_mm / current)

    def _apply_transform(self, transform) -> None:
        old_matrix = self.world_matrix.copy()
        self.transform = copy.deepcopy(transform)
        self._rebuild_world_mesh()
        delta = self.world_matrix @ np.linalg.inv(old_matrix)
        self._map_manual_contacts(delta)

    def _map_manual_contacts(self, delta: np.ndarray) -> None:
        rot = delta[:3, :3]
        normal_m = np.linalg.inv(rot).T
        kept = []
        for c in self.contacts:
            if not c.manual:
                continue
            c = copy.deepcopy(c)
            c.position = (rot @ np.asarray(c.position, float)) + delta[:3, 3]
            n = normal_m @ np.asarray(c.normal, float)
            ln = np.linalg.norm(n)
            c.normal = n / ln if ln > 0 else n
            kept.append(c)
        self.contacts = kept
        self.result = None
        self.result_params = None
        self.contacts_edited = bool(kept)
        self.contacts_version += 1

    # -- mesh repair ---------------------------------------------------------
    def replace_base_mesh(self, new_mesh: trimesh.Trimesh, text: str = "Repair mesh") -> None:
        """Undoable replacement of the working mesh (the imported mesh is kept)."""
        if self.base_mesh is None:
            return
        self.undo_stack.push(ReplaceMeshCommand(self, self.base_mesh, new_mesh.copy(),
                                                self.base_is_repaired, True, text), merge=False)

    # -- params / profiles -----------------------------------------------------
    def profile_params(self) -> SupportParams:
        p = self.profiles.get(self.profile_key)
        return copy.deepcopy(p.params) if p else SupportParams()

    def changed_from_profile(self) -> dict[str, tuple[Any, Any]]:
        """{field: (profile value, current value)} for every value differing from the profile."""
        base = self.profile_params().to_dict()
        cur = self.params.to_dict()
        out = {}
        for k, v in cur.items():
            b = base.get(k)
            if isinstance(v, float) and isinstance(b, (int, float)):
                if abs(v - b) > 1e-9:
                    out[k] = (b, v)
            elif v != b:
                out[k] = (b, v)
        return out

    def set_param(self, name: str, value: Any) -> None:
        if getattr(self.params, name) == value:
            return
        new = self.params.updated(**{name: value})
        self.undo_stack.push(SetParamsCommand(self, self.params, new, self.profile_key,
                                              self.profile_key, f"Change {name}", field=name))

    def set_params(self, params: SupportParams, text: str = "Change parameters") -> None:
        if params == self.params:
            return
        self.undo_stack.push(SetParamsCommand(self, self.params, copy.deepcopy(params),
                                              self.profile_key, self.profile_key, text))

    def apply_profile(self, key: str, keep_overrides: bool = False) -> None:
        """Switch profile: parameters are reset to the profile values (undoable)."""
        if key not in self.profiles:
            raise KeyError(f"Unknown profile {key!r}")
        new = copy.deepcopy(self.profiles[key].params)
        if keep_overrides:
            new = new.updated(**{k: v for k, (_, v) in self.changed_from_profile().items()})
        if key == self.profile_key and new == self.params:
            return
        self.undo_stack.push(SetParamsCommand(self, self.params, new, self.profile_key, key,
                                              f"Profile {self.profiles[key].label}"),
                             merge=False)

    def reset_param(self, name: str) -> None:
        self.set_param(name, getattr(self.profile_params(), name))

    def _apply_params(self, params: SupportParams, profile_key: str) -> None:
        self.params = copy.deepcopy(params)
        self.profile_key = profile_key

    # -- contacts --------------------------------------------------------------
    def next_contact_id(self) -> int:
        ids = [c.id for c in self.contacts]
        return (max(ids) + 1) if ids else 1

    def contact(self, contact_id: int) -> Optional[Contact]:
        for c in self.contacts:
            if c.id == contact_id:
                return c
        return None

    def manual_contacts(self) -> list[Contact]:
        return [c for c in self.contacts if c.manual]

    def add_manual_contact(self, position, normal, tip_diameter: Optional[float] = None) -> Contact:
        n = np.asarray(normal, float)
        ln = np.linalg.norm(n)
        c = Contact(id=self.next_contact_id(), position=np.asarray(position, float).copy(),
                    normal=n / ln if ln > 0 else np.array([0.0, 0.0, -1.0]),
                    reason=ContactReason.MANUAL, manual=True, tip_diameter=tip_diameter,
                    note="Placed by the user")
        self.undo_stack.push(AddContactCommand(self, c), merge=False)
        return c

    def remove_contact(self, contact_id: int) -> bool:
        if self.contact(contact_id) is None:
            return False
        self.undo_stack.push(RemoveContactCommand(self, contact_id), merge=False)
        return True

    def edit_contact(self, contact_id: int, **changes) -> None:
        """Undoable edit of tip_diameter / enabled (or any Contact field)."""
        c = self.contact(contact_id)
        if c is None:
            return
        old = {k: copy.deepcopy(getattr(c, k)) for k in changes}
        if all(_eq(old[k], v) for k, v in changes.items()):
            return
        self.undo_stack.push(EditContactCommand(self, contact_id, old, dict(changes)))

    def select_contact(self, contact_id: Optional[int]) -> None:
        if contact_id is not None and self.contact(contact_id) is None:
            contact_id = None
        if contact_id != self.selected_contact_id:
            self.selected_contact_id = contact_id
            self._emit("selection")

    def snapshot(self) -> ProjectState:
        return ProjectState(copy.deepcopy(self.contacts), self.result, self.contacts_edited,
                            self.result_params)

    def restore(self, s: ProjectState) -> None:
        self.contacts = copy.deepcopy(s.contacts)
        self.result = s.result
        self.result_params = s.result_params if s.result is not None else None
        self.contacts_edited = s.contacts_edited
        self.contacts_version += 1
        if self.selected_contact_id is not None and self.contact(self.selected_contact_id) is None:
            self.selected_contact_id = None

    # -- generation ------------------------------------------------------------
    def analysis_key(self) -> tuple:
        return (self.mesh_version,) + tuple(getattr(self.params, f) for f in DETECTION_FIELDS)

    def cached_analysis(self) -> Optional[AnalysisResult]:
        if self._analysis_cache and self._analysis_cache[0] == self.analysis_key():
            return self._analysis_cache[1]
        return None

    def suggested_mode(self) -> str:
        """'branches' when contacts were edited by hand since the last generation."""
        return "branches" if (self.contacts_edited and self.result is not None) else "full"

    def regenerate(self, full: bool = True) -> GenerationRequest:
        """Arguments for pipeline.generate.

        full=True : automatic contacts are placed again; manual contacts are kept
                    (passed as manual_contacts). The analysis is reused if the mesh and the
                    detection parameters did not change.
        full=False: 'branches only': the current contact list (with its manual edits,
                    disabled contacts and tip overrides) is used as is, the analysis is reused.
        """
        if self.mesh is None:
            raise ValueError("No model loaded")
        params = copy.deepcopy(self.params)
        # the mesh is copied: the engine runs in a worker thread while the UI keeps its own
        kwargs: dict[str, Any] = {"mesh": self.mesh.copy(), "params": params,
                                  "manual_contacts": copy.deepcopy(self.manual_contacts()),
                                  "analysis": self.cached_analysis()}
        mode = "full"
        if not full:
            if not self.contacts:
                mode = "full"       # nothing to rebuild from: fall back to a full generation
            else:
                mode = "branches"
                kwargs["contacts"] = copy.deepcopy(self.contacts)
        if mode == "full":
            kwargs["contacts"] = None
        if params.manual_only and mode == "full":
            # only the user's contacts: skip automatic placement entirely
            kwargs["contacts"] = copy.deepcopy(self.manual_contacts())
        return GenerationRequest(mode, kwargs, self.mesh_version, self.analysis_key(),
                                 self.contacts_version, copy.deepcopy(params),
                                 copy.deepcopy(self.contacts))

    def is_request_current(self, req: GenerationRequest) -> bool:
        """True if nothing that the request was built from changed since: model, contacts
        and parameters. A request whose model changed cannot be applied at all; one whose
        contacts or parameters changed is applied but marked out of date (apply_result)."""
        return (req.mesh_version == self.mesh_version
                and req.contacts_version == self.contacts_version
                and (req.params is None or not _params_diff(req.params, self.params)))

    def result_param_changes(self) -> dict[str, tuple[Any, Any]]:
        """{field: (value used for the current result, current value)}."""
        if self.result is None or self.result_params is None:
            return {}
        return _params_diff(self.result_params, self.params)

    @property
    def result_is_stale(self) -> bool:
        """The displayed supports do not match the current contacts or parameters."""
        return self.result is not None and (self.contacts_edited
                                            or bool(self.result_param_changes()))

    def export_params(self) -> SupportParams:
        """Parameters to write with an export: those that built the supports (QA-3)."""
        if self.result is not None and self.result_params is not None:
            return copy.deepcopy(self.result_params)
        return copy.deepcopy(self.params)

    def apply_result(self, result: GenerationResult, request: Optional[GenerationRequest] = None,
                     text: str = "Generate supports") -> bool:
        """Store a generation result (undoable). Returns False if the model changed while
        computing (the result cannot be used).

        Contact edits made while the result was computing are replayed on the result's
        contact list (deleted contacts stay deleted, added manual contacts stay, edited
        contacts keep their edit) and the result is then marked out of date
        (contacts_edited); parameters that changed are reported by result_param_changes().
        ``self.last_apply`` describes what happened."""
        rep = ApplyReport()
        self.last_apply = rep
        if request is not None and request.mesh_version != self.mesh_version:
            rep.applied = False
            return False
        if request is not None and result.analysis is not None:
            self._analysis_cache = (request.analysis_key, result.analysis)
        contacts = copy.deepcopy(result.contacts)
        edited = False
        if request is not None and request.contacts_version != self.contacts_version:
            contacts = self._replay_edits(contacts, request, rep)
            edited = bool(rep.kept_edits or rep.lost_edits)
        used = copy.deepcopy(request.params) if request is not None and request.params is not None \
            else copy.deepcopy(self.params)
        rep.changed_params = _params_diff(used, self.params)
        new_state = ProjectState(contacts, result, edited, used)
        self.undo_stack.push(SetStateCommand(self, self.snapshot(), new_state, text), merge=False)
        return True

    def _replay_edits(self, contacts: list[Contact], req: GenerationRequest,
                      rep: ApplyReport) -> list[Contact]:
        before = {c.id: c for c in req.contacts_before}
        now = {c.id: c for c in self.contacts}
        # in a full generation automatic contacts are new objects: their ids do not refer
        # to the contacts the user saw, only manual contacts (kept by id) can be mapped
        mappable = (lambda c: True) if req.mode == "branches" else (lambda c: c.manual)
        removed = {i for i in before if i not in now}
        modified = {i: now[i] for i in before if i in now
                    and before[i].to_dict() != now[i].to_dict()}
        added = [c for i, c in now.items() if i not in before]
        out = []
        for c in contacts:
            if c.id in removed and mappable(before[c.id]):
                rep.kept_edits.append(f"contact #{c.id} deleted")
                continue
            if c.id in modified and mappable(before[c.id]):
                rep.kept_edits.append(f"contact #{c.id} edited")
                c = copy.deepcopy(modified[c.id])
            out.append(c)
        for i in removed | set(modified):
            if not mappable(before[i]):
                rep.lost_edits.append(f"automatic contact #{i} "
                                      f"{'deleted' if i in removed else 'edited'} "
                                      "(replaced by the new automatic contacts)")
        used_ids = {c.id for c in out}
        for c in added:
            c = copy.deepcopy(c)
            if c.id in used_ids:
                new_id = max(used_ids) + 1
                if self.selected_contact_id == c.id:
                    self.selected_contact_id = new_id
                c.id = new_id
            used_ids.add(c.id)
            out.append(c)
            rep.kept_edits.append(f"contact #{c.id} added")
        return out

    # -- mesh diagnosis (QA-6) ---------------------------------------------------
    def set_mesh_report(self, mesh: Optional[trimesh.Trimesh], report) -> None:
        """Remember the check_mesh report of a base mesh (the imported or a repaired one)."""
        if mesh is not None and report is not None:
            self._mesh_reports[id(mesh)] = (mesh, report)

    def mesh_report(self):
        """check_mesh report of the current working mesh, or None if it was not checked."""
        if self.base_mesh is None:
            return None
        entry = self._mesh_reports.get(id(self.base_mesh))
        return entry[1] if entry is not None and entry[0] is self.base_mesh else None

    def mesh_problems(self) -> list:
        """Error-severity problems of the working mesh (empty if valid or not checked)."""
        r = self.mesh_report()
        return [x for x in getattr(r, "problems", []) if x.severity == "error"] if r else []

    def clear_supports(self) -> None:
        if not self.contacts and self.result is None:
            return
        self.undo_stack.push(SetStateCommand(self, self.snapshot(),
                                             ProjectState([], None, False), "Clear supports"),
                             merge=False)

    # -- undo / redo shortcuts --------------------------------------------------
    def undo(self) -> bool:
        return self.undo_stack.undo()

    def redo(self) -> bool:
        return self.undo_stack.redo()

    # -- persistence -------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": "arborfdm-project",
            "version": FILE_VERSION,
            "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "name": self.name,
            "source_path": self.source_path,
            "transform": self.transform.to_dict(),
            "profile": self.profile_key,
            "params": self.params.to_dict(),
            "contacts": [c.to_dict() for c in self.contacts],
            "contacts_edited": self.contacts_edited,
            "base_is_repaired": self.base_is_repaired,
            "load_warnings": self.load_warnings,
        }

    def save(self, path) -> Path:
        """Write a .arbor file: a zip holding project.json, mesh.stl (working mesh, binary)
        and imported.stl when the working mesh is a repaired copy."""
        if self.base_mesh is None:
            raise ValueError("Nothing to save: no model loaded")
        path = Path(path)
        if path.suffix.lower() != PROJECT_SUFFIX:
            path = path.with_suffix(PROJECT_SUFFIX)
        tmp = path.with_name(path.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps(self.to_dict(), indent=2))
            z.writestr("mesh.stl", _stl_bytes(self.base_mesh))
            if self.base_is_repaired and self.imported_mesh is not None:
                z.writestr("imported.stl", _stl_bytes(self.imported_mesh))
        tmp.replace(path)
        self.path = path
        self.undo_stack.set_clean()
        return path

    @classmethod
    def load(cls, path, profiles: Optional[dict[str, Profile]] = None) -> "Project":
        p = cls(profiles)
        p.load_into(path)
        return p

    def adopt(self, other: "Project") -> None:
        """Take the document content of another Project (e.g. loaded in a worker thread),
        keeping this object, its listeners and its profiles."""
        keep = {"listeners", "undo_stack", "profiles"}
        for k, v in other.__dict__.items():
            if k not in keep:
                setattr(self, k, v)
        self.undo_stack.clear()
        for ev in ("mesh", "transform", "params", "contacts", "result", "selection", "dirty"):
            self._emit(ev)

    def load_into(self, path) -> None:
        """Replace this document with the content of a .arbor file."""
        path = Path(path)
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "project.json" not in names or "mesh.stl" not in names:
                raise ValueError(f"{path.name} is not an ArborFDM project")
            data = json.loads(z.read("project.json").decode("utf-8"))
            base = _stl_load(z.read("mesh.stl"))
            imported = _stl_load(z.read("imported.stl")) if "imported.stl" in names else None
        if data.get("format") != "arborfdm-project":
            raise ValueError(f"{path.name} is not an ArborFDM project")
        if int(data.get("version", 0)) > FILE_VERSION:
            raise ValueError(f"{path.name} was written by a newer ArborFDM "
                             f"(format {data.get('version')})")
        tmod = _transform_module()
        transform = tmod.ModelTransform.from_dict(data["transform"])
        self.set_model(imported if imported is not None else base, data.get("name", path.stem),
                       data.get("source_path"), data.get("load_warnings", []), transform)
        if imported is not None:
            self.base_mesh = base
            self.base_is_repaired = bool(data.get("base_is_repaired", True))
            self._rebuild_world_mesh()
        key = data.get("profile", self.profile_key)
        if key not in self.profiles:
            key = self.profile_key
        self.profile_key = key
        # unknown keys (from a newer version) are ignored rather than failing the load
        known = set(SupportParams().to_dict())
        values = {k: v for k, v in data.get("params", {}).items() if k in known}
        self.params = SupportParams.from_dict({**self.profiles[key].params.to_dict(), **values})
        self.contacts = [Contact.from_dict(c) for c in data.get("contacts", [])]
        self.contacts_edited = bool(data.get("contacts_edited", bool(self.contacts)))
        self.result = None
        self.result_params = None
        self.contacts_version += 1
        self.path = path
        self.undo_stack.clear()
        for ev in ("mesh", "transform", "params", "contacts", "result", "selection", "dirty"):
            self._emit(ev)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
class AddContactCommand(Command):
    def __init__(self, project: Project, contact: Contact) -> None:
        self.p, self.contact = project, copy.deepcopy(contact)
        self.text = f"Add contact #{contact.id}"
        self.prev_edited = project.contacts_edited

    def redo(self) -> None:
        self.p.contacts.append(copy.deepcopy(self.contact))
        self.p.contacts_edited = True
        self.p.contacts_version += 1
        self.p._emit("contacts")

    def undo(self) -> None:
        self.p.contacts = [c for c in self.p.contacts if c.id != self.contact.id]
        self.p.contacts_edited = self.prev_edited
        self.p.contacts_version += 1
        if self.p.selected_contact_id == self.contact.id:
            self.p.selected_contact_id = None
            self.p._emit("selection")
        self.p._emit("contacts")


class RemoveContactCommand(Command):
    def __init__(self, project: Project, contact_id: int) -> None:
        self.p, self.id = project, contact_id
        self.index = next(i for i, c in enumerate(project.contacts) if c.id == contact_id)
        self.contact = copy.deepcopy(project.contacts[self.index])
        self.prev_edited = project.contacts_edited
        self.text = f"Remove contact #{contact_id}"

    def redo(self) -> None:
        self.p.contacts = [c for c in self.p.contacts if c.id != self.id]
        self.p.contacts_edited = True
        self.p.contacts_version += 1
        if self.p.selected_contact_id == self.id:
            self.p.selected_contact_id = None
            self.p._emit("selection")
        self.p._emit("contacts")

    def undo(self) -> None:
        self.p.contacts.insert(min(self.index, len(self.p.contacts)), copy.deepcopy(self.contact))
        self.p.contacts_edited = self.prev_edited
        self.p.contacts_version += 1
        self.p._emit("contacts")


class EditContactCommand(Command):
    def __init__(self, project: Project, contact_id: int, old: dict, new: dict) -> None:
        self.p, self.id, self.old, self.new = project, contact_id, old, new
        self.prev_edited = project.contacts_edited
        self.text = f"Edit contact #{contact_id}"

    def _set(self, values: dict, edited: bool) -> None:
        c = self.p.contact(self.id)
        if c is not None:
            for k, v in values.items():
                setattr(c, k, copy.deepcopy(v))
        self.p.contacts_edited = edited
        self.p.contacts_version += 1
        self.p._emit("contacts")

    def redo(self) -> None:
        self._set(self.new, True)

    def undo(self) -> None:
        self._set(self.old, self.prev_edited)

    def merge_with(self, other: Command) -> bool:
        if (isinstance(other, EditContactCommand) and other.id == self.id
                and set(other.new) == set(self.new)):
            self.new = other.new
            return True
        return False


class SetParamsCommand(Command):
    MERGE_WINDOW_S = 1.5

    def __init__(self, project: Project, old: SupportParams, new: SupportParams,
                 old_profile: str, new_profile: str, text: str, field: Optional[str] = None):
        self.p = project
        self.old, self.new = copy.deepcopy(old), copy.deepcopy(new)
        self.old_profile, self.new_profile = old_profile, new_profile
        self.text, self.field = text, field
        self.time = time.monotonic()

    def redo(self) -> None:
        self.p._apply_params(self.new, self.new_profile)
        self.p._emit("params")

    def undo(self) -> None:
        self.p._apply_params(self.old, self.old_profile)
        self.p._emit("params")

    def merge_with(self, other: Command) -> bool:
        if (isinstance(other, SetParamsCommand) and self.field is not None
                and other.field == self.field and other.new_profile == self.new_profile
                and other.time - self.time < self.MERGE_WINDOW_S):
            self.new, self.time = other.new, other.time
            return True
        return False


class SetTransformCommand(Command):
    def __init__(self, project: Project, old, new, text: str = "Transform") -> None:
        self.p, self.old, self.new, self.text = project, old, new, text
        self.state_before = project.snapshot()
        self.state_after: Optional[ProjectState] = None
        self.time = time.monotonic()

    def redo(self) -> None:
        self.p._apply_transform(self.new)
        if self.state_after is not None:      # identical to the first computation
            self.p.restore(self.state_after)
        else:
            self.state_after = self.p.snapshot()
        for ev in ("transform", "mesh", "contacts", "result"):
            self.p._emit(ev)

    def undo(self) -> None:
        self.p.transform = copy.deepcopy(self.old)
        self.p._rebuild_world_mesh()
        self.p.restore(self.state_before)
        for ev in ("transform", "mesh", "contacts", "result"):
            self.p._emit(ev)

    def merge_with(self, other: Command) -> bool:
        # successive spin box steps of the same kind collapse into one undo entry
        if isinstance(other, SetTransformCommand) and other.text == self.text \
                and self.text in ("Rotate", "Scale", "Move") \
                and other.time - self.time < SetParamsCommand.MERGE_WINDOW_S:
            self.new, self.state_after, self.time = other.new, other.state_after, other.time
            return True
        return False


class ReplaceMeshCommand(Command):
    def __init__(self, project: Project, old_mesh, new_mesh, old_repaired: bool,
                 new_repaired: bool, text: str) -> None:
        self.p, self.old_mesh, self.new_mesh = project, old_mesh, new_mesh
        self.old_repaired, self.new_repaired, self.text = old_repaired, new_repaired, text
        self.state_before = project.snapshot()

    def _set(self, mesh, repaired: bool) -> None:
        old_matrix = self.p.world_matrix.copy()
        self.p.base_mesh = mesh
        self.p.base_is_repaired = repaired
        self.p._rebuild_world_mesh()
        return old_matrix

    def redo(self) -> None:
        old_matrix = self._set(self.new_mesh, self.new_repaired)
        self.p._map_manual_contacts(self.p.world_matrix @ np.linalg.inv(old_matrix))
        for ev in ("mesh", "contacts", "result"):
            self.p._emit(ev)

    def undo(self) -> None:
        self._set(self.old_mesh, self.old_repaired)
        self.p.restore(self.state_before)
        for ev in ("mesh", "contacts", "result"):
            self.p._emit(ev)


class SetStateCommand(Command):
    """Replace the contacts + result at once (after a generation, or 'clear supports')."""

    def __init__(self, project: Project, before: ProjectState, after: ProjectState, text: str):
        self.p, self.before, self.after, self.text = project, before, after, text

    def redo(self) -> None:
        self.p.restore(self.after)
        self.p._emit("contacts")
        self.p._emit("result")

    def undo(self) -> None:
        self.p.restore(self.before)
        self.p._emit("contacts")
        self.p._emit("result")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _params_diff(a: SupportParams, b: SupportParams) -> dict[str, tuple[Any, Any]]:
    """{field: (a value, b value)} for every differing field."""
    da, db = a.to_dict(), b.to_dict()
    out = {}
    for k, va in da.items():
        vb = db.get(k)
        if isinstance(va, float) and isinstance(vb, (int, float)) and not isinstance(vb, bool):
            if abs(va - vb) > 1e-9:
                out[k] = (va, vb)
        elif va != vb:
            out[k] = (va, vb)
    return out


def _eq(a, b) -> bool:
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return np.array_equal(np.asarray(a), np.asarray(b))
    return a == b


def _transform_equal(a, b) -> bool:
    return np.allclose(np.asarray(a.to_matrix(), float), np.asarray(b.to_matrix(), float),
                       atol=1e-12)


def _scalar_scale(s) -> float:
    if isinstance(s, (list, tuple, np.ndarray)):
        return float(np.asarray(s, float).mean())
    return float(s)


def _like_scale(template, value: float):
    if isinstance(template, (list, tuple, np.ndarray)):
        return tuple(float(value) for _ in range(len(template)))
    return float(value)


def _stl_bytes(mesh: trimesh.Trimesh) -> bytes:
    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


def _stl_load(data: bytes) -> trimesh.Trimesh:
    m = trimesh.load(io.BytesIO(data), file_type="stl", force="mesh", process=False)
    m.merge_vertices()
    return m

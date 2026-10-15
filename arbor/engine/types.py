"""Shared data types of the support engine.

Coordinate frame (everywhere in the engine): millimetres, Z up, build plate at z = 0.
The model mesh handed to the engine is already transformed and placed on the plate.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import numpy as np


class ContactReason(str, Enum):
    ISLAND = "island"          # area whose first layer has nothing below it
    EDGE = "edge"              # along a hanging (downward facing) edge
    CORNER = "corner"          # corner of a flat overhang face
    SURFACE = "surface"        # filling a large flat overhang area
    LOW_POINT = "low_point"    # local minimum (tip of arm, chin, ...)
    MANUAL = "manual"          # placed by the user


@dataclass
class Contact:
    id: int
    position: np.ndarray            # (3,) point on the model surface
    normal: np.ndarray              # (3,) outward surface normal at the point
    reason: ContactReason
    manual: bool = False
    tip_diameter: Optional[float] = None   # per-contact override
    enabled: bool = True
    rests_on_model: bool = False    # set by the tree builder if its foot stands on the model
    note: str = ""                  # human readable explanation (why it was placed)

    def to_dict(self) -> dict:
        return {"id": self.id, "position": [float(v) for v in self.position],
                "normal": [float(v) for v in self.normal], "reason": self.reason.value,
                "manual": self.manual, "tip_diameter": self.tip_diameter,
                "enabled": self.enabled, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict) -> "Contact":
        return cls(id=int(d["id"]), position=np.asarray(d["position"], float),
                   normal=np.asarray(d["normal"], float), reason=ContactReason(d["reason"]),
                   manual=bool(d.get("manual", False)), tip_diameter=d.get("tip_diameter"),
                   enabled=bool(d.get("enabled", True)), note=d.get("note", ""))


@dataclass
class SupportRegion:
    """A connected area of a layer that has nothing printed under it."""
    kind: str                        # "island" | "overhang"
    z: float                         # layer height where the region appears
    polygon: object                  # shapely Polygon / MultiPolygon in XY
    area: float


@dataclass
class AnalysisResult:
    layer_height: float
    layer_z: np.ndarray                              # (L,) slice heights
    regions: list[SupportRegion] = field(default_factory=list)
    overhang_face_mask: Optional[np.ndarray] = None  # (F,) bool, faces needing support
    hanging_edges: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)
    corners: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    low_points: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    layer_polygons: Optional[list] = None            # optional per layer shapely geometry
    stats: dict = field(default_factory=dict)


class NodeKind(str, Enum):
    TIP = "tip"            # top of a tip cone (on the model)
    BRANCH = "branch"
    TRUNK = "trunk"
    FOOT = "foot"          # on the build plate (or raft)
    MODEL_FOOT = "model_foot"  # standing on the model


@dataclass
class TreeNode:
    id: int
    position: np.ndarray       # (3,)
    radius: float
    kind: NodeKind
    parent: Optional[int] = None    # the node below (towards the ground); None for feet
    contact_id: Optional[int] = None


@dataclass
class SupportTree:
    nodes: dict[int, TreeNode] = field(default_factory=dict)

    def add(self, position, radius, kind, parent=None, contact_id=None) -> TreeNode:
        nid = len(self.nodes)
        while nid in self.nodes:
            nid += 1
        n = TreeNode(nid, np.asarray(position, float), float(radius), kind, parent, contact_id)
        self.nodes[nid] = n
        return n

    def edges(self) -> list[tuple[int, int]]:
        """(child, parent) pairs, i.e. (upper, lower)."""
        return [(n.id, n.parent) for n in self.nodes.values() if n.parent is not None]

    def children(self) -> dict[int, list[int]]:
        ch: dict[int, list[int]] = {i: [] for i in self.nodes}
        for n in self.nodes.values():
            if n.parent is not None:
                ch[n.parent].append(n.id)
        return ch

    def roots(self) -> list[TreeNode]:
        return [n for n in self.nodes.values() if n.parent is None]

    def stats(self) -> dict:
        kinds = [n.kind for n in self.nodes.values()]
        ch = self.children()
        junctions = sum(1 for i, c in ch.items() if len(c) > 1)
        return {"tips": kinds.count(NodeKind.TIP),
                "feet": kinds.count(NodeKind.FOOT),
                "model_feet": kinds.count(NodeKind.MODEL_FOOT),
                "trees": len(self.roots()),
                "junctions": junctions,
                "segments": len(self.edges())}


@dataclass
class Issue:
    severity: str                       # "error" | "warning" | "info"
    code: str                           # machine readable, e.g. "unsupported_region"
    message: str
    position: Optional[np.ndarray] = None
    contact_id: Optional[int] = None


@dataclass
class GenerationResult:
    contacts: list[Contact]
    tree: SupportTree
    support_mesh: object          # trimesh.Trimesh (may be empty)
    raft_mesh: object             # trimesh.Trimesh or None
    issues: list[Issue]
    analysis: AnalysisResult
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


class Cancelled(Exception):
    """Raised by Progress.check() when the user cancelled the computation."""


class Progress:
    """Progress reporting + cooperative cancellation, safe to use from a worker thread."""

    def __init__(self, callback: Optional[Callable[[float, str], None]] = None,
                 cancel_event: Optional[threading.Event] = None):
        self.callback = callback
        self.cancel_event = cancel_event or threading.Event()
        self._lo, self._hi = 0.0, 1.0

    def sub(self, lo: float, hi: float) -> "Progress":
        """A child progress mapping [0,1] to [lo,hi] of this one."""
        p = Progress(self.callback, self.cancel_event)
        span = self._hi - self._lo
        p._lo, p._hi = self._lo + lo * span, self._lo + hi * span
        return p

    def update(self, fraction: float, message: str = "") -> None:
        self.check()
        if self.callback:
            f = self._lo + max(0.0, min(1.0, fraction)) * (self._hi - self._lo)
            self.callback(f, message)

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        self.cancel_event.set()

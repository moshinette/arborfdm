"""Support generation parameters and printing profiles.

All lengths are millimetres, all angles are degrees.
Convention for angles: measured from the vertical (Z) axis.
  overhang_angle_deg: a face whose downward tilt from vertical exceeds this value needs
  support (0 = vertical wall, 90 = flat ceiling). Typical FDM default: 50-60.
  branch_angle_deg: maximum tilt of a support segment from vertical.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

RAFT_COVERAGES = ("feet", "footprint", "bed")
RAFT_LIFT_MAX = 5.0          # mm: largest model lift above the raft (auto or manual)
RAFT_RELEASE_GAP_MAX = 0.5   # mm: largest release gap between raft and model

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"


@dataclass
class SupportParams:
    # --- printer / process -------------------------------------------------
    nozzle_diameter: float = 0.4
    line_width: float = 0.42
    layer_height: float = 0.12

    # --- detection ----------------------------------------------------------
    overhang_angle_deg: float = 55.0
    # Unsupported areas smaller than this (mm^2) are ignored (noise / bridging-safe)
    min_region_area: float = 0.15
    # Maximum horizontal distance a layer can extend beyond the layer below
    # before it is considered unsupported (mm). <= 0 means derived from angle.
    max_unsupported_overhang: float = 0.0

    # --- contacts -----------------------------------------------------------
    contact_spacing: float = 3.0          # max spacing of contacts across flat overhang areas
    edge_spacing: float = 2.0             # spacing of contacts along hanging edges
    min_contact_distance: float = 0.8     # contacts closer than this are merged
    contact_density: float = 1.0          # multiplier (>1 = more contacts)
    manual_only: bool = False             # only user-placed contacts are used
    # Overhang severity (docs/ALGORITHM.md, "Sévérité des surplombs"): an overhang whose
    # layers step out by more than the overhang threshold but less than
    # SEVERE_STEP_LINE_WIDTHS line widths per layer is "moderate" (it prints with some
    # sagging); its contacts are mild_overhang_spacing times farther apart. Islands,
    # hanging edges, corners, low points and steeper overhangs keep the full density.
    # 1 = no distinction (v0.3 behaviour).
    mild_overhang_spacing: float = 1.0
    # Island anchoring (docs/ALGORITHM.md, "Ancrage des debuts d'ilots"): an island whose
    # first layer is much smaller than its footprint island_anchor_height higher (round
    # bottom that starts in a point: hand, ball, tip of a tail) gets a ring of 6 contacts
    # of radius island_anchor_spacing around its lowest point, on that footprint, and
    # these contacts plus the island's own contact get thicker tips (island_tip_diameter,
    # ~2.5 line widths, so that the slicer still extrudes the top of the tip).
    # island_anchor_height = 0 disables the anchoring.
    island_anchor_height: float = 1.0
    island_anchor_spacing: float = 1.2
    island_tip_diameter: float = 1.0

    # --- tips ---------------------------------------------------------------
    tip_diameter: float = 0.45            # diameter where the tip touches the model
    tip_length: float = 1.6               # length of the conical tip
    tip_base_diameter: float = 1.0        # diameter where the tip meets its branch
    tip_penetration: float = 0.05         # controlled penetration into the model
    tip_shape: str = "cone"               # "cone" | "ball"
    z_gap: float = 0.0                    # extra vertical clearance (subtracted from tip)
    # Support interface (docs/ALGORITHM.md, "Jeu d'interface"): edge, corner and surface
    # tips stop this many layers below the model, so the slicer leaves an air layer and
    # the tip is not fused to the model (the "top Z distance" of slicer supports). Island
    # contacts (and their anchors) and low points stay welded: a part that starts in
    # the air needs its first layers held. 0 = every tip welded (v0.3 behaviour).
    interface_gap_layers: int = 0
    xy_clearance: float = 0.8             # min horizontal clearance of branches from the model

    # --- branches / trunks --------------------------------------------------
    branch_min_diameter: float = 1.0
    branch_max_diameter: float = 2.4
    branch_angle_deg: float = 40.0
    segment_length: float = 1.0
    merge_distance: float = 6.0           # max horizontal distance for two branches to merge
    junction_radius_factor: float = 1.15  # junction sphere radius / branch radius
    trunk_diameter: float = 2.6
    trunk_growth_per_mm: float = 0.02     # diameter growth per mm descended
    min_tree_distance: float = 1.5
    walls: int = 3                        # informational: perimeters advised in slicer

    # --- feet ---------------------------------------------------------------
    foot_diameter: float = 6.0
    foot_height: float = 0.6
    collar_diameter: float = 4.0
    collar_height: float = 1.2

    # --- braces -------------------------------------------------------------
    braces_enabled: bool = True
    brace_diameter: float = 0.8
    brace_min_height: float = 12.0        # only trunks taller than this get braces
    brace_spacing_z: float = 8.0
    brace_max_length: float = 12.0

    # --- raft ---------------------------------------------------------------
    raft_enabled: bool = True
    raft_thickness: float = 0.8
    raft_margin: float = 2.0
    # raft_coverage (docs/ALGORITHM.md, "Raft"):
    #   "feet"      raft under the feet only (hulls of the foot clusters, minus the model
    #               footprint, joined by a ring); the model stands on the plate (v0.1)
    #   "footprint" one plate = convex hull of the model base + the feet, + raft_margin;
    #               the model rests on the raft
    #   "bed"       bounding rectangle of the model and the feet, + raft_margin (never
    #               the physical plate); the model rests on the raft
    raft_coverage: str = "footprint"
    # Export only: in the "separate" 3MF layout, write "2 walls, 10 % infill, no solid small
    # areas" on the supports object for OrcaSlicer / Bambu Studio (-31 % material and time on
    # the figurine's supports, measured with Orca CLI). Off by default: not validated by print.
    light_slicer_supports: bool = False
    # Model lift above the raft (only when the model rests on the raft: coverage
    # "footprint" / "bed"). raft_lift_auto: raise the model by the smallest amount that
    # leaves room for a full tip under overhangs that are too close to the raft, unless
    # that would make things worse (flat base to support); see placement.plan_lift.
    # raft_lift: lift used when raft_lift_auto is False (0 = model on the raft).
    raft_lift_auto: bool = True
    raft_lift: float = 0.0
    # Model on the raft: release gap between the raft top and the bottom of the model,
    # under the whole footprint (the slicer prints the first model layer over it like a
    # support interface, so the raft peels off). One layer height in the profiles;
    # 0 = model welded onto the raft.
    raft_release_gap: float = 0.12

    # --- hollow supports ----------------------------------------------------
    # Wide trunk / branch sections get a sealed inner void, hollow_wall thick walls
    # (tips, thin branches, feet and raft stay solid). Off by default.
    hollow_supports: bool = False
    hollow_wall: float = 1.26           # 3 line widths

    # --- supports standing on the model -------------------------------------
    allow_on_model: bool = True
    on_model_tip_diameter: float = 0.4

    # --- meshing ------------------------------------------------------------
    circle_segments: int = 12

    # ------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupportParams":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown parameter(s): {sorted(unknown)}")
        return cls(**data)

    def updated(self, **kw: Any) -> "SupportParams":
        d = self.to_dict()
        d.update(kw)
        return SupportParams.from_dict(d)

    def validate(self) -> list[str]:
        """Return a list of human readable problems (empty = valid)."""
        import math
        p = []
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, float) and not math.isfinite(v):
                p.append(f"{f.name} must be a finite number")
        if p:
            return p
        positive = ("line_width", "layer_height", "tip_diameter", "tip_length", "tip_base_diameter",
                    "branch_min_diameter", "branch_max_diameter", "segment_length",
                    "trunk_diameter", "foot_diameter", "collar_diameter", "on_model_tip_diameter",
                    "brace_diameter", "brace_spacing_z", "brace_max_length")
        for name in positive:
            if getattr(self, name) <= 0:
                p.append(f"{name} must be > 0")
        non_negative = ("merge_distance", "min_region_area", "tip_penetration", "z_gap", "xy_clearance",
                        "trunk_growth_per_mm", "min_tree_distance", "foot_height", "collar_height",
                        "brace_min_height", "raft_margin", "raft_lift", "raft_release_gap")
        for name in non_negative:
            if getattr(self, name) < 0:
                p.append(f"{name} must be >= 0")
        if self.raft_enabled and self.raft_thickness <= 0:
            p.append("raft_thickness must be > 0 when the raft is enabled")
        if self.raft_coverage not in RAFT_COVERAGES:
            p.append("raft_coverage must be one of " + ", ".join(RAFT_COVERAGES))
        if self.raft_release_gap > RAFT_RELEASE_GAP_MAX:
            p.append(f"raft_release_gap must be <= {RAFT_RELEASE_GAP_MAX:g} mm")
        if self.raft_lift > RAFT_LIFT_MAX:
            p.append(f"raft_lift must be <= {RAFT_LIFT_MAX:g} mm")
        if self.hollow_supports and self.hollow_wall < 2 * self.line_width:
            p.append("hollow_wall must be >= 2 line widths")
        if self.junction_radius_factor < 1.0:
            p.append("junction_radius_factor must be >= 1")
        if not 0.1 <= self.nozzle_diameter <= 1.2:
            p.append("nozzle_diameter out of range [0.1, 1.2]")
        if not 0.02 <= self.layer_height <= 0.8 * self.nozzle_diameter:
            p.append("layer_height must be in [0.02, 0.8 x nozzle]")
        if not 10 <= self.overhang_angle_deg <= 89:
            p.append("overhang_angle_deg out of range [10, 89]")
        if self.tip_diameter < 0.5 * self.nozzle_diameter:
            p.append("tip_diameter below half the nozzle diameter is not printable")
        if self.island_anchor_height < 0:
            p.append("island_anchor_height must be >= 0")
        if self.island_anchor_spacing <= 0:
            p.append("island_anchor_spacing must be > 0")
        if self.island_tip_diameter < 0.5 * self.nozzle_diameter:
            p.append("island_tip_diameter below half the nozzle diameter is not printable")
        if self.tip_base_diameter < self.tip_diameter:
            p.append("tip_base_diameter must be >= tip_diameter")
        if self.branch_min_diameter > self.branch_max_diameter:
            p.append("branch_min_diameter must be <= branch_max_diameter")
        if not 0 <= self.branch_angle_deg <= 60:
            p.append("branch_angle_deg out of range [0, 60]")
        if self.min_contact_distance <= 0 or self.contact_spacing <= 0 or self.edge_spacing <= 0:
            p.append("contact spacings must be positive")
        if self.contact_density <= 0:
            p.append("contact_density must be positive")
        if not 1.0 <= self.mild_overhang_spacing <= 5.0:
            p.append("mild_overhang_spacing out of range [1, 5]")
        g = self.interface_gap_layers
        if not (isinstance(g, (int, float)) and float(g).is_integer() and 0 <= g <= 3):
            p.append("interface_gap_layers must be an integer in [0, 3]")
        if self.circle_segments < 6:
            p.append("circle_segments must be >= 6")
        if self.tip_shape not in ("cone", "ball"):
            p.append("tip_shape must be 'cone' or 'ball'")
        return p


def model_on_raft(params: SupportParams) -> bool:
    """True when the model rests on the raft (coverage "footprint" / "bed")."""
    return bool(params.raft_enabled) and params.raft_coverage != "feet"


def support_base_z(params: SupportParams) -> float:
    """z (engine frame) of the surface the supports stand on (raft top or plate).

    The model's own ground is always z = 0 (analysis and contacts treat what is printed
    on it as resting). Raft under the feet only: the model is on the plate and the raft
    occupies [0, raft_thickness]. Model on the raft: the raft top is raft_release_gap
    below the model's ground, the raft occupies [-gap - raft_thickness, -gap] (the model
    is never moved when it is not lifted)."""
    if not params.raft_enabled:
        return 0.0
    if params.raft_coverage == "feet":
        return float(params.raft_thickness)
    return -float(params.raft_release_gap)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
@dataclass
class Profile:
    key: str
    label: str
    description: str
    params: SupportParams = field(default_factory=SupportParams)


def load_profiles(directory: Path | None = None) -> dict[str, Profile]:
    """Load every *.json profile. A profile may 'inherit' another and override values."""
    directory = directory or PROFILE_DIR
    raw: dict[str, dict] = {}
    for f in sorted(directory.glob("*.json")):
        raw[f.stem] = json.loads(f.read_text(encoding="utf-8"))

    resolved: dict[str, dict] = {}

    def resolve(key: str, stack: tuple = ()) -> dict:
        if key in resolved:
            return resolved[key]
        if key in stack:
            raise ValueError(f"Profile inheritance cycle: {stack + (key,)}")
        if key not in raw:
            raise KeyError(f"Unknown profile {key!r}")
        d = raw[key]
        base = resolve(d["inherits"], stack + (key,))["values"] if d.get("inherits") else {}
        values = {**base, **d.get("values", {})}
        resolved[key] = {**d, "values": values}
        return resolved[key]

    out = {}
    for key in raw:
        d = resolve(key)
        if d.get("abstract"):
            continue
        out[key] = Profile(key, d.get("label", key), d.get("description", ""),
                           SupportParams.from_dict(d["values"]))
    return out

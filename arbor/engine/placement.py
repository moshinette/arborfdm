"""Model placement above the raft (raft_lift): how far the model is raised.

Frames. The model handed to the engine (user frame) rests on z = 0. When the model rests
on the raft (raft_coverage "footprint" / "bed", params.model_on_raft), the raft top is
the ground: z = 0 in the engine frame, the raft occupies [-raft_thickness, 0]. With a
lift L > 0 the engine works on the model translated by +L (engine frame) and every
result (supports, raft, tree, contacts, analysis, issue positions) is translated back
by -L before it is returned: the model itself is never moved nor modified, everything
else is placed under it. GenerationResult.model_lift = L, GenerationResult.plate_z = z
of the build plate in the user frame (-(raft_thickness + raft_release_gap + L) on a raft
under the model,
0 otherwise).

Automatic lift (raft_lift_auto). An overhang whose lowest point is between one layer
and lift_clearance(params) above the ground cannot get a real tip: the router builds a
short vertical cone (or nothing below one layer), which prints badly and is hard to
remove. Raising the model by the smallest L such that every downward face is at least
lift_clearance above the ground gives each of them a full tip + branch; the bottom of
the model then becomes an overhang too and is supported (the analysis sees an island).
It is refused (L = 0, reason given) when that makes things worse:
  * the flat base that would have to be supported is larger than LIFT_MAX_BASE_AREA
    (a figurine socle, a block: it is stable on the raft, supporting it all is worse);
  * the base is larger than the low overhang area it would fix;
  * the lift needed is above RAFT_LIFT_MAX.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh

from .params import RAFT_LIFT_MAX, SupportParams, model_on_raft

LIFT_EXTRA_LAYERS = 2          # layers of foot under a full tip
LIFT_MAX_BASE_AREA = 25.0      # mm2: flat base above which the model is never lifted
LIFT_STEP = 0.01               # mm: lift rounded up to this


@dataclass
class LiftPlan:
    lift: float = 0.0              # mm the model is raised above the ground (raft top)
    mode: str = "none"             # "none" | "auto" | "manual"
    refused: bool = False          # a lift was needed but refused (reason)
    reason: str = ""
    clearance: float = 0.0         # required free height under an overhang (mm)
    low_overhang_area: float = 0.0 # mm2 of downward faces closer than clearance
    base_area: float = 0.0         # mm2 of downward faces printed on the first layer
    min_gap: float = 0.0           # lowest downward face above the ground (mm)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def lift_clearance(params: SupportParams) -> float:
    """Free height under a contact for a full tip on a foot: tip_length + room for a
    branch (same reach as the router's short support test) + LIFT_EXTRA_LAYERS layers."""
    P = params
    return float(P.tip_length + P.branch_min_diameter / 2 + 0.1 + LIFT_EXTRA_LAYERS * P.layer_height)


def plan_lift(mesh: trimesh.Trimesh, params: SupportParams) -> LiftPlan:
    """Lift of the model above the raft top (see module docstring)."""
    P = params
    if not model_on_raft(P):
        return LiftPlan(reason="the model stands on the plate (no raft under the model)")
    clear = lift_clearance(P)
    if not P.raft_lift_auto:
        L = float(min(max(P.raft_lift, 0.0), RAFT_LIFT_MAX))
        return LiftPlan(lift=L, mode="manual" if L > 0 else "none", clearance=clear,
                        reason=f"manual lift {L:.2f} mm" if L > 0 else "manual: no lift")
    from .analysis import effective_overhang_angle_deg
    V = np.asarray(mesh.vertices, float)
    F = np.asarray(mesh.faces, np.int64)
    if not len(F):
        return LiftPlan(reason="empty model")
    fz = V[:, 2][F]                 # height above the ground (z = 0, raft top)
    n = np.asarray(mesh.face_normals, float)
    area = np.asarray(mesh.area_faces, float)
    h = float(P.layer_height)
    sin_t = math.sin(math.radians(effective_overhang_angle_deg(P)))
    down = -n[:, 2] > sin_t
    lo, hi = fz.min(axis=1), fz.max(axis=1)
    on_plate = hi < h
    low = down & ~on_plate & (lo < clear)
    plan = LiftPlan(mode="auto", clearance=clear,
                    low_overhang_area=float(area[low].sum()),
                    base_area=float(area[down & on_plate].sum()),
                    min_gap=float(lo[down & ~on_plate].min()) if np.any(down & ~on_plate) else math.inf)
    if plan.low_overhang_area < P.min_region_area:
        plan.reason = (f"no overhang closer than {clear:.2f} mm to the raft: the model rests on it")
        return plan
    # every downward face (base included) must end up at least `clear` above the ground
    need = clear - float(lo[down].min())
    need = math.ceil(need / LIFT_STEP - 1e-9) * LIFT_STEP
    if need > RAFT_LIFT_MAX + 1e-9:
        plan.refused = True
        plan.reason = f"lift needed {need:.2f} mm is above the {RAFT_LIFT_MAX:g} mm limit"
        return plan
    if plan.base_area > LIFT_MAX_BASE_AREA:
        plan.refused = True
        plan.reason = (f"{plan.low_overhang_area:.1f} mm2 of overhang closer than {clear:.2f} mm to the "
                       f"raft, but lifting would leave a flat base of {plan.base_area:.1f} mm2 to "
                       f"support (> {LIFT_MAX_BASE_AREA:g} mm2): the model rests on the raft")
        return plan
    if plan.base_area > plan.low_overhang_area:
        plan.refused = True
        plan.reason = (f"lifting would add more area to support ({plan.base_area:.1f} mm2 of base) "
                       f"than it fixes ({plan.low_overhang_area:.1f} mm2 of low overhang)")
        return plan
    plan.lift = float(need)
    plan.reason = (f"{plan.low_overhang_area:.1f} mm2 of overhang closer than {clear:.2f} mm to the raft "
                   f"(lowest {max(plan.min_gap, 0.0):.2f} mm): model raised by {need:.2f} mm so that "
                   "each gets a full tip; its bottom is supported")
    return plan

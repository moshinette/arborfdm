"""Overhang threshold: step cap at one line width and coherence between the
layer detector and the face mask (docs/research/overhang_angles.md)."""
from __future__ import annotations

import math

import pytest
import trimesh

from arbor.engine import analysis as A
from arbor.engine import pipeline
from arbor.engine.params import load_profiles
from arbor.engine.types import Progress
from tests.models import synthetic as S

PROFILES = load_profiles()
P02 = PROFILES["nozzle_02_normal"].params      # 0.22 mm lines, 0.08 mm layers
P04 = PROFILES["nozzle_04_normal"].params      # 0.42 mm lines, 0.12 mm layers
EX = __import__("pathlib").Path(__file__).resolve().parent.parent / "examples"


def _analyze(mesh, params):
    return A.analyze(mesh, params, Progress())


# ---------------------------------------------------------------------------
# step and effective angle
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("params", [P02, P04], ids=["0.2", "0.4"])
def test_default_profiles_unchanged(params):
    """The defaults (55 deg) stay below the cap: same step as before."""
    assert params.overhang_angle_deg == 55.0
    d, src = A.max_layer_step(params)
    assert src == "angle"
    assert d == pytest.approx(params.layer_height * math.tan(math.radians(55.0)))
    assert A.effective_overhang_angle_deg(params) == 55.0


@pytest.mark.parametrize("params,lo,hi", [(P02, 0.50, 0.53), (P04, 0.40, 0.42)], ids=["0.2", "0.4"])
def test_default_step_in_line_widths(params, lo, hi):
    """Default step per layer, in line widths: 0.52 (0.2 mm) and 0.41 (0.4 mm),
    inside the 0.34-0.79 range of the slicer defaults listed in the research note
    and around the 0.5 used by the automatic modes of PrusaSlicer and OrcaSlicer."""
    d, _ = A.max_layer_step(params)
    assert lo <= d / params.line_width <= hi


def test_step_capped_at_one_line_width():
    p = P02.updated(overhang_angle_deg=80.0)          # 0.08 x tan(80) = 0.45 mm > 0.22
    d, src = A.max_layer_step(p)
    assert src == "line_width" and d == pytest.approx(0.22)
    # atan(0.22 / 0.08) = 70.0 deg
    assert A.effective_overhang_angle_deg(p) == pytest.approx(70.02, abs=0.01)
    # just below the cap: untouched
    p = P02.updated(overhang_angle_deg=69.0)
    assert A.max_layer_step(p)[1] == "angle"


def test_explicit_step_is_not_capped():
    p = P02.updated(max_unsupported_overhang=2.0)
    assert A.max_layer_step(p) == (2.0, "explicit")
    assert A.effective_overhang_angle_deg(p) == pytest.approx(math.degrees(math.atan2(2.0, 0.08)))


# ---------------------------------------------------------------------------
# coherence face mask / layers
# ---------------------------------------------------------------------------
def test_explicit_step_drives_face_mask():
    """60 deg slope, explicit step 0.2 mm. Before: the face mask used the 55 deg
    parameter and flagged the slope while the layers did not (48 contacts with
    no region on examples/inclined_60deg.stl, 0.2 profile)."""
    m = S.inclined_surface(60.0)
    # 0.08 x tan(60) = 0.139 < 0.2: no overhang for either detector
    r = _analyze(m, P02.updated(max_unsupported_overhang=0.2))
    assert r.stats["effective_overhang_angle_deg"] == pytest.approx(68.2, abs=0.1)
    assert not r.overhang_face_mask.any()
    assert r.stats["overhang_area"] == 0
    # 0.12 x tan(60) = 0.208 > 0.2: both see it (effective angle 59.0 deg)
    r = _analyze(m, P04.updated(max_unsupported_overhang=0.2))
    assert r.stats["effective_overhang_angle_deg"] == pytest.approx(59.04, abs=0.01)
    assert r.overhang_face_mask.any()


@pytest.mark.parametrize("slope,expect", [(65.0, False), (75.0, True)])
def test_capped_angle_still_detects_unprintable_slopes(slope, expect):
    """overhang_angle_deg = 89 on the 0.2 profile. A 75 deg slope steps 0.30 mm
    per 0.08 mm layer: more than a 0.22 mm line, nothing under the outer bead.
    A 65 deg slope steps 0.17 mm: printable, still ignored."""
    m = S.inclined_surface(slope)
    r = _analyze(m, P02.updated(overhang_angle_deg=89.0))
    assert r.stats["max_step_source"] == "line_width"
    assert bool(r.overhang_face_mask.any()) == expect
    assert (r.stats["overhang_area"] > 0) == expect


def test_without_cap_the_75deg_slope_was_missed(monkeypatch):
    """Previous behaviour, reproduced by disabling the cap: the 75 deg slope got
    no region and no masked face at 89 deg."""
    monkeypatch.setattr(A, "MAX_STEP_LINE_WIDTHS", float("inf"))
    r = _analyze(S.inclined_surface(75.0), P02.updated(overhang_angle_deg=89.0))
    assert not r.overhang_face_mask.any()
    assert r.stats["overhang_area"] == 0


def test_high_angle_suspended_arm_has_no_unsupported_region():
    """examples/suspended_arm.stl, 0.2 profile at 89 deg: before the cap the
    analysis kept slice regions the face mask did not cover, 1 contact was placed
    and verify reported an unsupported region (error). Measured after: 8 contacts."""
    mesh = trimesh.load(EX / "suspended_arm.stl", force="mesh")
    r = pipeline.generate(mesh, P02.updated(overhang_angle_deg=89.0))
    assert not [i for i in r.issues if i.code == "unsupported_region"]
    assert len(r.contacts) >= 5

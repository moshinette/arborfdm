"""QA checks of arbor.core.repair on the deliberately broken synthetic meshes."""
from __future__ import annotations

import pytest

from tests.models import synthetic as S

repair = pytest.importorskip("arbor.core.repair")


@pytest.mark.parametrize("name", sorted(S.GENERATORS))
def test_clean_models_report_valid(name):
    r = repair.check_mesh(S.GENERATORS[name]())
    assert r.is_valid, f"{name}: {r.summary()}"
    assert r.watertight and r.winding_consistent and not r.inverted


def test_detect_hole():
    r = repair.check_mesh(S.broken_hole())
    assert not r.is_valid and r.n_boundary_edges > 0 and r.n_holes == 1


def test_detect_flipped():
    r = repair.check_mesh(S.broken_flipped())
    assert not r.is_valid and r.n_inconsistent_edges > 0


def test_detect_degenerate():
    m = S.broken_degenerate()
    r = repair.check_mesh(m)
    assert r.n_degenerate_faces == m.metadata["degenerate"]


def test_detect_nonmanifold():
    r = repair.check_mesh(S.broken_nonmanifold())
    assert r.n_non_manifold_edges >= 1 and not r.is_valid


def test_detect_inverted():
    m = S.cube()
    m.invert()
    assert repair.check_mesh(m).inverted


@pytest.mark.parametrize("name", ["broken_hole", "broken_flipped", "broken_degenerate"])
def test_repair_fixes(name):
    m = S.BROKEN_GENERATORS[name]()
    before_v = m.vertices.copy()
    fixed, rep = repair.repair_mesh(m)
    assert (m.vertices == before_v).all(), "input mesh was modified"
    assert rep.success, rep.summary()
    assert fixed.is_watertight and fixed.is_winding_consistent
    assert fixed.volume == pytest.approx(8000, rel=1e-6)


def test_repair_inverted():
    m = S.cube()
    m.invert()
    fixed, rep = repair.repair_mesh(m)
    assert rep.success and fixed.volume == pytest.approx(8000)


def test_repair_nonmanifold_reports_or_fixes():
    fixed, rep = repair.repair_mesh(S.broken_nonmanifold())
    if rep.success:
        assert repair.check_mesh(fixed).n_non_manifold_edges == 0
    else:
        assert rep.failure_reasons, "failed without reason"


def test_repair_pinched_hole():
    """Hole whose boundary touches itself at a vertex (common in real broken STL)."""
    fixed, rep = repair.repair_mesh(S.broken_pinched_hole())
    assert rep.success, rep.summary()
    assert fixed.is_watertight

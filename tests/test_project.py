"""Project document: undo/redo, save/load, manual contacts, generation requests (Qt free)."""
from __future__ import annotations

import numpy as np
import pytest
import trimesh

from arbor.core.project import Project
from arbor.engine.params import load_profiles
from arbor.engine.types import (AnalysisResult, Contact, ContactReason, GenerationResult,
                                SupportTree)


@pytest.fixture(scope="module")
def profiles():
    return load_profiles()


@pytest.fixture
def project(profiles):
    p = Project(profiles)
    p.set_model(trimesh.creation.box((10, 20, 30)), name="box")
    return p


def fake_result(project, contacts):
    an = AnalysisResult(project.params.layer_height, np.zeros(0))
    return GenerationResult(contacts, SupportTree(), trimesh.creation.box((1, 1, 1)), None, [], an,
                            {"analysis": 0.1})


def test_model_is_placed_on_plate(project):
    assert project.mesh.bounds[0][2] == pytest.approx(0.0)
    assert np.allclose(project.mesh.bounds.mean(0)[:2], 0.0)
    assert np.allclose(project.dimensions(), (10, 20, 30))
    assert not project.dirty


def test_add_remove_contact_undo_redo(project):
    c = project.add_manual_contact((1, 2, 3), (0, 0, -2))
    assert c.manual and c.reason is ContactReason.MANUAL
    assert np.allclose(c.normal, (0, 0, -1))
    assert project.dirty and project.undo_stack.can_undo()
    c2 = project.add_manual_contact((4, 5, 6), (0, 0, -1))
    assert c2.id != c.id
    assert project.remove_contact(c.id)
    assert [x.id for x in project.contacts] == [c2.id]
    project.undo()
    assert [x.id for x in project.contacts] == [c.id, c2.id]
    project.undo()
    project.undo()
    assert project.contacts == [] and not project.dirty
    project.redo()
    project.redo()
    project.redo()
    assert [x.id for x in project.contacts] == [c2.id]
    assert not project.undo_stack.can_redo()


def test_edit_contact_merge_and_undo(project):
    c = project.add_manual_contact((0, 0, 5), (0, 0, -1))
    project.edit_contact(c.id, tip_diameter=0.5)
    project.edit_contact(c.id, tip_diameter=0.6)      # merged with the previous edit
    assert project.contact(c.id).tip_diameter == 0.6
    project.undo()
    assert project.contact(c.id).tip_diameter is None
    project.redo()
    project.edit_contact(c.id, enabled=False)
    assert project.contact(c.id).enabled is False
    project.undo()
    assert project.contact(c.id).enabled is True


def test_params_profile_and_changes(project, profiles):
    assert project.profile_key in profiles
    project.set_param("overhang_angle_deg", 60.0)
    assert project.changed_from_profile() == {"overhang_angle_deg": (55.0, 60.0)}
    project.apply_profile("nozzle_02_light")
    assert project.profile_key == "nozzle_02_light"
    assert project.params == profiles["nozzle_02_light"].params
    assert project.changed_from_profile() == {}
    project.undo()
    assert project.params.overhang_angle_deg == 60.0
    assert project.profile_key != "nozzle_02_light"
    project.reset_param("overhang_angle_deg")
    assert project.changed_from_profile() == {}


def test_transform_maps_manual_contacts_and_drops_auto(project):
    top = project.mesh.bounds[1][2]
    m = project.add_manual_contact((0, 0, top), (0, 0, 1))
    auto = Contact(99, np.array([1.0, 1.0, 0.0]), np.array([0, 0, -1.0]), ContactReason.ISLAND)
    project.apply_result(fake_result(project, [project.contact(m.id), auto]))
    assert len(project.contacts) == 2 and project.result is not None
    project.set_scale(2.0)
    assert project.dimensions()[2] == pytest.approx(60.0)
    assert [c.id for c in project.contacts] == [m.id]          # auto contacts dropped
    assert project.contacts[0].position[2] == pytest.approx(60.0)   # manual contact follows
    assert project.result is None
    project.undo()
    assert project.dimensions()[2] == pytest.approx(30.0)
    assert len(project.contacts) == 2 and project.result is not None
    project.redo()
    assert project.contacts[0].position[2] == pytest.approx(60.0)


def test_rotation_keeps_model_on_plate(project):
    project.set_rotation((90, 0, 0))
    assert project.mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-9)
    assert project.dimensions()[2] == pytest.approx(20.0)
    project.set_scale_for_height(40.0)
    assert project.dimensions()[2] == pytest.approx(40.0)


def test_regenerate_requests(project):
    m = project.add_manual_contact((0, 0, 30), (0, 0, 1))
    req = project.regenerate(full=True)
    assert req.mode == "full" and req.kwargs["contacts"] is None
    assert [c.id for c in req.kwargs["manual_contacts"]] == [m.id]
    assert req.kwargs["analysis"] is None
    an = AnalysisResult(0.12, np.zeros(0))
    res = fake_result(project, [project.contact(m.id)])
    res.analysis = an
    assert project.apply_result(res, req)
    # analysis is reused while mesh and detection params are unchanged
    project.add_manual_contact((1, 0, 30), (0, 0, 1))
    assert project.suggested_mode() == "branches"
    req2 = project.regenerate(full=False)
    assert req2.mode == "branches" and len(req2.kwargs["contacts"]) == 2
    assert req2.kwargs["analysis"] is an
    project.set_param("contact_spacing", 5.0)           # not a detection parameter
    assert project.regenerate().kwargs["analysis"] is an
    project.set_param("overhang_angle_deg", 50.0)       # detection parameter
    assert project.regenerate().kwargs["analysis"] is None


def test_full_regeneration_keeps_manual_contacts_and_stale_results(project):
    m = project.add_manual_contact((0, 0, 30), (0, 0, 1))
    req = project.regenerate(full=True)
    project.set_scale(1.5)                               # model changed while computing
    assert not project.apply_result(fake_result(project, []), req)
    assert [c.id for c in project.manual_contacts()] == [m.id]


def test_manual_only_request(project):
    project.set_param("manual_only", True)
    project.add_manual_contact((0, 0, 30), (0, 0, 1))
    req = project.regenerate(full=True)
    assert [c.manual for c in req.kwargs["contacts"]] == [True]


def test_save_load_roundtrip(project, profiles, tmp_path):
    project.set_rotation((0, 0, 30))
    project.set_param("tip_diameter", 0.5)
    c = project.add_manual_contact((1, 2, 3), (0, 0, -1), tip_diameter=0.6)
    project.edit_contact(c.id, enabled=False)
    auto = Contact(50, np.array([0.0, 0.0, 1.0]), np.array([0, 0, -1.0]), ContactReason.EDGE,
                   note="edge")
    project.apply_result(fake_result(project, [project.contact(c.id), auto]))
    path = project.save(tmp_path / "proj")
    assert path.suffix == ".arbor" and not project.dirty

    q = Project.load(path, profiles)
    assert q.name == "box" and not q.dirty
    assert q.transform.rotation_deg == pytest.approx((0, 0, 30))
    assert q.params.tip_diameter == 0.5
    assert q.changed_from_profile() == {"tip_diameter": (0.45, 0.5)}
    assert np.allclose(q.mesh.bounds, project.mesh.bounds)
    assert len(q.mesh.faces) == len(project.mesh.faces)
    by_id = {x.id: x for x in q.contacts}
    assert by_id[c.id].manual and by_id[c.id].tip_diameter == 0.6 and not by_id[c.id].enabled
    assert by_id[c.id].reason is ContactReason.MANUAL
    assert np.allclose(by_id[c.id].position, (1, 2, 3))
    assert by_id[50].reason is ContactReason.EDGE and not by_id[50].manual
    # manual contacts survive a full regeneration request after reload
    req = q.regenerate(full=True)
    assert [x.id for x in req.kwargs["manual_contacts"]] == [c.id]


def test_repair_is_undoable_and_keeps_original(project, profiles, tmp_path):
    original = project.imported_mesh.copy()
    bigger = trimesh.creation.box((10, 20, 40))
    project.replace_base_mesh(bigger)
    assert project.base_is_repaired and project.dimensions()[2] == pytest.approx(40)
    assert np.allclose(project.imported_mesh.vertices, original.vertices)
    q = Project.load(project.save(tmp_path / "r.arbor"), profiles)
    assert q.base_is_repaired and q.dimensions()[2] == pytest.approx(40)
    assert np.allclose(q.imported_mesh.extents, (10, 20, 30))
    project.undo()
    assert not project.base_is_repaired and project.dimensions()[2] == pytest.approx(30)


def test_listeners_and_new(project):
    events = []
    project.listeners.append(events.append)
    project.add_manual_contact((0, 0, 1), (0, 0, -1))
    assert "contacts" in events and "undo" in events
    project.new()
    assert project.mesh is None and project.contacts == [] and not project.undo_stack.can_undo()


def test_load_rejects_foreign_file(tmp_path, profiles):
    bad = tmp_path / "x.arbor"
    bad.write_bytes(b"not a zip")
    with pytest.raises(Exception):
        Project.load(bad, profiles)

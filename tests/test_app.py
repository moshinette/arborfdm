"""GUI logic tests (offscreen platform, no GL rendering needed).

Run with: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_app.py -q
The real support pipeline is used when its modules are importable; otherwise a tiny
stand-in generator is injected so the UI plumbing (worker thread, status bar) is still tested.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def app():
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a.setProperty("arbor_no_dialogs", True)
    from arbor.app.theme import apply_theme
    apply_theme(a)
    return a


def _engine_ok() -> bool:
    try:
        from arbor.engine import pipeline  # noqa: F401
        return True
    except Exception:
        return False


def _stand_in(progress, mesh, params, manual_contacts=None, analysis=None, contacts=None):
    from arbor.engine.types import (AnalysisResult, Contact, ContactReason, GenerationResult,
                                    NodeKind, SupportTree)
    for k in range(5):
        progress.update(k / 5, "stand-in")
        time.sleep(0.02)
    if contacts is None:
        b = mesh.bounds
        contacts = [Contact(1, np.array([b[1][0] - 0.5, 0, b[0][2] + 5]), np.array([0, 0, -1.0]),
                            ContactReason.EDGE)] + list(manual_contacts or [])
    tree = SupportTree()
    f = tree.add([0, 0, 0], 2, NodeKind.FOOT)
    for c in contacts:
        tree.add(c.position, 0.3, NodeKind.TIP, f.id, c.id)
    return GenerationResult(contacts, tree, trimesh.creation.cylinder(1, 5), None, [],
                            AnalysisResult(params.layer_height, np.zeros(0)), {"total": 0.01})


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    from arbor.app import main_window as mw
    # user presets go to a private folder (never the real user folder)
    monkeypatch.setenv("ARBOR_USER_PRESET_DIR", str(tmp_path / "user_presets"))
    # settings (OrcaSlicer path) and files sent to the slicer: private folders too
    monkeypatch.setenv("ARBOR_USER_CONFIG_DIR", str(tmp_path / "user_config"))
    monkeypatch.setenv("ARBOR_TO_SLICER_DIR", str(tmp_path / "to_slicer"))
    if not _engine_ok():
        monkeypatch.setattr(mw, "run_generation", _stand_in)
    w = mw.MainWindow()
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    yield w
    w.project.undo_stack.set_clean()
    w.close()
    app.processEvents()


def wait_for(app, window, timeout=120.0):
    end = time.monotonic() + timeout
    while window.runner.is_running() and time.monotonic() < end:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        time.sleep(0.005)
    app.processEvents()
    assert not window.runner.is_running(), "task did not finish in time"


def overhang_model() -> trimesh.Trimesh:
    """A post with a horizontal arm (a clear overhang), ~12 mm tall."""
    post = trimesh.creation.box((4, 4, 12))
    post.apply_translation([0, 0, 6])
    arm = trimesh.creation.box((10, 3, 2))
    arm.apply_translation([6, 0, 10])
    return trimesh.util.concatenate([post, arm])


@pytest.fixture
def stl_path(tmp_path):
    p = tmp_path / "overhang.stl"
    overhang_model().export(p)
    return p


def test_window_builds(window):
    assert window.windowTitle().endswith("ArborFDM")
    assert window.left.profile_combo.count() == 6
    labels = [window.left.profile_combo.itemText(i) for i in range(6)]
    assert labels[0].startswith("0.2") and labels[-1].startswith("0.4")
    assert not window.act_generate.isEnabled()      # no model yet
    assert len(window.right.fields) == len(window.project.params.to_dict())


def test_every_toolbar_action_has_a_real_slot(window):
    actions = [a for a in window.toolbar.actions() if not a.isSeparator()]
    texts = []
    for a in actions:
        if isinstance(window.toolbar.widgetForAction(a), QtWidgets.QToolButton) and a.text():
            texts.append(a.text())
            assert a.receivers("2triggered(bool)") >= 1 or a.receivers("2triggered()") >= 1, a.text()
    for expected in ("New", "Open", "Save", "Undo", "Redo", "Generate supports"):
        assert expected in texts
    # the export button carries a menu of four connected export actions (3MF, 3MF separate,
    # 2 STL) followed by "Open in OrcaSlicer" (added to the Export menu on purpose)
    menu = window.export_button.menu()
    exports = [a for a in menu.actions() if not a.isSeparator()]
    assert len(exports) == 5 and exports[-1] is window.act_open_orca
    for a in exports:
        assert a.receivers("2triggered(bool)") >= 1 or a.receivers("2triggered()") >= 1


def test_load_model_updates_panels(app, window, stl_path):
    assert window.open_path(str(stl_path))
    wait_for(app, window)
    p = window.project
    assert p.has_model and p.name
    assert window.left.object_list.count() == 1
    dims = window.left.dim_label.text()
    assert "13.00" in dims and "12.00" in dims
    assert window.left.height_spin.value() == pytest.approx(12.0)
    assert window.act_generate.isEnabled()
    assert p.mesh.bounds[0][2] == pytest.approx(0)
    # transform through the panel is undoable
    window.left.scale_spin.setValue(200.0)
    assert p.dimensions()[2] == pytest.approx(24.0)
    assert "24.00" in window.left.dim_label.text()
    window.undo()
    assert p.dimensions()[2] == pytest.approx(12.0)
    assert window.left.scale_spin.value() == pytest.approx(100.0)


def test_params_highlight_and_reset(app, window, stl_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    pf = window.right.fields["tip_diameter"]
    pf.widget.setValue(0.6)
    assert window.project.params.tip_diameter == pytest.approx(0.6)
    assert pf.widget.property("changed") is True
    assert window.right.reset_buttons["tip_diameter"].isEnabled()
    window.right.reset_buttons["tip_diameter"].click()
    assert window.project.params.tip_diameter == pytest.approx(0.45)
    assert pf.widget.property("changed") is False
    # profile switch through the combo
    combo = window.left.profile_combo
    i = combo.findData("nozzle_02_dense")
    combo.setCurrentIndex(i)
    combo.activated.emit(i)
    assert window.project.profile_key == "nozzle_02_dense"
    assert window.right.fields["nozzle_diameter"].widget.value() == pytest.approx(0.2)


def test_generate_through_worker_fills_status_bar(app, window, stl_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    seen = []
    window.runner.progress.connect(lambda f, m: seen.append(f))
    assert window.generate_full()
    assert window.runner.is_running()
    assert not window.act_generate.isEnabled()          # UI disabled, not blocked
    assert window.cancel_button.isVisibleTo(window)
    wait_for(app, window, timeout=300)
    assert window.last_error == "", window.last_error
    r = window.project.result
    assert r is not None
    assert window.contacts_label.text().startswith(f"Contacts: {len(r.contacts)}")
    assert window.tree_label.text().startswith("Trees: ") and "-" not in window.tree_label.text()
    assert window.time_label.text().startswith("Time: ") and window.time_label.text() != "Time: -"
    assert window.issues_label.text().startswith("Errors: ")
    assert seen and max(seen) <= 1.0
    assert not window.progress_bar.isVisibleTo(window)
    # undo removes the generation, redo restores it
    window.undo()
    assert window.project.result is None and window.tree_label.text() == "Trees: -"
    window.redo()
    assert window.project.result is r


def test_cancel_generation(app, window, monkeypatch, stl_path):
    from arbor.app import main_window as mw
    from arbor.engine.types import Cancelled  # noqa: F401

    def slow(progress, **kw):
        for k in range(400):
            progress.update(k / 400, "slow")
            time.sleep(0.01)
        raise AssertionError("not cancelled")
    monkeypatch.setattr(mw, "run_generation", slow)
    window.open_path(str(stl_path))
    wait_for(app, window)
    assert window.generate_full()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.15:
        app.processEvents()
    window.cancel_task()
    wait_for(app, window, timeout=10)
    assert window.status_msg.text() == "Cancelled"
    assert window.project.result is None
    assert window.act_generate.isEnabled()


def test_manual_contact_signals_and_editor(app, window, stl_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    vp = window.viewport
    vp.contactAddRequested.emit(np.array([8.0, 0.0, 9.0]), np.array([0.0, 0.0, -1.0]))
    p = window.project
    assert len(p.contacts) == 1 and p.contacts[0].manual
    cid = p.contacts[0].id
    assert p.selected_contact_id == cid
    ed = window.right.contact_editor
    assert ed.body.isVisibleTo(window)
    ed.override_check.setChecked(True)
    ed.tip_spin.setValue(0.7)
    assert p.contact(cid).tip_diameter == pytest.approx(0.7)
    ed.enabled_check.setChecked(False)
    assert not p.contact(cid).enabled
    assert "1 disabled" in window.contacts_label.text()
    vp.contactRemoveRequested.emit(cid)
    assert p.contacts == []
    window.undo()
    assert len(p.contacts) == 1
    assert window.act_rebuild.isEnabled()


def test_viewport_ray_math(app):
    """Camera ray / projection consistency, and exact hits on the mesh (no GL needed)."""
    from arbor.app.viewport import Viewport
    vp = Viewport()
    vp.resize(800, 600)
    m = trimesh.creation.box((10, 10, 10))
    m.apply_translation([0, 0, 5])
    vp.set_model(m, fit=True)
    for elev, az in ((25, -60), (-30, 20), (80, 135)):
        vp.setCameraPosition(elevation=elev, azimuth=az)
        target = np.array([[1.0, 2.0, 10.0]]) if elev > 0 else np.array([[1.0, 2.0, 0.0]])
        px, front = vp.project_points(target)
        assert front[0]
        hit = vp.pick_model(*px[0])
        assert hit is not None
        assert np.allclose(hit[0], target[0], atol=1e-3)
        assert np.dot(hit[1], vp.camera_ray(*px[0])[1]) < 0      # normal faces the camera
    # clicks: left on the model asks for a contact, right on a contact asks for removal
    added, removed = [], []
    vp.contactAddRequested.connect(lambda p, n: added.append(p))
    vp.contactRemoveRequested.connect(removed.append)
    vp.setCameraPosition(elevation=40, azimuth=-60)
    px, _ = vp.project_points(np.array([[0.0, 0.0, 10.0]]))
    assert vp.handle_click(*px[0], QtCore.Qt.MouseButton.LeftButton) == "add"
    from arbor.engine.types import Contact, ContactReason
    vp.set_contacts([Contact(7, added[0], np.array([0, 0, 1.0]), ContactReason.MANUAL, True)])
    assert vp.handle_click(*px[0], QtCore.Qt.MouseButton.RightButton) == "remove"
    assert removed == [7]
    # clipping hides the top: the ray continues to a visible lower face
    vp.clip_z = 5.0
    hit = vp.pick_model(*px[0])
    assert hit is None or hit[0][2] <= 5.0 + 1e-6


def test_save_and_open_project(app, window, stl_path, tmp_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    window.project.add_manual_contact((8, 0, 9), (0, 0, -1))
    target = tmp_path / "proj.arbor"
    window.path_provider = lambda save, cap, filt, default: str(target)
    assert window.save_project()
    assert target.exists() and "*" not in window.windowTitle()
    window.new_project()
    assert not window.project.has_model
    window.open_dialog()
    wait_for(app, window)
    assert window.project.has_model and len(window.project.manual_contacts()) == 1
    assert window.left.object_list.count() == 1


def test_export_3mf(app, window, stl_path, tmp_path):
    pytest.importorskip("arbor.io.export")
    window.open_path(str(stl_path))
    wait_for(app, window)
    window.generate_full()
    wait_for(app, window, timeout=300)
    out = tmp_path / "out.3mf"
    window.path_provider = lambda save, cap, filt, default: str(out)
    window.export_3mf()
    wait_for(app, window)
    assert out.exists(), window.last_error
    title, text = window.last_info
    assert "Disable the slicer's own supports" in text


# ---------------------------------------------------------------------------
# QA-3 / QA-6 / QA-11 / QA-13 (docs/QA_REVIEW.md)
# ---------------------------------------------------------------------------
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _slow_generation(monkeypatch, seconds=0.6):
    from arbor.app import main_window as mw
    real = mw.run_generation if _engine_ok() else _stand_in

    def slow(progress, **kw):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            progress.update(0.01, "slow")
            time.sleep(0.01)
        return real(progress=progress, **kw)
    monkeypatch.setattr(mw, "run_generation", slow)


def test_model_locked_and_banner_while_computing(app, window, monkeypatch, stl_path):
    """QA-3: the model controls are locked with a visible reason while a task runs."""
    _slow_generation(monkeypatch)
    window.open_path(str(stl_path))
    wait_for(app, window)
    L = window.left
    assert all(w.isEnabled() for w in L.model_widgets() if w is not L.orient_list)
    assert window.generate_full()
    assert window.runner.is_running()
    assert not any(w.isEnabled() for w in L.model_widgets())
    assert not L.import_button.isEnabled()
    assert "locked" in L.model_group.title()
    assert window.view.banner_kind == "busy" and window.view.banner.isVisibleTo(window)
    assert "locked" in window.view.banner.text()
    wait_for(app, window, timeout=300)
    assert all(w.isEnabled() for w in L.model_widgets() if w is not L.orient_list)
    assert window.view.banner_kind != "busy"


def test_param_change_during_run_marks_result_stale_and_export_uses_used_params(
        app, window, monkeypatch, stl_path, tmp_path):
    """QA-3: a parameter changed while computing: result kept but flagged out of date; the
    export metadata are the parameters that built the geometry."""
    pytest.importorskip("arbor.io.export")
    import json
    import re
    import zipfile
    _slow_generation(monkeypatch)
    window.open_path(str(stl_path))
    wait_for(app, window)
    p = window.project
    used_tip = p.params.tip_diameter
    assert window.generate_full()
    window.right.fields["tip_diameter"].widget.setValue(used_tip + 0.2)
    wait_for(app, window, timeout=300)
    assert p.result is not None
    assert p.result_params.tip_diameter == pytest.approx(used_tip)
    assert "tip_diameter" in p.result_param_changes()
    assert p.result_is_stale and window.act_generate.text().endswith("*")
    assert window.view.banner_kind == "stale" and "tip_diameter" in window.view.banner.text()
    assert "parameters changed during the computation" in window.status_msg.text()
    out = tmp_path / "stale.3mf"
    window.path_provider = lambda *a: str(out)
    window.export_3mf()
    wait_for(app, window)
    m = zipfile.ZipFile(out).read("3D/3dmodel.model").decode()
    meta = json.loads(re.search(r'name="arbor:support_params">([^<]*)<', m).group(1)
                      .replace("&quot;", '"'))
    assert meta["tip_diameter"] == pytest.approx(used_tip)
    assert "Parameters changed" in window.last_info[1] and "WARNING" in window.status_msg.text()
    # undo of the parameter: the result matches again
    p.set_param("tip_diameter", used_tip)
    assert not p.result_is_stale and window.view.banner_kind == ""


def test_contact_edits_during_branch_rebuild_are_replayed(app):
    """QA-3 at document level: edits made while 'Rebuild branches' runs are kept."""
    from arbor.core.project import Project
    from arbor.engine.types import AnalysisResult, GenerationResult, SupportTree
    p = Project()
    p.set_model(overhang_model(), name="m")
    a = p.add_manual_contact((8, 0, 9), (0, 0, -1))
    b = p.add_manual_contact((9, 0, 9), (0, 0, -1))
    req = p.regenerate(full=False)
    assert req.mode == "branches"
    p.remove_contact(a.id)                                  # edits while "computing"
    p.edit_contact(b.id, tip_diameter=0.9)
    c = p.add_manual_contact((10, 0, 9), (0, 0, -1))
    assert not p.is_request_current(req)
    res = GenerationResult(req.kwargs["contacts"], SupportTree(), trimesh.creation.box((1, 1, 1)),
                           None, [], AnalysisResult(0.2, np.zeros(0)), {})
    assert p.apply_result(res, req)
    assert p.contact(a.id) is None
    assert p.contact(b.id).tip_diameter == pytest.approx(0.9)
    assert p.contact(c.id) is not None
    assert p.contacts_edited and p.result_is_stale and p.last_apply.stale
    p.undo()                                                # back to the edited state
    assert p.result is None and p.contact(a.id) is None and p.contact(c.id) is not None


def test_broken_mesh_warns_before_generating(app, window):
    """QA-6: a mesh with errors is reported at load and before generating; nothing is
    repaired without the user's choice; the result carries an invalid_model_mesh issue."""
    path = os.path.join(EXAMPLES, "broken_hole.stl")
    window.open_path(path)
    wait_for(app, window)
    p = window.project
    assert p.mesh_problems()
    assert "error" in window.status_msg.text() and "Repair" in window.status_msg.text()
    assert window.view.banner_kind == "mesh"
    assert "repair" in window.left.mesh_report.text().lower()
    box = window.broken_mesh_box()
    assert {b.text() for b in box.buttons()} >= {"Repair, then generate", "Generate anyway"}
    box.deleteLater()
    window.broken_mesh_choice = "cancel"
    assert not window.generate_full() and not window.runner.is_running()
    assert "mesh has errors" in window.status_msg.text()
    window.broken_mesh_choice = "generate"
    assert window.generate_full()
    assert "WARNING" in window.status_msg.text()
    wait_for(app, window, timeout=300)
    assert not p.base_is_repaired and p.base_mesh is not None
    assert np.array_equal(p.base_mesh.faces, p.imported_mesh.faces)     # untouched
    codes = [i.code for i in p.result.issues]
    assert "invalid_model_mesh" in codes
    texts = [window.right.issue_list.item(k).text() for k in range(window.right.issue_list.count())]
    assert any("mesh has errors" in t for t in texts)
    # "Repair, then generate": repair (undoable copy), then generation on the repaired mesh
    window.broken_mesh_choice = "repair"
    assert window.generate_full()
    wait_for(app, window, timeout=300)
    assert p.base_is_repaired and not p.mesh_problems()
    assert p.result is not None
    assert "invalid_model_mesh" not in [i.code for i in p.result.issues]


def test_picking_structure_built_in_background(app):
    """QA-11: large meshes get their picking structure in a background thread; hover does
    not block while it builds; once built, hits match trimesh exactly."""
    from arbor.app import picking
    from arbor.app.viewport import Viewport
    m = trimesh.creation.icosphere(subdivisions=6, radius=20)     # 81 920 faces
    m.apply_translation([0, 0, 20])
    assert len(m.faces) > picking.SYNC_BUILD_MAX_FACES
    vp = Viewport()
    vp.resize(800, 600)
    t0 = time.perf_counter()
    vp.set_model(m, fit=True)
    assert time.perf_counter() - t0 < 1.0
    end = time.monotonic() + 30
    while not vp.picker_ready() and time.monotonic() < end:
        assert vp.pick_model(400, 300, wait=False) is None or vp.picker_ready()
        time.sleep(0.005)
    assert vp.picker_ready()
    rng = np.random.default_rng(3)
    for _ in range(30):
        x, y = rng.uniform(250, 550), rng.uniform(150, 450)
        hit = vp.pick_model(x, y)
        o, d = vp.camera_ray(x, y)
        locs, _, _ = m.ray.intersects_location([o], [d], multiple_hits=True)
        if len(locs) == 0:
            assert hit is None
            continue
        best = locs[np.argmin((locs - o) @ d)]
        assert hit is not None and np.allclose(hit[0], best, atol=1e-6)
        assert np.dot(hit[1], d) < 0
    t0 = time.perf_counter()
    for _ in range(20):
        vp.pick_model(400, 300)
    assert (time.perf_counter() - t0) / 20 < 0.05


def test_cut_is_reset_when_another_model_is_loaded(app, window, stl_path):
    """A cut left active on the previous model must not slice the next one open."""
    window.open_path(stl_path)
    wait_for(app, window)
    window.view.clip_slider.setValue(500)
    assert window.viewport.clip_z is not None
    assert "font-weight: bold" in window.view.clip_label.styleSheet()   # active cut is visible
    window.open_path(os.path.join(EXAMPLES, "shelf.stl"))
    wait_for(app, window)
    assert window.view.clip_slider.value() == 1000
    assert window.viewport.clip_z is None and window.view.clip_label.text() == "all"
    window.view.clip_slider.setValue(300)
    window.new_project()
    assert window.viewport.clip_z is None


def test_cut_is_reset_when_another_model_is_loaded_v2(app, window, stl_path):
    """A cut left active on the previous model must not slice the next one open."""
    window.open_path(stl_path)
    wait_for(app, window)
    window.view.clip_slider.setValue(500)
    assert window.viewport.clip_z is not None
    assert "font-weight: bold" in window.view.clip_label.styleSheet()   # active cut is visible
    window.open_path(os.path.join(EXAMPLES, "shelf.stl"))
    wait_for(app, window)
    assert window.view.clip_slider.value() == 1000
    assert window.viewport.clip_z is None and window.view.clip_label.text() == "all"
    window.view.clip_slider.setValue(300)
    window.new_project()
    assert window.viewport.clip_z is None


# ---------------------------------------------------------------------------
# Overhang colours (display only)
# ---------------------------------------------------------------------------
def test_overhang_bands_follow_threshold():
    from arbor.app import overhang as oh
    # angles from vertical: wall 0, 40, 50, 60, 80, ceiling 90, upward face -90
    ang = np.array([0, 40, 50, 60, 80, 90, -90], float)
    normals = np.c_[np.cos(np.radians(ang)), np.zeros(len(ang)), -np.sin(np.radians(ang))]
    a = oh.face_angles(normals)
    assert np.allclose(a, ang, atol=1e-3)
    assert oh.classify(a, 55).tolist() == [0, 0, 1, 2, 3, 3, 0]
    assert oh.classify(a, 45).tolist() == [0, 1, 2, 2, 3, 3, 0]
    assert oh.band_edges(55) == (45.0, 55.0, 72.5)
    labels = [b.label for b in oh.bands(55)]
    assert "45 - 55" in labels[1] and "55 - 72" in labels[2] and "72 - 90" in labels[3]
    # faces resting on the plate are never coloured
    top = np.array([5, 5, 5, 5, 0.05, 0.05, 5], float)
    assert oh.classify(a, 55, top, 0.12).tolist() == [0, 0, 1, 2, 0, 0, 0]
    rgba = oh.vertex_colors(oh.classify(a, 55), (0.8, 0.78, 0.74, 1.0))
    assert rgba.shape == (3 * len(a), 4) and rgba.dtype == np.uint8


def test_overhang_colours_in_window(app, window, stl_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    vp, view, p = window.viewport, window.view, window.project
    verts_before = p.mesh.vertices.copy()
    # automatic: on while there is no support
    assert view.overhang_button.isChecked() and vp.overhang_enabled
    assert vp.model_item.has_custom_colors()
    assert vp.overhang_legend.isVisibleTo(window)
    cls = vp.overhang_classes
    assert cls is not None and len(cls) == len(p.mesh.faces)
    n = np.asarray(p.mesh.face_normals)
    top = np.asarray(p.mesh.triangles)[:, :, 2].max(axis=1)
    ceiling = (n[:, 2] < -0.99) & (top > 1)            # underside of the arm
    plate = (n[:, 2] < -0.99) & (top < 0.1)            # bottom of the post on the plate
    assert (cls[ceiling] == 3).all() and (cls[plate] == 0).all()
    assert (cls[np.abs(n[:, 2]) < 1e-6] == 0).all()   # walls
    assert "55" in vp.overhang_legend.title.text()
    # threshold change: recoloured, legend updated, geometry untouched
    window.right.fields["overhang_angle_deg"].widget.setValue(80.0)
    assert vp.overhang_threshold == 80.0 and "80" in vp.overhang_legend.title.text()
    assert "85 - 90" in vp.overhang_legend.range_labels["severe"].text()
    assert (vp.overhang_classes[ceiling] == 3).all()
    np.testing.assert_array_equal(p.mesh.vertices, verts_before)
    # rotating the model recomputes the colours on the new world mesh
    window.left.rot_spins[0].setValue(180.0)
    assert vp._angle_cache[0] is p.mesh
    assert len(vp.overhang_classes) == len(p.mesh.faces)
    n = np.asarray(p.mesh.face_normals)
    top = np.asarray(p.mesh.triangles)[:, :, 2].max(axis=1)
    assert (vp.overhang_classes[(n[:, 2] < -0.99) & (top < 0.1)] == 0).all()
    window.undo()
    # user choice: off, kept; View > automatic restores the automatic mode
    view.overhang_button.click()
    assert not vp.overhang_enabled and not vp.model_item.has_custom_colors()
    assert not vp.overhang_legend.isVisibleTo(window)
    view.set_supports_present(False)
    assert not vp.overhang_enabled
    window.act_overhang_auto.trigger()
    assert vp.overhang_enabled
    view.set_supports_present(True)            # automatic: off once supports exist
    assert not vp.overhang_enabled and not view.overhang_button.isChecked()
    view.overhang_button.click()               # forced on with supports
    assert vp.overhang_enabled
    # supports-only view: no model, no colours
    view.mode_buttons["supports"].click()
    assert vp.overhang_classes is None and not vp.overhang_legend.isVisibleTo(window)
    view.mode_buttons["both"].click()
    assert vp.overhang_classes is not None


def test_overhang_mode_keeps_manual_picking(app):
    from arbor.app.viewport import Viewport
    vp = Viewport()
    vp.resize(800, 600)
    m = trimesh.creation.box((10, 10, 10))
    m.apply_translation([0, 0, 5])
    vp.set_model(m, fit=True)
    vp.set_overhang_display(True)
    assert vp.model_item.has_custom_colors()
    added = []
    vp.contactAddRequested.connect(lambda pos, nrm: added.append(pos))
    vp.setCameraPosition(elevation=40, azimuth=-60)
    px, _ = vp.project_points(np.array([[1.0, -2.0, 10.0]]))
    assert vp.handle_click(*px[0], QtCore.Qt.MouseButton.LeftButton) == "add"
    assert np.allclose(added[0], [1.0, -2.0, 10.0], atol=1e-3)
    # clipped display: colours follow the shown (cut) mesh
    vp.clip_z = 5.0
    vp._rebuild_geometry()
    assert len(vp.overhang_classes) == len(vp._model_shown.faces)


def test_overhang_recolour_is_fast_on_1m_triangles(app):
    from arbor.app.viewport import Viewport
    mesh = trimesh.creation.icosphere(subdivisions=8, radius=20)      # 1 310 720 faces
    mesh.apply_translation([0, 0, 20])
    vp = Viewport()
    vp.resize(400, 300)
    vp.set_model(mesh)
    vp.set_overhang_display(True)                  # first pass: angles computed once
    first = dict(vp.overhang_timings)
    t0 = time.perf_counter()
    for k in range(5):
        vp.set_overhang_params(40.0 + 5 * k, 0.12)  # threshold change: classify + colours
    per = (time.perf_counter() - t0) / 5
    assert first["faces"] == len(mesh.faces) > 1_000_000
    assert per < 0.5, per
    assert first["angles_s"] < 1.0, first


# ---------------------------------------------------------------------------
# User presets
# ---------------------------------------------------------------------------
def test_save_rename_export_delete_preset(app, window, stl_path, tmp_path):
    import json
    window.open_path(str(stl_path))
    wait_for(app, window)
    p, combo = window.project, window.left.profile_combo
    assert combo.count() == 6                      # no user preset yet: no separator
    assert not window.act_preset_delete.isEnabled()
    window.right.fields["overhang_angle_deg"].widget.setValue(48.0)
    # raft on by default since v0.2: turning it off is the changed value
    window.left.fields["raft_enabled"].widget.setChecked(False)
    window.text_provider = lambda cap, label, default: "My minis"
    assert window.save_preset()
    key = "user:my_minis"
    assert p.profile_key == key and p.changed_from_profile() == {}
    assert combo.currentData() == key and combo.currentText().endswith("My minis")
    assert combo.count() == 6 + 3                  # separator, header, preset
    header = combo.model().item(7)
    assert header.text() == "My presets" and not (header.flags() & QtCore.Qt.ItemIsEnabled)
    data = json.loads((tmp_path / "user_presets" / "my_minis.json").read_text("utf-8"))
    assert data["inherits"] == "nozzle_04_normal"
    assert data["values"] == {"overhang_angle_deg": 48.0, "raft_enabled": False}
    assert window.act_preset_delete.isEnabled() and "Preset:" in window.right.profile_label.text()
    # rename keeps the selection and the values
    window.text_provider = lambda cap, label, default: "Minis 32mm"
    assert window.rename_preset()
    assert p.profile_key == "user:minis_32mm" and combo.currentText().endswith("Minis 32mm")
    # export
    out = tmp_path / "share.json"
    window.path_provider = lambda save, cap, filt, default: str(out)
    assert window.export_preset() and json.loads(out.read_text("utf-8"))["label"] == "Minis 32mm"
    # undo of the profile switch goes back to the built-in profile with the same values
    window.undo()
    assert p.profile_key == "nozzle_04_normal" and p.params.overhang_angle_deg == 48.0
    window.redo()
    assert p.profile_key == "user:minis_32mm"
    # delete: values kept, shown as changes against the built-in base
    window.confirm_provider = lambda title, text: True
    assert window.delete_preset()
    assert p.profile_key == "nozzle_04_normal" and combo.count() == 6
    assert set(p.changed_from_profile()) == {"overhang_angle_deg", "raft_enabled"}
    window.undo()
    window.undo()
    assert p.profile_key in p.profiles            # history never points to a deleted preset
    # import the exported file back
    window.path_provider = lambda save, cap, filt, default: str(out)
    assert window.import_preset()
    assert p.profile_key == "user:minis_32mm" and not p.params.raft_enabled


def test_import_invalid_preset_reports_without_crash(app, window, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"label": "x", "values": {"overhang_angle_deg": 200}}', encoding="utf-8")
    window.path_provider = lambda save, cap, filt, default: str(bad)
    before = dict(window.project.profiles)
    assert not window.import_preset()
    assert "overhang_angle_deg" in window.status_msg.text()
    assert window.last_info[0] == "Cannot import preset"
    assert set(window.project.profiles) == set(before)


def test_invalid_preset_files_are_reported_at_startup(app, monkeypatch, tmp_path):
    from arbor.app import main_window as mw
    d = tmp_path / "presets"
    d.mkdir()
    (d / "ok.json").write_text('{"label": "Ok", "inherits": "nozzle_02_light", '
                               '"values": {"walls": 2}}', encoding="utf-8")
    (d / "broken.json").write_text("{oops", encoding="utf-8")
    monkeypatch.setenv("ARBOR_USER_PRESET_DIR", str(d))
    w = mw.MainWindow()
    try:
        assert "user:ok" in w.project.profiles
        assert w.left.profile_combo.findData("user:ok") > 0
        assert "1 preset file(s) ignored" in w.status_msg.text()
        assert "broken.json" in w.status_msg.text()
    finally:
        w.close()


def test_project_with_user_preset_reopens(app, window, stl_path, tmp_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    window.right.fields["walls"].widget.setValue(5)
    window.text_provider = lambda *a: "Five walls"
    assert window.save_preset()
    target = tmp_path / "proj.arbor"
    window.path_provider = lambda *a: str(target)
    assert window.save_project_as()
    window.new_project()
    assert window.project.profile_key == "nozzle_04_normal"
    window.open_path(str(target))
    wait_for(app, window)
    assert window.project.profile_key == "user:five_walls"
    assert window.project.params.walls == 5
    assert window.left.profile_combo.currentText().endswith("Five walls")


def test_export_3mf_separate_objects(app, window, tmp_path):
    """Reddit request: model and supports as separate objects (own layer heights in Orca)."""
    import zipfile
    window.open_path(os.path.join(EXAMPLES, "shelf.stl"))
    wait_for(app, window)
    assert not window.act_export_3mf_sep.isEnabled()          # needs generated supports
    # two objects need the model on the plate: raft under the feet only (with the v0.2
    # default raft under the model, the separate layout writes one object)
    window.project.set_param("raft_coverage", "feet")
    window.generate_full()
    wait_for(app, window, timeout=300)
    assert window.act_export_3mf_sep.isEnabled()
    out = tmp_path / "sep.3mf"
    window.path_provider = lambda save, cap, filt, default: str(out)
    window.export_3mf_separate()
    wait_for(app, window)
    assert out.exists(), window.last_error
    with zipfile.ZipFile(out) as z:
        xml = z.read("3D/3dmodel.model").decode()
    assert xml.count("<item ") == 2


def test_open_in_orca_and_preferences_entries(app, window, stl_path, monkeypatch, tmp_path):
    """Export menu + toolbar button (enabled only with supports) and File > Preferences...
    opening a dialog with a "Slicers" section."""
    from arbor.io import slicer_launch as sl
    monkeypatch.setattr(sl, "candidate_paths", lambda *a, **k: [])
    launched = []
    monkeypatch.setattr(sl, "launch_orcaslicer", lambda f, e, **k: launched.append((f, e)))
    a = window.act_open_orca
    assert a.text() == "Open in OrcaSlicer"
    assert a in window.export_menu.actions() and a in window.toolbar.actions()
    tb = window.toolbar.actions()
    assert tb.index(a) == tb.index(next(x for x in tb
                                        if window.toolbar.widgetForAction(x)
                                        is window.export_button)) + 1
    assert not a.isEnabled()
    window.open_path(str(stl_path))
    wait_for(app, window)
    assert not a.isEnabled()                       # model but no supports
    assert window.generate_full()
    assert not a.isEnabled()                       # locked while computing
    wait_for(app, window, timeout=300)
    assert a.isEnabled()
    a.trigger()                                    # Orca not found: dialog answered "cancel"
    wait_for(app, window)
    assert launched == []
    assert window.last_orca["file"] and not window.last_orca["opened"]
    assert window.last_orca["file"] in window.status_msg.text()
    file_menu = window.menuBar().actions()[0].menu()
    assert window.act_preferences in file_menu.actions()
    window.act_preferences.trigger()
    from arbor.app.preferences import PreferencesDialog
    dlg = window.preferences_dialog()
    assert isinstance(dlg, PreferencesDialog) and dlg.slicers_group.title() == "Slicers"
    assert dlg.sections["Slicers"] is dlg.slicers_form


def _fake_orca(tmp_path, name="orca-slicer"):
    """An executable file that is never run (launch_orcaslicer is monkeypatched)."""
    import sys
    exe = tmp_path / "fake_orca" / (name + (".exe" if sys.platform.startswith("win") else ""))
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return exe


def _generated(app, window, stl_path):
    window.open_path(str(stl_path))
    wait_for(app, window)
    assert window.generate_full()
    wait_for(app, window, timeout=300)
    assert window.project.result is not None, window.last_error


def test_open_in_orca_exports_in_worker_and_launches(app, window, stl_path, monkeypatch, tmp_path):
    import zipfile
    from arbor.core.settings import update_settings
    from arbor.io import slicer_launch as sl
    exe = _fake_orca(tmp_path)
    update_settings(orca_path=str(exe))
    launched, threads = [], []
    monkeypatch.setattr(sl, "launch_orcaslicer", lambda f, e, **k: launched.append((f, e)))
    real_export = sl.export_for_slicer

    def spy(*a, **k):
        threads.append(QtCore.QThread.currentThread())
        return real_export(*a, **k)
    monkeypatch.setattr(sl, "export_for_slicer", spy)
    src_bytes = stl_path.read_bytes()
    _generated(app, window, stl_path)
    window.project.set_param("contact_density", 1.7)   # after generation: not in the file
    used = window.project.export_params()
    assert window.open_in_orca()
    assert window.runner.is_running()                     # export runs in the worker
    wait_for(app, window)
    assert threads and threads[0] is not app.thread()
    assert len(launched) == 1
    f, e = launched[0]
    assert Path(e) == exe and Path(f).parent == tmp_path / "to_slicer"
    assert Path(f).suffix == ".3mf" and Path(f).exists()
    assert stl_path.read_bytes() == src_bytes             # source model untouched
    with zipfile.ZipFile(f) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
        xml = z.read("3D/3dmodel.model").decode()
    assert 'key="enable_support" value="0"' in cfg
    assert f'{used.contact_density}' in xml or "contact_density" in xml
    assert window.last_orca["opened"] and window.last_orca["exe"] == str(exe)
    title, text = window.last_info
    assert title == "Opening in OrcaSlicer" and "supports disabled" in text
    assert "contact_density" in text                      # stale-parameter warning kept
    # a second launch never overwrites the first file
    assert window.open_in_orca()
    wait_for(app, window)
    assert len(launched) == 2 and launched[1][0] != launched[0][0]
    assert Path(launched[0][0]).exists()


def test_open_in_orca_not_found_choose_executable(app, window, stl_path, monkeypatch, tmp_path):
    from arbor.core.settings import load_settings
    from arbor.io import slicer_launch as sl
    monkeypatch.setattr(sl, "candidate_paths", lambda *a, **k: [])
    launched, asked = [], []
    monkeypatch.setattr(sl, "launch_orcaslicer", lambda f, e, **k: launched.append((f, e)))
    exe = _fake_orca(tmp_path)
    _generated(app, window, stl_path)
    # 1. an invalid program is refused (nothing saved, nothing launched)
    window.orca_choice_provider = lambda path, reason: asked.append((path, reason)) or "choose"
    window.path_provider = lambda save, cap, filt, default: str(tmp_path)
    window.open_in_orca()
    wait_for(app, window)
    assert launched == [] and len(asked) == 1 and "not found" in asked[0][1]
    assert load_settings()["orca_path"] is None
    assert window.last_info[0] == "Invalid OrcaSlicer path"
    # 2. a valid program is remembered and launched
    window.path_provider = lambda save, cap, filt, default: str(exe)
    window.open_in_orca()
    wait_for(app, window)
    assert len(launched) == 1 and Path(launched[0][1]) == exe
    assert load_settings()["orca_path"] == str(exe)
    # 3. next time it is found without asking
    asked.clear()
    window.open_in_orca()
    wait_for(app, window)
    assert asked == [] and len(launched) == 2


def test_open_in_orca_not_found_open_folder_and_launch_error(app, window, stl_path, monkeypatch,
                                                              tmp_path):
    from arbor.core.settings import update_settings
    from arbor.io import slicer_launch as sl
    monkeypatch.setattr(sl, "candidate_paths", lambda *a, **k: [])
    _generated(app, window, stl_path)
    window.orca_choice_provider = lambda path, reason: "folder"
    window.open_in_orca()
    wait_for(app, window)
    info = window.last_orca
    assert info["folder_opened"] and not info["opened"] and Path(info["file"]).exists()
    assert info["file"] in window.status_msg.text()
    # the program exists but fails to start: the user is told why and can choose again
    exe = _fake_orca(tmp_path)
    update_settings(orca_path=str(exe))

    def boom(f, e, **k):
        raise sl.SlicerLaunchError("orca-slicer exited immediately with code 1")
    monkeypatch.setattr(sl, "launch_orcaslicer", boom)
    reasons = []
    window.orca_choice_provider = lambda path, reason: reasons.append(reason) or "cancel"
    window.open_in_orca()
    wait_for(app, window)
    assert reasons and "exited immediately" in reasons[0]
    assert not window.last_orca["opened"] and "exited immediately" in window.last_orca["error"]
    # the dialog itself: the three choices + Cancel
    box = window.orca_missing_box("/x/model.3mf", "not found")
    texts = [b.text() for b in box.buttons()]
    assert "Choose the executable..." in texts and "Preferences..." in texts
    assert "Open folder" in texts and "/x/model.3mf" in box.informativeText()
    box.deleteLater()


def test_open_in_orca_export_failure_is_reported(app, window, stl_path, monkeypatch):
    from arbor.io import slicer_launch as sl

    def fail(*a, **k):
        raise sl.SlicerExportError("cannot create the folder X: denied")
    monkeypatch.setattr(sl, "export_for_slicer", fail)
    launched = []
    monkeypatch.setattr(sl, "launch_orcaslicer", lambda f, e, **k: launched.append(f))
    _generated(app, window, stl_path)
    window.open_in_orca()
    wait_for(app, window)
    assert launched == [] and "denied" in window.status_msg.text()
    assert window.last_info[0] == "Open in OrcaSlicer" and "denied" in window.last_info[1]


def test_preferences_orca_path(app, window, monkeypatch, tmp_path):
    from arbor.core.settings import load_settings
    from arbor.io import slicer_launch as sl
    monkeypatch.setattr(sl, "candidate_paths", lambda *a, **k: [])
    dlg = window.preferences_dialog()
    dlg.load()
    assert dlg.orca_edit.text() == "" and dlg.orca_valid is False
    assert "not found" in dlg.orca_status.text()
    assert not dlg.set_orca_path(str(tmp_path / "missing.exe"))
    assert dlg.orca_valid is False and "not saved" in dlg.orca_status.text()
    assert load_settings()["orca_path"] is None
    exe = _fake_orca(tmp_path)
    seen = []
    dlg.orcaPathChanged.connect(seen.append)
    window.path_provider = lambda save, cap, filt, default: str(exe)
    dlg.orca_browse.click()
    assert dlg.orca_valid is True and dlg.orca_edit.text() == str(exe)
    assert load_settings()["orca_path"] == str(exe) and seen == [str(exe)]
    assert "OrcaSlicer path saved" in window.status_msg.text()
    assert dlg.set_orca_path("")                          # back to automatic detection
    assert load_settings()["orca_path"] is None and seen[-1] is None


def test_contact_keyboard_and_bulk_delete(app, window, stl_path):
    from PySide6.QtTest import QTest
    window.open_path(str(stl_path))
    wait_for(app, window)
    p = window.project
    vp = window.viewport
    for x in (6.0, 8.0, 10.0):
        vp.contactAddRequested.emit(np.array([x, 0.0, 9.0]), np.array([0.0, 0.0, -1.0]))
    assert len(p.manual_contacts()) == 3
    sel = p.selected_contact_id
    assert window.act_delete_contact.isEnabled() and window.act_deselect.isEnabled()
    assert window.act_delete_manual.text() == "Delete all manual contacts (3)"
    assert window.act_delete_reason.text() == "Delete all 'Manual' contacts (3)"
    # the info card describes the selected contact: reason and note
    html = window.view.info_card.text()
    assert window.view.info_card.isVisibleTo(window)
    assert f"#{sel} Manual" in html and "Placed by the user" in html
    # Esc deselects, Del deletes the selection (keyboard shortcuts of the window)
    window.activateWindow()
    vp.setFocus()
    app.processEvents()
    QTest.keyClick(vp, QtCore.Qt.Key_Escape)
    app.processEvents()
    assert p.selected_contact_id is None and not window.view.info_card.isVisibleTo(window)
    p.select_contact(sel)
    QTest.keyClick(vp, QtCore.Qt.Key_Delete)
    app.processEvents()
    assert p.contact(sel) is None and len(p.contacts) == 2
    window.undo()
    assert p.contact(sel) is not None
    # Delete in a parameter field edits the field, never deletes the contact
    p.select_contact(sel)
    spin = window.right.fields["tip_diameter"].widget
    spin.setFocus()
    app.processEvents()
    QTest.keyClick(spin, QtCore.Qt.Key_Delete)
    app.processEvents()
    assert p.contact(sel) is not None
    # bulk deletes: one undo step each
    assert window.delete_selected_reason() == 3 and p.contacts == []
    window.undo()
    assert len(p.contacts) == 3 and [c.id for c in p.contacts] == sorted(c.id for c in p.contacts)
    assert window.delete_manual_contacts() == 3 and p.contacts == []
    assert not window.act_delete_manual.isEnabled()
    window.undo()
    assert len(p.manual_contacts()) == 3
    window.redo()
    assert p.contacts == []


def test_bulk_delete_keeps_automatic_contacts(app):
    from arbor.core.project import Project
    from arbor.engine.types import Contact, ContactReason
    p = Project()
    p.contacts = [Contact(1, np.zeros(3), np.array([0, 0, -1.0]), ContactReason.EDGE),
                  Contact(2, np.ones(3), np.array([0, 0, -1.0]), ContactReason.ISLAND)]
    m = p.add_manual_contact([1, 2, 3], [0, 0, -1])
    p.select_contact(m.id)
    assert p.remove_manual_contacts() == 1
    assert [c.id for c in p.contacts] == [1, 2] and p.selected_contact_id is None
    assert p.remove_contacts_with_reason("edge") == 1 and [c.id for c in p.contacts] == [2]
    assert p.undo_stack.undo_text() == "Remove edge contacts"
    p.undo()
    assert [c.id for c in p.contacts] == [1, 2]
    p.undo()
    assert [c.id for c in p.contacts] == [1, 2, m.id]
    assert p.remove_contacts([]) == 0 and p.remove_contacts([99]) == 0


def test_issue_list_groups_and_unknown_codes(app):
    from types import SimpleNamespace
    from arbor.app.panels import RightPanel, issue_title
    from arbor.core.project import Project
    from arbor.engine.types import Issue
    issues = [Issue("warning", "fragile_support", f"Thin branch {k}", np.array([k, 0, 5.0]))
              for k in range(9)]
    issues += [Issue("error", "unrouted_contact", "Contact #3 not connected", None, 3),
               Issue("warning", "brand_new_check", "", np.array([1.0, 2.0, 3.0])),
               SimpleNamespace(severity="critical", code="odd", message="Strange",
                               position=[np.nan, 0, 0], contact_id=None),
               Issue("info", "hard_to_remove", "Hard to remove near the chin",
                     np.array([0, 1.0, 4.0]))]
    rp = RightPanel(Project())
    rp.set_issues(issues, True)
    L = rp.issue_list
    texts = [L.item(k).text() for k in range(L.count())]
    assert texts[0] == "Contact #3 not connected"            # errors first
    header = next(L.item(k) for k in range(L.count())
                  if L.item(k).data(rp.GROUP_ROLE) == "fragile_support")
    assert "Fragile support" in header.text() and "\u00d7 9" in header.text()
    children = [L.item(k) for k in range(L.count())
                if (L.item(k).data(QtCore.Qt.UserRole) or 0) < 9
                and L.item(k).data(rp.GROUP_ROLE) is None
                and L.item(k).text().strip().startswith("Thin branch")]
    assert len(children) == 9 and all(c.isHidden() for c in children)
    rp._on_issue(header)                                     # click: expand
    assert not any(c.isHidden() for c in children) and "\u25be" in header.text()
    rp.set_issues(issues, True)                              # stays open after a refresh
    assert any("\u25be" in L.item(k).text() for k in range(L.count()))
    assert "Brand new check" in texts                        # empty message: readable code
    assert issue_title("brand_new_check") == "Brand new check"
    strange = next(L.item(k) for k in range(L.count()) if L.item(k).text() == "Strange")
    assert strange.toolTip().startswith("Warning")           # unknown severity: as warning
    assert "<b>11</b> warning" in rp.issue_summary.text()      # unknown severity counted
    assert rp.tabs.tabText(1) == "Contacts and issues (12)"
    seen = []
    rp.issueActivated.connect(seen.append)
    third = next(L.item(k) for k in range(L.count()) if L.item(k).text().strip() == "Thin branch 2")
    rp._on_issue(third)
    assert seen and seen[0] is issues[2]
    # few issues of one code: no group (QA-7 behaviour kept)
    rp.set_issues(issues[:3], True)
    assert L.count() == 3 and all(L.item(k).data(rp.GROUP_ROLE) is None for k in range(3))


def test_viewport_shows_any_issue_code(app, window, stl_path):
    from types import SimpleNamespace
    from arbor.engine.types import Issue
    window.open_path(str(stl_path))
    wait_for(app, window)
    vp = window.viewport
    issues = [Issue("warning", "fragile_support", "Thin branch", np.array([0, 0, 5.0])),
              Issue("info", "some_future_code", "", np.array([1.0, 0, 5.0])),
              SimpleNamespace(severity="?", code="x", message="m", position="bad",
                              contact_id=None),
              Issue("error", "unsupported_region", "Hole", np.array([[2.0, 0, 5.0]]))]
    vp.set_issues(issues)                                     # no exception
    assert [k for k, _ in vp._visible_issue_points()] == [0, 1, 3]
    vp.hovered_issue = 1
    html = window.inspect_html()
    assert "Some future code" in html and "some_future_code" in html
    vp.hovered_issue = None


def test_format_timings_keeps_statistics_out_of_the_time():
    from arbor.app.main_window import format_timings
    total, tip = format_timings({"analysis": 0.5, "tree": 0.25, "fragile_supports": 3,
                                 "min_branch_mm": 0.42, "label": "x"})
    assert total == pytest.approx(0.75)
    assert "analysis: 0.500 s" in tip and "fragile_supports: 3" in tip
    assert "min_branch_mm: 0.42" in tip and "label: x" in tip
    assert format_timings({"total": 0.01})[0] == pytest.approx(0.01)
    assert format_timings({})[0] is None
    total, tip = format_timings({"tree": 1.0}, {"n_trees": 7, "nested": {"a": 1}})
    assert total == 1.0 and "n_trees: 7" in tip and "nested" not in tip


def test_busy_card_and_empty_state(app, window, monkeypatch, stl_path):
    """Feedback during long computations: progress card with step and Cancel over the view;
    a call to action when no model is loaded."""
    assert window.view.empty_label.isVisibleTo(window)
    assert not window.view.viewport.legend.isVisibleTo(window)
    _slow_generation(monkeypatch)
    window.open_path(str(stl_path))
    wait_for(app, window)
    assert not window.view.empty_label.isVisibleTo(window)
    assert window.view.viewport.legend.isVisibleTo(window)
    assert window.generate_full()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.2:
        app.processEvents()
    card = window.view.busy_card
    assert card.isVisibleTo(window) and card.title.text() == "Generating supports"
    assert card.percent.text().endswith("%")
    card.cancel_button.click()
    wait_for(app, window, timeout=30)
    assert window.status_msg.text() == "Cancelled" and not card.isVisibleTo(window)


# ---------------------------------------------------------------------------
# Cut slider: one layer per wheel notch; orientation list shows the overhang surface
# ---------------------------------------------------------------------------
def _wheel(widget, delta, mods=QtCore.Qt.NoModifier):
    from PySide6 import QtGui
    pos = QtCore.QPointF(widget.width() / 2, widget.height() / 2)
    ev = QtGui.QWheelEvent(pos, widget.mapToGlobal(pos), QtCore.QPoint(0, 0),
                           QtCore.QPoint(0, delta), QtCore.Qt.NoButton, mods,
                           QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(widget, ev)


def test_cut_wheel_steps_one_layer_and_bounds(app, window, stl_path):
    assert window.open_path(str(stl_path))
    wait_for(app, window)
    app.processEvents()
    v = window.view
    lh = window.project.params.layer_height
    n_max = v.layer_count()
    assert n_max == int(np.ceil(window.viewport.scene_height() / lh - 1e-6)) > 20
    assert v.clip_label.text() == "all"
    _wheel(v.clip_slider, 120)                       # up at the top: stays "all"
    assert v.clip_label.text() == "all" and window.viewport.clip_z is None
    _wheel(v.clip_slider, -120)                      # down: last layer below the top
    assert v.current_layer() == n_max - 1
    assert v.clip_label.text() == f"L {n_max - 1} · {(n_max - 1) * lh:.2f} mm"
    assert window.viewport.clip_z == pytest.approx((n_max - 1) * lh)
    _wheel(v.clip_slider, -120, QtCore.Qt.ShiftModifier)          # Shift: 10 layers
    assert v.current_layer() == n_max - 11
    _wheel(v.clip_slider, 120)
    assert v.current_layer() == n_max - 10
    for _ in range(3):
        _wheel(v.clip_slider, -120, QtCore.Qt.ShiftModifier)
    assert v.current_layer() >= 1
    _wheel(v.clip_slider, -120 * 20, QtCore.Qt.ShiftModifier)      # far below: clamps at L 1
    assert v.current_layer() == 1 and v.clip_label.text().startswith("L 1 ")
    _wheel(v.clip_slider, 120 * 20, QtCore.Qt.ShiftModifier)      # far above: back to all
    assert v.clip_label.text() == "all" and window.viewport.clip_z is None
    assert v.clip_slider.value() == 1000


def test_cut_wheel_burst_rebuilds_once(app, window, stl_path):
    assert window.open_path(str(stl_path))
    wait_for(app, window)
    app.processEvents()
    vp, v = window.viewport, window.view
    calls = []
    orig = vp._rebuild_geometry
    vp._rebuild_geometry = lambda: (calls.append(1), orig())[1]
    vp._clip_timer.timeout.disconnect()
    vp._clip_timer.timeout.connect(vp._rebuild_geometry)
    for _ in range(8):
        _wheel(v.clip_slider, -120)
    assert not calls                                  # nothing rebuilt per notch
    end = time.monotonic() + 1.0
    while time.monotonic() < end and not calls:
        app.processEvents()
        time.sleep(0.01)
    assert len(calls) == 1


def test_orientation_list_shows_overhang_surface_and_colour(app, window):
    from types import SimpleNamespace
    from arbor.app.panels import OVERHANG_LEVEL_COLORS

    def cand(pct):
        return SimpleNamespace(rotation_deg=(0, 0, 0), criteria={
            "overhang_area": 1.0, "height": 10.0, "overhang_surface_pct": pct,
            "overhang_surface_area": pct * 10}, describe=lambda: "x")
    window.left.show_candidates([cand(8.0), cand(22.0), cand(41.0)])
    lst = window.left.orient_list
    texts = [lst.item(i).text() for i in range(3)]
    assert "overhang surface 8.0 % (80 mm²)" in texts[0]
    levels = [lst.item(i).data(QtCore.Qt.UserRole) for i in range(3)]
    assert levels == ["good", "medium", "bad"]
    assert not lst.item(0).icon().isNull()
    assert "Colour" in lst.item(0).toolTip() and set(OVERHANG_LEVEL_COLORS) == set(levels)

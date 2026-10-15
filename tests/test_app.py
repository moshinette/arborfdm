"""GUI logic tests (offscreen platform, no GL rendering needed).

Run with: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_app.py -q
The real support pipeline is used when its modules are importable; otherwise a tiny
stand-in generator is injected so the UI plumbing (worker thread, status bar) is still tested.
"""
from __future__ import annotations

import os
import time

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
def window(app, monkeypatch):
    from arbor.app import main_window as mw
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
    # the export button carries a menu of three connected actions
    menu = window.export_button.menu()
    exports = [a for a in menu.actions() if not a.isSeparator()]
    assert len(exports) == 3
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

"""Render real screenshots of the ArborFDM window (needs a GL display, e.g. xvfb):

    xvfb-run -a -s "-screen 0 1600x1000x24" python3 scripts/screenshot.py [--out docs/screenshots]

Builds a synthetic figurine (cylinder body, sphere head, horizontal arms, a base disc),
loads it through the normal UI path, generates supports with the real pipeline (falls back
to a crude stand-in generator only if the engine modules are missing, and says so), adds a
manual contact by a simulated click, and saves several views.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import trimesh  # noqa: E402


def figurine() -> trimesh.Trimesh:
    body = trimesh.creation.cylinder(radius=4.0, height=18.0, sections=40)
    body.apply_translation([0, 0, 9.0 + 1.5])
    base = trimesh.creation.cylinder(radius=7.0, height=1.5, sections=48)
    base.apply_translation([0, 0, 0.75])
    head = trimesh.creation.icosphere(subdivisions=3, radius=3.6)
    head.apply_translation([0, 0, 19.5 + 1.5 + 3.0])
    arm = trimesh.creation.box((22.0, 2.6, 2.6))
    arm.apply_translation([0, 0, 15.0])
    hand_l = trimesh.creation.icosphere(subdivisions=2, radius=1.9)
    hand_l.apply_translation([11.5, 0, 15.0])
    hand_r = hand_l.copy()
    hand_r.apply_translation([-23.0, 0, 0])
    import manifold3d as mf

    def to_m(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                   tri_verts=np.asarray(m.faces, np.uint32)))
    acc = to_m(base)
    for part in (body, head, arm, hand_l, hand_r):
        acc = acc + to_m(part)
    out = acc.to_mesh()
    return trimesh.Trimesh(np.asarray(out.vert_properties)[:, :3], np.asarray(out.tri_verts))


def engine_available() -> bool:
    try:
        from arbor.engine import pipeline  # noqa: F401
        return True
    except Exception as exc:  # pragma: no cover - only while modules are being written
        print(f"[screenshot] real engine not importable ({exc}); using the stand-in generator")
        return False


def stand_in_generate(mesh, params, manual_contacts=None, progress=None, analysis=None,
                      contacts=None):
    """Very crude replacement of pipeline.generate, ONLY used when the engine is missing:
    vertical pillars under downward facing faces. Keeps the screenshot script usable."""
    from arbor.engine.types import (AnalysisResult, Contact, ContactReason, GenerationResult,
                                    Issue, NodeKind, SupportTree)
    if contacts is None:
        n = mesh.face_normals
        c = mesh.triangles_center
        down = (n[:, 2] < -0.8) & (c[:, 2] > 1.0)
        pts = c[down]
        chosen = []
        for p in pts[np.argsort(pts[:, 0])]:
            if all(np.linalg.norm(p - q) > 2.5 for q in chosen):
                chosen.append(p)
        contacts = [Contact(i + 1, p, np.array([0, 0, -1.0]), ContactReason.EDGE if i % 3
                            else ContactReason.LOW_POINT) for i, p in enumerate(chosen)]
        contacts += list(manual_contacts or [])
    tree = SupportTree()
    parts = []
    for c in contacts:
        if not c.enabled:
            continue
        top = np.asarray(c.position) - [0, 0, 1.6]
        cyl = trimesh.creation.cylinder(radius=0.8, segment=[[top[0], top[1], 0], top])
        tip = trimesh.creation.cone(radius=0.6, height=1.6)
        tip.apply_translation(top)
        foot = trimesh.creation.cylinder(radius=2.5, height=0.6)
        foot.apply_translation([top[0], top[1], 0.3])
        parts += [cyl, tip, foot]
        f = tree.add([top[0], top[1], 0], 2.5, NodeKind.FOOT)
        tree.add(top, 0.8, NodeKind.TIP, f.id, c.id)
    sup = trimesh.util.concatenate(parts) if parts else trimesh.Trimesh()
    issues = [Issue("error", "unsupported_region", "Stand-in: example unsupported region",
                    np.array([0.0, 3.6, 21.0])),
              Issue("warning", "thin_tip", "Stand-in: example warning", None, contacts[0].id)]
    return GenerationResult(contacts, tree, sup, None, issues,
                            AnalysisResult(params.layer_height, np.zeros(0)), {"stand-in": 0.0})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from PySide6 import QtCore, QtGui, QtWidgets
    fmt = QtGui.QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setProperty("arbor_no_dialogs", True)
    from arbor.app import main_window as mw
    from arbor.app.theme import apply_theme
    apply_theme(app)

    real = engine_available()
    if not real:
        mw.run_generation = lambda progress, **kw: stand_in_generate(progress=progress, **kw)

    win = mw.MainWindow()
    win.resize(1600, 960)
    win.show()

    def pump(seconds: float = 0.3) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents(QtCore.QEventLoop.AllEvents, 50)

    def wait_task(timeout: float = 300.0) -> None:
        end = time.monotonic() + timeout
        while win.runner.is_running() and time.monotonic() < end:
            app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        pump(0.3)

    def shot(name: str) -> None:
        pump(0.4)
        win.grab().save(str(out / f"{name}.png"))
        win.viewport.grabFramebuffer().save(str(out / f"{name}_viewport.png"))
        print("[screenshot]", out / f"{name}.png")

    pump(0.5)
    shot("01_empty")

    stl = out / "figurine.stl"
    figurine().export(stl)
    win.open_path(str(stl))
    wait_task()
    shot("02_model_loaded")

    win.check_mesh()
    wait_task()
    win.generate_full()
    t0 = time.monotonic()
    wait_task()
    print(f"[screenshot] generation {time.monotonic() - t0:.1f} s, status: "
          f"{win.status_msg.text()}")
    if win.last_error:
        print(win.last_error)
    r = win.project.result
    if r is not None:
        print(f"[screenshot] contacts={len(r.contacts)} issues={len(r.issues)} "
              f"support faces={len(r.support_mesh.faces) if r.support_mesh is not None else 0}")
    shot("03_supports_generated")

    # manual contact by a simulated click under the right hand
    vp = win.viewport
    vp.set_preset_view("front")
    vp.setCameraPosition(elevation=-20)
    pump(0.3)
    # underside of the left arm (arm box: z 13.7 .. 16.3 above the plate, x -11 .. 11)
    target = np.array([-7.0, 0.0, 13.7])
    px, _ = vp.project_points(target[None])
    what = vp.handle_click(float(px[0, 0]), float(px[0, 1]), QtCore.Qt.MouseButton.LeftButton)
    print("[screenshot] simulated click ->", what)
    hit = vp.pick_model(float(px[0, 0]) + 30, float(px[0, 1]))
    if hit is not None:
        vp.show_ghost(hit[0], hit[1])
    shot("04_manual_contact_below")
    vp.ghost_item.hide()

    win.generate_branches()
    wait_task()
    vp.set_preset_view("iso")
    vp.fit_view()
    shot("05_rebuilt_branches")

    win.view.mode_buttons["supports"].click()
    shot("06_supports_only")
    win.view.mode_buttons["both"].click()
    win.view.clip_slider.setValue(560)
    pump(0.3)
    shot("07_clipped")
    win.view.clip_slider.setValue(1000)
    win.right.tabs.setCurrentIndex(0)
    win.project.set_param("overhang_angle_deg", 50.0)
    shot("08_advanced_params")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

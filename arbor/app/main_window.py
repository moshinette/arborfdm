"""Main window: toolbar, panels, viewport, status bar, background tasks."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from arbor.core.project import PROJECT_SUFFIX, GenerationRequest, Project

from . import theme
from .panels import LeftPanel, RightPanel
from .viewport import ViewportContainer
from .worker import TaskRunner, run_generation

MODEL_FILTER = "3D models (*.stl *.obj *.3mf)"
OPEN_FILTER = f"Models and projects (*.stl *.obj *.3mf *{PROJECT_SUFFIX});;{MODEL_FILTER};;" \
              f"ArborFDM project (*{PROJECT_SUFFIX})"

EXPORT_REMINDER = (
    "Before slicing:\n\n"
    "  - Disable the slicer's own supports (they are already in the file).\n"
    "  - Keep the layer height of the profile ({layer:.2f} mm) so tips touch as designed.\n"
    "  - Print the supports with {walls} walls; do not move the parts relative to each other.")


# --- background task functions (run in the worker thread) ----------------------------
def _safe_check(mesh):
    """check_mesh report, or None if the diagnosis itself fails (never blocks a load)."""
    from arbor.core.repair import check_mesh
    try:
        return check_mesh(mesh)
    except Exception:
        return None


def _task_load(progress, path: str):
    from arbor.core.mesh_io import load_mesh
    progress.update(0.1, "Reading file")
    loaded = load_mesh(path)
    progress.update(0.7, "Checking mesh")
    return loaded, _safe_check(loaded.mesh)


def _task_load_project(progress, path: str, profiles):
    progress.update(0.1, "Reading project")
    project = Project.load(path, profiles)
    progress.update(0.7, "Checking mesh")
    return project, _safe_check(project.base_mesh)


def _task_check(progress, mesh):
    from arbor.core.repair import check_mesh
    progress.update(0.1, "Checking mesh")
    return check_mesh(mesh)


def _task_repair(progress, mesh):
    from arbor.core.repair import repair_mesh
    progress.update(0.1, "Repairing a copy of the mesh")
    return repair_mesh(mesh)


def _task_orient(progress, mesh, overhang_angle_deg):
    from arbor.core.orientation import suggest_orientations
    progress.update(0.1, "Evaluating orientations")
    return suggest_orientations(mesh, overhang_angle_deg=overhang_angle_deg, n=6)


def _task_export(progress, kind: str, path: str, model, supports, raft, params, name):
    from arbor.io import export as ex
    progress.update(0.2, "Writing file")
    if kind == "3mf":
        ex.export_3mf(path, model, supports, raft, params, model_name=name)
        progress.update(0.8, "Validating file")
        return ex.validate_export(path)
    parts = [m for m in ((model,) if kind == "stl_all" else ()) + (supports, raft)
             if m is not None and len(m.faces)]
    if not parts:
        raise ValueError("Nothing to export: generate supports first")
    ex.export_stl(parts if len(parts) > 1 else parts[0], path)
    return []


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, project: Optional[Project] = None, parent=None):
        super().__init__(parent)
        self.project = project or Project()
        self.runner = TaskRunner(self)
        self.last_error = ""
        self.last_mesh_report = None
        self.last_info = ("", "")
        self.last_mesh_warning = ""
        # what to do when generating on a mesh with errors and no dialog can be shown
        # (tests, scripted use): "generate" (with a visible warning), "repair" or "cancel"
        self.broken_mesh_choice = "generate"
        self._generate_after_repair: Optional[bool] = None
        self.setWindowTitle("ArborFDM")
        self.resize(1500, 920)

        self.view = ViewportContainer()
        self.viewport = self.view.viewport
        self.left = LeftPanel(self.project)
        self.right = RightPanel(self.project)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self.left)
        splitter.addWidget(self.view)
        splitter.addWidget(self.right)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(1, False)
        splitter.setSizes([340, 820, 360])
        self.setCentralWidget(splitter)

        self._build_actions()
        self._build_toolbar()
        self._build_menu()
        self._build_statusbar()
        self._connect()
        self._refresh_everything(fit=True)

    # ------------------------------------------------------------------ construction
    def _action(self, text: str, slot, icon=None, shortcut=None, tip: str = "") -> QtGui.QAction:
        a = QtGui.QAction(text, self)
        if icon is not None:
            a.setIcon(icon if isinstance(icon, QtGui.QIcon) else theme.std_icon(self, icon))
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
        a.setToolTip(tip or text)
        a.setStatusTip(tip or text)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        K = QtGui.QKeySequence.StandardKey
        self.act_new = self._action("New", self.new_project, "SP_FileIcon", K.New,
                                    "New empty project")
        self.act_open = self._action("Open", self.open_dialog, "SP_DialogOpenButton", K.Open,
                                     "Open a model (STL, OBJ, 3MF) or an ArborFDM project")
        self.act_save = self._action("Save", self.save_project, "SP_DialogSaveButton", K.Save,
                                     "Save the project (.arbor)")
        self.act_save_as = self._action("Save as...", self.save_project_as, None, K.SaveAs)
        self.act_undo = self._action("Undo", self.undo, "SP_ArrowBack", K.Undo)
        self.act_redo = self._action("Redo", self.redo, "SP_ArrowForward", K.Redo)
        self.act_generate = self._action("Generate supports", self.generate_full,
                                         theme.tree_icon(), "Ctrl+G",
                                         "Detect, place contacts and build the trees "
                                         "(manual contacts are kept)")
        self.act_rebuild = self._action("Rebuild branches", self.generate_branches,
                                        theme.rebuild_icon(), "Ctrl+B",
                                        "Rebuild the trees from the current contacts "
                                        "(after manual edits)")
        self.act_clear = self._action("Clear supports", self.project.clear_supports,
                                      "SP_DialogResetButton", None,
                                      "Remove all contacts and supports (undoable)")
        self.act_export_3mf = self._action("3MF: model + supports + raft", self.export_3mf, None,
                                           "Ctrl+E", "One 3MF with separate objects")
        self.act_export_stl_sup = self._action("STL: supports only", self.export_stl_supports)
        self.act_export_stl_all = self._action("STL: model + supports", self.export_stl_all)
        self.act_cancel = self._action("Cancel", self.cancel_task, "SP_BrowserStop", "Esc",
                                       "Cancel the running computation")
        self.act_fit = self._action("Fit view", lambda: self.viewport.fit_view(), None, "F")
        self.act_quit = self._action("Quit", self.close, None, K.Quit)

    def _build_toolbar(self) -> None:
        tb = QtWidgets.QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(20, 20))
        tb.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        for a in (self.act_new, self.act_open, self.act_save):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        tb.addAction(self.act_generate)
        btn = tb.widgetForAction(self.act_generate)
        if btn is not None:
            btn.setObjectName("primary")
        tb.addAction(self.act_rebuild)
        tb.addAction(self.act_clear)
        tb.addSeparator()
        self.export_menu = QtWidgets.QMenu("Export", self)
        self.export_menu.addAction(self.act_export_3mf)
        self.export_menu.addAction(self.act_export_stl_sup)
        self.export_menu.addAction(self.act_export_stl_all)
        self.export_button = QtWidgets.QToolButton()
        self.export_button.setText("Export")
        self.export_button.setIcon(theme.export_icon())
        self.export_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.export_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.export_button.setMenu(self.export_menu)
        self.export_button.setToolTip("Export the model and its supports")
        tb.addWidget(self.export_button)
        self.toolbar = tb

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addMenu(self.export_menu)
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&Edit")
        m.addAction(self.act_undo)
        m.addAction(self.act_redo)
        m = mb.addMenu("&Supports")
        for a in (self.act_generate, self.act_rebuild, self.act_clear, self.act_cancel):
            m.addAction(a)
        m = mb.addMenu("&View")
        m.addAction(self.act_fit)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.status_msg = QtWidgets.QLabel("Ready")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setIcon(theme.std_icon(self, "SP_BrowserStop"))
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        self.contacts_label = QtWidgets.QLabel("Contacts: 0")
        self.tree_label = QtWidgets.QLabel("Trees: -")
        self.time_label = QtWidgets.QLabel("Time: -")
        self.issues_label = QtWidgets.QLabel("Errors: 0  Warnings: 0")
        sb.addWidget(self.status_msg, 1)
        sb.addPermanentWidget(self.progress_bar)
        sb.addPermanentWidget(self.cancel_button)
        for w in (self.contacts_label, self.tree_label, self.time_label, self.issues_label):
            sb.addPermanentWidget(w)

    def _connect(self) -> None:
        self.project.listeners.append(self._on_project_event)
        self.left.importRequested.connect(self.import_dialog)
        self.left.checkRequested.connect(self.check_mesh)
        self.left.repairRequested.connect(self.repair_mesh)
        self.left.orientRequested.connect(self.suggest_orientation)
        self.right.issueActivated.connect(self._on_issue_activated)
        self.right.contact_editor.deleteRequested.connect(self.project.remove_contact)
        vp = self.viewport
        vp.contactAddRequested.connect(self._on_add_contact)
        vp.contactRemoveRequested.connect(self.project.remove_contact)
        vp.contactSelected.connect(self.project.select_contact)
        self.runner.progress.connect(self._on_progress)
        self.runner.running_changed.connect(self._on_running_changed)
        self.runner.finished.connect(self._on_task_finished)
        self.runner.failed.connect(self._on_task_failed)
        self.runner.cancelled.connect(self._on_task_cancelled)

    # ------------------------------------------------------------------ refresh
    def _refresh_everything(self, fit: bool = False) -> None:
        self.left.refresh_all()
        self.right.refresh_params()
        self._refresh_scene(fit)
        self._refresh_contacts()
        self._refresh_result()
        self._refresh_actions()
        self._refresh_title()

    def _refresh_scene(self, fit: bool = False) -> None:
        self.viewport.set_model(self.project.mesh, fit=fit)
        self.view.refresh_clip()

    def _refresh_contacts(self) -> None:
        p = self.project
        self.viewport.set_contacts(p.contacts)
        self.viewport.set_selected(p.selected_contact_id)
        self.right.contact_editor.refresh()
        n = len(p.contacts)
        nm = len(p.manual_contacts())
        nd = sum(1 for c in p.contacts if not c.enabled)
        txt = f"Contacts: {n}"
        if nm or nd:
            txt += f" ({nm} manual" + (f", {nd} disabled" if nd else "") + ")"
        self.contacts_label.setText(txt)
        self._refresh_actions()

    def _refresh_result(self) -> None:
        r = self.project.result
        self.viewport.set_supports(r.support_mesh if r else None, r.raft_mesh if r else None)
        self.viewport.set_issues(r.issues if r else [])
        self.right.set_issues(r.issues if r else [], r is not None)
        self.view.refresh_clip()
        if r is None:
            self.tree_label.setText("Trees: -")
            self.time_label.setText("Time: -")
            self.issues_label.setText("Errors: 0  Warnings: 0")
            self.issues_label.setStyleSheet("")
        else:
            st = r.tree.stats()
            self.tree_label.setText(f"Trees: {st['trees']}  Junctions: {st['junctions']}  "
                                    f"Feet: {st['feet']}" + (f" (+{st['model_feet']} on model)"
                                                             if st.get("model_feet") else ""))
            total = sum(r.timings.values()) if r.timings else 0.0
            self.time_label.setText(f"Time: {total:.2f} s")
            self.time_label.setToolTip("\n".join(f"{k}: {v:.3f} s" for k, v in r.timings.items()))
            ne, nw = len(r.errors), len(r.warnings)
            self.issues_label.setText(f"Errors: {ne}  Warnings: {nw}")
            self.issues_label.setStyleSheet(f"color: {theme.ERROR};" if ne else
                                            (f"color: {theme.WARNING};" if nw else ""))
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        p = self.project
        busy = self.runner.is_running()
        has = p.has_model
        res = p.result is not None
        self.act_undo.setEnabled(p.undo_stack.can_undo() and not busy)
        self.act_redo.setEnabled(p.undo_stack.can_redo() and not busy)
        self.act_undo.setToolTip("Undo " + p.undo_stack.undo_text())
        self.act_redo.setToolTip("Redo " + p.undo_stack.redo_text())
        self.act_save.setEnabled(has and not busy)
        self.act_save_as.setEnabled(has and not busy)
        self.act_generate.setEnabled(has and not busy)
        self.act_rebuild.setEnabled(has and not busy and bool(p.contacts))
        self.act_clear.setEnabled(not busy and (bool(p.contacts) or res))
        self.act_export_3mf.setEnabled(has and not busy)
        self.act_export_stl_sup.setEnabled(res and not busy)
        self.act_export_stl_all.setEnabled(has and not busy)
        self.export_button.setEnabled(has and not busy)
        self.act_new.setEnabled(not busy)
        self.act_open.setEnabled(not busy)
        self.act_cancel.setEnabled(busy)
        # QA-3: the model (transform, repair, import) is locked while a task runs; contact
        # and parameter edits stay possible and mark the result out of date when it arrives
        self.left.set_busy(busy)
        self.viewport.edit_enabled = has
        self._refresh_banner()
        self.viewport.set_tip_preview(p.params.tip_length, p.params.tip_base_diameter)
        if p.contacts_edited and res:
            self.act_rebuild.setText("Rebuild branches *")
        else:
            self.act_rebuild.setText("Rebuild branches")
        self.act_generate.setText("Generate supports *" if p.result_param_changes()
                                  else "Generate supports")

    def stale_reasons(self) -> list[str]:
        """Why the displayed supports do not match the document (empty if they do)."""
        p = self.project
        if p.result is None:
            return []
        out = []
        if p.contacts_edited:
            out.append("contacts edited since the generation (Rebuild branches)")
        ch = p.result_param_changes()
        if ch:
            names = ", ".join(f"{k} {a} -> {b}" for k, (a, b) in list(ch.items())[:4])
            more = f" (+{len(ch) - 4})" if len(ch) > 4 else ""
            out.append(f"parameters changed: {names}{more} (Generate supports)")
        return out

    def _refresh_banner(self) -> None:
        busy = self.runner.is_running()
        lines, kind = [], ""
        if busy:
            kind = "busy"
            lines.append(f"{self.runner.label or 'Computing'}...  The model is locked. Contact "
                         "and parameter edits made now are kept, the result will be marked out "
                         "of date.")
        stale = self.stale_reasons()
        if stale:
            kind = kind or "stale"
            lines.append("Supports out of date: " + "; ".join(stale))
        if self.project.has_model and self.project.mesh_problems():
            kind = kind or "mesh"
            lines.append("Mesh has errors (" + ", ".join(
                x.code for x in self.project.mesh_problems()[:3]) + "): use Mesh check > Repair "
                "before generating")
        self.view.set_banner("\n".join(lines), kind)

    def _refresh_title(self) -> None:
        p = self.project
        name = p.path.name if p.path else (p.name or "untitled")
        self.setWindowTitle(f"{name}{' *' if p.dirty else ''} - ArborFDM")

    def _on_project_event(self, ev: str) -> None:
        if ev == "mesh":
            self.left.refresh_model()
            self._refresh_scene(fit=False)
            self._refresh_banner()
        elif ev == "transform":
            self.left.refresh_transform()
        elif ev == "params":
            self.left.refresh_params()
            self.right.refresh_params()
            self._refresh_actions()
        elif ev == "contacts":
            self._refresh_contacts()
        elif ev == "selection":
            self.viewport.set_selected(self.project.selected_contact_id)
            self.right.contact_editor.refresh()
            if self.project.selected_contact_id is not None:
                self.right.tabs.setCurrentIndex(1)
        elif ev == "result":
            self._refresh_result()
        elif ev in ("undo", "dirty"):
            self._refresh_actions()
            self._refresh_title()

    # ------------------------------------------------------------------ helpers
    def message(self, text: str, error: bool = False, warning: bool = False) -> None:
        self.status_msg.setText(text)
        self.status_msg.setStyleSheet(f"color: {theme.ERROR};" if error else
                                      (f"color: {theme.WARNING};" if warning else ""))

    def _start(self, fn, kwargs: dict, context, label: str) -> bool:
        if self.runner.is_running():
            self.message("A computation is already running", error=True)
            return False
        self.message(label + "...")
        return self.runner.start(fn, kwargs, context=context, label=label)

    def _confirm_discard(self) -> bool:
        if not self.project.dirty or not self.project.has_model:
            return True
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            return True
        r = QtWidgets.QMessageBox.question(
            self, "Unsaved changes", "The project has unsaved changes. Discard them?",
            QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
        return r == QtWidgets.QMessageBox.Discard

    def _ask_path(self, save: bool, caption: str, filt: str, default: str = "") -> str:
        hook = getattr(self, "path_provider", None)       # tests inject paths here
        if hook is not None:
            return hook(save, caption, filt, default) or ""
        if save:
            return QtWidgets.QFileDialog.getSaveFileName(self, caption, default, filt)[0]
        return QtWidgets.QFileDialog.getOpenFileName(self, caption, default, filt)[0]

    def _info(self, title: str, text: str) -> None:
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            self.last_info = (title, text)
            return
        QtWidgets.QMessageBox.information(self, title, text)

    # ------------------------------------------------------------------ file slots
    def new_project(self) -> None:
        if self.runner.is_running() or not self._confirm_discard():
            return
        self.project.new()
        self.view.reset_clip()
        self._refresh_everything(fit=True)
        self.message("New project")

    def open_dialog(self) -> None:
        path = self._ask_path(False, "Open model or project", OPEN_FILTER)
        if path:
            self.open_path(path)

    def import_dialog(self) -> None:
        path = self._ask_path(False, "Import model", MODEL_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        if not self._confirm_discard():
            return False
        if Path(path).suffix.lower() == PROJECT_SUFFIX:
            return self._start(_task_load_project, {"path": path, "profiles": self.project.profiles},
                               ("open_project", path), f"Opening {Path(path).name}")
        return self._start(_task_load, {"path": path}, ("load", path),
                           f"Loading {Path(path).name}")

    def save_project(self) -> bool:
        if self.project.path is None:
            return self.save_project_as()
        return self._save_to(self.project.path)

    def save_project_as(self) -> bool:
        default = str(self.project.path or Path(self.project.name or "project")
                      .with_suffix(PROJECT_SUFFIX))
        path = self._ask_path(True, "Save project", f"ArborFDM project (*{PROJECT_SUFFIX})", default)
        return self._save_to(Path(path)) if path else False

    def _save_to(self, path: Path) -> bool:
        if not self.project.has_model:
            return False
        try:
            saved = self.project.save(path)
        except Exception as exc:
            self.message(f"Save failed: {exc}", error=True)
            return False
        self._refresh_title()
        self.message(f"Saved {saved}")
        return True

    # ------------------------------------------------------------------ edit slots
    def undo(self) -> None:
        if not self.runner.is_running():
            self.project.undo()

    def redo(self) -> None:
        if not self.runner.is_running():
            self.project.redo()

    def _on_add_contact(self, position, normal) -> None:
        c = self.project.add_manual_contact(position, normal)
        self.project.select_contact(c.id)
        self.message(f"Manual contact #{c.id} added at z = {position[2]:.2f} mm. "
                     "Use Rebuild branches to update the trees.")

    def _on_issue_activated(self, issue) -> None:
        pos = issue.position
        if pos is None and issue.contact_id is not None:
            c = self.project.contact(issue.contact_id)
            pos = c.position if c is not None else None
        if issue.contact_id is not None:
            self.project.select_contact(issue.contact_id)
        if pos is not None:
            self.viewport.focus_on(pos)

    # ------------------------------------------------------------------ mesh tools
    def check_mesh(self) -> None:
        if self.project.has_model:
            self._start(_task_check, {"mesh": self.project.base_mesh.copy()},
                        ("check", self.project.base_mesh), "Checking mesh")

    def repair_mesh(self) -> None:
        if self.project.has_model:
            self._start(_task_repair, {"mesh": self.project.base_mesh.copy()}, ("repair",
                        self.project.mesh_version), "Repairing mesh")

    def suggest_orientation(self) -> None:
        if self.project.has_model:
            self._start(_task_orient, {"mesh": self.project.base_mesh.copy(),
                                       "overhang_angle_deg": self.project.params.overhang_angle_deg},
                        ("orient", self.project.mesh_version), "Evaluating orientations")

    # ------------------------------------------------------------------ generation
    def generate_full(self) -> bool:
        return self._generate(full=True)

    def generate_branches(self) -> bool:
        return self._generate(full=False)

    def broken_mesh_text(self) -> str:
        p = self.project
        probs = p.mesh_problems()
        if not probs:
            return ""
        return ("The model mesh has errors: " + "; ".join(x.message.rstrip(".") for x in probs[:4])
                + (f" (+{len(probs) - 4} more)" if len(probs) > 4 else "") + ".\n"
                "Supports computed on it may be wrong (contacts in holes, unroutable "
                "contacts, false collisions). Repair it first (a repaired copy is used, the "
                "imported file is unchanged, undoable).")

    def broken_mesh_box(self) -> QtWidgets.QMessageBox:
        """The question asked before generating on a broken mesh (QA-6)."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Mesh has errors")
        box.setText("This mesh is not a valid closed solid.")
        box.setInformativeText(self.broken_mesh_text())
        box.repair_button = box.addButton("Repair, then generate", QtWidgets.QMessageBox.AcceptRole)
        box.generate_button = box.addButton("Generate anyway", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(box.repair_button)
        return box

    def _ask_broken_mesh(self) -> str:
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            return self.broken_mesh_choice
        box = self.broken_mesh_box()
        box.exec()
        if box.clickedButton() is box.repair_button:
            return "repair"
        if box.clickedButton() is box.generate_button:
            return "generate"
        return "cancel"

    def _generate(self, full: bool, mesh_checked: bool = False) -> bool:
        p = self.project
        if not p.has_model:
            return False
        problems = p.params.validate()
        if problems:
            self.message("Invalid parameters: " + "; ".join(problems), error=True)
            return False
        warn = self.broken_mesh_text()
        self.last_mesh_warning = warn
        if warn and not mesh_checked and not self.runner.is_running():
            choice = self._ask_broken_mesh()
            if choice == "cancel":
                self.message("Generation cancelled: the mesh has errors (Mesh check > Repair)",
                             warning=True)
                return False
            if choice == "repair":
                self._generate_after_repair = full
                self.repair_mesh()
                return True
        req = p.regenerate(full=full)
        label = "Generating supports" if req.mode == "full" else "Rebuilding branches"
        ok = self._start(run_generation, req.kwargs, ("generate", req, warn), label)
        if ok and warn:
            self.message(label + "... WARNING: the mesh has errors, results may be wrong "
                         "(Mesh check > Repair)", warning=True)
        return ok

    def cancel_task(self) -> None:
        if self.runner.is_running():
            self.runner.cancel()
            self.message("Cancelling...")

    # ------------------------------------------------------------------ export
    def export_3mf(self) -> None:
        self._export("3mf", "Export 3MF", "3MF (*.3mf)", ".3mf")

    def export_stl_supports(self) -> None:
        self._export("stl_supports", "Export supports STL", "STL (*.stl)", "_supports.stl")

    def export_stl_all(self) -> None:
        self._export("stl_all", "Export model + supports STL", "STL (*.stl)", "_supported.stl")

    def _export(self, kind: str, caption: str, filt: str, suffix: str) -> None:
        p = self.project
        if not p.has_model:
            return
        r = p.result
        if r is None and kind != "stl_all" and kind != "3mf":
            self.message("Generate supports first", error=True)
            return
        warnings = []
        if r is not None and p.contacts_edited:
            warnings.append("Contacts were edited since the last generation: the file does not "
                            "include these edits (use Rebuild branches, then export again).")
        changed = p.result_param_changes() if r is not None else {}
        if changed:
            warnings.append("Parameters changed since the supports were generated ("
                            + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items())
                            + "): the file holds the supports as generated, with the "
                            "parameters really used; generate again to apply the new values.")
        if warnings:
            self.message(" ".join(warnings), warning=True)
        base = (p.name or "model").rsplit(".", 1)[0]
        path = self._ask_path(True, caption, filt, base + suffix)
        if not path:
            return
        # snapshot taken now: the parameters written are those that built the geometry
        params = p.export_params()
        self._start(_task_export, {"kind": kind, "path": path, "model": p.mesh.copy(),
                                   "supports": r.support_mesh if r else None,
                                   "raft": r.raft_mesh if r else None, "params": params,
                                   "name": base}, ("export", path, kind, warnings, params),
                    "Exporting")

    # ------------------------------------------------------------------ task results
    def _on_running_changed(self, running: bool) -> None:
        self.progress_bar.setVisible(running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        if running:
            self.progress_bar.setValue(0)
        self._refresh_actions()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress_bar.setValue(int(fraction * 1000))
        if message:
            self.status_msg.setText(f"{self.runner.label}: {message}")

    def _on_task_failed(self, message: str, tb: str, context) -> None:
        self.last_error = tb
        kind = context[0] if context else ""
        self._generate_after_repair = None
        self.message(f"{self.runner.label or kind} failed: {message}", error=True)
        if kind in ("load", "open_project"):
            self._info("Cannot open file", message)

    def _on_task_cancelled(self, context) -> None:
        self._generate_after_repair = None
        self.message("Cancelled")

    def _on_task_finished(self, result, context) -> None:
        kind = context[0]
        elapsed = self.runner.elapsed
        p = self.project
        if kind == "load":
            result, report = result
            p.set_model(result.mesh, name=result.name, source_path=str(context[1]),
                        warnings=list(result.warnings))
            p.set_mesh_report(p.base_mesh, report)
            self.last_mesh_report = report
            self.left.show_candidates([])
            self._show_mesh_state(report)
            self.view.reset_clip()
            self._refresh_everything(fit=True)
            msg = (f"Loaded {result.name}: {len(result.mesh.faces):,} triangles"
                   + (f", {len(result.warnings)} warning(s)" if result.warnings else ""))
            if p.mesh_problems():
                self.message(msg + f". The mesh has {len(p.mesh_problems())} error(s): "
                             "use Mesh check > Repair before generating.", warning=True)
            else:
                self.message(msg)
        elif kind == "open_project":
            result, report = result
            p.adopt(result)                  # same document object: panels keep their refs
            p.set_mesh_report(p.base_mesh, report)
            self.last_mesh_report = report
            self._show_mesh_state(report)
            self.view.reset_clip()
            self._refresh_everything(fit=True)
            self.message(f"Opened project {Path(context[1]).name}"
                         + (" (the mesh has errors: Repair recommended)" if p.mesh_problems()
                            else ""), warning=bool(p.mesh_problems()))
        elif kind == "check":
            self.last_mesh_report = result
            if len(context) > 1:
                p.set_mesh_report(context[1], result)
                self._refresh_banner()
            ok = getattr(result, "is_valid", not getattr(result, "problems", []))
            text = result.summary() if hasattr(result, "summary") else str(result)
            self.left.show_mesh_report(text, ok)
            self.message("Mesh check: " + ("no error" if ok else
                                          f"{len(result.problems)} problem(s)"))
        elif kind == "repair":
            new_mesh, report = result
            text = report.summary() if hasattr(report, "summary") else str(report)
            self.left.show_mesh_report(text, bool(getattr(report, "success", True)))
            pending, self._generate_after_repair = self._generate_after_repair, None
            if context[1] != p.mesh_version:
                self.message("Model changed during the repair: result discarded", error=True)
                pending = None
            elif getattr(report, "changed", True) and new_mesh is not None:
                p.replace_base_mesh(new_mesh)
                p.set_mesh_report(p.base_mesh, getattr(report, "after", None))
                self.message("Mesh repaired (undo restores the previous mesh; the imported "
                             "file is unchanged)")
            else:
                p.set_mesh_report(p.base_mesh, getattr(report, "after", None))
                self.message("Repair: nothing to change")
            self._refresh_banner()
            if pending is not None:
                # the user chose "Repair, then generate"; errors that remain are reported
                self._generate(pending, mesh_checked=True)
        elif kind == "orient":
            self.left.show_candidates(result)
            self.message(f"{len(result)} orientation(s) found: click one to apply it")
        elif kind == "generate":
            req: GenerationRequest = context[1]
            warn = context[2] if len(context) > 2 else ""
            if warn:
                # the diagnostic from the mesh check is more specific than the engine's
                # generic invalid_model_mesh warning: keep only the detailed one
                from arbor.engine.types import Issue
                result.issues[:] = [i for i in result.issues if i.code != "invalid_model_mesh"]
                result.issues.insert(0, Issue("warning", "invalid_model_mesh",
                                              warn.replace("\n", " ")))
            if not p.apply_result(result, req, text="Generate supports" if req.mode == "full"
                                  else "Rebuild branches"):
                self.message("The model changed during the computation: result discarded",
                             error=True)
                return
            msg = (f"{'Supports generated' if req.mode == 'full' else 'Branches rebuilt'}"
                   f" in {elapsed:.2f} s: {len(result.contacts)} contacts, "
                   f"{len(result.errors)} error(s), {len(result.warnings)} warning(s)")
            ar = p.last_apply
            notes = []
            if warn:
                notes.append("the mesh has errors, results may be wrong (Repair)")
            if ar is not None and (ar.kept_edits or ar.lost_edits):
                notes.append("edits made during the computation: "
                             + ", ".join((ar.kept_edits + ar.lost_edits)[:3])
                             + "; supports out of date, use Rebuild branches")
            if ar is not None and ar.changed_params:
                notes.append("parameters changed during the computation ("
                             + ", ".join(ar.changed_params) + "): built with the previous "
                             "values, use Generate supports")
            self.message(msg + (". WARNING: " + "; ".join(notes) if notes else ""),
                         warning=bool(notes))
            self._refresh_banner()
            self.right.tabs.setCurrentIndex(1 if result.issues else self.right.tabs.currentIndex())
        elif kind == "export":
            problems = result or []
            path = context[1]
            warnings = context[3] if len(context) > 3 else []
            pp = context[4] if len(context) > 4 else p.params
            if problems:
                self.message(f"Exported {Path(path).name} with {len(problems)} validation "
                             "problem(s)", error=True)
            elif warnings:
                # QA-13: the warning given before the export stays visible after it
                self.message(f"Exported {Path(path).name}. WARNING: " + " ".join(warnings),
                             warning=True)
            else:
                self.message(f"Exported {path}")
            text = EXPORT_REMINDER.format(layer=pp.layer_height, walls=pp.walls)
            if warnings:
                text = "WARNING:\n" + "\n".join(f"  - {w}" for w in warnings) + "\n\n" + text
            if problems:
                text += "\n\nValidation problems:\n" + "\n".join(f"  - {x}" for x in problems)
            self._info("Export done", f"Saved {path}\n\n{text}")

    def _show_mesh_state(self, report) -> None:
        if report is None:
            self.left.mesh_report.setText("Not checked")
            self.left.mesh_report.setStyleSheet("")
            return
        ok = getattr(report, "is_valid", True)
        text = report.summary() if hasattr(report, "summary") else str(report)
        if not ok:
            text = "Errors found: repair before generating\n" + text
        self.left.show_mesh_report(text, ok)

    # ------------------------------------------------------------------ close
    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        if not self._confirm_discard():
            ev.ignore()
            return
        if self.runner.is_running():
            self.runner.cancel()
            self.runner.wait(10000)
        ev.accept()

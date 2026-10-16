"""Main window: toolbar, panels, viewport, status bar, background tasks."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from arbor.core.presets import PresetError, PresetStore, is_user_key, slugify
from arbor.core.project import PROJECT_SUFFIX, GenerationRequest, Project

from . import icons, theme
from .panels import LeftPanel, RightPanel, issue_severity, issue_text, issue_title
from .preferences import PreferencesDialog
from .viewport import ViewportContainer
from .worker import TaskRunner, run_generation

PRESET_FILTER = "ArborFDM preset (*.json)"
MODEL_FILTER = "3D models (*.stl *.obj *.3mf)"
OPEN_FILTER = f"Models and projects (*.stl *.obj *.3mf *{PROJECT_SUFFIX});;{MODEL_FILTER};;" \
              f"ArborFDM project (*{PROJECT_SUFFIX})"

EXPORT_REMINDER = (
    "Before slicing:\n\n"
    "  - Disable the slicer's own supports (they are already in the file).\n"
    "  - Keep the layer height of the profile ({layer:.2f} mm) so tips touch as designed.\n"
    "  - Print the supports with {walls} walls; do not move the parts relative to each other.")


def _esc(text) -> str:
    from html import escape
    return escape(str(text))


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
    if kind in ("3mf", "3mf_separate"):
        # "separate": model and supports(+raft) as two objects both resting on the plate, so
        # Orca / Bambu Studio keep them apart (own layer height per object) without moving
        # them (see docs/SLICERS.md)
        ex.export_3mf(path, model, supports, raft, params, model_name=name,
                      layout="separate" if (kind == "3mf_separate" or (raft is not None and len(raft.faces))) else "objects")
        progress.update(0.8, "Validating file")
        return ex.validate_export(path)
    parts = [m for m in ((model,) if kind == "stl_all" else ()) + (supports, raft)
             if m is not None and len(m.faces)]
    if not parts:
        raise ValueError("Nothing to export: generate supports first")
    ex.export_stl(parts if len(parts) > 1 else parts[0], path)
    return []


def _task_orca_export(progress, model, supports, raft, params, name):
    """Write the 3MF for OrcaSlicer in its own folder (never over the source model or an
    existing file; the slicer's supports are switched off per object)."""
    from arbor.io import slicer_launch as sl
    progress.update(0.2, "Writing the 3MF for OrcaSlicer")
    notes: list = []
    path = sl.export_for_slicer(model, supports, raft, params, name, notes=notes)
    progress.update(0.9, "File validated")
    return path, notes


ORCA_REMINDER = (
    "Keep OrcaSlicer's own supports disabled: the supports are already in the file (they are "
    "switched off for these objects in the file, do not enable them again).\n"
    "Keep the layer height of the profile ({layer:.2f} mm) for the supports so the tips "
    "touch as designed, print them with {walls} walls and do not move the two objects "
    "relative to each other.")

# timings keys that are durations of the generation stages (others are statistics)
STAGE_KEYS = ("analysis", "contacts", "tree", "mesh", "verify")


def format_timings(timings, extra=None) -> tuple[Optional[float], str]:
    """(total time in s or None, tooltip). Unknown keys and non numeric values of
    ``timings`` are listed as statistics, never summed into the time; ``stats`` (dict of
    GenerationResult.stats, when the engine provides it: ``extra``) is listed after them."""
    timings = dict(timings or {})

    def num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f else None

    stages = [(k, num(timings[k])) for k in STAGE_KEYS if num(timings.get(k)) is not None]
    if stages:
        total = sum(v for _, v in stages)
    else:
        total = num(timings.get("total"))
    lines = [f"{k}: {v:.3f} s" for k, v in stages]
    stats = [(k, v) for k, v in timings.items() if k not in STAGE_KEYS
             and not (k == "total" and not stages)]
    if isinstance(extra, dict):
        stats += [(k, v) for k, v in extra.items() if not isinstance(v, (dict, list, tuple))]
    if stats:
        lines.append("Statistics:")
        for k, v in stats:
            f = num(v)
            if isinstance(v, bool) or f is None:
                txt = str(v)
            elif float(f).is_integer() and not isinstance(v, float):
                txt = f"{int(f)}"
            else:
                txt = f"{f:.4g}"
            lines.append(f"  {k}: {txt}")
    tip = "Computation time of the last generation, per stage"
    return total, tip + ("\n" + "\n".join(lines) if lines else "")


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
        self.last_orca: Optional[dict] = None
        self.setWindowTitle("ArborFDM")
        self.resize(1500, 920)

        # user presets: loaded from the user folder, shown after the built-in profiles
        self.presets = PresetStore(self.project.profiles)
        self.project.profiles = self.presets.profiles()
        self.preset_load_errors = list(self.presets.errors)

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
        if self.preset_load_errors:
            self.message(self._preset_errors_text(self.preset_load_errors), warning=True)

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
        self.act_new = self._action("New", self.new_project, icons.new(), K.New,
                                    "New empty project (Ctrl+N)")
        self.act_open = self._action("Open", self.open_dialog, icons.open_(), K.Open,
                                     "Open a model (STL, OBJ, 3MF) or an ArborFDM project "
                                     "(Ctrl+O)")
        self.act_save = self._action("Save", self.save_project, icons.save(), K.Save,
                                     "Save the project (.arbor) (Ctrl+S)")
        self.act_save_as = self._action("Save as...", self.save_project_as, None, K.SaveAs)
        self.act_undo = self._action("Undo", self.undo, icons.undo(), K.Undo)
        self.act_redo = self._action("Redo", self.redo, icons.redo(), K.Redo)
        self.act_generate = self._action("Generate supports", self.generate_full,
                                         icons.generate(), "Ctrl+G",
                                         "Detect, place contacts and build the trees "
                                         "(manual contacts are kept) (Ctrl+G)")
        self.act_rebuild = self._action("Rebuild branches", self.generate_branches,
                                        icons.rebuild(), "Ctrl+B",
                                        "Rebuild the trees from the current contacts "
                                        "(after manual edits) (Ctrl+B)")
        self.act_clear = self._action("Clear supports", self.project.clear_supports,
                                      icons.clear(), None,
                                      "Remove all contacts and supports (undoable)")
        self.act_export_3mf = self._action("3MF: model + supports + raft", self.export_3mf, None,
                                           "Ctrl+E", "One 3MF with separate objects")
        self.act_export_3mf_sep = self._action(
            "3MF for Orca / Bambu: separate objects", self.export_3mf_separate, None, None,
            "Model and supports as two objects that stay aligned: each can get its own layer "
            "height in OrcaSlicer / Bambu Studio")
        self.act_export_stl_sup = self._action("STL: supports only", self.export_stl_supports)
        self.act_export_stl_all = self._action("STL: model + supports", self.export_stl_all)
        self.act_open_orca = self._action(
            "Open in OrcaSlicer", self.open_in_orca, icons.slicer(), None,
            "Export model and supports and open them in OrcaSlicer")
        self.act_cancel = self._action("Cancel", self.cancel_task, icons.cancel(), "Esc",
                                       "Cancel the running computation (Esc)")
        self.act_fit = self._action("Fit view", lambda: self.viewport.fit_view(), icons.fit(), "F")
        self.act_preferences = self._action("Preferences...", self.open_preferences,
                                            icons.preferences(), "Ctrl+,",
                                            "Application settings (slicers)")
        self.act_preferences.setMenuRole(QtGui.QAction.PreferencesRole)
        self.act_quit = self._action("Quit", self.close, None, K.Quit)
        self.act_preset_save = self._action("Save preset...", self.save_preset, None, None,
                                            "Save the current parameters as a named preset")
        self.act_preset_rename = self._action("Rename preset...", self.rename_preset)
        self.act_preset_delete = self._action("Delete preset", self.delete_preset)
        self.act_preset_export = self._action("Export preset...", self.export_preset, None, None,
                                              "Write the selected preset to a .json file to "
                                              "share it")
        self.act_preset_import = self._action("Import preset...", self.import_preset, None, None,
                                              "Add a preset from a .json file")
        self.act_preset_folder = self._action("Open presets folder", self.open_preset_folder,
                                              None, None, str(self.presets.directory))
        self.preset_menu = QtWidgets.QMenu("Presets", self)
        for a in (self.act_preset_save, self.act_preset_rename, self.act_preset_delete, None,
                  self.act_preset_export, self.act_preset_import, None, self.act_preset_folder):
            self.preset_menu.addSeparator() if a is None else self.preset_menu.addAction(a)
        self.left.set_preset_actions(self.act_preset_save, self.preset_menu)
        self.right.set_preset_actions(self.act_preset_save)
        # contact editing (all undoable, through the Project)
        self.act_delete_contact = self._action(
            "Delete selected contact", self.delete_selected_contact, icons.clear(),
            K.Delete, "Delete the selected contact (Del)")
        self.act_deselect = self._action("Deselect contact", self.deselect_contact, None, "Esc",
                                         "Clear the contact selection (Esc)")
        self.act_delete_manual = self._action(
            "Delete all manual contacts", self.delete_manual_contacts, None, None,
            "Remove every contact placed by hand (one undoable step)")
        self.act_delete_reason = self._action(
            "Delete contacts of this reason", self.delete_selected_reason, None, None,
            "Remove every contact with the same reason as the selected one (one undoable step)")
        self.contacts_menu = QtWidgets.QMenu("Contacts", self)
        for a in (self.act_delete_contact, self.act_deselect, None, self.act_delete_reason,
                  self.act_delete_manual):
            self.contacts_menu.addSeparator() if a is None else self.contacts_menu.addAction(a)
        self.right.contact_editor.set_bulk_menu(self.contacts_menu)
        self.act_overhang_auto = self._action(
            "Overhang colors: automatic", lambda: self.view.set_overhang_choice(None), None, None,
            "Overhang colours on while no support is generated, off afterwards")

    def _build_toolbar(self) -> None:
        tb = QtWidgets.QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(theme.TOOLBAR_ICON, theme.TOOLBAR_ICON))
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
        self.export_menu.addAction(self.act_export_3mf_sep)
        self.export_menu.addAction(self.act_export_stl_sup)
        self.export_menu.addAction(self.act_export_stl_all)
        self.export_menu.addSeparator()
        self.export_menu.addAction(self.act_open_orca)
        self.export_button = QtWidgets.QToolButton()
        self.export_button.setText("Export")
        self.export_button.setIcon(icons.export())
        self.export_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.export_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.export_button.setMenu(self.export_menu)
        self.export_button.setToolTip("Export the model and its supports")
        tb.addWidget(self.export_button)
        tb.addAction(self.act_open_orca)
        self.orca_button = tb.widgetForAction(self.act_open_orca)
        self.toolbar = tb

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addMenu(self.export_menu)
        m.addSeparator()
        m.addAction(self.act_preferences)
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&Edit")
        m.addAction(self.act_undo)
        m.addAction(self.act_redo)
        m.addSeparator()
        for a in (self.act_delete_contact, self.act_deselect, self.act_delete_reason,
                  self.act_delete_manual):
            m.addAction(a)
        m = mb.addMenu("&Supports")
        for a in (self.act_generate, self.act_rebuild, self.act_clear, self.act_cancel):
            m.addAction(a)
        m.addSeparator()
        m.addMenu(self.preset_menu)
        m = mb.addMenu("&View")
        m.addAction(self.act_fit)
        m.addAction(self.act_overhang_auto)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_msg = QtWidgets.QLabel("Ready")
        # a long message is cut, never widens the window (full text in the tooltip)
        self.status_msg.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                      QtWidgets.QSizePolicy.Preferred)
        self.status_msg.setMinimumWidth(80)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setIcon(icons.cancel())
        self.cancel_button.setToolTip("Cancel the running computation (Esc)")
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        self.contacts_label = QtWidgets.QLabel("Contacts: 0")
        self.tree_label = QtWidgets.QLabel("Trees: -")
        self.time_label = QtWidgets.QLabel("Time: -")
        self.issues_label = QtWidgets.QLabel("Errors: 0  Warnings: 0")
        for w in (self.contacts_label, self.tree_label, self.time_label, self.issues_label):
            w.setObjectName("stat")
        self.time_label.setToolTip("Computation time of the last generation, per stage")
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
        vp.hoverTargetChanged.connect(self._refresh_inspect)
        self.runner.progress.connect(self._on_progress)
        self.runner.running_changed.connect(self._on_running_changed)
        self.runner.finished.connect(self._on_task_finished)
        self.runner.failed.connect(self._on_task_failed)
        self.runner.cancelled.connect(self._on_task_cancelled)
        self.view.busy_card.cancelRequested.connect(self.cancel_task)
        self._busy_progress = (0.0, "")
        self._busy_timer = QtCore.QTimer(self, interval=500)
        self._busy_timer.timeout.connect(self._refresh_busy_card)

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
        self.view.set_empty(not self.project.has_model)
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
        self._refresh_inspect()

    def _refresh_result(self) -> None:
        r = self.project.result
        self.viewport.set_supports(r.support_mesh if r else None, r.raft_mesh if r else None)
        self.view.set_supports_present(r is not None)
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
            total, tip = format_timings(r.timings, getattr(r, "stats", None))
            self.time_label.setText(f"Time: {total:.2f} s" if total is not None else "Time: -")
            self.time_label.setToolTip(tip)
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
        self.act_export_3mf_sep.setEnabled(res and not busy)
        self.act_export_stl_sup.setEnabled(res and not busy)
        self.act_export_stl_all.setEnabled(has and not busy)
        self.export_button.setEnabled(has and not busy)
        self.act_open_orca.setEnabled(res and not busy)
        self.act_new.setEnabled(not busy)
        self.act_open.setEnabled(not busy)
        self.act_cancel.setEnabled(busy)
        sel = p.contact(p.selected_contact_id) if p.selected_contact_id is not None else None
        # contact edits stay possible during a computation (QA-3); Esc cancels it first
        self.act_delete_contact.setEnabled(sel is not None)
        self.act_deselect.setEnabled(sel is not None and not busy)
        nm = len(p.manual_contacts())
        self.act_delete_manual.setEnabled(nm > 0)
        self.act_delete_manual.setText(f"Delete all manual contacts ({nm})" if nm else
                                       "Delete all manual contacts")
        if sel is not None:
            from .theme import REASON_STYLE
            label = REASON_STYLE.get(sel.reason.value, (sel.reason.value,))[0]
            n = len(p.contacts_with_reason(sel.reason))
            self.act_delete_reason.setText(f"Delete all '{label}' contacts ({n})")
            self.act_delete_reason.setEnabled(True)
        else:
            self.act_delete_reason.setText("Delete contacts of this reason")
            self.act_delete_reason.setEnabled(False)
        # QA-3: the model (transform, repair, import) is locked while a task runs; contact
        # and parameter edits stay possible and mark the result out of date when it arrives
        self.left.set_busy(busy)
        self.viewport.edit_enabled = has
        self._refresh_banner()
        self.viewport.set_tip_preview(p.params.tip_length, p.params.tip_base_diameter)
        self.viewport.set_overhang_params(p.params.overhang_angle_deg, p.params.layer_height)
        user = is_user_key(p.profile_key) and p.profile_key in self.presets.presets
        for a in (self.act_preset_rename, self.act_preset_delete, self.act_preset_export):
            a.setEnabled(user)
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
            self._refresh_actions()
            self._refresh_inspect()
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
        self.status_msg.setToolTip(text)
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

    # ------------------------------------------------------------------ presets
    def _ask_text(self, caption: str, label: str, default: str = "") -> str:
        hook = getattr(self, "text_provider", None)       # tests inject answers here
        if hook is not None:
            return hook(caption, label, default) or ""
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            return ""
        text, ok = QtWidgets.QInputDialog.getText(self, caption, label,
                                                  QtWidgets.QLineEdit.Normal, default)
        return text if ok else ""

    def _confirm(self, title: str, text: str) -> bool:
        hook = getattr(self, "confirm_provider", None)
        if hook is not None:
            return bool(hook(title, text))
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            return True
        r = QtWidgets.QMessageBox.question(self, title, text,
                                           QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        return r == QtWidgets.QMessageBox.Yes

    @staticmethod
    def _preset_errors_text(errors) -> str:
        names = "; ".join(msg for _, msg in errors[:3])
        more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        return f"{len(errors)} preset file(s) ignored: {names}{more}"

    def _sync_presets(self) -> None:
        """Profiles of the project = built-in profiles + user presets of the store."""
        self.project.profiles = self.presets.profiles()
        self.left.rebuild_profile_combo()
        self.left.refresh_params()
        self.right.refresh_params()
        self._refresh_actions()

    def current_user_preset(self):
        key = self.project.profile_key
        return self.presets.presets.get(key) if is_user_key(key) else None

    def save_preset(self) -> bool:
        """Save the current parameters (simple and advanced) as a named user preset."""
        p = self.project
        cur = self.current_user_preset()
        name = self._ask_text("Save preset", "Preset name:", cur.label if cur else "").strip()
        if not name:
            return False
        existing = self.presets.find_by_label(name)
        if existing is not None and not self._confirm(
                "Replace preset", f"A preset named '{existing.label}' already exists. "
                "Replace it with the current parameters?"):
            return False
        try:
            preset = self.presets.save(name, p.params, self.presets.base_key(p.profile_key)
                                       or p.profile_key, overwrite=existing is not None)
        except PresetError as exc:
            self.message(f"Preset not saved: {exc}", error=True)
            self._info("Preset not saved", str(exc))
            return False
        self._sync_presets()
        p.apply_profile(preset.key)          # undoable; the values are those just saved
        self._sync_presets()
        base = p.profiles.get(preset.inherits)
        self.message(f"Preset '{preset.label}' saved: {len(preset.values)} value(s) differ from "
                     f"{base.label if base else 'the defaults'} ({preset.path.name} in the presets "
                     "folder)")
        return True

    def rename_preset(self) -> bool:
        cur = self.current_user_preset()
        if cur is None:
            return False
        name = self._ask_text("Rename preset", "New name:", cur.label).strip()
        if not name or name == cur.label:
            return False
        try:
            new = self.presets.rename(cur.key, name)
        except PresetError as exc:
            self.message(f"Preset not renamed: {exc}", error=True)
            self._info("Preset not renamed", str(exc))
            return False
        self.project.profiles = self.presets.profiles()
        if new.key != cur.key:
            self.project.rebind_profile(cur.key, new.key)
        self._sync_presets()
        self.message(f"Preset '{cur.label}' renamed to '{new.label}'")
        return True

    def delete_preset(self) -> bool:
        cur = self.current_user_preset()
        if cur is None:
            return False
        if not self._confirm("Delete preset", f"Delete the preset '{cur.label}'? Its file is "
                             "removed; the current parameter values are kept."):
            return False
        try:
            self.presets.delete(cur.key)
        except PresetError as exc:
            self.message(f"Preset not deleted: {exc}", error=True)
            return False
        base = cur.inherits if cur.inherits in self.presets.builtin else \
            next(iter(self.presets.builtin), "")
        self.project.profiles = self.presets.profiles()
        # values unchanged: they now show as differences from the built-in base profile
        self.project.rebind_profile(cur.key, base)
        self._sync_presets()
        self.message(f"Preset '{cur.label}' deleted; values kept on profile "
                     f"{self.project.profiles[base].label if base else '-'}")
        return True

    def export_preset(self) -> bool:
        cur = self.current_user_preset()
        if cur is None:
            return False
        path = self._ask_path(True, "Export preset", PRESET_FILTER, slugify(cur.label) + ".json")
        if not path:
            return False
        try:
            out = self.presets.export(cur.key, path)
        except PresetError as exc:
            self.message(f"Export failed: {exc}", error=True)
            self._info("Export failed", str(exc))
            return False
        self.message(f"Preset '{cur.label}' exported to {out}")
        return True

    def import_preset(self) -> bool:
        path = self._ask_path(False, "Import preset", PRESET_FILTER)
        if not path:
            return False
        try:
            preset = self.presets.import_file(path)
        except PresetError as exc:
            self.message(f"Import failed: {exc}", error=True)
            self._info("Cannot import preset", str(exc))
            return False
        self._sync_presets()
        self.project.apply_profile(preset.key)       # undoable
        self.message(f"Preset '{preset.label}' imported from {Path(path).name} and selected")
        return True

    def open_preset_folder(self) -> None:
        d = self.presets.directory
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.message(f"Cannot create {d}: {exc}", error=True)
            return
        if not QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(d)))
        self.message(f"Presets folder: {d}")

    # ------------------------------------------------------------------ slicer, preferences
    def _stale_export_warnings(self) -> list[str]:
        p = self.project
        r = p.result
        out = []
        if r is not None and p.contacts_edited:
            out.append("Contacts were edited since the last generation: the file does not "
                       "include these edits (use Rebuild branches, then export again).")
        changed = p.result_param_changes() if r is not None else {}
        if changed:
            out.append("Parameters changed since the supports were generated ("
                       + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items())
                       + "): the file holds the supports as generated, with the "
                       "parameters really used; generate again to apply the new values.")
        return out

    def open_in_orca(self) -> bool:
        """Export model + supports (+ raft) as a 3MF in the to_slicer folder (worker thread),
        then open it in OrcaSlicer (see _orca_exported)."""
        p = self.project
        if not p.has_model or self.runner.is_running():
            return False
        r = p.result
        if r is None:
            self.message("Generate supports first", error=True)
            return False
        warnings = self._stale_export_warnings()
        if warnings:
            self.message(" ".join(warnings), warning=True)
        base = (p.name or "model").rsplit(".", 1)[0]
        params = p.export_params()      # the parameters that built the geometry
        self.last_orca = {"file": None, "exe": None, "opened": False, "error": "",
                          "folder_opened": False}
        return self._start(_task_orca_export,
                           {"model": p.mesh.copy(), "supports": r.support_mesh,
                            "raft": r.raft_mesh, "params": params, "name": base},
                           ("orca", warnings, params), "Exporting for OrcaSlicer")

    def find_orca(self):
        """OrcaSlicer executable: the path of the preferences if valid, else automatic
        detection; None if not found."""
        from arbor.io import slicer_launch as sl
        from .preferences import configured_orca_path
        return sl.find_orcaslicer(configured_orca_path())

    def _orca_exported(self, result, context) -> None:
        path, notes = result
        warnings = context[1] if len(context) > 1 else []
        pp = context[2] if len(context) > 2 else self.project.params
        info = getattr(self, "last_orca", None) or {}
        self.last_orca = info
        info.update(file=str(path), notes=list(notes or []), warnings=list(warnings))
        exe = self.find_orca()
        while True:
            if exe is None:
                choice = self._ask_orca_missing(path, "OrcaSlicer was not found on this "
                                                "computer.")
                exe = self._resolve_orca_choice(choice, path)
                if exe is None:
                    return
            if self._launch_orca(path, exe, pp, warnings, notes):
                return
            # launch failed: same choices (another program, preferences, folder)
            choice = self._ask_orca_missing(path, info.get("error", ""), failed=True)
            exe = self._resolve_orca_choice(choice, path)
            if exe is None:
                return

    def _resolve_orca_choice(self, choice: str, path):
        """Apply the user's choice in the missing-OrcaSlicer dialog; the executable to
        launch, or None (stop)."""
        if choice == "choose":
            exe = self.choose_orca_exe()
            if exe is None:
                self.message(f"Not opened in OrcaSlicer. The file is in {path}", warning=True)
            return exe
        if choice == "preferences":
            self.open_preferences()
            exe = self.find_orca()
            if exe is None:
                self.message(f"OrcaSlicer still not found. The file is in {path}", warning=True)
            return exe
        if choice == "folder":
            self.open_orca_folder(path)
        self.message(f"Not opened in OrcaSlicer. The file is in {path}", warning=True)
        return None

    def choose_orca_exe(self):
        """Ask for the OrcaSlicer program, check it and remember it (settings orca_path)."""
        from arbor.core.settings import SettingsError, update_settings
        from arbor.io import slicer_launch as sl
        from .preferences import orca_file_filter
        exe = self._ask_path(False, "Choose OrcaSlicer (orca-slicer.exe)", orca_file_filter())
        if not exe:
            return None
        why = sl.check_orca_path(exe)
        if why is not None:
            self.message(f"Not an OrcaSlicer program: {why}", error=True)
            self._info("Invalid OrcaSlicer path", f"{exe}\n\n{why}")
            return None
        try:
            update_settings(orca_path=str(exe))
        except SettingsError as exc:
            self.message(f"OrcaSlicer path not saved: {exc}", warning=True)
        dlg = getattr(self, "_preferences_dialog", None)
        if dlg is not None:
            dlg.load()
        return Path(exe)

    def open_orca_folder(self, path) -> None:
        folder = Path(path).parent
        if getattr(self, "last_orca", None) is not None:
            self.last_orca["folder_opened"] = True
        if not QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def orca_missing_box(self, path, reason: str, failed: bool = False) -> QtWidgets.QMessageBox:
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("OrcaSlicer could not be opened" if failed else "OrcaSlicer not found")
        box.setText(("OrcaSlicer could not be started." if failed else
                     "ArborFDM could not find OrcaSlicer.")
                    + "\nThe model and its supports were exported anyway.")
        box.setInformativeText(
            (f"{reason}\n\n" if reason else "")
            + f"File: {path}\n\nChoose the OrcaSlicer program (orca-slicer.exe), set it in "
            "Preferences, or open the file yourself from its folder.")
        box.choose_button = box.addButton("Choose the executable...",
                                          QtWidgets.QMessageBox.AcceptRole)
        box.preferences_button = box.addButton("Preferences...", QtWidgets.QMessageBox.ActionRole)
        box.folder_button = box.addButton("Open folder", QtWidgets.QMessageBox.ActionRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(box.choose_button)
        return box

    def _ask_orca_missing(self, path, reason: str, failed: bool = False) -> str:
        """"choose", "preferences", "folder" or "cancel"."""
        hook = getattr(self, "orca_choice_provider", None)      # tests answer here
        if hook is not None:
            return hook(str(path), reason) or "cancel"
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            self.last_info = ("OrcaSlicer not found", f"{reason}\n{path}")
            return "cancel"
        box = self.orca_missing_box(path, reason, failed)
        box.exec()
        b = box.clickedButton()
        return {box.choose_button: "choose", box.preferences_button: "preferences",
                box.folder_button: "folder"}.get(b, "cancel")

    def _launch_orca(self, path, exe, pp, warnings, notes) -> bool:
        """Start OrcaSlicer on the file; True if started (a reminder is shown)."""
        from arbor.io import slicer_launch as sl
        info = self.last_orca
        info["exe"] = str(exe)
        try:
            sl.launch_orcaslicer(path, exe)
        except (sl.SlicerNotFound, sl.SlicerLaunchError) as exc:
            info["error"] = str(exc)
            self.message(f"OrcaSlicer: {exc}", error=True)
            return False
        info["opened"] = True
        info["error"] = ""
        if warnings:
            self.message(f"Opening {Path(path).name} in OrcaSlicer. WARNING: "
                         + " ".join(warnings), warning=True)
        else:
            self.message(f"Opening {Path(path).name} in OrcaSlicer ({exe})")
        text = ORCA_REMINDER.format(layer=pp.layer_height, walls=pp.walls)
        if warnings:
            text = "WARNING:\n" + "\n".join(f"  - {w}" for w in warnings) + "\n\n" + text
        if notes:
            text += "\n\nNotes:\n" + "\n".join(f"  - {n}" for n in notes)
        self._info("Opening in OrcaSlicer",
                   f"OrcaSlicer is starting with:\n{path}\n\n{text}")
        return True

    def preferences_dialog(self) -> PreferencesDialog:
        """The preferences dialog (created once, kept: its fields keep their state)."""
        dlg = getattr(self, "_preferences_dialog", None)
        if dlg is None:
            dlg = self._preferences_dialog = PreferencesDialog(self)
            dlg.orcaPathChanged.connect(
                lambda path: self.message(f"OrcaSlicer path saved: {path}" if path else
                                          "OrcaSlicer: automatic detection"))
        return dlg

    def open_preferences(self) -> None:
        dlg = self.preferences_dialog()
        if QtCore.QCoreApplication.instance().property("arbor_no_dialogs"):
            return
        dlg.exec()

    # ------------------------------------------------------------------ edit slots
    def undo(self) -> None:
        if not self.runner.is_running():
            self.project.undo()

    def redo(self) -> None:
        if not self.runner.is_running():
            self.project.redo()

    def delete_selected_contact(self) -> bool:
        cid = self.project.selected_contact_id
        if cid is None or not self.project.remove_contact(cid):
            return False
        self.message(f"Contact #{cid} deleted (undoable). Use Rebuild branches to update the "
                     "trees.")
        return True

    def deselect_contact(self) -> None:
        self.project.select_contact(None)

    def delete_manual_contacts(self) -> int:
        n = self.project.remove_manual_contacts()
        if n:
            self.message(f"{n} manual contact(s) deleted (undoable). Use Rebuild branches to "
                         "update the trees.")
        return n

    def delete_selected_reason(self) -> int:
        p = self.project
        sel = p.contact(p.selected_contact_id) if p.selected_contact_id is not None else None
        if sel is None:
            return 0
        from .theme import REASON_STYLE
        label = REASON_STYLE.get(sel.reason.value, (sel.reason.value,))[0]
        n = p.remove_contacts_with_reason(sel.reason)
        if n:
            self.message(f"{n} '{label}' contact(s) deleted (undoable). Use Rebuild branches to "
                         "update the trees.")
        return n

    def inspect_html(self) -> str:
        """Text of the info card over the view: the hovered contact or issue marker, else
        the selected contact (reason, state, position, note)."""
        from .theme import REASON_STYLE
        p, vp = self.project, self.viewport
        cid = vp.hovered_contact_id
        hovered = cid is not None and p.contact(cid) is not None
        if not hovered and vp.hovered_issue is not None and 0 <= vp.hovered_issue < len(vp.issues):
            i = vp.issues[vp.hovered_issue]
            sev = issue_severity(i)
            col = {"error": theme.ERROR, "warning": theme.WARNING}.get(sev, theme.TEXT_DIM)
            code = str(getattr(i, "code", "") or "")
            return (f"<span style='color:{col}'>&#9679;</span>&nbsp;<b style='color:"
                    f"{theme.TEXT_STRONG}'>{_esc(issue_title(code))}</b>"
                    f"&nbsp;<span style='color:{theme.TEXT_DIM}'>{sev} [{_esc(code)}]</span>"
                    f"<br>{_esc(issue_text(i))}")
        if not hovered:
            cid = p.selected_contact_id
        c = p.contact(cid) if cid is not None else None
        if c is None:
            return ""
        label, col = REASON_STYLE.get(c.reason.value, (c.reason.value, theme.TEXT))
        state = []
        if c.manual:
            state.append("manual")
        if not c.enabled:
            state.append("disabled")
        if c.tip_diameter is not None:
            state.append(f"tip {c.tip_diameter:.2f} mm")
        kind = "Hovered" if hovered and cid != p.selected_contact_id else "Selected"
        pos = c.position
        html = (f"<span style='color:{theme.TEXT_DIM}'>{kind}</span>&nbsp;&nbsp;"
                f"<span style='color:{col}'>&#9679;</span>&nbsp;<b style='color:"
                f"{theme.TEXT_STRONG}'>#{c.id} {_esc(label)}</b>"
                + (f"&nbsp;&middot;&nbsp;{', '.join(state)}" if state else "")
                + f"&nbsp;&middot;&nbsp;<span style='color:{theme.TEXT_DIM}'>z {pos[2]:.2f} mm"
                  "</span>")
        if c.note:
            html += f"<br><span style='color:{theme.TEXT}'>{_esc(c.note)}</span>"
        if kind == "Selected":
            html += (f"<br><span style='color:{theme.TEXT_DIM}'>Del: delete&nbsp;&nbsp;&middot;"
                     "&nbsp;&nbsp;Esc: deselect&nbsp;&nbsp;&middot;&nbsp;&nbsp;Right click: "
                     "delete</span>")
        return html

    def _refresh_inspect(self) -> None:
        self.view.set_info(self.inspect_html() if self.project.has_model else "")

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

    def export_3mf_separate(self) -> None:
        self._export("3mf_separate", "Export 3MF (separate objects)", "3MF (*.3mf)",
                     "_separate.3mf")

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
        warnings = self._stale_export_warnings()
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
            self._busy_progress = (0.0, "")
            self._busy_timer.start()
        else:
            self._busy_timer.stop()
        self._refresh_busy_card()
        self._refresh_actions()

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress_bar.setValue(int(fraction * 1000))
        self._busy_progress = (float(fraction), message or self._busy_progress[1])
        if message:
            self.status_msg.setText(f"{self.runner.label}: {message}")
        self._refresh_busy_card()

    def _refresh_busy_card(self) -> None:
        """Progress card over the 3D view: task, step, percentage, elapsed time, Cancel."""
        if not self.runner.is_running():
            self.view.set_busy("", 0.0, "", 0.0)
            return
        started = getattr(self.runner, "started_at", 0.0) or time.perf_counter()
        fraction, step = self._busy_progress
        self.view.set_busy(self.runner.label or "Computing", fraction, step,
                           max(0.0, time.perf_counter() - started))

    def _on_task_failed(self, message: str, tb: str, context) -> None:
        self.last_error = tb
        kind = context[0] if context else ""
        self._generate_after_repair = None
        self.message(f"{self.runner.label or kind} failed: {message}", error=True)
        if kind in ("load", "open_project"):
            self._info("Cannot open file", message)
        elif kind == "orca":
            if getattr(self, "last_orca", None) is not None:
                self.last_orca["error"] = message
            self._info("Open in OrcaSlicer", f"The file for OrcaSlicer could not be written:\n"
                       f"{message}\n\nNothing was opened; the model and the project are "
                       "unchanged.")

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
        elif kind == "orca":
            self._orca_exported(result, context)
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

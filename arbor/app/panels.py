"""Side panels. They only read the Project and call its methods (or emit requests that the
main window turns into background tasks). No geometry is computed here."""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Callable, Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from arbor.core.project import Project
from arbor.engine.params import SupportParams

from . import theme

# name: (group, label, min, max, step, decimals, unit, tooltip)
PARAM_META: dict[str, tuple] = {
    "nozzle_diameter": ("Printer", "Nozzle diameter", 0.1, 1.2, 0.05, 2, "mm", "Printer nozzle"),
    "line_width": ("Printer", "Line width", 0.1, 1.5, 0.01, 2, "mm", "Extrusion width"),
    "layer_height": ("Printer", "Layer height", 0.02, 0.8, 0.01, 3, "mm",
                     "Layer height used for the layer by layer analysis"),
    "walls": ("Printer", "Walls (advice)", 1, 10, 1, 0, "", "Perimeters advised in the slicer"),
    "overhang_angle_deg": ("Detection", "Overhang angle", 10, 89, 1, 1, "deg",
                           "From vertical: faces tilted more than this need support"),
    "min_region_area": ("Detection", "Min region area", 0.0, 50, 0.05, 2, "mm2",
                        "Smaller unsupported areas are ignored"),
    "max_unsupported_overhang": ("Detection", "Max overhang step", 0.0, 10, 0.05, 2, "mm",
                                 "Max horizontal step between layers (0 = from angle)"),
    "contact_spacing": ("Contacts", "Surface spacing", 0.2, 30, 0.1, 2, "mm",
                        "Max spacing of contacts on flat overhangs"),
    "edge_spacing": ("Contacts", "Edge spacing", 0.2, 30, 0.1, 2, "mm",
                     "Spacing of contacts along hanging edges"),
    "min_contact_distance": ("Contacts", "Merge distance", 0.05, 10, 0.05, 2, "mm",
                             "Contacts closer than this are merged"),
    "contact_density": ("Contacts", "Density", 0.1, 5, 0.05, 2, "x",
                        "Multiplier: more than 1 means more contacts"),
    "manual_only": ("Contacts", "Manual contacts only", None, None, None, None, "",
                    "Ignore automatic placement"),
    "tip_diameter": ("Tips", "Tip diameter", 0.05, 3, 0.01, 3, "mm",
                     "Diameter where the tip touches the model"),
    "tip_length": ("Tips", "Tip length", 0.2, 10, 0.1, 2, "mm", "Length of the conical tip"),
    "tip_base_diameter": ("Tips", "Tip base diameter", 0.1, 5, 0.05, 2, "mm",
                          "Diameter where the tip meets its branch"),
    "tip_penetration": ("Tips", "Penetration", 0.0, 1, 0.01, 3, "mm",
                        "Controlled penetration into the model"),
    "tip_shape": ("Tips", "Tip shape", None, None, None, None, "", "cone or ball"),
    "z_gap": ("Tips", "Z gap", 0.0, 2, 0.01, 3, "mm", "Extra vertical clearance"),
    "xy_clearance": ("Tips", "XY clearance", 0.0, 10, 0.05, 2, "mm",
                     "Min horizontal clearance of branches from the model"),
    "branch_min_diameter": ("Branches / trunks", "Branch min diameter", 0.2, 10, 0.05, 2, "mm", ""),
    "branch_max_diameter": ("Branches / trunks", "Branch max diameter", 0.2, 10, 0.05, 2, "mm", ""),
    "branch_angle_deg": ("Branches / trunks", "Branch angle", 0, 60, 1, 1, "deg",
                         "Max tilt of a branch from vertical"),
    "segment_length": ("Branches / trunks", "Segment length", 0.1, 10, 0.1, 2, "mm", ""),
    "merge_distance": ("Branches / trunks", "Merge distance", 0, 50, 0.5, 2, "mm",
                       "Max horizontal distance for two branches to merge"),
    "junction_radius_factor": ("Branches / trunks", "Junction factor", 1.0, 3, 0.05, 2, "x", ""),
    "trunk_diameter": ("Branches / trunks", "Trunk diameter", 0.5, 15, 0.1, 2, "mm", ""),
    "trunk_growth_per_mm": ("Branches / trunks", "Trunk growth", 0, 0.5, 0.005, 3, "mm/mm",
                            "Diameter growth per mm descended"),
    "min_tree_distance": ("Branches / trunks", "Min tree distance", 0, 20, 0.1, 2, "mm", ""),
    "foot_diameter": ("Feet", "Foot diameter", 0.5, 30, 0.5, 2, "mm", ""),
    "foot_height": ("Feet", "Foot height", 0.0, 5, 0.1, 2, "mm", ""),
    "collar_diameter": ("Feet", "Collar diameter", 0.5, 30, 0.5, 2, "mm", ""),
    "collar_height": ("Feet", "Collar height", 0.0, 10, 0.1, 2, "mm", ""),
    "braces_enabled": ("Braces", "Braces", None, None, None, None, "", "Cross braces between trunks"),
    "brace_diameter": ("Braces", "Brace diameter", 0.2, 5, 0.05, 2, "mm", ""),
    "brace_min_height": ("Braces", "Min trunk height", 0, 200, 1, 1, "mm", ""),
    "brace_spacing_z": ("Braces", "Vertical spacing", 1, 100, 0.5, 1, "mm", ""),
    "brace_max_length": ("Braces", "Max length", 1, 100, 0.5, 1, "mm", ""),
    "raft_enabled": ("Raft", "Raft", None, None, None, None, "", "Print a raft under the feet"),
    "raft_thickness": ("Raft", "Raft thickness", 0.1, 5, 0.1, 2, "mm", ""),
    "raft_margin": ("Raft", "Raft margin", 0, 20, 0.5, 2, "mm", ""),
    "allow_on_model": ("Model supports", "Allow supports on model", None, None, None, None, "",
                       "Supports may stand on the model when the plate is not reachable"),
    "on_model_tip_diameter": ("Model supports", "Foot tip diameter", 0.05, 3, 0.01, 3, "mm", ""),
    "circle_segments": ("Meshing", "Circle segments", 6, 64, 1, 0, "", "Smoothness of cylinders"),
}
GROUP_ORDER = ["Printer", "Detection", "Contacts", "Tips", "Branches / trunks", "Feet", "Braces",
               "Raft", "Model supports", "Meshing"]


def _set_changed(w: QtWidgets.QWidget, changed: bool) -> None:
    if w.property("changed") != changed:
        w.setProperty("changed", changed)
        w.style().unpolish(w)
        w.style().polish(w)


class ParamField(QtCore.QObject):
    """Editor widget for one SupportParams field."""
    edited = QtCore.Signal(str, object)

    def __init__(self, name: str, default: Any, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.name = name
        meta = PARAM_META.get(name, ("Other", name, 0, 1000, 0.1, 3, "", ""))
        _, self.label, lo, hi, step, dec, unit, tip = meta
        if isinstance(default, bool):
            w = QtWidgets.QCheckBox(parent)
            w.toggled.connect(lambda v: self.edited.emit(self.name, bool(v)))
        elif isinstance(default, str):
            w = QtWidgets.QComboBox(parent)
            w.addItems(["cone", "ball"])
            w.currentTextChanged.connect(lambda v: self.edited.emit(self.name, v))
        elif isinstance(default, int):
            w = QtWidgets.QSpinBox(parent)
            w.setRange(int(lo), int(hi))
            w.setKeyboardTracking(False)
            w.valueChanged.connect(lambda v: self.edited.emit(self.name, int(v)))
        else:
            w = QtWidgets.QDoubleSpinBox(parent)
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setSingleStep(step)
            w.setKeyboardTracking(False)
            if unit:
                w.setSuffix(f" {unit}")
            w.valueChanged.connect(lambda v: self.edited.emit(self.name, float(v)))
        if isinstance(w, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
            w.setFocusPolicy(QtCore.Qt.StrongFocus)
            w.installEventFilter(self)
        w.setToolTip(f"{tip}\n[{name}]" if tip else f"[{name}]")
        self.widget = w

    def eventFilter(self, obj, ev) -> bool:
        # wheel over a spin box in a scroll area should scroll the panel, not edit the value
        if ev.type() == QtCore.QEvent.Wheel and not obj.hasFocus():
            ev.ignore()
            return True
        return False

    def set_value(self, value: Any, changed: bool, profile_value: Any = None) -> None:
        w = self.widget
        w.blockSignals(True)
        if isinstance(w, QtWidgets.QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QtWidgets.QComboBox):
            w.setCurrentText(str(value))
        else:
            w.setValue(value)
        w.blockSignals(False)
        _set_changed(w, changed)
        base = w.toolTip().split("\n(profile")[0]
        w.setToolTip(base + (f"\n(profile: {profile_value})" if changed else ""))


def _group(title: str) -> tuple[QtWidgets.QGroupBox, QtWidgets.QVBoxLayout]:
    g = QtWidgets.QGroupBox(title)
    lay = QtWidgets.QVBoxLayout(g)
    lay.setContentsMargins(8, 10, 8, 8)
    lay.setSpacing(6)
    return g, lay


def _form() -> QtWidgets.QFormLayout:
    f = QtWidgets.QFormLayout()
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(8)
    f.setVerticalSpacing(5)
    f.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
    return f


def _scroll(inner: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    sa.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    # never narrower than the content: nothing is clipped horizontally
    sa.setMinimumWidth(inner.minimumSizeHint().width() + sa.verticalScrollBar().sizeHint().width()
                       + 4)
    return sa


def ordered_profiles(project: Project) -> list[str]:
    order = {"light": 0, "normal": 1, "dense": 2}

    def key(k: str):
        p = project.profiles[k]
        tail = k.rsplit("_", 1)[-1]
        return (p.params.nozzle_diameter, order.get(tail, 9), k)
    return sorted(project.profiles, key=key)


# ---------------------------------------------------------------------------
# Left panel
# ---------------------------------------------------------------------------
class LeftPanel(QtWidgets.QWidget):
    importRequested = QtCore.Signal()
    checkRequested = QtCore.Signal()
    repairRequested = QtCore.Signal()
    orientRequested = QtCore.Signal()

    SIMPLE_FIELDS = ("overhang_angle_deg", "contact_density", "trunk_diameter", "raft_enabled",
                     "braces_enabled", "allow_on_model", "manual_only")
    SIMPLE_LABELS = {"overhang_angle_deg": "Overhang angle", "contact_density": "Density",
                     "trunk_diameter": "Trunk thickness", "raft_enabled": "Raft",
                     "braces_enabled": "Braces", "allow_on_model": "Supports on model",
                     "manual_only": "Manual contacts only"}

    def __init__(self, project: Project, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.project = project
        self.candidates: list = []
        inner = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(inner)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(4)

        # --- model -------------------------------------------------------------
        g, lay = _group("Model")
        self.import_button = QtWidgets.QPushButton("Import model...")
        self.import_button.setIcon(theme.std_icon(self, "SP_DialogOpenButton"))
        self.import_button.clicked.connect(self.importRequested)
        lay.addWidget(self.import_button)
        self.object_list = QtWidgets.QListWidget()
        self.object_list.setMaximumHeight(34)
        lay.addWidget(self.object_list)
        f = _form()
        self.dim_label = QtWidgets.QLabel("-", objectName="value")
        self.dim_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        f.addRow(QtWidgets.QLabel("Size (mm)", objectName="dim"), self.dim_label)
        self.tri_label = QtWidgets.QLabel("-", objectName="dim")
        f.addRow(QtWidgets.QLabel("Triangles", objectName="dim"), self.tri_label)
        lay.addLayout(f)
        self.load_warning = QtWidgets.QLabel("")
        self.load_warning.setWordWrap(True)
        self.load_warning.setStyleSheet(f"color: {theme.WARNING};")
        self.load_warning.hide()
        lay.addWidget(self.load_warning)
        root.addWidget(g)

        # --- transform -----------------------------------------------------------
        g, lay = _group("Orientation and size")
        self.model_group = g
        f = _form()
        rot_row = QtWidgets.QHBoxLayout()
        rot_row.setSpacing(4)
        self.rot_spins = []
        for axis in "XYZ":
            s = QtWidgets.QDoubleSpinBox()
            s.setRange(-360, 360)
            s.setDecimals(1)
            s.setSingleStep(15)
            s.setWrapping(True)
            s.setKeyboardTracking(False)
            s.setPrefix(f"{axis} ")
            s.setMinimumWidth(62)
            s.setToolTip(f"Rotation about {axis} (degrees)")
            s.valueChanged.connect(self._on_rotation)
            self.rot_spins.append(s)
            rot_row.addWidget(s)
        f.addRow(QtWidgets.QLabel("Rotation", objectName="dim"), rot_row)
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setRange(1, 10000)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setKeyboardTracking(False)
        self.scale_spin.valueChanged.connect(lambda v: self.project.set_scale(v / 100.0))
        f.addRow(QtWidgets.QLabel("Scale", objectName="dim"), self.scale_spin)
        self.height_spin = QtWidgets.QDoubleSpinBox()
        self.height_spin.setRange(0.1, 2000)
        self.height_spin.setDecimals(2)
        self.height_spin.setSuffix(" mm")
        self.height_spin.setKeyboardTracking(False)
        self.height_spin.setToolTip("Target height (Z) of the model: sets a uniform scale")
        self.height_spin.valueChanged.connect(self.project.set_scale_for_height)
        f.addRow(QtWidgets.QLabel("Height", objectName="dim"), self.height_spin)
        lay.addLayout(f)
        row = QtWidgets.QHBoxLayout()
        self.plate_button = QtWidgets.QPushButton("Place on plate")
        self.plate_button.setToolTip("Center on the plate, lowest point at z = 0")
        self.plate_button.clicked.connect(self.project.place_on_plate)
        self.orient_button = QtWidgets.QPushButton("Suggest orientation")
        self.orient_button.clicked.connect(self.orientRequested)
        row.addWidget(self.plate_button)
        row.addWidget(self.orient_button)
        lay.addLayout(row)
        self.orient_list = QtWidgets.QListWidget()
        self.orient_list.setToolTip("Click a candidate to apply it (undoable)")
        self.orient_list.setMaximumHeight(120)
        self.orient_list.itemClicked.connect(self._on_candidate)
        self.orient_list.hide()
        lay.addWidget(self.orient_list)
        root.addWidget(g)

        # --- mesh check ------------------------------------------------------------
        g, lay = _group("Mesh check")
        self.mesh_group = g
        row = QtWidgets.QHBoxLayout()
        self.check_button = QtWidgets.QPushButton("Check")
        self.check_button.clicked.connect(self.checkRequested)
        self.repair_button = QtWidgets.QPushButton("Repair")
        self.repair_button.setToolTip("Repair a copy of the mesh (the original is kept, undoable)")
        self.repair_button.clicked.connect(self.repairRequested)
        row.addWidget(self.check_button)
        row.addWidget(self.repair_button)
        lay.addLayout(row)
        self.mesh_report = QtWidgets.QLabel("Not checked")
        self.mesh_report.setObjectName("dim")
        self.mesh_report.setWordWrap(True)
        self.mesh_report.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.mesh_report)
        root.addWidget(g)

        # --- simple parameters ---------------------------------------------------------
        g, lay = _group("Supports")
        f = _form()
        self.profile_combo = QtWidgets.QComboBox()
        for key in ordered_profiles(project):
            self.profile_combo.addItem(project.profiles[key].label, key)
            self.profile_combo.setItemData(self.profile_combo.count() - 1,
                                           project.profiles[key].description,
                                           QtCore.Qt.ToolTipRole)
        self.profile_combo.activated.connect(self._on_profile)
        f.addRow(QtWidgets.QLabel("Profile", objectName="dim"), self.profile_combo)
        self.fields: dict[str, ParamField] = {}
        defaults = SupportParams().to_dict()
        checks = QtWidgets.QGridLayout()
        checks.setHorizontalSpacing(6)
        n_checks = 0
        for name in self.SIMPLE_FIELDS:
            pf = ParamField(name, defaults[name], self)
            pf.edited.connect(self.project.set_param)
            self.fields[name] = pf
            if isinstance(pf.widget, QtWidgets.QCheckBox):
                pf.widget.setText(self.SIMPLE_LABELS[name])
                checks.addWidget(pf.widget, n_checks // 2, n_checks % 2)
                n_checks += 1
            else:
                f.addRow(QtWidgets.QLabel(self.SIMPLE_LABELS[name], objectName="dim"), pf.widget)
        lay.addLayout(f)
        lay.addLayout(checks)
        self.changed_label = QtWidgets.QLabel("")
        self.changed_label.setStyleSheet(f"color: {theme.AMBER};")
        self.changed_label.setWordWrap(True)
        lay.addWidget(self.changed_label)
        root.addWidget(g)
        root.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(inner))
        self.refresh_all()

    # -- refresh ----------------------------------------------------------------------
    def refresh_all(self) -> None:
        self.refresh_model()
        self.refresh_transform()
        self.refresh_params()

    def refresh_model(self) -> None:
        p = self.project
        has = p.has_model
        self.object_list.clear()
        if has:
            label = p.name + ("  (repaired copy)" if p.base_is_repaired else "")
            item = QtWidgets.QListWidgetItem(label)
            item.setToolTip(p.source_path or "")
            self.object_list.addItem(item)
            d = p.dimensions()
            self.dim_label.setText(f"{d[0]:.2f}  x  {d[1]:.2f}  x  {d[2]:.2f}")
            self.tri_label.setText(f"{len(p.mesh.faces):,}".replace(",", " "))
        else:
            self.dim_label.setText("-")
            self.tri_label.setText("-")
            self.mesh_report.setText("Not checked")
            self.orient_list.clear()
            self.orient_list.hide()
        if p.load_warnings:
            self.load_warning.setText("\n".join(p.load_warnings))
            self.load_warning.show()
        else:
            self.load_warning.hide()
        self._apply_enabled()

    def model_widgets(self) -> list[QtWidgets.QWidget]:
        """Controls that change the model geometry (locked while a task runs: QA-3)."""
        return [self.plate_button, self.orient_button, self.check_button, self.repair_button,
                self.scale_spin, self.height_spin, *self.rot_spins, self.orient_list]

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        has = self.project.has_model
        busy = getattr(self, "_busy", False)
        for w in self.model_widgets():
            w.setEnabled(has and not busy)
        self.import_button.setEnabled(not busy)
        tip = ("Locked while a computation runs (the model must not change under it)"
               if busy else "")
        self.model_group.setToolTip(tip)
        self.model_group.setTitle("Orientation and size" + ("  (locked: computing)" if busy else ""))

    def refresh_transform(self) -> None:
        p = self.project
        t = p.transform
        for s, v in zip(self.rot_spins, t.rotation_deg):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)
        for s, v in ((self.scale_spin, float(np.mean(t.scale)) * 100.0),
                     (self.height_spin, float(p.dimensions()[2]) if p.has_model else 0.1)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        if p.has_model:
            d = p.dimensions()
            self.dim_label.setText(f"{d[0]:.2f}  x  {d[1]:.2f}  x  {d[2]:.2f}")

    def refresh_params(self) -> None:
        p = self.project
        i = self.profile_combo.findData(p.profile_key)
        self.profile_combo.blockSignals(True)
        self.profile_combo.setCurrentIndex(i)
        self.profile_combo.blockSignals(False)
        changed = p.changed_from_profile()
        base = p.profile_params()
        for name, pf in self.fields.items():
            pf.set_value(getattr(p.params, name), name in changed, getattr(base, name))
        if changed:
            self.changed_label.setText(f"{len(changed)} value(s) differ from the profile "
                                       "(amber). See Advanced to reset.")
        else:
            self.changed_label.setText("")

    def show_mesh_report(self, text: str, ok: bool) -> None:
        self.mesh_report.setText(text)
        self.mesh_report.setStyleSheet(f"color: {theme.OK if ok else theme.WARNING};")

    def show_candidates(self, candidates: list) -> None:
        self.candidates = list(candidates)
        self.orient_list.clear()
        for i, c in enumerate(self.candidates):
            crit = getattr(c, "criteria", {}) or {}
            rx, ry, rz = c.rotation_deg
            parts = [f"#{i + 1}  rot {rx:.0f}/{ry:.0f}/{rz:.0f}"]
            if "overhang_area" in crit:
                parts.append(f"overhang {crit['overhang_area']:.0f} mm2")
            if "height" in crit:
                parts.append(f"h {crit['height']:.1f}")
            item = QtWidgets.QListWidgetItem("   ".join(parts))
            tip = c.describe() if hasattr(c, "describe") else str(crit)
            item.setToolTip(tip)
            self.orient_list.addItem(item)
        self.orient_list.setVisible(bool(self.candidates))

    # -- slots ----------------------------------------------------------------------------
    def _on_rotation(self, _v=None) -> None:
        self.project.set_rotation(tuple(s.value() for s in self.rot_spins))

    def _on_profile(self, index: int) -> None:
        key = self.profile_combo.itemData(index)
        if key and key != self.project.profile_key:
            self.project.apply_profile(key)

    def _on_candidate(self, item: QtWidgets.QListWidgetItem) -> None:
        i = self.orient_list.row(item)
        if 0 <= i < len(self.candidates):
            self.project.apply_orientation(self.candidates[i].rotation_deg,
                                           text=f"Orientation #{i + 1}")


# ---------------------------------------------------------------------------
# Right panel
# ---------------------------------------------------------------------------
class ContactEditor(QtWidgets.QGroupBox):
    deleteRequested = QtCore.Signal(int)

    def __init__(self, project: Project, parent=None):
        super().__init__("Selected contact", parent)
        self.project = project
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 8)
        self.empty = QtWidgets.QLabel("Click a contact in the 3D view to edit it.",
                                      objectName="dim")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        self.body = QtWidgets.QWidget()
        f = _form()
        self.body.setLayout(f)
        self.id_label = QtWidgets.QLabel("", objectName="value")
        self.reason_label = QtWidgets.QLabel("")
        self.pos_label = QtWidgets.QLabel("", objectName="dim")
        self.note_label = QtWidgets.QLabel("", objectName="dim")
        self.note_label.setWordWrap(True)
        self.enabled_check = QtWidgets.QCheckBox("Enabled")
        self.enabled_check.toggled.connect(self._on_enabled)
        self.override_check = QtWidgets.QCheckBox("Override tip diameter")
        self.override_check.toggled.connect(self._on_override)
        self.tip_spin = QtWidgets.QDoubleSpinBox()
        self.tip_spin.setRange(0.05, 3.0)
        self.tip_spin.setDecimals(3)
        self.tip_spin.setSingleStep(0.02)
        self.tip_spin.setSuffix(" mm")
        self.tip_spin.setKeyboardTracking(False)
        self.tip_spin.valueChanged.connect(self._on_tip)
        self.delete_button = QtWidgets.QPushButton("Delete contact")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self._on_delete)
        f.addRow(QtWidgets.QLabel("Contact", objectName="dim"), self.id_label)
        f.addRow(QtWidgets.QLabel("Reason", objectName="dim"), self.reason_label)
        f.addRow(QtWidgets.QLabel("Position", objectName="dim"), self.pos_label)
        f.addRow(self.note_label)
        f.addRow(self.enabled_check)
        f.addRow(self.override_check)
        f.addRow(QtWidgets.QLabel("Tip diameter", objectName="dim"), self.tip_spin)
        f.addRow(self.delete_button)
        lay.addWidget(self.body)
        self.refresh()

    def contact(self):
        cid = self.project.selected_contact_id
        return self.project.contact(cid) if cid is not None else None

    def refresh(self) -> None:
        c = self.contact()
        self.empty.setVisible(c is None)
        self.body.setVisible(c is not None)
        if c is None:
            return
        label, col = theme.REASON_STYLE.get(c.reason.value, (c.reason.value, "#fff"))
        self.id_label.setText(f"#{c.id}" + ("  (manual)" if c.manual else ""))
        self.reason_label.setText(f"<span style='color:{col}'>&#9679;</span> {label}")
        p = c.position
        self.pos_label.setText(f"{p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f} mm")
        self.note_label.setText(c.note or "")
        self.note_label.setVisible(bool(c.note))
        for w, v in ((self.enabled_check, c.enabled),
                     (self.override_check, c.tip_diameter is not None)):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        self.tip_spin.blockSignals(True)
        self.tip_spin.setValue(c.tip_diameter if c.tip_diameter is not None
                               else self.project.params.tip_diameter)
        self.tip_spin.blockSignals(False)
        self.tip_spin.setEnabled(c.tip_diameter is not None)

    def _on_enabled(self, v: bool) -> None:
        c = self.contact()
        if c is not None:
            self.project.edit_contact(c.id, enabled=bool(v))

    def _on_override(self, v: bool) -> None:
        c = self.contact()
        if c is not None:
            self.project.edit_contact(c.id, tip_diameter=float(self.tip_spin.value()) if v else None)

    def _on_tip(self, v: float) -> None:
        c = self.contact()
        if c is not None and c.tip_diameter is not None:
            self.project.edit_contact(c.id, tip_diameter=float(v))

    def _on_delete(self) -> None:
        c = self.contact()
        if c is not None:
            self.deleteRequested.emit(c.id)


class RightPanel(QtWidgets.QWidget):
    issueActivated = QtCore.Signal(object)       # Issue

    def __init__(self, project: Project, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.project = project
        self.tabs = QtWidgets.QTabWidget()

        # --- advanced parameters -------------------------------------------------------
        inner = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(inner)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(4)
        top = QtWidgets.QHBoxLayout()
        self.profile_label = QtWidgets.QLabel("", objectName="dim")
        self.reset_all_button = QtWidgets.QPushButton("Reset all")
        self.reset_all_button.setToolTip("Reset every value to the profile")
        self.reset_all_button.clicked.connect(self._reset_all)
        top.addWidget(self.profile_label, 1)
        top.addWidget(self.reset_all_button)
        root.addLayout(top)
        self.fields: dict[str, ParamField] = {}
        self.reset_buttons: dict[str, QtWidgets.QPushButton] = {}
        defaults = SupportParams().to_dict()
        groups: dict[str, QtWidgets.QFormLayout] = {}
        for gname in GROUP_ORDER:
            g, lay = _group(gname)
            f = _form()
            lay.addLayout(f)
            groups[gname] = f
            root.addWidget(g)
        for fld in fields(SupportParams):
            name = fld.name
            gname = PARAM_META.get(name, ("Meshing",))[0]
            pf = ParamField(name, defaults[name], self)
            pf.edited.connect(self.project.set_param)
            self.fields[name] = pf
            reset = QtWidgets.QPushButton("reset", objectName="small")
            reset.setToolTip("Back to the profile value")
            reset.setFixedWidth(48)
            reset.clicked.connect(lambda _=False, n=name: self.project.reset_param(n))
            self.reset_buttons[name] = reset
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(pf.widget, 1)
            row.addWidget(reset)
            lab = QtWidgets.QLabel(pf.label)
            lab.setToolTip(pf.widget.toolTip())
            groups[gname].addRow(lab, row)
        root.addStretch(1)
        self.tabs.addTab(_scroll(inner), "Advanced")

        # --- contacts & issues ------------------------------------------------------------
        inner2 = QtWidgets.QWidget()
        r2 = QtWidgets.QVBoxLayout(inner2)
        r2.setContentsMargins(8, 4, 8, 8)
        self.contact_editor = ContactEditor(project)
        r2.addWidget(self.contact_editor)
        g, lay = _group("Issues")
        self.issue_summary = QtWidgets.QLabel("No generation yet", objectName="dim")
        lay.addWidget(self.issue_summary)
        self.issue_list = QtWidgets.QListWidget()
        self.issue_list.setWordWrap(True)
        self.issue_list.setToolTip("Click an issue to show it in the 3D view")
        self.issue_list.itemClicked.connect(self._on_issue)
        self.issue_list.setMinimumHeight(160)
        lay.addWidget(self.issue_list, 1)
        r2.addWidget(g, 1)
        self.tabs.addTab(inner2, "Contacts and issues")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.addWidget(self.tabs)
        self._issues: list = []
        self.refresh_params()

    def refresh_params(self) -> None:
        p = self.project
        changed = p.changed_from_profile()
        base = p.profile_params()
        prof = p.profiles.get(p.profile_key)
        self.profile_label.setText(f"Profile: {prof.label if prof else '-'}"
                                   + (f"   <span style='color:{theme.AMBER}'>"
                                      f"{len(changed)} changed</span>" if changed else ""))
        self.reset_all_button.setEnabled(bool(changed))
        for name, pf in self.fields.items():
            pf.set_value(getattr(p.params, name), name in changed, getattr(base, name))
            self.reset_buttons[name].setEnabled(name in changed)

    def _reset_all(self) -> None:
        self.project.set_params(self.project.profile_params(), text="Reset parameters")

    def set_issues(self, issues: list, has_result: bool) -> None:
        self._issues = list(issues)
        self.issue_list.clear()
        sev_order = {"error": 0, "warning": 1, "info": 2}
        icons = {"error": theme.ERROR, "warning": theme.WARNING, "info": theme.TEXT_DIM}
        # indexes come from enumerate: Issue holds numpy arrays, list.index / == on it is
        # ambiguous and two identical messages are legitimate (QA-7)
        order = sorted(range(len(self._issues)),
                       key=lambda k: (sev_order.get(self._issues[k].severity, 3), k))
        for idx in order:
            i = self._issues[idx]
            item = QtWidgets.QListWidgetItem(f"{i.severity.upper()}  {i.message}")
            item.setForeground(QtGui.QBrush(QtGui.QColor(icons.get(i.severity, theme.TEXT))))
            item.setData(QtCore.Qt.UserRole, idx)
            if i.position is None and i.contact_id is None:
                item.setToolTip("No position attached")
            self.issue_list.addItem(item)
        if not has_result:
            self.issue_summary.setText("No generation yet")
        else:
            ne = sum(1 for i in issues if i.severity == "error")
            nw = sum(1 for i in issues if i.severity == "warning")
            self.issue_summary.setText(f"{ne} error(s), {nw} warning(s)" if issues
                                       else "No issue found")

    def _on_issue(self, item: QtWidgets.QListWidgetItem) -> None:
        idx = item.data(QtCore.Qt.UserRole)
        if idx is not None and 0 <= idx < len(self._issues):
            self.issueActivated.emit(self._issues[idx])

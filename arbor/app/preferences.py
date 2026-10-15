"""Application preferences dialog (File > Preferences...).

Sections are QGroupBox cards with a form layout; other parts of the application add their
rows to them. The "Slicers" section holds the OrcaSlicer path (saved in the user settings,
arbor.core.settings, key ``orca_path``; empty = automatic detection).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets

from arbor.core import settings as user_settings
from arbor.io import slicer_launch as sl

from . import icons, theme

ORCA_FILTER_WIN = "OrcaSlicer (orca-slicer.exe);;Programs (*.exe);;All files (*)"
ORCA_FILTER = "All files (*)"


def orca_file_filter() -> str:
    return ORCA_FILTER_WIN if sys.platform.startswith("win") else ORCA_FILTER


def configured_orca_path() -> Optional[str]:
    """orca_path of the user settings (None = automatic detection). Never raises."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", user_settings.SettingsWarning)
        return user_settings.load_settings().get("orca_path")


class PreferencesDialog(QtWidgets.QDialog):
    orcaPathChanged = QtCore.Signal(object)        # new saved path (str) or None

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(theme.SP_L, theme.SP_S, theme.SP_L, theme.SP_L)
        lay.setSpacing(theme.SP_M)
        self.sections: dict[str, QtWidgets.QFormLayout] = {}

        # --- slicers ---------------------------------------------------------------
        self.slicers_group, self.slicers_form = self.add_section("Slicers")
        self.slicers_hint = QtWidgets.QLabel(
            "Program used by Open in OrcaSlicer. Leave empty to detect it automatically.",
            objectName="dim")
        self.slicers_hint.setWordWrap(True)
        self.slicers_form.addRow(self.slicers_hint)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.SP_S)
        self.orca_edit = QtWidgets.QLineEdit()
        self.orca_edit.setPlaceholderText("Automatic detection")
        self.orca_edit.setClearButtonEnabled(True)
        self.orca_edit.setToolTip("Path of orca-slicer.exe (Windows), OrcaSlicer.app (macOS) or "
                                  "the orca-slicer program / AppImage (Linux)")
        self.orca_edit.editingFinished.connect(lambda: self.set_orca_path(self.orca_edit.text()))
        self.orca_browse = QtWidgets.QPushButton("Browse...")
        self.orca_browse.setIcon(icons.open_())
        self.orca_browse.setToolTip("Choose the OrcaSlicer program")
        self.orca_browse.clicked.connect(self.browse_orca)
        row.addWidget(self.orca_edit, 1)
        row.addWidget(self.orca_browse)
        self.slicers_form.addRow(QtWidgets.QLabel("OrcaSlicer", objectName="dim"), row)
        self.orca_status = QtWidgets.QLabel("")
        self.orca_status.setWordWrap(True)
        self.orca_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.slicers_form.addRow("", self.orca_status)
        self.orca_valid: Optional[bool] = None

        lay.addStretch(1)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)
        lay.addWidget(self.buttons)
        self.setMinimumWidth(520)
        self.load()

    def add_section(self, title: str) -> tuple[QtWidgets.QGroupBox, QtWidgets.QFormLayout]:
        """A titled card with a form layout, inserted before the dialog buttons."""
        g = QtWidgets.QGroupBox(title)
        f = QtWidgets.QFormLayout(g)
        f.setContentsMargins(theme.SP_M, theme.SP_M + 2, theme.SP_M, theme.SP_M)
        f.setHorizontalSpacing(theme.SP_M)
        f.setVerticalSpacing(theme.SP_S + 2)
        f.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        lay = self.layout()
        # before the stretch and the buttons (when they exist)
        idx = lay.count() - 2 if lay.count() >= 2 else lay.count()
        lay.insertWidget(idx, g)
        self.sections[title] = f
        return g, f

    # -- OrcaSlicer path ------------------------------------------------------------
    def load(self) -> None:
        """Show the saved path (or the automatic detection) and its state."""
        path = configured_orca_path()
        self.orca_edit.blockSignals(True)
        self.orca_edit.setText(path or "")
        self.orca_edit.blockSignals(False)
        self.refresh_orca_status()

    def showEvent(self, ev) -> None:
        self.load()          # the path may have been set elsewhere (missing-Orca dialog)
        super().showEvent(ev)

    def _status(self, text: str, ok: Optional[bool], soft: bool = False) -> None:
        """ok True: green check; False: red cross (amber when ``soft``: nothing is wrong
        yet, OrcaSlicer is just not found automatically)."""
        col = theme.TEXT_DIM if ok is None else (
            theme.OK if ok else (theme.WARNING if soft else theme.ERROR))
        mark = "" if ok is None else ("✓ " if ok else "✗ ")
        self.orca_status.setText(mark + text)
        self.orca_status.setStyleSheet(f"color: {col};")
        self.orca_valid = ok

    def refresh_orca_status(self) -> None:
        text = self.orca_edit.text().strip()
        if text:
            why = sl.check_orca_path(text)
            if why is None:
                self._status("Valid OrcaSlicer program", True)
            else:
                self._status(f"Invalid: {why}", False)
            return
        found = sl.find_orcaslicer(None)
        if found is not None:
            self._status(f"Automatic detection: {found}", True)
        else:
            self._status("Automatic detection: OrcaSlicer not found. Choose the program with "
                         "Browse...", False, soft=True)

    def set_orca_path(self, text: Optional[str]) -> bool:
        """Check and save a new OrcaSlicer path (empty = automatic detection).
        An invalid path is shown in red and not saved. Returns True if saved."""
        text = (text or "").strip().strip('"')
        if self.orca_edit.text() != text:
            self.orca_edit.blockSignals(True)
            self.orca_edit.setText(text)
            self.orca_edit.blockSignals(False)
        if text and sl.check_orca_path(text) is not None:
            self.refresh_orca_status()
            self.orca_status.setText(self.orca_status.text() + " (not saved)")
            return False
        new = text or None
        if new == configured_orca_path():
            self.refresh_orca_status()
            return False
        try:
            user_settings.update_settings(orca_path=new)
        except user_settings.SettingsError as exc:
            self._status(f"Not saved: {exc}", False)
            return False
        self.refresh_orca_status()
        self.orcaPathChanged.emit(new)
        return True

    def browse_orca(self) -> None:
        start = self.orca_edit.text().strip()
        start = str(Path(start).parent) if start else ""
        hook = getattr(self.parent(), "path_provider", None) if self.parent() else None
        if hook is not None:
            path = hook(False, "Choose OrcaSlicer", orca_file_filter(), start) or ""
        else:
            path = QtWidgets.QFileDialog.getOpenFileName(self, "Choose OrcaSlicer", start,
                                                         orca_file_filter())[0]
        if path:
            self.set_orca_path(path)

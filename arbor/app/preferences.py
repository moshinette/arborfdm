"""Application preferences dialog (File > Preferences...).

Sections are QGroupBox cards with a form layout; other parts of the application add their
rows to them (for example the OrcaSlicer path in the "Slicers" section).
"""
from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from . import theme


class PreferencesDialog(QtWidgets.QDialog):
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
            "Slicers that ArborFDM can open the exported file in.", objectName="dim")
        self.slicers_hint.setWordWrap(True)
        self.slicers_form.addRow(self.slicers_hint)

        lay.addStretch(1)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)
        lay.addWidget(self.buttons)
        self.setMinimumWidth(440)

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

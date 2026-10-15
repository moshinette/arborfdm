"""Dark theme, colours of the 3D scene and small painted icons."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

# --- UI palette -------------------------------------------------------------
BG = "#16181d"
BG_PANEL = "#1d2027"
BG_INPUT = "#262a33"
BG_HOVER = "#2e333d"
BORDER = "#323743"
TEXT = "#d9dde5"
TEXT_DIM = "#8a92a3"
ACCENT = "#3d9be9"
AMBER = "#f0a020"          # value changed from the profile
ERROR = "#ff4d4f"
WARNING = "#f5a623"
OK = "#52c41a"

# --- 3D scene (RGBA floats) --------------------------------------------------
SCENE_BG = (0.105, 0.115, 0.135, 1.0)
MODEL_COLOR = (0.80, 0.78, 0.74, 1.0)          # light grey PLA
SUPPORT_COLOR = (0.18, 0.70, 0.66, 1.0)        # teal: never used for contacts
RAFT_COLOR = (0.13, 0.50, 0.47, 1.0)
GHOST_COLOR = (0.95, 0.95, 1.0, 0.45)
UNSUPPORTED_COLOR = (1.0, 0.18, 0.15, 1.0)
WARNING_MARK_COLOR = (1.0, 0.62, 0.10, 1.0)
SELECTED_COLOR = (1.0, 1.0, 1.0, 1.0)
GRID_COLOR = (120, 130, 150, 60)

# contact reason -> (label, hex colour)
REASON_STYLE = {
    "island": ("Island", "#f06595"),
    "edge": ("Edge", "#4dabf7"),
    "corner": ("Corner", "#fcc419"),
    "surface": ("Surface", "#a9e34b"),
    "low_point": ("Low point", "#ff922b"),
    "manual": ("Manual", "#cc5de8"),
}


def hex_rgba(h: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    c = QtGui.QColor(h)
    return (c.redF(), c.greenF(), c.blueF(), alpha)


def reason_rgba(reason: str, alpha: float = 1.0):
    return hex_rgba(REASON_STYLE.get(reason, ("", "#ffffff"))[1], alpha)


STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 9pt; }}
QMainWindow::separator {{ background: {BORDER}; width: 1px; height: 1px; }}
QToolBar {{ background: {BG_PANEL}; border: none; border-bottom: 1px solid {BORDER};
           spacing: 2px; padding: 3px; }}
QToolBar QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px;
                       padding: 4px 8px; color: {TEXT}; }}
QToolBar QToolButton:hover {{ background: {BG_HOVER}; border-color: {BORDER}; }}
QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{ background: #24364a; border-color: {ACCENT}; }}
QToolBar QToolButton:disabled {{ color: #555c69; }}
QToolButton#primary {{ background: #1f5f99; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QToolButton#primary:hover {{ background: #2672b5; }}
QToolButton#primary:disabled {{ background: #23303d; border-color: {BORDER}; color: #66707e; }}
QMenuBar {{ background: {BG_PANEL}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item:selected {{ background: {BG_HOVER}; }}
QMenu {{ background: {BG_PANEL}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 5px 22px 5px 22px; }}
QMenu::item:selected {{ background: #24364a; }}
QMenu::item:disabled {{ color: #555c69; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG_PANEL}; border: none; }}
QFrame#panel {{ background: {BG_PANEL}; }}
QGroupBox {{ background: {BG_PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
            margin-top: 14px; padding: 8px 6px 6px 6px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {TEXT_DIM};
                   text-transform: uppercase; }}
QGroupBox QWidget {{ background: {BG_PANEL}; }}
QGroupBox::indicator {{ width: 12px; height: 12px; }}
QLabel {{ background: transparent; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#value {{ color: white; font-weight: 600; }}
QLabel#title {{ font-size: 11pt; font-weight: 700; color: white; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 4px; padding: 2px 4px;
    min-height: 20px; selection-background-color: {ACCENT}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 14px; border: none; background: transparent; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {BG_HOVER}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url(@ARROWS@/up.png); width: 8px; height: 5px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QComboBox::down-arrow {{
    image: url(@ARROWS@/down.png); width: 8px; height: 5px; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: #59606d; }}
QSpinBox[changed="true"], QDoubleSpinBox[changed="true"], QComboBox[changed="true"] {{
    border: 1px solid {AMBER}; color: {AMBER}; }}
QCheckBox[changed="true"] {{ color: {AMBER}; }}
QComboBox QAbstractItemView {{ background: {BG_INPUT}; border: 1px solid {BORDER};
                              selection-background-color: #24364a; }}
QPushButton {{ background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 4px;
              padding: 4px 10px; min-height: 20px; }}
QPushButton:hover {{ background: {BG_HOVER}; border-color: #46505f; }}
QPushButton:pressed {{ background: #24364a; }}
QPushButton:disabled {{ color: #59606d; }}
QPushButton:checked {{ background: #24364a; border-color: {ACCENT}; }}
QPushButton#small {{ padding: 0px 4px; min-height: 16px; }}
QPushButton#danger {{ border-color: #6b2b2d; }}
QListWidget, QTreeWidget, QTextEdit, QPlainTextEdit {{
    background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 4px; }}
QListWidget::item {{ padding: 3px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: #24364a; color: white; }}
QStatusBar {{ background: {BG_PANEL}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ padding: 0 8px; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{ background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: 3px;
               text-align: center; max-height: 14px; color: white; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::groove:vertical {{ background: {BG_INPUT}; width: 6px; border-radius: 3px; }}
QSlider::handle:vertical {{ background: {ACCENT}; height: 14px; margin: 0 -5px; border-radius: 7px; }}
QSlider::add-page:vertical {{ background: #24364a; border-radius: 3px; }}
QScrollBar:vertical {{ background: {BG_PANEL}; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a404c; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {BG_PANEL}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3a404c; border-radius: 4px; min-width: 24px; }}
QToolTip {{ background: {BG_INPUT}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {BG_PANEL}; padding: 5px 12px; border: 1px solid {BORDER};
               border-bottom: none; color: {TEXT_DIM}; }}
QTabBar::tab:selected {{ color: white; background: {BG_INPUT}; }}
QSplitter::handle {{ background: {BORDER}; }}
QFrame#viewbar {{ background: {BG_PANEL}; border-bottom: 1px solid {BORDER}; }}
QFrame#viewbar QPushButton {{ padding: 2px 10px; }}
QDialog {{ background: {BG_PANEL}; }}
"""


def apply_theme(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    roles = {
        QtGui.QPalette.Window: BG, QtGui.QPalette.WindowText: TEXT,
        QtGui.QPalette.Base: BG_INPUT, QtGui.QPalette.AlternateBase: BG_PANEL,
        QtGui.QPalette.Text: TEXT, QtGui.QPalette.Button: BG_INPUT,
        QtGui.QPalette.ButtonText: TEXT, QtGui.QPalette.Highlight: ACCENT,
        QtGui.QPalette.HighlightedText: "#ffffff", QtGui.QPalette.ToolTipBase: BG_INPUT,
        QtGui.QPalette.ToolTipText: TEXT, QtGui.QPalette.PlaceholderText: TEXT_DIM,
    }
    for role, col in roles.items():
        pal.setColor(role, QtGui.QColor(col))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor("#59606d"))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor("#59606d"))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET.replace("@ARROWS@", _arrow_dir()))


def _arrow_dir() -> str:
    """Small arrow images for spin boxes / combo boxes (painted once, cached in temp)."""
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "arborfdm_theme"
    d.mkdir(exist_ok=True)
    for name, pts in (("up", [(0, 5), (4, 0), (8, 5)]), ("down", [(0, 0), (8, 0), (4, 5)])):
        f = d / f"{name}.png"
        if f.exists():
            continue
        pm = QtGui.QPixmap(16, 10)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(TEXT_DIM))
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x * 2, y * 2) for x, y in pts]))
        p.end()
        pm.save(str(f))
    return d.as_posix()


# --- painted icons ------------------------------------------------------------
def _icon(paint, size: int = 32) -> QtGui.QIcon:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(TEXT), 2.2)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    paint(p, size)
    p.end()
    return QtGui.QIcon(pm)


def tree_icon() -> QtGui.QIcon:
    """Generate: a small support tree."""
    def paint(p: QtGui.QPainter, s: int) -> None:
        pen = p.pen()
        pen.setColor(QtGui.QColor(hex_to_q(SUPPORT_COLOR)))
        pen.setWidthF(2.6)
        p.setPen(pen)
        p.drawLine(16, 28, 16, 17)
        p.drawLine(16, 17, 8, 7)
        p.drawLine(16, 17, 24, 7)
        p.drawLine(16, 17, 16, 5)
        p.drawLine(10, 28, 22, 28)
        p.setBrush(QtGui.QColor(REASON_STYLE["edge"][1]))
        p.setPen(QtCore.Qt.NoPen)
        for x, y in ((8, 6), (24, 6), (16, 4)):
            p.drawEllipse(QtCore.QPointF(x, y), 2.4, 2.4)
    return _icon(paint)


def export_icon() -> QtGui.QIcon:
    def paint(p: QtGui.QPainter, s: int) -> None:
        p.drawRect(6, 14, 20, 13)
        p.drawLine(16, 4, 16, 20)
        p.drawLine(11, 9, 16, 4)
        p.drawLine(21, 9, 16, 4)
    return _icon(paint)


def rebuild_icon() -> QtGui.QIcon:
    def paint(p: QtGui.QPainter, s: int) -> None:
        pen = p.pen()
        pen.setColor(QtGui.QColor(hex_to_q(SUPPORT_COLOR)))
        p.setPen(pen)
        p.drawLine(16, 28, 16, 16)
        p.drawLine(16, 16, 9, 8)
        p.drawLine(16, 16, 23, 8)
        pen.setColor(QtGui.QColor(REASON_STYLE["manual"][1]))
        p.setPen(pen)
        p.drawArc(4, 4, 24, 24, 30 * 16, 120 * 16)
    return _icon(paint)


def hex_to_q(rgba) -> str:
    r, g, b = (int(round(c * 255)) for c in rgba[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def std_icon(widget: QtWidgets.QWidget, name: str) -> QtGui.QIcon:
    return widget.style().standardIcon(getattr(QtWidgets.QStyle.StandardPixmap, name))

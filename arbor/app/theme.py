"""Dark theme, colours of the 3D scene and small painted icons."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

# --- design tokens ------------------------------------------------------------------
# Every colour, spacing, font size and radius of the interface is defined here once; the
# widgets refer to these names (stylesheet below, overlays in viewport.py, panels.py).

# surfaces, from the darkest (window) to the lightest (hover)
BG = "#16181d"             # window, 3D view surroundings
BG_PANEL = "#1d2027"       # side panels, bars, menus
BG_CARD = "#21252c"        # grouped sections inside panels
BG_INPUT = "#282c35"       # edit fields, lists
BG_HOVER = "#30353f"
BG_SELECTED = "#243a52"    # selected / pressed / checked
BORDER = "#323743"
BORDER_SOFT = "#2a2e37"    # card outlines (quieter than BORDER)
BORDER_STRONG = "#46505f"  # hovered controls

# text
TEXT = "#d9dde5"
TEXT_STRONG = "#ffffff"    # values, titles
TEXT_DIM = "#8f97a8"       # labels, hints (contrast >= 4.5:1 on BG_PANEL)
TEXT_DISABLED = "#59606d"

# accents and states
ACCENT = "#3d9be9"
ACCENT_STRONG = "#1f6aab"  # primary button
ACCENT_HOVER = "#2a7cc4"
AMBER = "#f0a020"          # value changed from the profile
ERROR = "#ff4d4f"
WARNING = "#f5a623"
OK = "#52c41a"
CUT_ACTIVE = "#ffb347"     # height cut in use
USER_PRESET = "#8fd0ff"    # user presets in the profile list

# overlays drawn over the 3D view (legends, hints, cards)
OVERLAY_BG = "rgba(22,24,29,222)"
OVERLAY_BG_SOFT = "rgba(22,24,29,170)"

# state banners over the 3D view: background, text, accent border
BANNER = {
    "busy": ("rgba(32,52,84,236)", "#cfe0ff", "#4f8fe0"),
    "stale": ("rgba(80,58,16,238)", "#ffe2a8", "#e0a030"),
    "mesh": ("rgba(88,32,34,238)", "#ffd0d0", "#e05a5a"),
}

# spacing scale (px)
SP_XS = 2
SP_S = 4
SP_M = 8
SP_L = 12
SP_XL = 16

# font sizes (pt)
FS_SMALL = 8.5
FS_BASE = 9.0
FS_SECTION = 9.0
FS_TITLE = 11.0

# corner radii (px)
R_S = 3
R_M = 4
R_L = 6

# control metrics (px)
CONTROL_H = 22             # min height of edit fields and buttons
TOOLBAR_ICON = 18
ICON_BUTTON = 22           # square icon-only buttons (reset)
PANEL_MIN_W = 276          # side panels never get narrower than this (fits 1366 x 768)

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
/* child widgets are transparent: each surface (window, panel, card) is painted once */
QWidget {{ background: transparent; color: {TEXT}; font-size: {FS_BASE}pt; }}
QMainWindow, QDialog, QMessageBox, QInputDialog {{ background: {BG_PANEL}; }}
QMainWindow {{ background: {BG}; }}
QWidget#panel {{ background: {BG_PANEL}; }}
QMainWindow::separator {{ background: {BORDER}; width: 1px; height: 1px; }}

/* --- toolbar ------------------------------------------------------------------- */
QToolBar {{ background: {BG_PANEL}; border: none; border-bottom: 1px solid {BORDER};
           spacing: {SP_XS}px; padding: {SP_S}px {SP_M}px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: {SP_S}px {SP_M - 2}px; }}
QToolBar QToolButton {{ background: transparent; border: 1px solid transparent;
                       border-radius: {R_M}px; padding: {SP_S}px {SP_M}px; color: {TEXT}; }}
QToolBar QToolButton:hover {{ background: {BG_HOVER}; border-color: {BORDER}; }}
QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{
    background: {BG_SELECTED}; border-color: {ACCENT}; }}
QToolBar QToolButton:disabled {{ color: {TEXT_DISABLED}; background: transparent;
                                border-color: transparent; }}
QToolBar QToolButton[popupMode="2"] {{ padding-right: {SP_L + 4}px; }}
QToolBar QToolButton::menu-indicator {{ image: url(@ARROWS@/down.png); width: 8px; height: 5px;
    subcontrol-position: right center; subcontrol-origin: padding; right: {SP_S + 1}px; }}
QToolButton#primary {{ background: {ACCENT_STRONG}; border: 1px solid {ACCENT}; color: {TEXT_STRONG};
                      font-weight: 600; padding: {SP_S}px {SP_L}px; }}
QToolButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QToolButton#primary:disabled {{ background: #232f3c; border-color: {BORDER}; color: #67717f; }}

/* --- menus ------------------------------------------------------------------- */
QMenuBar {{ background: {BG_PANEL}; border-bottom: 1px solid {BORDER}; padding: 1px {SP_S}px; }}
QMenuBar::item {{ padding: {SP_S}px {SP_M}px; border-radius: {R_S}px; }}
QMenuBar::item:selected {{ background: {BG_HOVER}; }}
QMenu {{ background: {BG_PANEL}; border: 1px solid {BORDER}; padding: {SP_S}px 0; }}
QMenu::item {{ padding: 5px {SP_XL + 8}px 5px {SP_L}px; }}
QMenu::item:selected {{ background: {BG_SELECTED}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::icon {{ padding-left: {SP_M}px; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: {SP_S}px {SP_M}px; }}

/* --- side panels and sections ------------------------------------------------ */
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG_PANEL}; border: none; }}
QGroupBox {{ background: {BG_CARD}; border: 1px solid {BORDER_SOFT}; border-radius: {R_L}px;
            margin-top: {SP_XL + 2}px; padding: {SP_XS}px;
            font-weight: 600; font-size: {FS_SECTION}pt; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: {SP_XS}px;
                   top: 0px; padding: 0 {SP_S}px; color: {TEXT_DIM}; background: transparent; }}
QGroupBox QWidget {{ font-weight: normal; }}
QGroupBox::indicator {{ width: 12px; height: 12px; }}
QLabel {{ background: transparent; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#value {{ color: {TEXT_STRONG}; font-weight: 600; }}
QLabel#title {{ font-size: {FS_TITLE}pt; font-weight: 700; color: {TEXT_STRONG}; }}
QLabel#hint {{ color: {TEXT_DIM}; font-size: {FS_SMALL}pt; }}
QLabel#paramLabel {{ color: {TEXT}; }}

/* --- edit fields --------------------------------------------------------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: {R_M}px;
    padding: 2px {SP_S + 2}px; min-height: {CONTROL_H - 2}px;
    selection-background-color: {ACCENT}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none; background: transparent; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {BG_HOVER}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url(@ARROWS@/up.png); width: 8px; height: 5px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QComboBox::down-arrow {{
    image: url(@ARROWS@/down.png); width: 8px; height: 5px; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {{
    color: {TEXT_DISABLED}; background: {BG_CARD}; border-color: {BORDER_SOFT}; }}
QSpinBox[changed="true"], QDoubleSpinBox[changed="true"], QComboBox[changed="true"] {{
    border: 1px solid {AMBER}; color: {AMBER}; }}
QCheckBox {{ spacing: {SP_S + 2}px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER_STRONG};
                       border-radius: {R_S}px; background: {BG_INPUT}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
                               image: url(@ARROWS@/check.png); }}
QCheckBox::indicator:disabled {{ background: {BG_CARD}; border-color: {BORDER_SOFT}; }}
QCheckBox::indicator:checked:disabled {{ background: #2b4560; border-color: #2b4560; }}
QCheckBox[changed="true"] {{ color: {AMBER}; }}
QCheckBox[changed="true"]::indicator {{ border-color: {AMBER}; }}
QCheckBox[changed="true"]::indicator:checked {{ background: {AMBER}; }}
QCheckBox:disabled {{ color: {TEXT_DISABLED}; }}
QComboBox QFrame {{ background: {BG_INPUT}; border: none; }}
QComboBox QAbstractItemView {{ background: {BG_INPUT}; border: 1px solid {BORDER};
                              selection-background-color: {BG_SELECTED}; outline: none; }}

/* --- buttons ----------------------------------------------------------------- */
QPushButton, QToolButton {{ background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: {R_M}px;
              padding: 3px {SP_M + 2}px; min-height: {CONTROL_H - 6}px; }}
QPushButton:hover, QToolButton:hover {{ background: {BG_HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {BG_SELECTED}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_DISABLED}; background: {BG_CARD};
                                              border-color: {BORDER_SOFT}; }}
QPushButton:checked {{ background: {BG_SELECTED}; border-color: {ACCENT}; color: {TEXT_STRONG}; }}
QToolButton[popupMode="2"] {{ padding-right: {SP_L + 4}px; }}
QToolButton::menu-indicator {{ image: url(@ARROWS@/down.png); width: 8px; height: 5px;
    subcontrol-position: right center; subcontrol-origin: padding; right: {SP_S + 1}px; }}
QPushButton#small {{ padding: 0px {SP_S}px; min-height: 16px; }}
QPushButton#danger {{ border-color: #6b2b2d; color: #ffb3b3; }}
QPushButton#danger:hover {{ background: #3a2225; border-color: {ERROR}; }}
QPushButton#reset {{ background: transparent; border: 1px solid transparent; padding: 0px;
                    min-height: 0px; border-radius: {R_S}px; }}
QPushButton#reset:hover {{ background: {BG_HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton#reset:disabled {{ background: transparent; border-color: transparent; }}

/* --- lists, text ---------------------------------------------------------------- */
QListWidget, QTreeWidget, QTextEdit, QPlainTextEdit {{
    background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: {R_M}px; outline: none; }}
QListWidget::item {{ padding: {SP_S}px {SP_S}px; border-radius: {R_S}px; }}
QListWidget::item:hover {{ background: {BG_HOVER}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {BG_SELECTED}; color: {TEXT_STRONG}; }}
QListWidget#objectList {{ background: transparent; border: none; font-weight: 600; color: {TEXT_STRONG}; }}
QListWidget#objectList::item {{ padding: 0px; }}
QListWidget#objectList::item:hover, QListWidget#objectList::item:selected {{ background: transparent; }}
QListWidget#issueList::item {{ padding: {SP_S + 1}px {SP_S}px; border-bottom: 1px solid {BORDER_SOFT};
                                border-radius: 0px; }}

/* --- status bar --------------------------------------------------------------- */
QStatusBar {{ background: {BG_PANEL}; border-top: 1px solid {BORDER}; min-height: 24px; }}
QStatusBar QLabel {{ padding: 0 {SP_M}px; color: {TEXT_DIM}; }}
QStatusBar QLabel#stat {{ border-left: 1px solid {BORDER}; padding: 0 {SP_L - 2}px; color: {TEXT}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QPushButton {{ padding: 1px {SP_M}px; min-height: 0px; }}
QProgressBar {{ background: {BG_INPUT}; border: 1px solid {BORDER}; border-radius: {R_S}px;
               text-align: center; max-height: 12px; color: {TEXT_STRONG}; font-size: {FS_SMALL}pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

/* --- sliders, scrollbars, tooltips ------------------------------------------------- */
QSlider::groove:vertical {{ background: {BG_INPUT}; width: 6px; border-radius: 3px; }}
QSlider::handle:vertical {{ background: {ACCENT}; height: 14px; margin: 0 -5px; border-radius: 7px; }}
QSlider::handle:vertical:hover {{ background: #6cb6f2; }}
QSlider::add-page:vertical {{ background: {BG_SELECTED}; border-radius: 3px; }}
QScrollBar:vertical {{ background: {BG_PANEL}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a404c; border-radius: 4px; min-height: 24px; margin: 1px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5160; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: {BG_PANEL}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3a404c; border-radius: 4px; min-width: 24px; margin: 1px; }}
QToolTip {{ background: {BG_INPUT}; color: {TEXT}; border: 1px solid {BORDER}; padding: {SP_S}px; }}

/* --- tabs ------------------------------------------------------------------------ */
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; }}
QTabWidget::tab-bar {{ left: {SP_M}px; }}
QTabBar::tab {{ background: transparent; padding: {SP_S + 2}px {SP_L}px; border: none;
               border-bottom: 2px solid transparent; color: {TEXT_DIM}; margin-right: {SP_XS}px; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT_STRONG}; border-bottom: 2px solid {ACCENT}; }}

/* --- 3D view bars ------------------------------------------------------------------ */
QSplitter::handle {{ background: {BORDER}; }}
QFrame#viewbar {{ background: {BG_PANEL}; border-bottom: 1px solid {BORDER}; }}
QFrame#cutbar {{ background: {BG_PANEL}; border-left: 1px solid {BORDER}; }}
QFrame#viewbar QLabel#dim {{ padding: 0 {SP_XS}px 0 {SP_S}px; }}
QFrame#segment {{ background: transparent; }}
QFrame#segment QPushButton {{ border-radius: 0px; padding: 2px {SP_M}px; margin-left: -1px; }}
QFrame#segment QPushButton[seg="first"] {{ border-top-left-radius: {R_M}px;
    border-bottom-left-radius: {R_M}px; margin-left: 0px; }}
QFrame#segment QPushButton[seg="last"] {{ border-top-right-radius: {R_M}px;
    border-bottom-right-radius: {R_M}px; }}
QFrame#viewbar QPushButton {{ padding: 2px {SP_M}px; }}
QDialog QGroupBox {{ min-width: 360px; }}
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
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(TEXT_DISABLED))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(TEXT_DISABLED))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET.replace("@ARROWS@", _arrow_dir()))


def _arrow_dir() -> str:
    """Small arrow images for spin boxes / combo boxes (painted once, cached in temp)."""
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "arborfdm_theme_v2"
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
    f = d / "check.png"
    if not f.exists():
        pm = QtGui.QPixmap(28, 28)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(TEXT_STRONG), 3.6)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(6, 14.5), QtCore.QPointF(11.5, 20),
                                        QtCore.QPointF(22, 8)]))
        p.end()
        pm.save(str(f))
    return d.as_posix()


# --- painted icons (kept for compatibility, see icons.py) -------------------------------
def tree_icon() -> QtGui.QIcon:
    from . import icons
    return icons.generate()


def export_icon() -> QtGui.QIcon:
    from . import icons
    return icons.export()


def rebuild_icon() -> QtGui.QIcon:
    from . import icons
    return icons.rebuild()


def hex_to_q(rgba) -> str:
    r, g, b = (int(round(c * 255)) for c in rgba[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def std_icon(widget: QtWidgets.QWidget, name: str) -> QtGui.QIcon:
    return widget.style().standardIcon(getattr(QtWidgets.QStyle.StandardPixmap, name))

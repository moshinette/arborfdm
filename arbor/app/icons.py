"""Line icons painted with QPainter (no external resource).

Every icon is drawn on a 24 x 24 grid and rendered at several pixel sizes so it stays crisp
in the toolbar (18 px), in buttons (16 px) and on high DPI screens. A greyed copy is added
for the disabled state, so disabled actions read as disabled without relying on the style.
"""
from __future__ import annotations

from typing import Callable

from PySide6 import QtCore, QtGui

from . import theme

_SIZES = (16, 18, 20, 24, 32, 48)
_CACHE: dict[str, QtGui.QIcon] = {}

Painter = Callable[[QtGui.QPainter, Callable[[str], QtGui.QColor]], None]


def _pixmap(paint: Painter, size: int, disabled: bool,
            disabled_color: str = "") -> QtGui.QPixmap:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)

    def col(c: str) -> QtGui.QColor:
        return QtGui.QColor((disabled_color or theme.TEXT_DISABLED) if disabled else c)

    pen = QtGui.QPen(col(theme.TEXT), 1.7)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    paint(p, col)
    p.end()
    return pm


def make_icon(name: str, paint: Painter, disabled_color: str = "") -> QtGui.QIcon:
    """disabled_color: colour of the disabled variant (default: theme.TEXT_DISABLED)."""
    if name in _CACHE:
        return _CACHE[name]
    icon = QtGui.QIcon()
    for s in _SIZES:
        icon.addPixmap(_pixmap(paint, s, False), QtGui.QIcon.Normal)
        icon.addPixmap(_pixmap(paint, s, True, disabled_color), QtGui.QIcon.Disabled)
    _CACHE[name] = icon
    return icon


def _pen(p: QtGui.QPainter, color: QtGui.QColor, width: float = 1.7) -> None:
    pen = p.pen()
    pen.setColor(color)
    pen.setWidthF(width)
    p.setPen(pen)


def _poly(p: QtGui.QPainter, pts, closed: bool = False) -> None:
    poly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in pts])
    p.drawPolygon(poly) if closed else p.drawPolyline(poly)


# --- file ------------------------------------------------------------------------------
def new() -> QtGui.QIcon:
    def paint(p, c):
        _poly(p, [(14, 3), (6, 3), (6, 21), (18, 21), (18, 7), (14, 3)], closed=True)
        _poly(p, [(14, 3), (14, 7), (18, 7)])
    return make_icon("new", paint)


def open_() -> QtGui.QIcon:
    def paint(p, c):
        _poly(p, [(3, 19), (3, 5), (9, 5), (11, 7), (19, 7), (19, 10)])
        _poly(p, [(3, 19), (6, 10), (22, 10), (19, 19)], closed=True)
    return make_icon("open", paint)


def save() -> QtGui.QIcon:
    def paint(p, c):
        _poly(p, [(4, 4), (17, 4), (20, 7), (20, 20), (4, 20)], closed=True)
        p.drawRect(QtCore.QRectF(8, 4, 7, 5))
        p.drawRect(QtCore.QRectF(7.5, 13, 9, 7))
    return make_icon("save", paint)


def _curved_arrow(p, mirror: bool) -> None:
    if mirror:
        p.translate(24, 0)
        p.scale(-1, 1)
    path = QtGui.QPainterPath(QtCore.QPointF(5, 10))
    path.lineTo(14, 10)
    path.cubicTo(22, 10, 22, 20, 14, 20)
    path.lineTo(9, 20)
    p.drawPath(path)
    _poly(p, [(9, 6), (5, 10), (9, 14)])


def undo() -> QtGui.QIcon:
    return make_icon("undo", lambda p, c: _curved_arrow(p, False))


def redo() -> QtGui.QIcon:
    return make_icon("redo", lambda p, c: _curved_arrow(p, True))


def _tray(p) -> None:
    _poly(p, [(4, 14), (4, 20), (20, 20), (20, 14)])


def export() -> QtGui.QIcon:
    def paint(p, c):
        _tray(p)
        p.drawLine(QtCore.QPointF(12, 4), QtCore.QPointF(12, 15))
        _poly(p, [(7.5, 8.5), (12, 4), (16.5, 8.5)])
    return make_icon("export", paint)


def import_() -> QtGui.QIcon:
    def paint(p, c):
        _tray(p)
        p.drawLine(QtCore.QPointF(12, 3), QtCore.QPointF(12, 15))
        _poly(p, [(7.5, 10.5), (12, 15), (16.5, 10.5)])
    return make_icon("import", paint)


def slicer() -> QtGui.QIcon:
    """Stacked layers: hand the model to a slicer."""
    def paint(p, c):
        _poly(p, [(12, 4), (21, 8.5), (12, 13), (3, 8.5)], closed=True)
        _poly(p, [(3, 12.5), (12, 17), (21, 12.5)])
        _pen(p, c(theme.ACCENT))
        _poly(p, [(3, 16.5), (12, 21), (21, 16.5)])
    return make_icon("slicer", paint)


def preferences() -> QtGui.QIcon:
    def paint(p, c):
        for y, x in ((6, 15), (12, 8), (18, 13)):
            p.drawLine(QtCore.QPointF(4, y), QtCore.QPointF(20, y))
            p.setBrush(QtGui.QColor(theme.BG_PANEL))
            p.drawEllipse(QtCore.QPointF(x, y), 2.2, 2.2)
            p.setBrush(QtCore.Qt.NoBrush)
    return make_icon("preferences", paint)


# --- supports --------------------------------------------------------------------------
def generate() -> QtGui.QIcon:
    """A small support tree (support colour) with contact tips (edge colour)."""
    def paint(p, c):
        _pen(p, c(theme.hex_to_q(theme.SUPPORT_COLOR)), 2.0)
        p.drawLine(QtCore.QPointF(12, 21), QtCore.QPointF(12, 13))
        _poly(p, [(6, 5.5), (12, 13), (18, 5.5)])
        p.drawLine(QtCore.QPointF(12, 13), QtCore.QPointF(12, 4.5))
        p.drawLine(QtCore.QPointF(7.5, 21), QtCore.QPointF(16.5, 21))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(c(theme.REASON_STYLE["edge"][1]))
        for x, y in ((6, 4.5), (18, 4.5), (12, 3.2)):
            p.drawEllipse(QtCore.QPointF(x, y), 1.9, 1.9)
    return make_icon("generate", paint)


def rebuild() -> QtGui.QIcon:
    def paint(p, c):
        _pen(p, c(theme.hex_to_q(theme.SUPPORT_COLOR)), 2.0)
        p.drawLine(QtCore.QPointF(12, 21), QtCore.QPointF(12, 13))
        _poly(p, [(7, 7), (12, 13), (17, 7)])
        _pen(p, c(theme.REASON_STYLE["manual"][1]), 1.7)
        p.drawArc(QtCore.QRectF(3, 3, 18, 18), 35 * 16, 110 * 16)
        _poly(p, [(17.5, 2.8), (18.6, 6.4), (15, 6.8)])
    return make_icon("rebuild", paint)


def clear() -> QtGui.QIcon:
    def paint(p, c):
        p.drawLine(QtCore.QPointF(4, 7), QtCore.QPointF(20, 7))
        _poly(p, [(9, 7), (9, 4), (15, 4), (15, 7)])
        _poly(p, [(6, 7), (7, 20), (17, 20), (18, 7)])
        p.drawLine(QtCore.QPointF(10, 11), QtCore.QPointF(10, 16))
        p.drawLine(QtCore.QPointF(14, 11), QtCore.QPointF(14, 16))
    return make_icon("clear", paint)


def cancel() -> QtGui.QIcon:
    def paint(p, c):
        _pen(p, c(theme.ERROR))
        p.drawEllipse(QtCore.QPointF(12, 12), 8.5, 8.5)
        p.drawLine(QtCore.QPointF(9, 9), QtCore.QPointF(15, 15))
        p.drawLine(QtCore.QPointF(15, 9), QtCore.QPointF(9, 15))
    return make_icon("cancel", paint)


def reset() -> QtGui.QIcon:
    """Back to the profile value (counter-clockwise arrow)."""
    def paint(p, c):
        _pen(p, c(theme.AMBER), 2.0)
        p.drawArc(QtCore.QRectF(5, 5, 14, 14), 100 * 16, 280 * 16)
        _poly(p, [(6.5, 3.5), (10.6, 5.2), (8, 8.8)])
    # disabled (value equals the profile): barely visible, 40 of them must not add noise
    return make_icon("reset", paint, disabled_color=theme.BORDER)


# --- view ------------------------------------------------------------------------------
def fit() -> QtGui.QIcon:
    def paint(p, c):
        for pts in ([(4, 9), (4, 4), (9, 4)], [(15, 4), (20, 4), (20, 9)],
                    [(20, 15), (20, 20), (15, 20)], [(9, 20), (4, 20), (4, 15)]):
            _poly(p, pts)
        p.drawRect(QtCore.QRectF(9, 9, 6, 6))
    return make_icon("fit", paint)


def overhang() -> QtGui.QIcon:
    """Angle bands of the overhang colouring."""
    from . import overhang as oh

    def paint(p, c):
        p.setPen(QtCore.Qt.NoPen)
        for k, key in enumerate(("near", "over", "severe")):
            p.setBrush(c(oh.BAND_HEX.get(key, theme.TEXT)))
            p.drawRoundedRect(QtCore.QRectF(4 + k * 6, 5, 5, 14), 1.2, 1.2)
    return make_icon("overhang", paint)


# --- severities --------------------------------------------------------------------------
def severity(kind: str) -> QtGui.QIcon:
    """error: red disc with a bar; warning: amber triangle; info: grey ring with i."""
    def paint(p, c):
        if kind == "error":
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(c(theme.ERROR))
            p.drawEllipse(QtCore.QPointF(12, 12), 9, 9)
            _pen(p, QtGui.QColor("#ffffff"), 2.4)
            p.drawLine(QtCore.QPointF(12, 7), QtCore.QPointF(12, 13))
            p.drawPoint(QtCore.QPointF(12, 17))
        elif kind == "warning":
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(c(theme.WARNING))
            _poly(p, [(12, 3), (22, 20.5), (2, 20.5)], closed=True)
            _pen(p, QtGui.QColor(theme.BG), 2.4)
            p.drawLine(QtCore.QPointF(12, 9), QtCore.QPointF(12, 14))
            p.drawPoint(QtCore.QPointF(12, 17.5))
        else:
            _pen(p, c(theme.TEXT_DIM), 1.7)
            p.drawEllipse(QtCore.QPointF(12, 12), 8.5, 8.5)
            _pen(p, c(theme.TEXT_DIM), 2.2)
            p.drawLine(QtCore.QPointF(12, 11), QtCore.QPointF(12, 16.5))
            p.drawPoint(QtCore.QPointF(12, 7.5))
    return make_icon("severity_" + kind, paint)

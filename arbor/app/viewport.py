"""3D viewport: pyqtgraph.opengl GLViewWidget with model, supports, contacts, issues.

Navigation: left drag orbit, middle drag (or Shift + left drag) pan, wheel zoom,
Ctrl + left drag pan in the view plane.
Editing: left click on a contact selects it, left click on the model adds a manual contact
at the exact ray / mesh hit, right click on a contact removes it.
The viewport never edits the project: it emits signals handled by the main window.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from OpenGL import GL
from pyqtgraph.opengl import shaders
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .picking import BackgroundPicker

CLICK_TOLERANCE_PX = 5          # a press/release closer than this is a click, not a drag
CONTACT_PICK_RADIUS_PX = 9
HOVER_INTERVAL_MS = 40          # hover picking is deferred and coalesced (QA-11)

# depth test off: markers always visible through the model
OVERLAY_GL = {GL.GL_DEPTH_TEST: False, GL.GL_BLEND: True, GL.GL_CULL_FACE: False,
              "glBlendFunc": (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)}

_LIT_SHADER = shaders.ShaderProgram("arbor_lit", [
    shaders.VertexShader("""
        uniform mat4 u_mvp;
        uniform mat3 u_normal;
        attribute vec4 a_position;
        attribute vec3 a_normal;
        attribute vec4 a_color;
        varying vec4 v_color;
        varying vec3 v_normal;
        void main() {
            v_normal = normalize(u_normal * a_normal);
            v_color = a_color;
            gl_Position = u_mvp * a_position;
        }
    """),
    shaders.FragmentShader("""
        #ifdef GL_ES
        precision mediump float;
        #endif
        varying vec4 v_color;
        varying vec3 v_normal;
        void main() {
            vec3 n = normalize(v_normal);
            vec3 key = normalize(vec3(0.35, 0.55, 1.0));
            vec3 fill = normalize(vec3(-0.6, -0.2, 0.6));
            float d = abs(dot(n, key));
            float f = abs(dot(n, fill));
            float rim = pow(1.0 - abs(n.z), 3.0);
            vec3 h = normalize(key + vec3(0.0, 0.0, 1.0));
            float s = pow(abs(dot(n, h)), 40.0) * 0.18;
            vec3 rgb = v_color.rgb * (0.22 + 0.62 * d + 0.18 * f) + vec3(s) + 0.06 * rim;
            gl_FragColor = vec4(rgb, v_color.a);
        }
    """),
])


def _qmatrix(m: np.ndarray) -> QtGui.QMatrix4x4:
    return QtGui.QMatrix4x4(*[float(v) for v in np.asarray(m, float).reshape(-1)])


def _np_matrix(q: QtGui.QMatrix4x4) -> np.ndarray:
    # QMatrix4x4.data() is column-major
    return np.array(q.data(), dtype=float).reshape(4, 4).T


def clip_mesh(mesh: trimesh.Trimesh, z: float) -> Optional[trimesh.Trimesh]:
    """Part of the mesh below z (open cut, display only)."""
    if mesh is None or len(mesh.faces) == 0:
        return None
    lo, hi = mesh.bounds[:, 2]
    if z >= hi:
        return mesh
    if z <= lo:
        return None
    try:
        out = trimesh.intersections.slice_mesh_plane(mesh, plane_normal=[0, 0, -1],
                                                     plane_origin=[0, 0, z], cap=False)
        return out if len(out.faces) else None
    except Exception:
        keep = mesh.triangles[:, :, 2].max(axis=1) <= z
        if not keep.any():
            return None
        return trimesh.Trimesh(mesh.vertices, mesh.faces[keep], process=False)


class Legend(QtWidgets.QFrame):
    """Contact reasons with colours and counts, drawn over the 3D view."""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("QFrame { background: rgba(22,24,29,210); border: 1px solid #323743;"
                           " border-radius: 6px; } QLabel { border: none; background: transparent;"
                           " font-size: 8.5pt; }")
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(2)
        self.count_labels: dict[str, QtWidgets.QLabel] = {}
        rows = [(k, lbl, col) for k, (lbl, col) in theme.REASON_STYLE.items()]
        rows += [("supports", "Supports", theme.hex_to_q(theme.SUPPORT_COLOR)),
                 ("unsupported", "Unsupported", theme.hex_to_q(theme.UNSUPPORTED_COLOR))]
        for r, (key, label, col) in enumerate(rows):
            dot = QtWidgets.QLabel()
            shape = "border-radius: 5px;" if key not in ("supports",) else "border-radius: 2px;"
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {col}; {shape} border: none;")
            name = QtWidgets.QLabel(label)
            cnt = QtWidgets.QLabel("0")
            cnt.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            cnt.setStyleSheet("color: white; font-weight: 600; border: none;")
            lay.addWidget(dot, r, 0)
            lay.addWidget(name, r, 1)
            lay.addWidget(cnt, r, 2)
            self.count_labels[key] = cnt
        self.adjustSize()

    def set_counts(self, counts: dict[str, int]) -> None:
        for k, lab in self.count_labels.items():
            lab.setText(str(counts.get(k, 0)) if k != "supports" else counts.get(k, "-"))
        self.adjustSize()


class Viewport(gl.GLViewWidget):
    contactAddRequested = QtCore.Signal(object, object)     # position (3,), normal (3,)
    contactRemoveRequested = QtCore.Signal(int)
    contactSelected = QtCore.Signal(object)                 # contact id or None
    hoverChanged = QtCore.Signal(object)                    # hit position or None

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setBackgroundColor(tuple(int(c * 255) for c in theme.SCENE_BG))
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.opts["fov"] = 40

        self.view_mode = "both"                 # "model" | "supports" | "both"
        self.clip_z: Optional[float] = None      # None = no clipping
        self.edit_enabled = True
        self.ghost_enabled = True
        self.tip_length = 1.6
        self.tip_radius = 0.5

        self.model_mesh: Optional[trimesh.Trimesh] = None
        self.support_mesh: Optional[trimesh.Trimesh] = None
        self.raft_mesh: Optional[trimesh.Trimesh] = None
        self.contacts: list = []
        self.issues: list = []
        self.selected_id: Optional[int] = None
        self._press_pos: Optional[QtCore.QPointF] = None
        self._press_button = None
        self._hover_pos: Optional[QtCore.QPointF] = None
        self._picker: Optional[BackgroundPicker] = None

        self.grid = gl.GLGridItem(color=theme.GRID_COLOR)
        self.grid.setSize(200, 200)
        self.grid.setSpacing(10, 10)
        self.addItem(self.grid)
        self.plate_outline = gl.GLLinePlotItem(color=(0.35, 0.55, 0.8, 0.8), width=1.5,
                                               mode="line_strip", antialias=True)
        self.addItem(self.plate_outline)
        self.axes = gl.GLLinePlotItem(mode="lines", width=2, antialias=True)
        self.addItem(self.axes)

        self.model_item = self._mesh_item(theme.MODEL_COLOR)
        self.support_item = self._mesh_item(theme.SUPPORT_COLOR)
        self.raft_item = self._mesh_item(theme.RAFT_COLOR)
        self.selection_item = gl.GLScatterPlotItem(pxMode=True, glOptions=OVERLAY_GL)
        self.selection_item.setDepthValue(5)
        self.addItem(self.selection_item)
        self.contact_item = gl.GLScatterPlotItem(pxMode=True, glOptions="translucent")
        self.contact_item.setDepthValue(6)
        self.addItem(self.contact_item)
        self.issue_item = gl.GLScatterPlotItem(pxMode=True, glOptions=OVERLAY_GL)
        self.issue_item.setDepthValue(10)
        self.addItem(self.issue_item)
        self.issue_lines = gl.GLLinePlotItem(mode="lines", width=1.5, glOptions=OVERLAY_GL,
                                             antialias=True)
        self.issue_lines.setDepthValue(9)
        self.addItem(self.issue_lines)
        cone = trimesh.creation.cone(radius=1.0, height=1.0, sections=20)
        cone.apply_transform(np.diag([1.0, 1.0, -1.0, 1.0]))   # apex at origin, base at z=-1
        cone.apply_translation([0, 0, 1.0])                  # base at z=1 (along +normal)
        cone.invert()
        self.ghost_item = gl.GLMeshItem(vertexes=np.asarray(cone.vertices, np.float32),
                                        faces=np.asarray(cone.faces, np.uint32),
                                        color=theme.GHOST_COLOR, smooth=False,
                                        shader=_LIT_SHADER, glOptions="translucent")
        self.ghost_item.setDepthValue(20)
        self.ghost_item.hide()
        self.addItem(self.ghost_item)

        self.legend = Legend(self)
        self._hover_timer = QtCore.QTimer(self, singleShot=True, interval=HOVER_INTERVAL_MS)
        self._hover_timer.timeout.connect(self._update_hover)
        self._clip_timer = QtCore.QTimer(self, singleShot=True, interval=60)
        self._clip_timer.timeout.connect(self._rebuild_geometry)
        self._update_grid(None)
        self.setCameraPosition(distance=160, elevation=25, azimuth=-60)

    # ------------------------------------------------------------------ items
    def _mesh_item(self, color) -> gl.GLMeshItem:
        item = gl.GLMeshItem(color=color, smooth=False, shader=_LIT_SHADER, glOptions="opaque")
        item.hide()
        self.addItem(item)
        return item

    @staticmethod
    def _set_mesh(item: gl.GLMeshItem, mesh: Optional[trimesh.Trimesh], visible: bool) -> None:
        if mesh is None or len(mesh.faces) == 0 or not visible:
            item.hide()
            return
        item.setMeshData(vertexes=np.asarray(mesh.vertices, np.float32),
                         faces=np.asarray(mesh.faces, np.uint32))
        item.show()

    # ------------------------------------------------------------------ data
    def set_model(self, mesh: Optional[trimesh.Trimesh], fit: bool = False) -> None:
        if mesh is not self.model_mesh:
            # acceleration structure for picking: built once per mesh, in a background
            # thread for large meshes (the UI thread never builds it)
            self._picker = BackgroundPicker(mesh) if mesh is not None and len(mesh.faces) else None
        self.model_mesh = mesh
        self._update_grid(mesh)
        self._rebuild_geometry()
        if fit:
            self.fit_view()

    def set_supports(self, support_mesh, raft_mesh) -> None:
        self.support_mesh = support_mesh if support_mesh is not None and len(
            getattr(support_mesh, "faces", [])) else None
        self.raft_mesh = raft_mesh if raft_mesh is not None and len(
            getattr(raft_mesh, "faces", [])) else None
        self._rebuild_geometry()

    def set_contacts(self, contacts: list) -> None:
        self.contacts = list(contacts)
        self._update_contacts()

    def set_issues(self, issues: list) -> None:
        self.issues = list(issues)
        self._update_issues()

    def set_selected(self, contact_id: Optional[int]) -> None:
        self.selected_id = contact_id
        self._update_contacts()

    def set_view_mode(self, mode: str) -> None:
        if mode not in ("model", "supports", "both"):
            raise ValueError(mode)
        self.view_mode = mode
        self._rebuild_geometry()

    def set_clip(self, z: Optional[float]) -> None:
        self.clip_z = z
        self._clip_timer.start()

    def scene_height(self) -> float:
        hs = [m.bounds[1][2] for m in (self.model_mesh, self.support_mesh, self.raft_mesh)
              if m is not None and len(m.faces)]
        return float(max(hs)) if hs else 0.0

    def set_tip_preview(self, length: float, base_diameter: float) -> None:
        self.tip_length = max(0.2, float(length))
        self.tip_radius = max(0.1, float(base_diameter) / 2)

    # ------------------------------------------------------------------ rendering
    def _rebuild_geometry(self) -> None:
        z = self.clip_z
        show_model = self.view_mode in ("model", "both")
        show_supports = self.view_mode in ("supports", "both")
        model = self.model_mesh
        supports, raft = self.support_mesh, self.raft_mesh
        if z is not None:
            model = clip_mesh(model, z) if model is not None else None
            supports = clip_mesh(supports, z) if supports is not None else None
            raft = clip_mesh(raft, z) if raft is not None else None
        self._set_mesh(self.model_item, model, show_model)
        self._set_mesh(self.support_item, supports, show_supports)
        self._set_mesh(self.raft_item, raft, show_supports)
        self._update_contacts()
        self._update_issues()
        self.update()

    def _visible_contacts(self) -> list:
        return [c for c in self.contacts
                if self.clip_z is None or c.position[2] <= self.clip_z + 1e-6]

    def _update_contacts(self) -> None:
        cs = self._visible_contacts()
        counts: dict[str, int] = {}
        for c in self.contacts:
            counts[c.reason.value] = counts.get(c.reason.value, 0) + 1
        counts["unsupported"] = sum(1 for i in self.issues if i.position is not None
                                    and i.severity == "error")
        counts["supports"] = "yes" if self.support_mesh is not None else "-"
        self.legend.set_counts(counts)
        self._place_legend()
        if not cs:
            self.contact_item.setData(pos=np.zeros((0, 3)))
            self.selection_item.setData(pos=np.zeros((0, 3)))
            return
        pos = np.array([np.asarray(c.position, float) + 0.12 * np.asarray(c.normal, float)
                        for c in cs])
        col = np.array([theme.reason_rgba(c.reason.value, 1.0 if c.enabled else 0.35)
                        for c in cs])
        size = np.array([9.0 if c.enabled else 7.0 for c in cs])
        self.contact_item.setData(pos=pos, color=col, size=size)
        sel = [p for p, c in zip(pos, cs) if c.id == self.selected_id]
        if sel:
            self.selection_item.setData(pos=np.array(sel), color=theme.SELECTED_COLOR, size=17.0)
        else:
            self.selection_item.setData(pos=np.zeros((0, 3)))

    def _update_issues(self) -> None:
        pts, cols, lines, lcols = [], [], [], []
        for i in self.issues:
            if i.position is None:
                continue
            p = np.asarray(i.position, float)
            if self.clip_z is not None and p[2] > self.clip_z:
                continue
            c = theme.UNSUPPORTED_COLOR if i.severity == "error" else theme.WARNING_MARK_COLOR
            pts.append(p)
            cols.append(c)
            # a short vertical drop line makes the marker readable in 3D
            lines += [p, p - np.array([0, 0, min(4.0, max(p[2], 0.0))])]
            lcols += [c, (c[0], c[1], c[2], 0.0)]
        if pts:
            self.issue_item.setData(pos=np.array(pts), color=np.array(cols), size=13.0)
            self.issue_lines.setData(pos=np.array(lines), color=np.array(lcols))
            self.issue_lines.show()
        else:
            self.issue_item.setData(pos=np.zeros((0, 3)))
            self.issue_lines.hide()

    def _update_grid(self, mesh: Optional[trimesh.Trimesh]) -> None:
        if mesh is not None and len(mesh.faces):
            ext = float(np.max(mesh.extents[:2]))
            size = max(60.0, math.ceil(ext * 2.0 / 20.0) * 20.0)
        else:
            size = 120.0
        spacing = 5.0 if size <= 160 else 10.0
        self.grid.setSize(size, size)
        self.grid.setSpacing(spacing, spacing)
        h = size / 2
        self.plate_outline.setData(pos=np.array([[-h, -h, 0], [h, -h, 0], [h, h, 0], [-h, h, 0],
                                                 [-h, -h, 0]], float))
        a = 10.0
        self.axes.setData(pos=np.array([[-h, -h, 0], [-h + a, -h, 0], [-h, -h, 0], [-h, -h + a, 0],
                                        [-h, -h, 0], [-h, -h, a]], float),
                          color=np.array([(0.9, 0.3, 0.3, 1)] * 2 + [(0.4, 0.85, 0.4, 1)] * 2
                                         + [(0.35, 0.6, 1.0, 1)] * 2))

    def fit_view(self) -> None:
        meshes = [m for m in (self.model_mesh, self.support_mesh) if m is not None]
        if not meshes:
            self.setCameraPosition(pos=QtGui.QVector3D(0, 0, 0), distance=160)
            return
        lo = np.min([m.bounds[0] for m in meshes], axis=0)
        hi = np.max([m.bounds[1] for m in meshes], axis=0)
        c = (lo + hi) / 2
        radius = float(np.linalg.norm(hi - lo)) / 2
        dist = max(radius / math.tan(math.radians(self.opts["fov"]) / 2) * 1.15, 10.0)
        self.setCameraPosition(pos=QtGui.QVector3D(*c), distance=dist)

    def focus_on(self, position) -> None:
        p = np.asarray(position, float)
        dist = self.opts["distance"]
        if self.model_mesh is not None:
            dist = min(dist, float(np.linalg.norm(self.model_mesh.extents)) * 0.9)
        self.setCameraPosition(pos=QtGui.QVector3D(*p), distance=max(dist, 8.0))

    def set_preset_view(self, name: str) -> None:
        presets = {"iso": (25, -60), "front": (0, -90), "side": (0, 0), "top": (90, -90),
                   "bottom": (-90, -90)}
        elev, azim = presets[name]
        self.setCameraPosition(elevation=elev, azimuth=azim)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place_legend()

    def _place_legend(self) -> None:
        self.legend.adjustSize()
        self.legend.move(10, self.height() - self.legend.height() - 10)

    # ------------------------------------------------------------------ picking
    def mvp(self) -> np.ndarray:
        vp = self.getViewport()
        proj = _np_matrix(self.projectionMatrix(vp, vp))
        view = _np_matrix(self.viewMatrix())
        return proj @ view

    def camera_ray(self, x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
        """World space ray (origin, unit direction) through widget pixel (x, y)."""
        w, h = max(self.width(), 1), max(self.height(), 1)
        ndc_x = 2.0 * x / w - 1.0
        ndc_y = 1.0 - 2.0 * y / h
        inv = np.linalg.inv(self.mvp())
        near = inv @ np.array([ndc_x, ndc_y, -1.0, 1.0])
        far = inv @ np.array([ndc_x, ndc_y, 1.0, 1.0])
        near, far = near[:3] / near[3], far[:3] / far[3]
        d = far - near
        return near, d / np.linalg.norm(d)

    def project_points(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """World points -> widget pixels (N, 2) and a mask of points in front of the camera."""
        pts = np.asarray(pts, float).reshape(-1, 3)
        h4 = np.c_[pts, np.ones(len(pts))] @ self.mvp().T
        front = h4[:, 3] > 1e-9
        ndc = h4[:, :3] / np.where(front, h4[:, 3], 1.0)[:, None]
        px = np.c_[(ndc[:, 0] + 1) * 0.5 * self.width(), (1 - ndc[:, 1]) * 0.5 * self.height()]
        return px, front

    def picker_ready(self) -> bool:
        return self._picker is None or self._picker.ready

    def pick_model(self, x: float, y: float, wait: bool = True
                   ) -> Optional[tuple[np.ndarray, np.ndarray, int]]:
        """Exact hit on the model mesh: (position, outward face normal, face index).

        wait=False (hover) returns None while the picking structure is still being built
        in the background instead of blocking the UI thread."""
        mesh = self.model_mesh
        if mesh is None or self.view_mode == "supports" or self._picker is None:
            return None
        picker = self._picker.picker if not wait else self._picker.wait()
        if picker is None:
            return None
        origin, direction = self.camera_ray(x, y)
        locs, dist, tri = picker.intersect(origin, direction)
        for k in range(len(dist)):
            if self.clip_z is not None and locs[k][2] > self.clip_z + 1e-6:
                continue        # clipped away: the ray continues to the visible part
            n = picker.face_normal(int(tri[k]))
            if np.dot(n, direction) > 0:
                n = -n          # the visible side faces the camera
            return np.asarray(locs[k], float), n, int(tri[k])
        return None

    def pick_contact(self, x: float, y: float, radius_px: float = CONTACT_PICK_RADIUS_PX,
                     wait: bool = True) -> Optional[int]:
        cs = self._visible_contacts()
        if not cs:
            return None
        px, front = self.project_points(np.array([c.position for c in cs]))
        d = np.hypot(px[:, 0] - x, px[:, 1] - y)
        d[~front] = np.inf
        # prefer contacts that are not hidden behind the model
        k = int(np.argmin(d))
        if d[k] > radius_px:
            return None
        hit = self.pick_model(x, y, wait=wait)
        if hit is not None:
            cam = self.camera_ray(x, y)[0]
            if np.linalg.norm(hit[0] - cam) + 0.5 < np.linalg.norm(cs[k].position - cam):
                hidden = np.linalg.norm(hit[0] - cs[k].position) > 2.0
                if hidden:
                    return None
        return cs[k].id

    # ------------------------------------------------------------------ mouse
    def handle_click(self, x: float, y: float, button) -> str:
        """Interpret a click. Returns what happened (used by tests)."""
        if button == QtCore.Qt.MouseButton.RightButton:
            cid = self.pick_contact(x, y)
            if cid is not None and self.edit_enabled:
                self.contactRemoveRequested.emit(cid)
                return "remove"
            return "none"
        if button != QtCore.Qt.MouseButton.LeftButton:
            return "none"
        cid = self.pick_contact(x, y)
        if cid is not None:
            self.contactSelected.emit(cid)
            return "select"
        hit = self.pick_model(x, y)
        if hit is not None and self.edit_enabled:
            self.contactAddRequested.emit(hit[0], hit[1])
            return "add"
        self.contactSelected.emit(None)
        return "deselect"

    def mousePressEvent(self, ev) -> None:
        self._press_pos = ev.position()
        self._press_button = ev.button()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        lpos = ev.position()
        if ev.buttons() == QtCore.Qt.MouseButton.NoButton:
            self._hover_pos = lpos
            self._hover_timer.start()
            self.mousePos = lpos
            return
        self.ghost_item.hide()
        if ev.buttons() == QtCore.Qt.MouseButton.LeftButton and \
                ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            diff = lpos - self.mousePos
            self.mousePos = lpos
            self.pan(diff.x(), diff.y(), 0, relative="view-upright")
            return
        if ev.buttons() == QtCore.Qt.MouseButton.RightButton:
            self.mousePos = lpos
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._press_pos is not None and ev.button() == self._press_button:
            moved = (ev.position() - self._press_pos).manhattanLength()
            if moved <= CLICK_TOLERANCE_PX:
                self.handle_click(ev.position().x(), ev.position().y(), ev.button())
        self._press_pos = None
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.ghost_item.hide()
        self.hoverChanged.emit(None)
        self.update()
        super().leaveEvent(ev)

    def _update_hover(self) -> None:
        if self._hover_pos is None:
            return
        x, y = self._hover_pos.x(), self._hover_pos.y()
        hit = (self.pick_model(x, y, wait=False) if (self.ghost_enabled and self.edit_enabled)
               else None)
        if hit is None or self.pick_contact(x, y, wait=False) is not None:
            if self.ghost_item.visible():
                self.ghost_item.hide()
                self.update()
            self.hoverChanged.emit(hit[0] if hit is not None else None)
            return
        self.show_ghost(hit[0], hit[1])
        self.hoverChanged.emit(hit[0])

    def show_ghost(self, position, normal) -> None:
        """Preview of a tip cone at a surface point (apex on the surface, along the normal)."""
        n = np.asarray(normal, float)
        n = n / (np.linalg.norm(n) or 1.0)
        z = np.array([0.0, 0.0, 1.0])
        v = np.cross(z, n)
        c = float(np.dot(z, n))
        if np.linalg.norm(v) < 1e-9:
            rot = np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
        else:
            k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
            rot = np.eye(3) + k + k @ k * (1.0 / (1.0 + c))
        m = np.eye(4)
        m[:3, :3] = rot @ np.diag([self.tip_radius, self.tip_radius, self.tip_length])
        m[:3, 3] = np.asarray(position, float)
        self.ghost_item.setTransform(_qmatrix(m))
        self.ghost_item.show()
        self.update()


class ViewportContainer(QtWidgets.QWidget):
    """Viewport + view bar (mode, presets) + clipping slider."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.viewport = Viewport(self)
        bar = QtWidgets.QFrame(objectName="viewbar")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(6, 4, 6, 4)
        bl.setSpacing(4)
        bl.addWidget(QtWidgets.QLabel("Show", objectName="dim"))
        self.mode_buttons: dict[str, QtWidgets.QPushButton] = {}
        group = QtWidgets.QButtonGroup(self)
        for key, label in (("both", "Model + supports"), ("model", "Model"),
                           ("supports", "Supports")):
            b = QtWidgets.QPushButton(label, checkable=True)
            b.clicked.connect(lambda _=False, k=key: self.viewport.set_view_mode(k))
            group.addButton(b)
            bl.addWidget(b)
            self.mode_buttons[key] = b
        self.mode_buttons["both"].setChecked(True)
        bl.addSpacing(16)
        bl.addWidget(QtWidgets.QLabel("View", objectName="dim"))
        for key, label in (("iso", "Iso"), ("front", "Front"), ("side", "Side"), ("top", "Top"),
                           ("bottom", "Below")):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self.viewport.set_preset_view(k))
            bl.addWidget(b)
        fit = QtWidgets.QPushButton("Fit")
        fit.clicked.connect(self.viewport.fit_view)
        bl.addWidget(fit)
        bl.addStretch(1)
        self.hint = QtWidgets.QLabel("Left click: add / select contact    Right click: delete    "
                                     "Drag: orbit    Shift or middle drag: pan    Wheel: zoom",
                                     self.viewport)
        self.hint.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.hint.setStyleSheet("background: rgba(22,24,29,170); color: #8a92a3; "
                                "border-radius: 4px; padding: 3px 8px; font-size: 8.5pt;")
        self.hint.adjustSize()
        self.hint.move(10, 10)
        # state banner: computation running, supports out of date, mesh with errors
        self.banner = QtWidgets.QLabel("", self.viewport)
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.banner.hide()
        self.banner_kind = ""

        side = QtWidgets.QFrame(objectName="viewbar")
        sl = QtWidgets.QVBoxLayout(side)
        sl.setContentsMargins(4, 6, 4, 6)
        self.clip_label = QtWidgets.QLabel("all", objectName="dim")
        self.clip_label.setAlignment(QtCore.Qt.AlignCenter)
        self.clip_label.setFixedWidth(46)
        self.clip_slider = QtWidgets.QSlider(QtCore.Qt.Vertical)
        self.clip_slider.setRange(0, 1000)
        self.clip_slider.setValue(1000)
        self.clip_slider.setToolTip("Cut by height: show only what is below this height")
        self.clip_slider.valueChanged.connect(self._on_clip)
        cut = QtWidgets.QLabel("Cut", objectName="dim")
        cut.setAlignment(QtCore.Qt.AlignCenter)
        sl.addWidget(cut)
        sl.addWidget(self.clip_slider, 1, QtCore.Qt.AlignHCenter)
        sl.addWidget(self.clip_label)

        mid = QtWidgets.QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self.viewport, 1)
        mid.addWidget(side)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addLayout(mid, 1)

    BANNER_STYLE = {
        "busy": ("rgba(40,62,98,230)", "#cfe0ff", "#4f7fd0"),
        "stale": ("rgba(92,66,16,235)", "#ffe2a8", "#d39b2a"),
        "mesh": ("rgba(98,36,36,235)", "#ffd0d0", "#d05050"),
    }

    def set_banner(self, text: str, kind: str = "") -> None:
        """Persistent message over the 3D view (empty text hides it)."""
        self.banner_kind = kind if text else ""
        if not text:
            self.banner.hide()
            self.banner.setText("")
            return
        bg, fg, border = self.BANNER_STYLE.get(kind, self.BANNER_STYLE["stale"])
        self.banner.setStyleSheet(f"background: {bg}; color: {fg}; border: 1px solid {border};"
                                  " border-radius: 5px; padding: 5px 9px; font-size: 9pt;")
        self.banner.setText(text)
        self._place_banner()
        self.banner.show()
        self.banner.raise_()

    def _place_banner(self) -> None:
        vp = self.viewport
        w = max(200, vp.width() - 20)
        self.banner.setFixedWidth(w)
        self.banner.adjustSize()
        self.banner.move(10, self.hint.y() + self.hint.height() + 6)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self.banner.isVisible():
            self._place_banner()

    def _on_clip(self, value: int) -> None:
        h = self.viewport.scene_height()
        if value >= 1000 or h <= 0:
            self.clip_label.setText("all")
            self.viewport.set_clip(None)
        else:
            z = h * value / 1000.0
            self.clip_label.setText(f"{z:.1f}")
            self.viewport.set_clip(z)

    def refresh_clip(self) -> None:
        self._on_clip(self.clip_slider.value())

"""Interactive 3D model viewer using QOpenGLWidget.

Minimal real-time viewport to orbit/pan/zoom STL/OBJ meshes or STEP bounds.
Relies on trimesh for mesh loading and PySide6 OpenGL wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import math

import numpy as np

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtOpenGL import (
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLBuffer,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

try:  # pragma: no cover - exercised at runtime
    import trimesh  # type: ignore
except Exception:  # pragma: no cover - fallback if unavailable
    trimesh = None  # type: ignore

from ..importer import extract_step_metadata


VERT_SHADER = """
#version 330 core
layout(location = 0) in vec3 position;
layout(location = 1) in vec3 normal;

uniform mat4 u_mvp;
uniform mat4 u_model;
uniform vec3 u_lightDir;

out vec3 v_normal;
out float v_lighting;

void main() {
    gl_Position = u_mvp * vec4(position, 1.0);
    vec3 n = normalize((u_model * vec4(normal, 0.0)).xyz);
    v_normal = n;
    v_lighting = max(dot(n, normalize(u_lightDir)), 0.1);
}
"""


FRAG_SHADER = """
#version 330 core
in vec3 v_normal;
in float v_lighting;
out vec4 fragColor;

void main() {
    vec3 base = vec3(0.45, 0.65, 0.85);
    vec3 color = base * v_lighting;
    fragColor = vec4(color, 1.0);
}
"""


@dataclass
class _MeshData:
    vertices: np.ndarray  # (N, 3) float32
    normals: np.ndarray   # (N, 3) float32
    indices: np.ndarray   # (M,) uint32
    center: np.ndarray    # (3,) float32
    radius: float         # scalar


class ModelViewer(QOpenGLWidget):
    """A simple OpenGL-based model viewer with orbit controls."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: Optional[Path] = None
        self._mesh: Optional[_MeshData] = None
        self._program: Optional[QOpenGLShaderProgram] = None
        self._last_pos = QPoint()
        self._yaw = 0.0
        self._pitch = 0.0
        self._distance = 3.5
        self._auto_fit_applied = False
        self._user_modified = False
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_path(self, path: Path) -> None:
        self._path = path
        self._load_mesh()
        self.update()

    # ------------------------------------------------------------------
    # QOpenGLWidget overrides
    # ------------------------------------------------------------------
    def initializeGL(self) -> None:  # pragma: no cover - requires GL context
        self._program = QOpenGLShaderProgram(self.context())
        self._program.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT_SHADER)
        self._program.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG_SHADER)
        self._program.link()

        # Use generic and extra function sets; ensure they are initialized.
        self.funcs = self.context().functions()
        try:
            self.funcs.initializeOpenGLFunctions()
        except Exception:
            pass
        try:
            self.extra = self.context().extraFunctions()
            self.extra.initializeOpenGLFunctions()
        except Exception:
            self.extra = None
        # OpenGL enums (define locally to avoid PyOpenGL dependency)
        self._GL_DEPTH_TEST = 0x0B71
        self._GL_CULL_FACE = 0x0B44
        self._GL_COLOR_BUFFER_BIT = 0x00004000
        self._GL_DEPTH_BUFFER_BIT = 0x00000100
        self._GL_ARRAY_BUFFER = 0x8892
        self._GL_ELEMENT_ARRAY_BUFFER = 0x8893
        self._GL_STATIC_DRAW = 0x88E4
        self._GL_FLOAT = 0x1406
        self._GL_TRIANGLES = 0x0004
        self._GL_UNSIGNED_INT = 0x1405

        self.funcs.glEnable(self._GL_DEPTH_TEST)
        self.funcs.glEnable(self._GL_CULL_FACE)

    def resizeGL(self, w: int, h: int) -> None:  # pragma: no cover
        del w, h
        if self._mesh is not None and not self._user_modified:
            self._fit_to_view()

    def paintGL(self) -> None:  # pragma: no cover - visual
        gl = self.funcs
        gl.glViewport(0, 0, self.width(), self.height())
        gl.glClearColor(18/255, 22/255, 28/255, 1.0)
        gl.glClear(self._GL_COLOR_BUFFER_BIT | self._GL_DEPTH_BUFFER_BIT)

        if self._program is None or self._mesh is None:
            return

        mvp, model = self._compute_matrices()

        self._program.bind()
        self._program.setUniformValue("u_mvp", mvp)
        self._program.setUniformValue("u_model", model)
        self._program.setUniformValue("u_lightDir", 0.45, 0.55, 0.7)

        # Upload buffers on each paint for simplicity (small meshes typical)
        vertices = self._mesh.vertices.astype(np.float32)
        normals = self._mesh.normals.astype(np.float32)
        indices = self._mesh.indices.astype(np.uint32)

        # Interleave position and normal
        interleaved = np.hstack([vertices, normals]).astype(np.float32)
        stride = interleaved.shape[1] * 4

        vao = QOpenGLVertexArrayObject(self)
        vao.create(); vao.bind()

        # VBO with interleaved positions and normals
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create(); vbo.bind()
        vbo.allocate(interleaved.tobytes(), interleaved.nbytes)

        # EBO for indices
        ebo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
        ebo.create(); ebo.bind()
        ebo.allocate(indices.tobytes(), indices.nbytes)

        # Attributes
        pos_loc = 0
        nrm_loc = 1
        self._program.enableAttributeArray(pos_loc)
        self._program.setAttributeBuffer(pos_loc, self._GL_FLOAT, 0, 3, stride)
        self._program.enableAttributeArray(nrm_loc)
        self._program.setAttributeBuffer(nrm_loc, self._GL_FLOAT, 12, 3, stride)

        # Draw indexed geometry via extra functions when available
        count = int(indices.size)
        drew = False
        if self.extra is not None:
            try:
                self.extra.glDrawElements(self._GL_TRIANGLES, count, self._GL_UNSIGNED_INT, 0)
                drew = True
            except Exception:
                drew = False
        if not drew:
            # Fallback: map to non-indexed draw without leaving a broken state
            tri_indices = indices.reshape(-1)
            flat_count = int(tri_indices.shape[0])
            # Reuse the same VBO by reallocating flat data
            flat_interleaved = np.hstack([vertices[tri_indices], normals[tri_indices]]).astype(np.float32)
            vbo.bind(); vbo.allocate(flat_interleaved.tobytes(), flat_interleaved.nbytes)
            self._program.setAttributeBuffer(pos_loc, self._GL_FLOAT, 0, 3, flat_interleaved.shape[1]*4)
            self._program.setAttributeBuffer(nrm_loc, self._GL_FLOAT, 12, 3, flat_interleaved.shape[1]*4)
            self.funcs.glDrawArrays(self._GL_TRIANGLES, 0, flat_count)

        # Cleanup
        self._program.disableAttributeArray(pos_loc)
        self._program.disableAttributeArray(nrm_loc)
        ebo.release(); ebo.destroy()
        vbo.release(); vbo.destroy()
        vao.release(); vao.destroy()

        self._program.release()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # type: ignore[override]
        self._last_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):  # type: ignore[override]
        delta = event.position().toPoint() - self._last_pos
        self._last_pos = event.position().toPoint()
        if event.buttons() & Qt.LeftButton:
            self._yaw += delta.x() * 0.5
            self._pitch = max(-89.0, min(89.0, self._pitch + delta.y() * 0.5))
            self._user_modified = True
            self.update()
        elif event.buttons() & Qt.RightButton:
            self._pan_x += delta.x() * 0.002 * self._distance
            self._pan_y -= delta.y() * 0.002 * self._distance
            self._user_modified = True
            self.update()

    def wheelEvent(self, event):  # type: ignore[override]
        delta = event.angleDelta().y() / 120.0
        self._distance = float(max(0.2, min(100.0, self._distance * (0.9 ** delta))))
        self._user_modified = True
        self.update()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_matrices(self) -> tuple[QMatrix4x4, QMatrix4x4]:
        w, h = max(1, self.width()), max(1, self.height())
        aspect = w / h

        model = QMatrix4x4()
        model.setToIdentity()

        # Orbit camera around target
        yaw_r = math.radians(self._yaw)
        pitch_r = math.radians(self._pitch)

        cx = math.cos(yaw_r) * math.cos(pitch_r)
        cy = math.sin(pitch_r)
        cz = math.sin(yaw_r) * math.cos(pitch_r)

        eye = np.array([cx, cy, cz], dtype=np.float32) * self._distance
        target = np.array([self._pan_x, self._pan_y, 0.0], dtype=np.float32)

        view = QMatrix4x4()
        view.lookAt(
            self._to_vec3(eye + target),
            self._to_vec3(target),
            self._to_vec3(np.array([0.0, 1.0, 0.0], dtype=np.float32)),
        )

        proj = QMatrix4x4()
        proj.perspective(45.0, float(aspect), 0.01, 1000.0)

        # Fit model within unit cube using its center/radius
        if self._mesh is not None:
            s = 1.0 / max(self._mesh.radius, 1e-6)
            model.scale(s)
            model.translate(-float(self._mesh.center[0]), -float(self._mesh.center[1]), -float(self._mesh.center[2]))

        mvp = proj * view * model
        return mvp, model

    def _to_vec3(self, arr: np.ndarray) -> QVector3D:
        return QVector3D(float(arr[0]), float(arr[1]), float(arr[2]))

    def _load_mesh(self) -> None:
        self._mesh = None
        if self._path is None or not self._path.exists():
            return

        suffix = self._path.suffix.lower()
        vertices: Optional[np.ndarray] = None
        faces: Optional[np.ndarray] = None

        if suffix in {".stl", ".obj"} and trimesh is not None:
            try:
                mesh = trimesh.load(self._path, force="mesh")  # type: ignore[call-arg]
                if hasattr(mesh, "geometry") and mesh.geometry:
                    mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))  # type: ignore[assignment]
                if hasattr(mesh, "vertices") and hasattr(mesh, "faces") and len(mesh.vertices) and len(mesh.faces):
                    vertices = np.asarray(mesh.vertices, dtype=np.float32)
                    faces = np.asarray(mesh.faces, dtype=np.int32)
            except Exception:
                vertices = None
                faces = None

        if vertices is None or faces is None:
            if suffix in {".step", ".stp"}:
                meta = extract_step_metadata(self._path)
                mins = meta.get("bounding_box_min") or [0, 0, 0]
                maxs = meta.get("bounding_box_max") or [1, 1, 1]
                v, f = self._build_box(np.array(mins, dtype=float), np.array(maxs, dtype=float))
                vertices, faces = v, f

        if vertices is None or faces is None:
            return

        # Compute normals
        normals = np.zeros_like(vertices, dtype=np.float32)
        tris = vertices[faces]
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        lens = np.linalg.norm(n, axis=1)
        valid = lens > 0
        n[valid] /= lens[valid][:, None]
        for i, face in enumerate(faces):
            normals[face] += n[i]
        lens = np.linalg.norm(normals, axis=1)
        valid = lens > 0
        normals[valid] /= lens[valid][:, None]

        center = vertices.mean(axis=0).astype(np.float32)
        radius = float(np.linalg.norm(vertices - center, axis=1).max())

        self._mesh = _MeshData(
            vertices=vertices.astype(np.float32),
            normals=normals.astype(np.float32),
            indices=faces.astype(np.uint32).ravel(),
            center=center,
            radius=radius if radius > 0 else 1.0,
        )
        # Reset view and apply auto-fit on new model
        self._yaw = 0.0
        self._pitch = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._user_modified = False
        self._auto_fit_applied = False
        self._fit_to_view()

    def _build_box(self, mins: np.ndarray, maxs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x0, y0, z0 = mins
        x1, y1, z1 = maxs
        verts = np.array(
            [
                (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
            ], dtype=np.float32
        )
        faces = np.array(
            [
                (0,1,2),(0,2,3), (4,5,6),(4,6,7), (0,1,5),(0,5,4),
                (2,3,7),(2,7,6), (1,2,6),(1,6,5), (3,0,4),(3,4,7),
            ], dtype=np.int32
        )
        return verts, faces

    def _fit_to_view(self) -> None:
        # Compute a distance that fits a unit-radius sphere within the viewport
        # after model normalization in _compute_matrices().
        w, h = max(1, self.width()), max(1, self.height())
        aspect = w / h
        vfov_deg = 45.0
        vfov = math.radians(vfov_deg)
        tan_v = math.tan(vfov / 2.0)
        tan_h = tan_v * aspect
        # Distance required to fit radius=1 vertically/horizontally
        d_v = 1.0 / max(tan_v, 1e-6)
        d_h = 1.0 / max(tan_h, 1e-6)
        self._distance = float(max(d_v, d_h) * 1.15)
        self._auto_fit_applied = True

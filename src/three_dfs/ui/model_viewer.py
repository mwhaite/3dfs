"""Interactive 3D model viewer using QOpenGLWidget.

Minimal real-time viewport to orbit/pan/zoom common mesh formats such as
STL/OBJ/PLY/GLTF/GLB/3MF (via :mod:`trimesh`), STEP bounding boxes, optional
FBX files, and reconstructed toolpaths for supported G-code programs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMatrix4x4, QVector3D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..gcode import GCodePreviewError, analyze_gcode_program
from ..importer import GCODE_EXTENSIONS, extract_step_metadata

try:  # pragma: no cover - exercised at runtime
    import trimesh  # type: ignore
except Exception:  # pragma: no cover - fallback if unavailable
    trimesh = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import fbx  # type: ignore
except Exception:  # pragma: no cover - gracefully ignore when absent
    fbx = None  # type: ignore


VERT_SHADER = """
#version 330 core
layout(location = 0) in vec3 position;
layout(location = 1) in vec3 normal;

uniform mat4 u_mvp;
uniform mat4 u_model;
uniform vec3 u_lightDir;
uniform vec3 u_fillDir;
uniform float u_ambient;

out vec3 v_normal;
out float v_lighting;

void main() {
    gl_Position = u_mvp * vec4(position, 1.0);
    vec3 n = normalize((u_model * vec4(normal, 0.0)).xyz);
    v_normal = n;
    float key = max(dot(n, normalize(u_lightDir)), 0.0);
    float fill = max(dot(n, normalize(u_fillDir)), 0.0) * 0.7;
    v_lighting = max(u_ambient, (key * 0.9) + fill);
}
"""


FRAG_SHADER = """
#version 330 core
in vec3 v_normal;
in float v_lighting;
out vec4 fragColor;

void main() {
    vec3 base = vec3(0.72, 0.82, 0.94);
    vec3 color = base * clamp(v_lighting, 0.0, 1.8);
    fragColor = vec4(color, 1.0);
}
"""


@dataclass
class _MeshData:
    vertices: np.ndarray  # (N, 3) float32
    normals: np.ndarray  # (N, 3) float32
    indices: np.ndarray  # (M,) uint32
    center: np.ndarray  # (3,) float32
    radius: float  # scalar


def _load_with_trimesh_mesh(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    if trimesh is None:
        return None
    try:
        mesh = trimesh.load(path, force="mesh")  # type: ignore[call-arg]
    except Exception:
        return None

    if hasattr(mesh, "geometry") and getattr(mesh, "geometry", None):
        try:
            mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))  # type: ignore[assignment]
        except Exception:
            return None

    if not hasattr(mesh, "vertices") or not hasattr(mesh, "faces"):
        return None

    try:
        has_data = len(mesh.vertices) and len(mesh.faces)
    except Exception:
        return None

    if not has_data:
        return None

    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.int32)
    if vertices.size == 0 or faces.size == 0:
        return None
    return vertices, faces


def _load_fbx_mesh_arrays(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    if fbx is None:
        return None

    manager = fbx.FbxManager.Create()
    if manager is None:
        return None
    try:
        ios = fbx.FbxIOSettings.Create(manager, fbx.IOSROOT)
        manager.SetIOSettings(ios)

        importer = fbx.FbxImporter.Create(manager, "")
        if importer is None:
            return None
        try:
            if not importer.Initialize(str(path), -1, manager.GetIOSettings()):
                return None

            scene = fbx.FbxScene.Create(manager, "scene")
            if scene is None:
                return None
            if not importer.Import(scene):
                return None

            converter = fbx.FbxGeometryConverter(manager)
            try:
                converter.Triangulate(scene, True)
            except Exception:
                pass

            root = scene.GetRootNode()
            if root is None:
                return None

            vertices: list[np.ndarray] = []
            faces: list[list[int]] = []
            offset = 0

            def visit(node) -> None:  # type: ignore[no-untyped-def]
                nonlocal offset
                if node is None:
                    return
                attr = node.GetNodeAttribute()
                if attr is not None and attr.GetAttributeType() == fbx.FbxNodeAttribute.eMesh:
                    mesh = node.GetMesh()
                    if mesh is not None:
                        count = mesh.GetControlPointsCount()
                        if count:
                            control_points = mesh.GetControlPoints()
                            verts = np.array(
                                [
                                    (
                                        control_points[i][0],
                                        control_points[i][1],
                                        control_points[i][2],
                                    )
                                    for i in range(count)
                                ],
                                dtype=np.float32,
                            )
                            vertices.append(verts)
                            for poly_index in range(mesh.GetPolygonCount()):
                                if mesh.GetPolygonSize(poly_index) == 3:
                                    faces.append(
                                        [
                                            mesh.GetPolygonVertex(poly_index, 0) + offset,
                                            mesh.GetPolygonVertex(poly_index, 1) + offset,
                                            mesh.GetPolygonVertex(poly_index, 2) + offset,
                                        ]
                                    )
                            offset += count
                for i in range(node.GetChildCount()):
                    visit(node.GetChild(i))

            visit(root)

            if not vertices or not faces:
                return None

            all_vertices = np.vstack(vertices).astype(np.float32)
            all_faces = np.asarray(faces, dtype=np.int32)
            return all_vertices, all_faces
        finally:
            importer.Destroy()
    finally:
        manager.Destroy()
    return None


def _build_box_arrays(
    mins: np.ndarray,
    maxs: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, z0 = mins
    x1, y1, z1 = maxs
    verts = np.array(
        [
            (x0, y0, z0),
            (x1, y0, z0),
            (x1, y1, z0),
            (x0, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x1, y1, z1),
            (x0, y1, z1),
        ],
        dtype=np.float32,
    )
    faces = np.array(
        [
            (0, 1, 2),
            (0, 2, 3),
            (4, 5, 6),
            (4, 6, 7),
            (0, 1, 5),
            (0, 5, 4),
            (2, 3, 7),
            (2, 7, 6),
            (1, 2, 6),
            (1, 6, 5),
            (3, 0, 4),
            (3, 4, 7),
        ],
        dtype=np.int32,
    )
    return verts, faces


def _build_toolpath_arrays(path: Path, *, line_width: float = 0.35) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        analysis = analyze_gcode_program(path)
    except GCodePreviewError:
        return None

    segments = analysis.segments
    if not segments:
        return None

    vertices: list[np.ndarray] = []
    faces: list[tuple[int, int, int]] = []
    half_width = max(line_width, 1e-3) / 2.0

    for segment in segments:
        start = np.array(segment.start, dtype=np.float32)
        end = np.array(segment.end, dtype=np.float32)
        direction = end - start
        length = np.linalg.norm(direction)
        if not np.isfinite(length) or length <= 0:
            continue

        axis = direction / length
        up = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        if abs(float(np.dot(axis, up))) > 0.95:
            up = np.array([0.0, 1.0, 0.0], dtype=np.float32)

        side = np.cross(axis, up)
        side_length = np.linalg.norm(side)
        if side_length <= 1e-6:
            up = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            side = np.cross(axis, up)
            side_length = np.linalg.norm(side)
            if side_length <= 1e-6:
                continue
        side /= side_length
        up_vec = np.cross(side, axis)
        up_length = np.linalg.norm(up_vec)
        if up_length <= 1e-6:
            continue
        up_vec /= up_length

        offsets = (
            (side * half_width) + (up_vec * half_width),
            (-side * half_width) + (up_vec * half_width),
            (-side * half_width) + (-up_vec * half_width),
            (side * half_width) + (-up_vec * half_width),
        )
        segment_vertices = [start + offset for offset in offsets]
        segment_vertices.extend(end + offset for offset in offsets)
        base_index = len(vertices)
        vertices.extend(segment_vertices)

        quads = (
            (0, 1, 2, 3),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        )
        for quad in quads:
            a, b, c, d = (base_index + idx for idx in quad)
            faces.append((a, b, c))
            faces.append((a, c, d))

    if not vertices or not faces:
        return None

    return (
        np.asarray(vertices, dtype=np.float32),
        np.asarray(faces, dtype=np.int32),
    )


def load_mesh_data(path: Path | None) -> tuple[_MeshData | None, str | None]:
    if path is None:
        return None, "No model selected."
    if not path.exists():
        return None, "Model file does not exist."

    suffix = path.suffix.lower()
    vertices: np.ndarray | None = None
    faces: np.ndarray | None = None
    error_message: str | None = None

    if suffix in {".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf"}:
        if trimesh is None:
            return (
                None,
                "Install the `trimesh` dependency to enable STL/OBJ/PLY/GLB/GLTF/3MF previews.",
            )
        trimesh_result = _load_with_trimesh_mesh(path)
        if trimesh_result is not None:
            vertices, faces = trimesh_result
        else:
            label = suffix.lstrip(".").upper() or "3D"
            error_message = f"Could not parse {label} mesh."

    if (vertices is None or faces is None) and suffix == ".fbx":
        if fbx is None:
            return (
                None,
                "Autodesk FBX SDK is not available, so FBX previews are disabled.",
            )
        fbx_results = _load_fbx_mesh_arrays(path)
        if fbx_results is not None:
            vertices, faces = fbx_results
        elif error_message is None:
            error_message = "Could not parse FBX mesh."

    if vertices is None or faces is None:
        if suffix in {".step", ".stp"}:
            meta = extract_step_metadata(path)
            mins = meta.get("bounding_box_min") or [0, 0, 0]
            maxs = meta.get("bounding_box_max") or [1, 1, 1]
            v, f = _build_box_arrays(
                np.array(mins, dtype=float),
                np.array(maxs, dtype=float),
            )
            vertices, faces = v, f

    if vertices is None or faces is None:
        if suffix in GCODE_EXTENSIONS:
            toolpath = _build_toolpath_arrays(path)
            if toolpath is not None:
                vertices, faces = toolpath
            elif error_message is None:
                error_message = "Could not parse G-code toolpath."

    if vertices is None or faces is None:
        if error_message is None:
            label = suffix.lstrip(".").upper() or "this format"
            error_message = f"3D preview is not available for {label}."
        return None, error_message

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

    mesh = _MeshData(
        vertices=vertices.astype(np.float32),
        normals=normals.astype(np.float32),
        indices=faces.astype(np.uint32).ravel(),
        center=center,
        radius=radius if radius > 0 else 1.0,
    )
    return mesh, None


class ModelViewer(QOpenGLWidget):
    """A simple OpenGL-based model viewer with orbit controls."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._mesh: _MeshData | None = None
        self._program: QOpenGLShaderProgram | None = None
        self._last_pos = QPoint()
        self._yaw = 0.0
        self._pitch = 0.0
        self._distance = 3.5
        self._auto_fit_applied = False
        self._user_modified = False
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._last_error_message: str | None = None
        self._vao: QOpenGLVertexArrayObject | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._ebo: QOpenGLBuffer | None = None
        self._draw_mode: str | None = None
        self._draw_count: int = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_path(self, path: Path) -> None:
        self._path = path
        success, error = self._load_mesh()
        if not success:
            self._last_error_message = error
            raise ValueError(error or "Unable to load 3D model preview.")
        self._last_error_message = None
        self.update()

    @property
    def last_error_message(self) -> str | None:
        return self._last_error_message

    def clear(self) -> None:
        self._mesh = None
        self._path = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._user_modified = False
        self._auto_fit_applied = False
        self._release_buffers()
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

    def _release_buffers(self) -> None:
        """Dispose of any existing GL buffers/arrays."""

        if self._ebo is not None:
            try:
                self._ebo.destroy()
            except Exception:
                pass
            self._ebo = None

        if self._vbo is not None:
            try:
                self._vbo.destroy()
            except Exception:
                pass
            self._vbo = None

        if self._vao is not None:
            try:
                self._vao.destroy()
            except Exception:
                pass
            self._vao = None

        self._draw_mode = None
        self._draw_count = 0

    def _ensure_mesh_buffers(self) -> bool:
        """Upload mesh data to GPU buffers once and reuse each frame."""

        if self._mesh is None or self._program is None:
            return False

        if (
            self._vao is not None
            and self._vbo is not None
            and (self._ebo is not None or self._draw_mode == "arrays")
        ):
            return True

        self._release_buffers()

        vertices = self._mesh.vertices.astype(np.float32)
        normals = self._mesh.normals.astype(np.float32)
        indices = self._mesh.indices.astype(np.uint32)

        interleaved = np.hstack([vertices, normals]).astype(np.float32)
        stride = interleaved.shape[1] * 4

        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(interleaved.tobytes(), interleaved.nbytes)

        pos_loc = 0
        nrm_loc = 1
        self._program.enableAttributeArray(pos_loc)
        self._program.setAttributeBuffer(
            pos_loc,
            self._GL_FLOAT,
            0,
            3,
            stride,
        )
        self._program.enableAttributeArray(nrm_loc)
        self._program.setAttributeBuffer(
            nrm_loc,
            self._GL_FLOAT,
            12,
            3,
            stride,
        )

        # Prefer indexed drawing when available; fallback to non-indexed data when
        # glDrawElements cannot be used.
        if self.extra is not None:
            self._ebo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
            self._ebo.create()
            self._ebo.bind()
            self._ebo.allocate(indices.tobytes(), indices.nbytes)
            self._draw_mode = "elements"
            self._draw_count = int(indices.size)
        else:
            tri_indices = indices.reshape(-1)
            flat_interleaved = np.hstack([vertices[tri_indices], normals[tri_indices]]).astype(
                np.float32
            )
            flat_stride = flat_interleaved.shape[1] * 4
            self._vbo.bind()
            self._vbo.allocate(flat_interleaved.tobytes(), flat_interleaved.nbytes)
            self._program.setAttributeBuffer(
                pos_loc,
                self._GL_FLOAT,
                0,
                3,
                flat_stride,
            )
            self._program.setAttributeBuffer(
                nrm_loc,
                self._GL_FLOAT,
                12,
                3,
                flat_stride,
            )
            self._draw_mode = "arrays"
            self._draw_count = int(tri_indices.shape[0])

        self._vao.release()
        self._vbo.release()
        if self._ebo is not None:
            self._ebo.release()

        return True

    def resizeGL(self, w: int, h: int) -> None:  # pragma: no cover
        del w, h
        if self._mesh is not None and not self._user_modified:
            self._fit_to_view()

    def paintGL(self) -> None:  # pragma: no cover - visual
        gl = self.funcs
        gl.glViewport(0, 0, self.width(), self.height())
        gl.glClearColor(36 / 255, 42 / 255, 52 / 255, 1.0)
        gl.glClear(self._GL_COLOR_BUFFER_BIT | self._GL_DEPTH_BUFFER_BIT)

        if self._program is None or self._mesh is None:
            return

        mvp, model = self._compute_matrices()

        self._program.bind()
        self._program.setUniformValue("u_mvp", mvp)
        self._program.setUniformValue("u_model", model)
        self._program.setUniformValue("u_lightDir", 0.35, 0.65, 0.7)
        self._program.setUniformValue("u_fillDir", -0.55, -0.25, 0.45)
        ambient_loc = self._program.uniformLocation("u_ambient")
        if ambient_loc != -1:
            self._program.setUniformValue(ambient_loc, 0.55)

        if not self._ensure_mesh_buffers():
            self._program.release()
            return

        if self._vao is not None:
            self._vao.bind()

        if self._draw_mode == "elements" and self._draw_count > 0:
            self.funcs.glDrawElements(
                self._GL_TRIANGLES,
                self._draw_count,
                self._GL_UNSIGNED_INT,
                0,
            )
        elif self._draw_mode == "arrays" and self._draw_count > 0:
            self.funcs.glDrawArrays(self._GL_TRIANGLES, 0, self._draw_count)

        if self._vao is not None:
            self._vao.release()

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
            self._pitch = max(
                -89.0,
                min(89.0, self._pitch + delta.y() * 0.5),
            )
            self._user_modified = True
            self.update()
        elif event.buttons() & Qt.RightButton:
            self._pan_x += delta.x() * 0.002 * self._distance
            self._pan_y -= delta.y() * 0.002 * self._distance
            self._user_modified = True
            self.update()

    def wheelEvent(self, event):  # type: ignore[override]
        delta = event.angleDelta().y() / 120.0
        self._distance = float(max(0.2, min(100.0, self._distance * (0.9**delta))))
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
            model.translate(
                -float(self._mesh.center[0]),
                -float(self._mesh.center[1]),
                -float(self._mesh.center[2]),
            )

        mvp = proj * view * model
        return mvp, model

    def _to_vec3(self, arr: np.ndarray) -> QVector3D:
        return QVector3D(float(arr[0]), float(arr[1]), float(arr[2]))

    def _load_mesh(self) -> tuple[bool, str | None]:
        mesh, error = load_mesh_data(self._path)
        if mesh is None:
            self._mesh = None
            return False, error
        self.set_mesh_data(mesh, self._path)
        return True, None

    def set_mesh_data(self, mesh: _MeshData, path: Path | None = None) -> None:
        self._release_buffers()
        self._mesh = mesh
        self._path = path
        self._yaw = 0.0
        self._pitch = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._user_modified = False
        self._auto_fit_applied = False
        self._fit_to_view()
        self.update()

    def _load_with_trimesh(self, path: Path) -> tuple[np.ndarray, np.ndarray] | None:
        if trimesh is None:
            return None
        try:
            mesh = trimesh.load(path, force="mesh")  # type: ignore[call-arg]
        except Exception:
            return None

        if hasattr(mesh, "geometry") and getattr(mesh, "geometry", None):
            try:
                mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))  # type: ignore[assignment]
            except Exception:
                return None

        if not hasattr(mesh, "vertices") or not hasattr(mesh, "faces"):
            return None

        try:
            has_data = len(mesh.vertices) and len(mesh.faces)
        except Exception:
            return None

        if not has_data:
            return None

        vertices = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.int32)
        if vertices.size == 0 or faces.size == 0:
            return None
        return vertices, faces

    def _load_fbx_mesh(self, path: Path) -> tuple[np.ndarray, np.ndarray] | None:
        if fbx is None:
            return None

        manager = fbx.FbxManager.Create()
        if manager is None:
            return None
        try:
            ios = fbx.FbxIOSettings.Create(manager, fbx.IOSROOT)
            manager.SetIOSettings(ios)

            importer = fbx.FbxImporter.Create(manager, "")
            if importer is None:
                return None
            try:
                if not importer.Initialize(str(path), -1, manager.GetIOSettings()):
                    return None

                scene = fbx.FbxScene.Create(manager, "scene")
                if scene is None:
                    return None
                if not importer.Import(scene):
                    return None

                converter = fbx.FbxGeometryConverter(manager)
                try:
                    converter.Triangulate(scene, True)
                except Exception:
                    # Even if triangulation fails we attempt to proceed.
                    pass

                root = scene.GetRootNode()
                if root is None:
                    return None

                vertices: list[np.ndarray] = []
                faces: list[list[int]] = []
                offset = 0

                def visit(node) -> None:  # type: ignore[no-untyped-def]
                    nonlocal offset
                    if node is None:
                        return
                    attr = node.GetNodeAttribute()
                    if attr is not None and attr.GetAttributeType() == fbx.FbxNodeAttribute.eMesh:
                        mesh = node.GetMesh()
                        if mesh is not None:
                            count = mesh.GetControlPointsCount()
                            if count:
                                control_points = mesh.GetControlPoints()
                                verts = np.array(
                                    [
                                        (
                                            float(control_points[i][0]),
                                            float(control_points[i][1]),
                                            float(control_points[i][2]),
                                        )
                                        for i in range(count)
                                    ],
                                    dtype=np.float32,
                                )
                                poly_count = mesh.GetPolygonCount()
                                tris: list[list[int]] = []
                                for poly in range(poly_count):
                                    poly_size = mesh.GetPolygonSize(poly)
                                    if poly_size < 3:
                                        continue
                                    indices = [int(mesh.GetPolygonVertex(poly, j)) for j in range(poly_size)]
                                    for j in range(1, len(indices) - 1):
                                        tris.append(
                                            [
                                                indices[0] + offset,
                                                indices[j] + offset,
                                                indices[j + 1] + offset,
                                            ]
                                        )
                                if verts.size and tris:
                                    vertices.append(verts)
                                    faces.extend(tris)
                                    offset += verts.shape[0]
                    for i in range(node.GetChildCount()):
                        visit(node.GetChild(i))

                visit(root)

                if not vertices or not faces:
                    return None
                all_vertices = np.vstack(vertices).astype(np.float32)
                all_faces = np.asarray(faces, dtype=np.int32)
                return all_vertices, all_faces
            finally:
                importer.Destroy()
        finally:
            manager.Destroy()
        return None

    def _build_box(
        self,
        mins: np.ndarray,
        maxs: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        x0, y0, z0 = mins
        x1, y1, z1 = maxs
        verts = np.array(
            [
                (x0, y0, z0),
                (x1, y0, z0),
                (x1, y1, z0),
                (x0, y1, z0),
                (x0, y0, z1),
                (x1, y0, z1),
                (x1, y1, z1),
                (x0, y1, z1),
            ],
            dtype=np.float32,
        )
        faces = np.array(
            [
                (0, 1, 2),
                (0, 2, 3),
                (4, 5, 6),
                (4, 6, 7),
                (0, 1, 5),
                (0, 5, 4),
                (2, 3, 7),
                (2, 7, 6),
                (1, 2, 6),
                (1, 6, 5),
                (3, 0, 4),
                (3, 4, 7),
            ],
            dtype=np.int32,
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

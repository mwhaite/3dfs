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
from PySide6.QtCore import QObject, QPoint, QRunnable, QSettings, Qt, QThreadPool, Signal, Slot
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

# Maximum number of triangles allowed before decimation is applied
MAX_TRIANGLES_FOR_PERFORMANCE = 100000  # Configurable maximum for performance

# Quality levels for mesh simplification
MESH_QUALITY_SETTINGS = {
    "high": 150000,  # High quality - up to 150k triangles
    "medium": 100000,  # Medium quality - up to 100k triangles
    "low": 50000,  # Low quality - up to 50k triangles
    "very_low": 25000,  # Very low quality - up to 25k triangles
}

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


def _simplify_mesh_if_needed(
    vertices: np.ndarray, faces: np.ndarray, max_triangles: int = MAX_TRIANGLES_FOR_PERFORMANCE
) -> tuple[np.ndarray, np.ndarray]:
    """
    Simplify mesh if it exceeds the maximum triangle count.

    Args:
        vertices: The mesh vertices as a numpy array of shape (N, 3)
        faces: The mesh faces as a numpy array of shape (M, 3)
        max_triangles: Maximum number of triangles before simplification

    Returns:
        tuple of (vertices, faces) - possibly simplified
    """
    # If the mesh is already under the limit, return as is
    if len(faces) <= max_triangles:
        return vertices, faces

    # Try to use trimesh for quality simplification if available
    if trimesh is not None:
        try:
            # Create a temporary mesh for simplification
            temp_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)

            # Simplify to the target triangle count with quality reduction
            target_face_count = min(len(faces), max_triangles)
            simplified_mesh = temp_mesh.simplify_quadric_decimation(face_count=target_face_count)

            return simplified_mesh.vertices.astype(np.float32), simplified_mesh.faces.astype(np.int32)
        except Exception as e:
            # If trimesh simplification fails, log the error and return original mesh
            print(f"Trimesh simplification failed: {e}, returning original mesh")
            return vertices, faces  # Don't apply problematic fallback, return original

    # If trimesh is not available (not installed), return original mesh
    # User should install trimesh for proper simplification
    print("trimesh not available - simplification disabled, returning original mesh")
    return vertices, faces


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

    # Simplify complex meshes to improve performance
    vertices, faces = _simplify_mesh_if_needed(vertices, faces)

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


class _MeshLoaderSignals(QObject):
    """Signals emitted by :class:`MeshLoader`."""

    finished = Signal(object)  # Emits the loaded mesh data
    error = Signal(str)  # Emits error message


class MeshLoader(QRunnable):
    """Background task that loads mesh data with quality settings applied."""

    def __init__(self, path: Path, quality: str = "medium"):
        super().__init__()
        self._path = path
        self._quality = quality
        self.signals = _MeshLoaderSignals()

    def run(self) -> None:
        """Perform the mesh loading in a background thread."""
        try:
            # Use the quality-aware loading method
            mesh, error = _load_mesh_with_quality_internal(self._path, quality=self._quality)

            if mesh is not None:
                self.signals.finished.emit((mesh, None))
            else:
                self.signals.finished.emit((None, error or "Unknown error during background loading"))
        except Exception as e:
            self.signals.error.emit(str(e))


def _load_mesh_with_quality_internal(path: Path, *, quality: str = "medium") -> tuple[_MeshData | None, str | None]:
    """Internal function to load mesh with quality settings - can be used by background threads."""
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

    # Get max triangles based on quality setting
    max_triangles = MESH_QUALITY_SETTINGS.get(quality, MESH_QUALITY_SETTINGS["medium"])

    # Simplify complex meshes to improve performance based on quality setting
    vertices, faces = _simplify_mesh_if_needed(vertices, faces, max_triangles=max_triangles)

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
        # Buffer objects for performance optimization
        self._vao = None
        self._vbo = None
        self._ebo = None
        self._buffers_initialized = False  # Track if we've initialized our buffers
        self._current_mesh_signature = None  # Track mesh to know when it changes
        self._use_buffer_cache = True  # Feature flag for safe disable
        self._mesh_quality = "medium"  # Default quality setting (100k triangles)
        # Background loading
        self._thread_pool = QThreadPool.globalInstance()
        self._current_loader: MeshLoader | None = None
        # Load quality setting from persistent storage
        self.load_quality_setting()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_path(self, path: Path) -> None:
        # Cancel any existing background loading task
        if self._current_loader is not None:
            # In a real implementation, we'd cancel the task, but QRunnable cancellation is complex
            # For now, we'll let it run but not process its result
            # A more sophisticated approach would use a cancel token or flag
            pass

        self._path = path

        # Start background loading
        self._start_background_load()

    def _start_background_load(self) -> None:
        """Start loading the mesh in a background thread."""
        if self._path is None:
            return

        # Create a new loader with current quality setting
        self._current_loader = MeshLoader(self._path, self._mesh_quality)

        # Connect signals to handle the result
        self._current_loader.signals.finished.connect(self._on_mesh_loaded)
        self._current_loader.signals.error.connect(self._on_mesh_load_error)

        # Start the task in the thread pool
        self._thread_pool.start(self._current_loader)

    @Slot(object)
    def _on_mesh_loaded(self, result: tuple[_MeshData | None, str | None]) -> None:
        """Handle the result from background mesh loading."""
        mesh, error = result

        if mesh is not None:
            self._mesh = mesh
            self._last_error_message = None
            self.set_mesh_data(mesh, self._path)
        else:
            self._mesh = None
            self._last_error_message = error
            # Optionally emit a signal or update UI to show error

    @Slot(str)
    def _on_mesh_load_error(self, error: str) -> None:
        """Handle errors from background mesh loading."""
        self._last_error_message = error
        # Optionally emit a signal or update UI to show error

    @property
    def last_error_message(self) -> str | None:
        return self._last_error_message

    @property
    def mesh_quality(self) -> str:
        """Get the current mesh quality setting."""
        return self._mesh_quality

    @mesh_quality.setter
    def mesh_quality(self, quality: str) -> None:
        """Set the mesh quality setting (high, medium, low, very_low)."""
        if quality not in MESH_QUALITY_SETTINGS:
            raise ValueError(f"Invalid quality setting. Must be one of: {list(MESH_QUALITY_SETTINGS.keys())}")
        self._mesh_quality = quality
        # Save the setting persistently
        self.save_quality_setting()

    def get_available_quality_settings(self) -> list[str]:
        """Get list of available quality settings."""
        return list(MESH_QUALITY_SETTINGS.keys())

    def load_quality_setting(self) -> None:
        """Load the quality setting from persistent storage."""
        settings = QSettings("3DFS", "ModelViewer")
        saved_quality = settings.value("mesh_quality", "medium", type=str)

        # Validate the loaded setting
        if saved_quality in MESH_QUALITY_SETTINGS:
            self._mesh_quality = saved_quality
        else:
            self._mesh_quality = "medium"  # Fallback to default

    def save_quality_setting(self) -> None:
        """Save the current quality setting to persistent storage."""
        settings = QSettings("3DFS", "ModelViewer")
        settings.setValue("mesh_quality", self._mesh_quality)

    def clear(self) -> None:
        self._mesh = None
        self._path = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._user_modified = False
        self._auto_fit_applied = False
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

        # Initialize our buffer objects now that context is guaranteed available
        if self._use_buffer_cache:
            self._initialize_buffers()

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

        # Try optimized rendering path first
        if self._use_buffer_cache and self._buffers_initialized:
            # Update buffers if mesh has changed
            if self._update_buffers_if_needed():
                # Successfully updated buffers, now render using cached buffers
                if not self._render_with_optimized_buffers():
                    # If optimized rendering fails, fall back to original method
                    self._render_with_original_method()
            else:
                # Buffer update failed, fall back to original method
                self._render_with_original_method()
        else:
            # Use original method if optimization is disabled or not initialized
            self._render_with_original_method()

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

    def _initialize_buffers(self):
        """Initialize buffer objects when OpenGL context is valid."""
        try:
            # Create all buffer objects when context is guaranteed available
            self._vao = QOpenGLVertexArrayObject(self)
            self._vao.create()

            self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._vbo.create()

            self._ebo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
            self._ebo.create()

            self._buffers_initialized = True
        except Exception as e:
            # If buffer creation fails, disable the optimization but don't crash
            print(f"Failed to initialize buffer cache: {e}")
            self._buffers_initialized = False
            self._use_buffer_cache = False

    def _update_buffers_if_needed(self):
        """Update buffer contents if the mesh has changed."""
        if not self._use_buffer_cache or not self._buffers_initialized or self._mesh is None:
            return False

        # Create a signature for the current mesh to detect changes
        # Using id() of the arrays since they should be stable for the same mesh data
        current_signature = (id(self._mesh.vertices), id(self._mesh.normals), id(self._mesh.indices))

        # If mesh or mesh data has changed, update the buffers
        if self._current_mesh_signature != current_signature:
            try:
                # Bind VAO first
                self._vao.bind()

                # Prepare data
                vertices = self._mesh.vertices.astype(np.float32)
                normals = self._mesh.normals.astype(np.float32)
                indices = self._mesh.indices.astype(np.uint32)

                # Interleave position and normal data
                interleaved = np.hstack([vertices, normals]).astype(np.float32)
                stride = interleaved.shape[1] * 4

                # Upload to VBO
                self._vbo.bind()
                self._vbo.allocate(interleaved.tobytes(), interleaved.nbytes)

                # Attributes setup
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

                # Upload indices to EBO
                self._ebo.bind()
                self._ebo.allocate(indices.tobytes(), indices.nbytes)

                # Store the signature to know when it changes next time
                self._current_mesh_signature = current_signature

                return True
            except Exception as e:
                # If buffer update fails, fall back to original method
                print(f"Failed to update buffers: {e}")
                self._use_buffer_cache = False
                return False

        return True  # Buffers are already up to date

    def _render_with_optimized_buffers(self):
        """Render using cached buffers for better performance."""
        if not self._use_buffer_cache or not self._buffers_initialized or self._mesh is None:
            return False

        try:
            # Bind the VAO that has our configured buffers and attributes
            if self._vao is not None:
                self._vao.bind()

            # Get the indices to determine how to draw
            indices = self._mesh.indices.astype(np.uint32)

            # Draw using the cached buffer data
            if self.extra is not None:
                # Use indexed draw if extra functions are available
                self.funcs.glDrawElements(
                    self._GL_TRIANGLES,
                    int(indices.size),
                    self._GL_UNSIGNED_INT,
                    0,
                )
            else:
                # Fallback to arrays
                self.funcs.glDrawArrays(self._GL_TRIANGLES, 0, len(self._mesh.vertices))

            return True
        except Exception:
            # If optimized rendering fails, return False to indicate fallback is needed
            return False

    def _render_with_original_method(self):
        """Render using the original method as a fallback."""
        # Get current mesh data
        vertices = self._mesh.vertices.astype(np.float32)
        normals = self._mesh.normals.astype(np.float32)
        indices = self._mesh.indices.astype(np.uint32)

        # Interleave position and normal
        interleaved = np.hstack([vertices, normals]).astype(np.float32)
        stride = interleaved.shape[1] * 4

        vao = QOpenGLVertexArrayObject(self)
        vao.create()
        vao.bind()

        # VBO with interleaved positions and normals
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        vbo.allocate(interleaved.tobytes(), interleaved.nbytes)

        # EBO for indices
        ebo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
        ebo.create()
        ebo.bind()
        ebo.allocate(indices.tobytes(), indices.nbytes)

        # Attributes
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

        # Draw indexed geometry via extra functions when available
        count = int(indices.size)
        drew = False
        if self.extra is not None:
            try:
                self.extra.glDrawElements(
                    self._GL_TRIANGLES,
                    count,
                    self._GL_UNSIGNED_INT,
                    0,
                )
                drew = True
            except Exception:
                drew = False
        if not drew:
            # Fallback: map to non-indexed draw without leaving a broken state
            tri_indices = indices.reshape(-1)
            flat_count = int(tri_indices.shape[0])
            # Reuse the same VBO by reallocating flat data
            flat_interleaved = np.hstack([vertices[tri_indices], normals[tri_indices]]).astype(np.float32)
            vbo.bind()
            vbo.allocate(flat_interleaved.tobytes(), flat_interleaved.nbytes)
            flat_stride = flat_interleaved.shape[1] * 4
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
            self.funcs.glDrawArrays(self._GL_TRIANGLES, 0, flat_count)

        # Cleanup
        self._program.disableAttributeArray(pos_loc)
        self._program.disableAttributeArray(nrm_loc)
        ebo.release()
        ebo.destroy()
        vbo.release()
        vbo.destroy()
        vao.release()
        vao.destroy()

    def _to_vec3(self, arr: np.ndarray) -> QVector3D:
        return QVector3D(float(arr[0]), float(arr[1]), float(arr[2]))

    def _load_mesh(self) -> tuple[bool, str | None]:
        if self._path is None:
            return False, "No model selected."

        # Load the mesh data using the general function
        mesh, error = load_mesh_data(self._path)
        if mesh is None:
            self._mesh = None
            return False, error

        # If we have access to the original vertices/faces before they were processed by load_mesh_data,
        # we could apply quality settings, but since load_mesh_data includes simplification based on
        # the default MAX_TRIANGLES_FOR_PERFORMANCE, we'll use the quality setting in the
        # simplification function itself.

        # For now, let's create a quality-adjusted load by using our own loading approach
        # that respects the quality setting
        quality_adjusted_mesh = self._load_mesh_with_quality(self._path)

        if quality_adjusted_mesh is None:
            self._mesh = None
            return False, error

        self.set_mesh_data(quality_adjusted_mesh, self._path)
        return True, None

    def _load_mesh_with_quality(self, path: Path) -> tuple[_MeshData | None, str | None]:
        """Load mesh with quality settings applied."""
        # First, load the mesh using the original loading logic but with quality-appropriate max triangles
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

        # Get max triangles based on quality setting
        max_triangles = MESH_QUALITY_SETTINGS.get(self._mesh_quality, MESH_QUALITY_SETTINGS["medium"])

        # Simplify complex meshes to improve performance based on quality setting
        vertices, faces = _simplify_mesh_if_needed(vertices, faces, max_triangles=max_triangles)

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

    def set_mesh_data(self, mesh: _MeshData, path: Path | None = None) -> None:
        self._mesh = mesh
        self._path = path
        self._yaw = 0.0
        self._pitch = 0.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._user_modified = False
        self._auto_fit_applied = False
        # Reset mesh signature to force buffer update with new mesh
        self._current_mesh_signature = None
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

    def cleanup(self):
        """Call this when the widget is being destroyed to properly clean up OpenGL resources."""
        if self._vao is not None:
            try:
                # Only destroy if we're in a valid OpenGL context
                if self.context() is not None and self.context().isValid():
                    self._vao.destroy()
                    if self._vbo is not None:
                        self._vbo.destroy()
                    if self._ebo is not None:
                        self._ebo.destroy()
            except Exception:
                pass  # Don't crash if cleanup fails
        # Reset references
        self._vao = None
        self._vbo = None
        self._ebo = None
        self._buffers_initialized = False
        self._current_mesh_signature = None

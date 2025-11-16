"""Thumbnail generation and caching utilities for 3D assets."""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw

from ..importer import extract_step_metadata, load_trimesh_mesh

if TYPE_CHECKING:  # pragma: no cover - used for type checking only
    from ..storage import AssetRecord

__all__ = [
    "DEFAULT_THUMBNAIL_ROOT",
    "DEFAULT_THUMBNAIL_SIZE",
    "ThumbnailResult",
    "ThumbnailRenderer",
    "ThumbnailCache",
    "ThumbnailManager",
    "ThumbnailGenerationError",
]

logger = logging.getLogger(__name__)

DEFAULT_THUMBNAIL_SIZE: tuple[int, int] = (512, 512)
"""Default pixel dimensions for generated thumbnails."""

DEFAULT_THUMBNAIL_ROOT: Path = Path.home() / ".3dfs" / "thumbnails"
"""Filesystem location where cached thumbnails are stored."""


class ThumbnailGenerationError(RuntimeError):
    """Raised when a thumbnail cannot be produced for an asset."""


@dataclass(slots=True)
class ThumbnailResult:
    """Describe the outcome of a thumbnail generation request."""

    path: Path
    info: dict[str, Any]
    image_bytes: bytes
    updated: bool


class ThumbnailRenderer:
    """Render 3D meshes into 2D thumbnails using a software pipeline."""

    def __init__(
        self,
        *,
        background: tuple[int, int, int, int] = (18, 22, 28, 255),
        base_color: tuple[int, int, int, int] = (120, 170, 220, 255),
        highlight_color: tuple[int, int, int, int] = (180, 220, 255, 255),
        shadow_color: tuple[int, int, int, int] = (70, 110, 160, 255),
    ) -> None:
        self._background = background
        self._base_color = np.array(base_color, dtype=float)
        self._highlight_color = np.array(highlight_color, dtype=float)
        self._shadow_color = np.array(shadow_color, dtype=float)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def render(
        self,
        source_path: Path,
        *,
        metadata: Mapping[str, Any] | None = None,
        size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    ) -> Image.Image:
        """Return a Pillow image representing a thumbnail for *source_path*."""

        mesh_data = self._load_mesh_data(source_path, metadata)
        if mesh_data is None:
            raise ThumbnailGenerationError(f"No renderable mesh data found for {source_path!s}")

        vertices, faces = mesh_data
        return self._render_mesh(vertices, faces, size)

    # ------------------------------------------------------------------
    # Mesh loading helpers
    # ------------------------------------------------------------------
    def _load_mesh_data(
        self,
        source_path: Path,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        suffix = source_path.suffix.lower()

        if suffix in {".stl", ".obj", ".3mf"}:
            mesh = load_trimesh_mesh(source_path)
            if mesh is None:
                return None

            if mesh.vertices is None or mesh.faces is None:
                return None

            vertices = np.asarray(mesh.vertices, dtype=float)
            faces = np.asarray(mesh.faces, dtype=np.int32)
            if not len(vertices) or not len(faces):
                return None
            return vertices, faces

        if suffix in {".step", ".stp"}:
            bounds = self._resolve_bounds(metadata, source_path)
            if bounds is None:
                return None
            vertices, faces = self._build_box_mesh(*bounds)
            return vertices, faces

        mesh = load_trimesh_mesh(source_path)
        if mesh is None or mesh.vertices is None or mesh.faces is None:
            return None
        return (
            np.asarray(mesh.vertices, dtype=float),
            np.asarray(mesh.faces, dtype=np.int32),
        )

    def _resolve_bounds(
        self,
        metadata: Mapping[str, Any] | None,
        source_path: Path,
    ) -> tuple[Sequence[float], Sequence[float]] | None:
        if metadata:
            minimum = metadata.get("bounding_box_min")
            maximum = metadata.get("bounding_box_max")
            bounds = self._coerce_bounds(minimum, maximum)
            if bounds is not None:
                return bounds

        extracted = extract_step_metadata(source_path)
        minimum = extracted.get("bounding_box_min")
        maximum = extracted.get("bounding_box_max")
        return self._coerce_bounds(minimum, maximum)

    def _coerce_bounds(
        self,
        minimum: object,
        maximum: object,
    ) -> tuple[Sequence[float], Sequence[float]] | None:
        try:
            min_components = tuple(float(value) for value in minimum[:3])  # type: ignore[index]
            max_components = tuple(float(value) for value in maximum[:3])  # type: ignore[index]
        except (TypeError, ValueError):
            return None

        return min_components, max_components

    def _build_box_mesh(
        self,
        minimum: Sequence[float],
        maximum: Sequence[float],
    ) -> tuple[np.ndarray, np.ndarray]:
        min_x, min_y, min_z = minimum
        max_x, max_y, max_z = maximum

        if max_x == min_x:
            max_x += 1.0
            min_x -= 1.0
        if max_y == min_y:
            max_y += 1.0
            min_y -= 1.0
        if max_z == min_z:
            max_z += 1.0
            min_z -= 1.0

        vertices = np.array(
            [
                (min_x, min_y, min_z),
                (max_x, min_y, min_z),
                (max_x, max_y, min_z),
                (min_x, max_y, min_z),
                (min_x, min_y, max_z),
                (max_x, min_y, max_z),
                (max_x, max_y, max_z),
                (min_x, max_y, max_z),
            ],
            dtype=float,
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

        return vertices, faces

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------
    def _render_mesh(
        self,
        vertices: np.ndarray,
        faces: np.ndarray,
        size: tuple[int, int],
    ) -> Image.Image:
        width, height = size
        supersample = 2
        canvas_width = max(1, width * supersample)
        canvas_height = max(1, height * supersample)

        verts = np.asarray(vertices, dtype=float)
        faces = np.asarray(faces, dtype=np.int32)
        if verts.ndim != 2 or verts.shape[1] != 3:
            raise ThumbnailGenerationError("Mesh vertices must be 3D points")
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ThumbnailGenerationError("Mesh faces must be triangular")

        centroid = verts.mean(axis=0)
        verts = verts - centroid
        extents = np.linalg.norm(verts, axis=1)
        max_extent = float(extents.max()) if len(extents) else 1.0
        if not np.isfinite(max_extent) or max_extent == 0:
            max_extent = 1.0
        verts = verts / max_extent

        rotation = _compose_rotation_matrix(np.deg2rad(25.0), np.deg2rad(-35.0), np.deg2rad(30.0))
        rotated = verts @ rotation.T

        camera_distance = 3.5
        projected = rotated.copy()
        projected[:, 2] += camera_distance

        with np.errstate(divide="ignore", invalid="ignore"):
            screen = np.zeros((len(projected), 2), dtype=float)
            scale = 0.45 * min(canvas_width, canvas_height)
            screen[:, 0] = projected[:, 0] / projected[:, 2] * scale + canvas_width / 2
            screen[:, 1] = canvas_height / 2 - projected[:, 1] / projected[:, 2] * scale

        triangles = rotated[faces]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0],
            triangles[:, 2] - triangles[:, 0],
        )
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 0
        normals[valid] /= lengths[valid][:, None]
        light_dir = np.array([0.45, 0.55, 0.7], dtype=float)
        light_dir /= np.linalg.norm(light_dir)
        intensity = np.clip(normals @ light_dir, 0.0, 1.0)

        depths = triangles[:, :, 2].mean(axis=1)
        order = np.argsort(depths)

        image = Image.new("RGBA", (canvas_width, canvas_height), self._background)
        draw = ImageDraw.Draw(image, "RGBA")

        for index in order:
            face = faces[index]
            polygon = [(screen[idx, 0], screen[idx, 1]) for idx in face]
            shade = intensity[index]
            color = _lerp_color(self._shadow_color, self._highlight_color, shade)
            color = _lerp_color(color, self._base_color, 0.35)
            draw.polygon(polygon, fill=tuple(int(value) for value in color))

        if supersample > 1:
            image = image.resize((width, height), Image.Resampling.LANCZOS)

        return image


class ThumbnailCache:
    """Persist thumbnails to disk and reuse them when possible."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        renderer: ThumbnailRenderer | None = None,
    ) -> None:
        self._root = Path(root or DEFAULT_THUMBNAIL_ROOT).expanduser()
        self._renderer = renderer or ThumbnailRenderer()

    def get_or_render(
        self,
        source_path: Path,
        *,
        existing_info: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    ) -> ThumbnailResult:
        """Return the thumbnail for *source_path* generating it when needed."""

        if not source_path.exists():
            raise ThumbnailGenerationError(f"{source_path!s} does not exist")

        signature = self._hash_source(source_path)
        cache_path = self._cache_path(signature, size)

        if cache_path.exists():
            image_bytes = cache_path.read_bytes()
            if self._info_matches(existing_info, cache_path, signature, size):
                info = dict(existing_info)  # type: ignore[arg-type]
                return ThumbnailResult(cache_path, info, image_bytes, updated=False)

            info = self._build_info(cache_path, signature, size, existing_info)
            return ThumbnailResult(cache_path, info, image_bytes, updated=True)

        image = self._renderer.render(source_path, metadata=metadata, size=size)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = buffer.getvalue()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
        info = self._build_info(cache_path, signature, size, existing_info)

        return ThumbnailResult(cache_path, info, payload, updated=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _cache_path(self, signature: str, size: tuple[int, int]) -> Path:
        filename = f"{signature}_{size[0]}x{size[1]}.png"
        return self._root / filename

    def _hash_source(self, source_path: Path) -> str:
        digest = hashlib.blake2s(digest_size=16)

        try:
            with source_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError as exc:  # pragma: no cover - surface descriptive error
            raise ThumbnailGenerationError(f"Unable to read {source_path!s}: {exc}") from exc

        return digest.hexdigest()

    def _info_matches(
        self,
        info: Mapping[str, Any] | None,
        cache_path: Path,
        signature: str,
        size: tuple[int, int],
    ) -> bool:
        # Be defensive: if the structure is unexpected, treat as non-matching.
        try:
            if info is None:
                return False

            stored_hash = info.get("source_hash")
            stored_path = info.get("path")
            stored_size = info.get("size")

            if stored_hash != signature:
                return False

            # Accept either a 2-sequence of ints or strings that can be coerced.
            size_list = list(stored_size or [])
            if len(size_list) != 2:
                return False
            try:
                width = int(size_list[0])
                height = int(size_list[1])
            except (TypeError, ValueError):
                return False
            if [width, height] != [int(size[0]), int(size[1])]:
                return False

            if not stored_path:
                return False

            try:
                resolved = Path(str(stored_path)).expanduser().resolve(strict=False)
            except Exception:
                return False

            return resolved == cache_path.resolve(strict=False)
        except Exception:
            return False

    def _build_info(
        self,
        cache_path: Path,
        signature: str,
        size: tuple[int, int],
        existing_info: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        info = {
            "path": cache_path.as_posix(),
            "source_hash": signature,
            "size": [int(size[0]), int(size[1])],
            "generated_at": datetime.now(UTC).isoformat(),
        }

        if self._info_matches(existing_info, cache_path, signature, size):
            info["generated_at"] = str(existing_info.get("generated_at"))

        return info


class ThumbnailManager:
    """High level helper that orchestrates thumbnail generation for assets."""

    def __init__(self, cache: ThumbnailCache | None = None) -> None:
        self._cache = cache or ThumbnailCache()

    def render_for_asset(
        self,
        asset: AssetRecord,
        *,
        size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    ) -> ThumbnailResult:
        source_path = self._resolve_source_path(asset)
        metadata = getattr(asset, "metadata", {}) or {}
        existing = metadata.get("thumbnail") if isinstance(metadata, Mapping) else None
        try:
            return self._cache.get_or_render(
                source_path,
                existing_info=existing if isinstance(existing, Mapping) else None,
                metadata=metadata if isinstance(metadata, Mapping) else None,
                size=size,
            )
        except TypeError as exc:
            # Backward-compat: tolerate caches without a "metadata" parameter.
            if "metadata" in str(exc):
                return self._cache.get_or_render(
                    source_path,
                    existing_info=existing if isinstance(existing, Mapping) else None,
                    size=size,
                )
            raise

    def _resolve_source_path(self, asset: AssetRecord) -> Path:
        metadata = getattr(asset, "metadata", {}) or {}
        candidates: list[object] = []
        if isinstance(metadata, Mapping):
            for key in ("managed_path", "original_path"):
                value = metadata.get(key)
                if isinstance(value, str):
                    candidates.append(value)

        candidates.append(asset.path)

        for candidate in candidates:
            try:
                path = Path(candidate)  # type: ignore[arg-type]
            except TypeError as exc:  # pragma: no cover - defensive
                logger.debug("Invalid path candidate %s for %s: %s", candidate, asset, exc)
                continue
            resolved = path.expanduser()
            if resolved.exists():
                return resolved

        raise ThumbnailGenerationError(f"No readable source file found for asset {asset.path!s}")


def _compose_rotation_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """Return a rotation matrix composed from Euler angles."""

    cos_x, sin_x = np.cos(rx), np.sin(rx)
    cos_y, sin_y = np.cos(ry), np.sin(ry)
    cos_z, sin_z = np.cos(rz), np.sin(rz)

    rot_x = np.array(
        [[1, 0, 0], [0, cos_x, -sin_x], [0, sin_x, cos_x]],
        dtype=float,
    )
    rot_y = np.array(
        [[cos_y, 0, sin_y], [0, 1, 0], [-sin_y, 0, cos_y]],
        dtype=float,
    )
    rot_z = np.array(
        [[cos_z, -sin_z, 0], [sin_z, cos_z, 0], [0, 0, 1]],
        dtype=float,
    )

    return rot_z @ rot_y @ rot_x


def _lerp_color(
    start: np.ndarray,
    end: np.ndarray,
    factor: float,
) -> np.ndarray:
    """Linearly interpolate between two RGBA colors."""

    clamped = float(np.clip(factor, 0.0, 1.0))
    return start + (end - start) * clamped

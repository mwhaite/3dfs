"""Preview widget that renders thumbnails and metadata for repository assets."""

from __future__ import annotations

import io
import logging
import mimetypes
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..thumbnails import (
    DEFAULT_THUMBNAIL_SIZE,
    ThumbnailCache,
    ThumbnailGenerationError,
    ThumbnailResult,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from ..storage import AssetRecord, AssetService

__all__ = ["PreviewPane"]

logger = logging.getLogger(__name__)


try:
    _RESAMPLING_FILTER = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - Pillow < 9 fallback
    _RESAMPLING_FILTER = Image.LANCZOS

_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".tga",
        ".tiff",
        ".webp",
    }
)

_MODEL_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".fbx",
        ".gltf",
        ".glb",
        ".obj",
        ".ply",
        ".stl",
    }
)


@dataclass(slots=True)
class PreviewOutcome:
    """Container describing the result of a thumbnail extraction."""

    path: Path
    metadata: list[tuple[str, str]]
    thumbnail_bytes: bytes | None = None
    thumbnail_message: str | None = None
    thumbnail_info: dict[str, Any] | None = None
    asset_record: AssetRecord | None = None


class PreviewWorkerSignals(QObject):
    """Signals emitted by :class:`PreviewWorker`."""

    result = Signal(int, object)
    error = Signal(int, str)


class PreviewWorker(QRunnable):
    """Background task that extracts thumbnail and metadata for a file."""

    def __init__(
        self,
        token: int,
        path: Path,
        *,
        asset_metadata: Mapping[str, Any] | None = None,
        asset_service: AssetService | None = None,
        asset_record: AssetRecord | None = None,
        thumbnail_cache: ThumbnailCache | None = None,
        size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
    ) -> None:
        super().__init__()
        self._token = token
        self._path = path
        self._metadata = dict(asset_metadata) if asset_metadata else {}
        self._asset_service = asset_service
        self._asset_record = asset_record
        self._thumbnail_cache = thumbnail_cache
        self._size = size
        self.signals = PreviewWorkerSignals()

    def run(self) -> None:  # pragma: no cover - exercised indirectly via signals
        try:
            outcome = _build_preview_outcome(
                self._path,
                metadata=self._metadata,
                asset_service=self._asset_service,
                asset_record=self._asset_record,
                thumbnail_cache=self._thumbnail_cache,
                size=self._size,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to generate preview for %s", self._path)
            message = str(exc) or exc.__class__.__name__
            self.signals.error.emit(self._token, message)
        else:
            self.signals.result.emit(self._token, outcome)


class PreviewPane(QWidget):
    """Widget responsible for rendering previews of repository assets."""

    def __init__(
        self,
        base_path: str | Path | None = None,
        *,
        asset_service: AssetService | None = None,
        thumbnail_cache: ThumbnailCache | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_path = Path(base_path or Path.cwd()).expanduser().resolve()
        self._thread_pool = QThreadPool.globalInstance()
        self._asset_service = asset_service
        self._thumbnail_cache = thumbnail_cache

        self._current_task_id: int | None = None
        self._task_counter = 0
        self._current_raw_path: str | None = None
        self._current_absolute_path: Path | None = None
        self._current_pixmap: QPixmap | None = None
        self._current_thumbnail_message: str | None = None
        self._asset_metadata: dict[str, Any] = {}
        self._asset_record: AssetRecord | None = None
        self._workers: dict[int, PreviewWorker] = {}

        self._title_label = QLabel("Preview", self)
        self._title_label.setObjectName("previewTitle")
        self._title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._path_label = QLabel("", self)
        self._path_label.setObjectName("previewPath")
        self._path_label.setWordWrap(True)
        self._path_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self._description_label = QLabel("", self)
        self._description_label.setObjectName("previewDescription")
        self._description_label.setWordWrap(True)
        self._description_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._description_label.setVisible(False)

        self._message_label = QLabel("Select an item to preview", self)
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)

        self._thumbnail_label = QLabel(self)
        self._thumbnail_label.setObjectName("previewThumbnail")
        self._thumbnail_label.setAlignment(Qt.AlignCenter)
        self._thumbnail_label.setWordWrap(True)
        self._thumbnail_label.setMinimumHeight(220)
        self._thumbnail_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self._metadata_title = QLabel("File details", self)
        self._metadata_title.setObjectName("previewMetadataTitle")

        self._metadata_list = QListWidget(self)
        self._metadata_list.setObjectName("previewMetadataList")
        self._metadata_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._metadata_list.setFocusPolicy(Qt.NoFocus)

        self._stack = QStackedLayout()
        self._message_container = QWidget(self)
        message_layout = QVBoxLayout(self._message_container)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.addStretch(1)
        message_layout.addWidget(self._message_label)
        message_layout.addStretch(1)
        self._stack.addWidget(self._message_container)

        self._preview_container = QWidget(self)
        preview_layout = QVBoxLayout(self._preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(self._thumbnail_label, 3)
        preview_layout.addWidget(self._metadata_title)
        preview_layout.addWidget(self._metadata_list, 2)
        self._stack.addWidget(self._preview_container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._path_label)
        layout.addWidget(self._description_label)
        layout.addLayout(self._stack, 1)

        self._show_message("Select an item to preview")

    # ------------------------------------------------------------------
    # Qt API surface
    # ------------------------------------------------------------------
    @Slot()
    def clear(self) -> None:
        """Reset the preview pane to its idle state."""

        self._current_task_id = None
        self._current_raw_path = None
        self._current_absolute_path = None
        self._current_pixmap = None
        self._current_thumbnail_message = None
        self._asset_metadata.clear()
        self._asset_record = None
        self._title_label.setText("Preview")
        self._path_label.clear()
        self._description_label.clear()
        self._description_label.setVisible(False)
        self._metadata_list.clear()
        self._thumbnail_label.setToolTip("")
        self._show_message("Select an item to preview")

    def set_item(
        self,
        path: str | None,
        *,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        asset_record: AssetRecord | None = None,
    ) -> None:
        """Display the asset located at *path* in the preview pane."""

        if not path:
            self.clear()
            return

        self._asset_record = asset_record
        self._asset_metadata = dict(metadata) if metadata else {}
        if not self._asset_metadata and asset_record is not None:
            self._asset_metadata = dict(asset_record.metadata)
        self._current_raw_path = path
        absolute_path = self._resolve_path(path)
        self._current_absolute_path = absolute_path

        display_label = label or absolute_path.name
        self._title_label.setText(display_label)
        self._path_label.setText(path)

        description = self._asset_metadata.get("description")
        if description:
            self._description_label.setText(str(description))
            self._description_label.setVisible(True)
        else:
            self._description_label.clear()
            self._description_label.setVisible(False)

        self._metadata_list.clear()
        self._current_pixmap = None
        self._current_thumbnail_message = None
        self._thumbnail_label.setToolTip("")

        self._show_message(f"Loading preview for {display_label}…")
        self._enqueue_preview(absolute_path)

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_thumbnail_display()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (self._base_path / candidate).resolve()
        return candidate

    def _enqueue_preview(self, absolute_path: Path) -> None:
        self._task_counter += 1
        task_id = self._task_counter
        self._current_task_id = task_id

        cache = self._thumbnail_cache
        if cache is None and self._asset_service is None:
            cache = ThumbnailCache()
            self._thumbnail_cache = cache

        worker = PreviewWorker(
            task_id,
            absolute_path,
            asset_metadata=self._asset_metadata,
            asset_service=self._asset_service,
            asset_record=self._asset_record,
            thumbnail_cache=cache,
            size=DEFAULT_THUMBNAIL_SIZE,
        )
        worker.signals.result.connect(self._handle_worker_result)
        worker.signals.error.connect(self._handle_worker_error)
        self._workers[task_id] = worker
        self._thread_pool.start(worker)

    def _show_message(self, text: str) -> None:
        self._message_label.setText(text)
        self._stack.setCurrentWidget(self._message_container)

    @Slot(int, object)
    def _handle_worker_result(self, token: int, payload: object) -> None:
        outcome = payload if isinstance(payload, PreviewOutcome) else None
        worker = self._workers.pop(token, None)
        del worker  # allow worker to be garbage collected

        if token != self._current_task_id or outcome is None:
            return

        if (
            self._current_absolute_path is None
            or outcome.path != self._current_absolute_path
        ):
            return

        self._apply_outcome(outcome)

    @Slot(int, str)
    def _handle_worker_error(self, token: int, message: str) -> None:
        self._workers.pop(token, None)

        if token != self._current_task_id:
            return

        logger.error(
            "Preview generation failed for %s: %s", self._current_raw_path, message
        )
        self._current_task_id = None
        self._current_pixmap = None
        self._current_thumbnail_message = message
        self._metadata_list.clear()
        self._show_message(f"Unable to generate preview:\n{message}")

    def _apply_outcome(self, outcome: PreviewOutcome) -> None:
        self._current_task_id = None
        self._stack.setCurrentWidget(self._preview_container)

        if outcome.asset_record is not None:
            self._asset_record = outcome.asset_record
            self._asset_metadata = dict(outcome.asset_record.metadata)
        elif outcome.thumbnail_info is not None:
            self._asset_metadata["thumbnail"] = outcome.thumbnail_info

        if outcome.thumbnail_bytes:
            pixmap = QPixmap()
            pixmap.loadFromData(outcome.thumbnail_bytes)
            if pixmap.isNull():
                self._current_pixmap = None
                self._thumbnail_label.clear()
                message = (
                    outcome.thumbnail_message
                    or "Unable to display generated thumbnail."
                )
                self._thumbnail_label.setText(message)
                self._current_thumbnail_message = message
                self._thumbnail_label.setToolTip(message)
            else:
                self._current_pixmap = pixmap
                self._current_thumbnail_message = outcome.thumbnail_message
                self._thumbnail_label.setText("")
                if outcome.thumbnail_message:
                    self._thumbnail_label.setToolTip(outcome.thumbnail_message)
                else:
                    self._thumbnail_label.setToolTip("")
                self._update_thumbnail_display()
        else:
            self._current_pixmap = None
            message = (
                outcome.thumbnail_message or "No thumbnail available for this file."
            )
            self._thumbnail_label.clear()
            self._thumbnail_label.setText(message)
            self._current_thumbnail_message = message
            self._thumbnail_label.setToolTip(message)

        self._populate_metadata(outcome.metadata)

    def _update_thumbnail_display(self) -> None:
        if self._current_pixmap is None or self._current_pixmap.isNull():
            if self._current_thumbnail_message:
                self._thumbnail_label.setText(self._current_thumbnail_message)
            return

        if self._stack.currentWidget() is not self._preview_container:
            return

        available_width = max(1, self._thumbnail_label.width())
        available_height = max(1, self._thumbnail_label.height())
        scaled = self._current_pixmap.scaled(
            available_width,
            available_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._thumbnail_label.setPixmap(scaled)

    def _populate_metadata(self, metadata: Iterable[tuple[str, str]]) -> None:
        self._metadata_list.clear()

        for key, value in metadata:
            item = QListWidgetItem(f"{key}: {value}")
            item.setFlags(Qt.ItemIsEnabled)
            self._metadata_list.addItem(item)

        if self._asset_metadata:
            separator = QListWidgetItem("")
            separator.setFlags(Qt.ItemIsEnabled)
            self._metadata_list.addItem(separator)

            section = QListWidgetItem("Asset metadata")
            font = section.font()
            font.setBold(True)
            section.setFont(font)
            section.setFlags(Qt.ItemIsEnabled)
            self._metadata_list.addItem(section)

            for key, value in self._asset_metadata.items():
                item = QListWidgetItem(f"{key}: {value}")
                item.setFlags(Qt.ItemIsEnabled)
                self._metadata_list.addItem(item)


# ----------------------------------------------------------------------
# Preview helpers executed in background threads
# ----------------------------------------------------------------------


def _build_preview_outcome(
    path: Path,
    *,
    asset_metadata: Mapping[str, Any] | None = None,
    asset_service: AssetService | None = None,
    asset_record: AssetRecord | None = None,
    thumbnail_cache: ThumbnailCache | None = None,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
) -> PreviewOutcome:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    metadata: list[tuple[str, str]] = []
    stat = path.stat()
    metadata.append(("Kind", _classify_kind(path)))
    metadata.append(("Size", _format_size(stat.st_size)))
    metadata.append(("Modified", _format_timestamp(stat.st_mtime)))
    metadata.append(("Location", str(path)))

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        metadata.append(("MIME Type", mime_type))

    suffix = path.suffix.lower()

    if suffix in _IMAGE_EXTENSIONS:
        image_metadata, thumbnail_bytes = _build_image_preview(path)
        metadata.extend(image_metadata)
        return PreviewOutcome(
            path=path,
            metadata=metadata,
            thumbnail_bytes=thumbnail_bytes,
        )

    if suffix in _MODEL_EXTENSIONS:
        (
            model_metadata,
            thumbnail_bytes,
            message,
            thumbnail_info,
            updated_asset,
        ) = _build_model_preview(
            path,
            asset_metadata=asset_metadata,
            asset_service=asset_service,
            asset_record=asset_record,
            thumbnail_cache=thumbnail_cache,
            size=size,
        )
        metadata.extend(model_metadata)
        return PreviewOutcome(
            path=path,
            metadata=metadata,
            thumbnail_bytes=thumbnail_bytes,
            thumbnail_message=message,
            thumbnail_info=thumbnail_info,
            asset_record=updated_asset,
        )

    metadata.append(("Type", path.suffix or "Unknown"))
    return PreviewOutcome(
        path=path,
        metadata=metadata,
        thumbnail_bytes=None,
        thumbnail_message="No thumbnail available for this file type.",
    )


def _build_image_preview(path: Path) -> tuple[list[tuple[str, str]], bytes]:
    with Image.open(path) as img:
        image = ImageOps.exif_transpose(img)
        metadata = [
            ("Type", f"Image ({image.format or path.suffix.upper()})"),
            ("Dimensions", f"{image.width}×{image.height}"),
            ("Color Mode", image.mode),
        ]

        thumbnail = image.copy()
        thumbnail.thumbnail((768, 768), _RESAMPLING_FILTER)
        buffer = io.BytesIO()
        thumbnail.save(buffer, format="PNG")
        return metadata, buffer.getvalue()


def _extract_customizer_preview(
    asset_metadata: Mapping[str, Any] | None,
) -> tuple[bytes, dict[str, Any]] | None:
    if not isinstance(asset_metadata, Mapping):
        return None

    customization = asset_metadata.get("customization")
    if not isinstance(customization, Mapping):
        return None

    previews = customization.get("previews")
    if isinstance(previews, Mapping):
        candidates = [previews]
    elif isinstance(previews, Iterable) and not isinstance(previews, (str, bytes)):
        candidates = [item for item in previews if isinstance(item, Mapping)]
    else:
        return None

    for candidate in candidates:
        managed_path = candidate.get("managed_path")
        raw_path = candidate.get("path")
        path_hint = managed_path if isinstance(managed_path, str) else raw_path
        if not isinstance(path_hint, str) or not path_hint.strip():
            continue

        resolved = Path(path_hint).expanduser()
        if not resolved.exists():
            continue

        try:
            payload = resolved.read_bytes()
        except OSError:
            continue

        info = {
            "source": "customization",
            "path": str(resolved),
            "managed_path": str(resolved),
        }
        for key in ("asset_id", "relationship", "label", "content_type"):
            value = candidate.get(key)
            if value is not None:
                info[key] = value
        return payload, info

    return None


def _build_model_preview(
    path: Path,
    *,
    asset_metadata: Mapping[str, Any] | None = None,
    asset_service: AssetService | None = None,
    asset_record: AssetRecord | None = None,
    thumbnail_cache: ThumbnailCache | None = None,
    size: tuple[int, int] = DEFAULT_THUMBNAIL_SIZE,
) -> tuple[
    list[tuple[str, str]],
    bytes | None,
    str | None,
    dict[str, Any] | None,
    AssetRecord | None,
]:
    suffix = path.suffix.lower()
    metadata: list[tuple[str, str]] = [
        ("Type", f"3D Model ({suffix[1:].upper()})" if suffix else "3D Model"),
    ]

    stats = _extract_model_stats(path)
    if stats:
        metadata.extend(stats)
    else:
        metadata.append(("Model Stats", "Not available"))

    thumbnail_result: ThumbnailResult | None = None
    updated_asset = asset_record

    custom_preview = _extract_customizer_preview(asset_metadata)
    if custom_preview is not None:
        preview_bytes, preview_info = custom_preview
        return (
            metadata,
            preview_bytes,
            "Preview provided by customization backend.",
            preview_info,
            updated_asset,
        )

    if asset_service is not None and asset_record is not None:
        updated_asset, thumbnail_result = asset_service.ensure_thumbnail(
            asset_record,
            size=size,
        )
    else:
        cache = thumbnail_cache or ThumbnailCache()
        existing_info = None
        if asset_metadata and isinstance(asset_metadata, Mapping):
            candidate = asset_metadata.get("thumbnail")
            if isinstance(candidate, Mapping):
                existing_info = candidate
        try:
            thumbnail_result = cache.get_or_render(
                path,
                existing_info=existing_info,
                metadata=asset_metadata,
                size=size,
            )
        except ThumbnailGenerationError:
            thumbnail_result = None

    if thumbnail_result is not None:
        message = (
            "Generated new preview for 3D model."
            if thumbnail_result.updated
            else "Loaded cached preview for 3D model."
        )
        return (
            metadata,
            thumbnail_result.image_bytes,
            message,
            thumbnail_result.info,
            updated_asset,
        )

    try:
        thumbnail_bytes = _create_model_placeholder(suffix)
    except Exception:  # pragma: no cover - extremely unlikely to fail
        logger.exception("Failed to build placeholder thumbnail for %s", path)
        return metadata, None, "Model thumbnail not available.", None, updated_asset

    return (
        metadata,
        thumbnail_bytes,
        "Placeholder preview generated for 3D model.",
        None,
        updated_asset,
    )


def _extract_model_stats(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()

    if suffix != ".obj":
        return []

    vertices = 0
    faces = 0

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith("v "):
                    vertices += 1
                elif line.startswith("f "):
                    faces += 1
    except OSError:
        return []

    stats: list[tuple[str, str]] = []
    if vertices:
        stats.append(("Vertices", str(vertices)))
    if faces:
        stats.append(("Faces", str(faces)))
    return stats


def _create_model_placeholder(extension: str) -> bytes:
    size = (512, 512)
    background_color = (32, 38, 46, 255)
    accent_color = (93, 156, 236, 255)
    accent_light = (120, 182, 255, 255)

    image = Image.new("RGBA", size, background_color)
    draw = ImageDraw.Draw(image)

    margin = 80
    width, height = size
    front = [
        (margin, height - margin),
        (width - margin, height - margin),
        (width - margin, margin),
        (margin, margin),
    ]
    top = [
        (margin, margin),
        (width / 2, margin - margin / 3),
        (width - margin, margin),
        (width / 2, margin + margin / 3),
    ]
    side = [
        (width - margin, height - margin),
        (width - margin, margin),
        (width - margin + margin / 2, margin + margin / 3),
        (width - margin + margin / 2, height - margin + margin / 3),
    ]

    draw.polygon(front, fill=accent_color)
    draw.polygon(top, fill=accent_light)
    draw.polygon(side, fill=(70, 120, 210, 255))

    label = extension.upper().lstrip(".") or "3D"
    font = ImageFont.load_default()
    text_width, text_height = draw.textbbox((0, 0), label, font=font)[2:]
    text_position = (
        (width - text_width) / 2,
        height - margin / 2 - text_height / 2,
    )
    draw.text(text_position, label, fill=(255, 255, 255, 255), font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _classify_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "Image"
    if suffix in _MODEL_EXTENSIONS:
        return "3D Model"
    return "File"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"

    units = ["KB", "MB", "GB", "TB", "PB"]
    value = float(size)
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} EB"


def _format_timestamp(timestamp: float) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=UTC).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

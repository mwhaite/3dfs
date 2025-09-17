"""Preview pane widget for repository selections."""

from __future__ import annotations

import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

_IMAGE_EXTENSIONS: set[str] = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tiff",
    ".webp",
}
_MODEL_EXTENSIONS: set[str] = {
    ".fbx",
    ".gltf",
    ".glb",
    ".obj",
    ".stl",
}
_DEFAULT_THUMBNAIL_SIZE = QSize(320, 320)


@dataclass(slots=True)
class PreviewResult:
    """Container for worker results communicated back to the UI thread."""

    request_id: int
    thumbnail_bytes: bytes | None
    metadata: list[tuple[str, str]]
    error: str | None


class PreviewWorkerSignals(QObject):
    """Signals emitted by :class:`PreviewWorker`."""

    finished = Signal(object)


class PreviewWorker(QRunnable):
    """Background task that loads preview data for a given file."""

    def __init__(
        self,
        request_id: int,
        path: Path,
        display_name: str | None,
        thumbnail_size: QSize,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._path = path
        self._display_name = display_name
        self._thumbnail_size = thumbnail_size
        self.signals = PreviewWorkerSignals()

    # ------------------------------------------------------------------
    # QRunnable API
    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - executed in worker threads
        metadata: list[tuple[str, str]] = []
        error_message: str | None = None
        thumbnail_bytes: bytes | None = None

        display_name = self._display_name or self._path.name or str(self._path)
        metadata.append(("Name", display_name))
        metadata.append(("Location", str(self._path)))

        if not self._path.exists():
            error_message = "File not found."
            result = PreviewResult(self._request_id, None, metadata, error_message)
            self.signals.finished.emit(result)
            return

        if not self._path.is_file():
            error_message = "Selected item is not a file."
            result = PreviewResult(self._request_id, None, metadata, error_message)
            self.signals.finished.emit(result)
            return

        try:
            stat_info = self._path.stat()
            metadata.append(("Size", _format_size(stat_info.st_size)))
        except OSError as exc:  # pragma: no cover - unlikely but handled gracefully
            metadata.append(("Size", "Unavailable"))
            error_message = str(exc)
        else:
            mime_type, _ = mimetypes.guess_type(str(self._path))
            file_type = mime_type or self._path.suffix.lstrip(".") or "Unknown"
            metadata.append(("Type", file_type))

            suffix = self._path.suffix.lower()
            if suffix in _IMAGE_EXTENSIONS:
                try:
                    thumbnail_bytes, extra = _generate_image_preview(
                        self._path, self._thumbnail_size
                    )
                    metadata.extend(extra)
                    error_message = None
                except (OSError, UnidentifiedImageError) as exc:
                    error_message = f"Unable to load image preview: {exc}"
            elif suffix in _MODEL_EXTENSIONS:
                thumbnail_bytes, extra, message = _generate_model_preview(
                    self._path, self._thumbnail_size
                )
                metadata.extend(extra)
                if message:
                    error_message = message
            else:
                error_message = "No preview available for this file type."

        result = PreviewResult(
            self._request_id,
            thumbnail_bytes,
            metadata,
            error_message,
        )
        self.signals.finished.emit(result)


class PreviewPane(QWidget):
    """Widget that renders thumbnails and metadata for selected files."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        repository_root: str | Path | None = None,
        thumbnail_size: QSize | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository_root = Path(repository_root) if repository_root else Path.cwd()
        self._thumbnail_size = thumbnail_size or _DEFAULT_THUMBNAIL_SIZE
        self._thread_pool = QThreadPool(self)
        self._current_request_id = 0
        self._current_pixmap: QPixmap | None = None

        self._thumbnail_label = QLabel(self)
        self._thumbnail_label.setAlignment(Qt.AlignCenter)
        self._thumbnail_label.setWordWrap(True)
        self._thumbnail_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self._thumbnail_label.setMinimumHeight(220)

        self._message_label = QLabel(self)
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.setObjectName("previewMessage")

        self._metadata_label = QLabel(self)
        self._metadata_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._metadata_label.setWordWrap(True)
        self._metadata_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._metadata_label.setObjectName("previewMetadata")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._thumbnail_label)
        layout.addWidget(self._message_label)
        layout.addWidget(self._metadata_label)

        self.clear()

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def resizeEvent(self, event: QResizeEvent) -> None:  # pragma: no cover - GUI hook
        super().resizeEvent(event)
        self._update_thumbnail_display()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def display_asset(
        self, source: str | Path, display_name: str | None = None
    ) -> None:
        """Render preview information for ``source``.

        Parameters
        ----------
        source:
            The file path to display.
        display_name:
            Optional display name to show in metadata listings.
        """

        resolved = self._resolve_path(source)
        self._current_request_id += 1
        self._current_pixmap = None
        self._thumbnail_label.setPixmap(QPixmap())
        self._thumbnail_label.setText("Loading preview…")
        self._message_label.setText("")
        self._metadata_label.setText("Gathering metadata…")

        worker = PreviewWorker(
            self._current_request_id,
            resolved,
            display_name,
            self._thumbnail_size,
        )
        worker.signals.finished.connect(self._handle_worker_result)
        self._thread_pool.start(worker)

    def clear(self, message: str | None = None) -> None:
        """Reset the preview pane to an empty state."""

        self._current_request_id += 1
        self._current_pixmap = None
        self._thumbnail_label.setPixmap(QPixmap())
        self._thumbnail_label.setText(message or "Select an item to preview")
        self._message_label.setText("")
        self._metadata_label.setText("")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_worker_result(self, result: PreviewResult) -> None:
        if result.request_id != self._current_request_id:
            return

        if result.thumbnail_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(result.thumbnail_bytes):
                self._current_pixmap = pixmap
                self._thumbnail_label.setText("")
                self._update_thumbnail_display()
            else:  # pragma: no cover - depends on runtime image codecs
                self._current_pixmap = None
                self._thumbnail_label.setPixmap(QPixmap())
                self._thumbnail_label.setText("Unable to display preview image.")
        else:
            self._current_pixmap = None
            self._thumbnail_label.setPixmap(QPixmap())
            self._thumbnail_label.setText("Preview unavailable")

        self._message_label.setText(result.error or "")
        self._metadata_label.setText(self._format_metadata_text(result.metadata))

    def _update_thumbnail_display(self) -> None:
        if not self._current_pixmap:
            return

        label_size = self._thumbnail_label.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            return

        scaled = self._current_pixmap.scaled(
            label_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._thumbnail_label.setPixmap(scaled)

    def _resolve_path(self, source: str | Path) -> Path:
        path = Path(source)
        if not path.is_absolute():
            path = self._repository_root / path
        return path

    @staticmethod
    def _format_metadata_text(items: Sequence[tuple[str, str]]) -> str:
        if not items:
            return ""

        formatted_lines = [f"{label}: {value}" for label, value in items]
        return "\n".join(formatted_lines)


# ----------------------------------------------------------------------
# Helper functions used by the worker
# ----------------------------------------------------------------------


def _format_size(num_bytes: int) -> str:
    step_unit = 1024.0
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < step_unit or unit == "TB":
            if unit == "bytes":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= step_unit
    return f"{size:.1f} TB"


def _generate_image_preview(
    path: Path, thumbnail_size: QSize
) -> tuple[bytes, list[tuple[str, str]]]:
    with Image.open(path) as image:
        width, height = image.size
        metadata = [("Dimensions", f"{width} × {height}")]
        image = image.convert("RGBA")
        target_size = (thumbnail_size.width(), thumbnail_size.height())
        image.thumbnail(target_size, Image.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), metadata


def _generate_model_preview(
    path: Path, thumbnail_size: QSize
) -> tuple[bytes | None, list[tuple[str, str]], str | None]:
    metadata: list[tuple[str, str]] = []
    message: str | None = None
    suffix = path.suffix.lower()

    if suffix == ".obj":
        vertices = 0
        faces = 0
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.startswith("v "):
                        vertices += 1
                    elif line.startswith("f "):
                        faces += 1
        except OSError as exc:
            message = f"Unable to read model: {exc}"
        else:
            metadata.append(("Vertices", str(vertices)))
            metadata.append(("Faces", str(faces)))
    else:
        message = "Model statistics unavailable for this format."

    placeholder_label = f"{suffix.lstrip('.').upper()} model"
    thumbnail = _generate_placeholder_thumbnail(thumbnail_size, placeholder_label)
    return thumbnail, metadata, message


def _generate_placeholder_thumbnail(thumbnail_size: QSize, label: str) -> bytes:
    width = max(thumbnail_size.width(), 32)
    height = max(thumbnail_size.height(), 32)
    image = Image.new("RGBA", (width, height), (40, 40, 48, 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    text = label.strip() or "Model"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    position = (
        max((width - text_width) / 2, 0),
        max((height - text_height) / 2, 0),
    )
    draw.text(position, text, fill=(220, 220, 220, 255), font=font)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

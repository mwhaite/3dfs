"""Preview widget for displaying thumbnails and metadata for selected files."""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, UnidentifiedImageError
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

LOGGER = logging.getLogger(__name__)

_MODEL_EXTENSIONS: tuple[str, ...] = (".obj", ".ply", ".stl")


@dataclass(slots=True)
class PreviewResult:
    """Container for preview processing results."""

    path: Path
    image: QImage | None
    metadata: dict[str, str]
    message: str | None


class PreviewWorkerSignals(QObject):
    """Signals emitted by :class:`PreviewWorker`."""

    finished = Signal(object)
    error = Signal(object, str)

    def __init__(self) -> None:
        super().__init__()


class PreviewWorker(QRunnable):
    """Background job that prepares previews for a file."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.signals = PreviewWorkerSignals()

    def run(self) -> None:  # pragma: no cover - run is executed in worker threads
        try:
            result = _build_preview(self.path)
        except Exception as exc:  # pragma: no cover - defensive branch
            LOGGER.exception("Failed to build preview for %s", self.path)
            self.signals.error.emit(self.path, str(exc))
        else:
            self.signals.finished.emit(result)


class PreviewPane(QWidget):
    """Widget responsible for presenting thumbnails and metadata for files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thread_pool = QThreadPool.globalInstance()
        self._current_path: Path | None = None
        self._active_workers: dict[Path, PreviewWorker] = {}
        self._current_pixmap: QPixmap | None = None

        self._preview_label = QLabel()
        self._preview_label.setObjectName("previewImage")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setFrameShape(QFrame.StyledPanel)
        self._preview_label.setMinimumSize(240, 240)
        self._preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._message_label = QLabel("Select a file to preview.")
        self._message_label.setObjectName("previewMessage")
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        self._metadata_widget = QWidget()
        self._metadata_layout = QFormLayout(self._metadata_widget)
        self._metadata_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
        self._metadata_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._metadata_layout.setHorizontalSpacing(12)
        self._metadata_layout.setVerticalSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(self._preview_label, stretch=3)
        layout.addWidget(self._message_label)
        layout.addWidget(self._metadata_widget, stretch=2)

    def set_file(self, path: Path | str | None) -> None:
        """Begin loading preview data for *path* if provided."""

        if path is None:
            self._current_path = None
            self._active_workers.clear()
            self._update_pixmap(None)
            self._message_label.setText("Select a file to preview.")
            self._clear_metadata()
            return

        candidate = Path(path)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            self._current_path = None
            self._update_pixmap(None)
            self._message_label.setText("The selected file is no longer available.")
            self._clear_metadata()
            return

        if resolved == self._current_path:
            return

        self._current_path = resolved
        self._message_label.setText(f"Loading preview for {resolved.name}…")
        self._update_pixmap(None)
        self._clear_metadata()

        worker = PreviewWorker(resolved)
        worker.signals.finished.connect(self._handle_finished)
        worker.signals.error.connect(self._handle_error)
        self._active_workers[resolved] = worker
        self._thread_pool.start(worker)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401
        """Update the displayed pixmap when the widget changes size."""

        super().resizeEvent(event)
        self._apply_pixmap()

    @Slot(object)
    def _handle_finished(self, result: PreviewResult) -> None:
        worker = self._active_workers.pop(result.path, None)
        if worker is None:
            # Result is stale; ignore.
            return

        if result.path != self._current_path:
            return

        if result.message:
            self._message_label.setText(result.message)
        else:
            self._message_label.setText(" ")

        self._populate_metadata(result.metadata)

        pixmap: QPixmap | None = None
        if result.image is not None and not result.image.isNull():
            pixmap = QPixmap.fromImage(result.image)
        self._update_pixmap(pixmap)

    @Slot(object, str)
    def _handle_error(self, path: object, message: str) -> None:
        resolved = Path(path)
        self._active_workers.pop(resolved, None)

        if resolved != self._current_path:
            return

        LOGGER.warning("Preview generation failed for %s: %s", resolved, message)

        try:
            metadata = _build_basic_metadata(resolved)
        except OSError:
            metadata = {"Name": resolved.name}

        self._populate_metadata(metadata)
        self._update_pixmap(None)
        self._message_label.setText(f"Unable to generate a preview: {message}")

    def _update_pixmap(self, pixmap: QPixmap | None) -> None:
        self._current_pixmap = pixmap
        self._apply_pixmap()

    def _apply_pixmap(self) -> None:
        if self._current_pixmap is None or self._preview_label.width() <= 0:
            self._preview_label.clear()
            return

        scaled = self._current_pixmap.scaled(
            self._preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setPixmap(scaled)

    def _populate_metadata(self, metadata: Mapping[str, str]) -> None:
        self._clear_metadata()
        for key, value in metadata.items():
            key_label = QLabel(f"{key}:")
            key_label.setObjectName("previewMetadataKey")
            key_label.setAlignment(Qt.AlignRight | Qt.AlignTop)

            value_label = QLabel(value)
            value_label.setObjectName("previewMetadataValue")
            value_label.setWordWrap(True)
            value_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )

            self._metadata_layout.addRow(key_label, value_label)

    def _clear_metadata(self) -> None:
        while self._metadata_layout.count():
            item = self._metadata_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


def _build_preview(path: Path) -> PreviewResult:
    metadata = _build_basic_metadata(path)

    try:
        image, extra_metadata = _load_image_preview(path)
    except UnidentifiedImageError:
        image = None
        extra_metadata: dict[str, str] = {}
    except OSError as exc:
        LOGGER.exception("Unable to build image preview for %s", path)
        return PreviewResult(
            path=path,
            image=None,
            metadata=metadata,
            message=f"Unable to load image preview: {exc}",
        )
    else:
        metadata.update(extra_metadata)
        return PreviewResult(path=path, image=image, metadata=metadata, message=None)

    if path.suffix.lower() in _MODEL_EXTENSIONS:
        try:
            image, extra_metadata, message = _load_model_preview(path)
        except Exception as exc:  # pragma: no cover - defensive branch
            LOGGER.exception("Unable to build 3D preview for %s", path)
            return PreviewResult(
                path=path,
                image=None,
                metadata=metadata,
                message=f"Unable to load 3D preview: {exc}",
            )
        metadata.update(extra_metadata)
        return PreviewResult(path=path, image=image, metadata=metadata, message=message)

    return PreviewResult(
        path=path,
        image=None,
        metadata=metadata,
        message="No preview available for this file type.",
    )


def _build_basic_metadata(path: Path) -> dict[str, str]:
    stats = path.stat()
    metadata: dict[str, str] = {
        "Name": path.name,
        "Location": str(path),
        "Size": _human_readable_size(stats.st_size),
        "Modified": _format_timestamp(stats.st_mtime),
    }

    mime_type, encoding = mimetypes.guess_type(path.as_posix())
    if mime_type:
        metadata["Type"] = mime_type
    else:
        metadata["Type"] = path.suffix.lstrip(".").upper() or "Unknown"

    if encoding:
        metadata["Encoding"] = encoding

    return metadata


def _load_image_preview(path: Path) -> tuple[QImage, dict[str, str]]:
    with Image.open(path) as source:
        source.load()
        metadata: dict[str, str] = {
            "Dimensions": f"{source.width} × {source.height} px",
            "Mode": source.mode,
        }
        if source.format:
            metadata["Format"] = source.format

        preview = source.copy()
        preview.thumbnail((512, 512), Image.Resampling.LANCZOS)
        if preview.mode not in {"RGB", "RGBA"}:
            preview = preview.convert("RGBA")

    image = QImage(ImageQt(preview))
    return image, metadata


def _load_model_preview(path: Path) -> tuple[QImage, dict[str, str], str | None]:
    suffix = path.suffix.lower()
    metadata: dict[str, str] = {
        "Format": suffix.lstrip(".").upper() or "3D Model",
    }

    if suffix == ".obj":
        metadata.update(_parse_obj_metadata(path))
    elif suffix == ".stl":
        metadata.update(_parse_stl_metadata(path))
    elif suffix == ".ply":
        metadata.update(_parse_ply_metadata(path))

    preview_image = QImage(ImageQt(_generate_model_placeholder()))
    message = "3D previews are approximated with a stylized placeholder."
    return preview_image, metadata, message


def _parse_obj_metadata(path: Path) -> dict[str, str]:
    vertex_count = 0
    face_count = 0
    min_bounds = [float("inf"), float("inf"), float("inf")]
    max_bounds = [float("-inf"), float("-inf"), float("-inf")]

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith("v "):
                parts = line.split()
                vertex_count += 1
                if len(parts) >= 4:
                    try:
                        x, y, z = (float(parts[1]), float(parts[2]), float(parts[3]))
                    except ValueError:
                        continue
                    min_bounds[0] = min(min_bounds[0], x)
                    min_bounds[1] = min(min_bounds[1], y)
                    min_bounds[2] = min(min_bounds[2], z)
                    max_bounds[0] = max(max_bounds[0], x)
                    max_bounds[1] = max(max_bounds[1], y)
                    max_bounds[2] = max(max_bounds[2], z)
            elif line.startswith("f "):
                face_count += 1

    metadata: dict[str, str] = {}
    if vertex_count:
        metadata["Vertices"] = f"{vertex_count:,}"
    if face_count:
        metadata["Faces"] = f"{face_count:,}"

    if vertex_count and _has_valid_bounds(min_bounds, max_bounds):
        metadata["Bounds"] = (
            f"X[{min_bounds[0]:.2f}, {max_bounds[0]:.2f}] • "
            f"Y[{min_bounds[1]:.2f}, {max_bounds[1]:.2f}] • "
            f"Z[{min_bounds[2]:.2f}, {max_bounds[2]:.2f}]"
        )

    return metadata


def _parse_stl_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    with path.open("rb") as handle:
        header = handle.read(512)

    ascii_candidate = header.lower().startswith(b"solid")
    if ascii_candidate:
        metadata["Encoding"] = "ASCII"
        triangles = 0
        min_bounds = [float("inf"), float("inf"), float("inf")]
        max_bounds = [float("-inf"), float("-inf"), float("-inf")]

        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("facet"):
                    triangles += 1
                elif stripped.startswith("vertex"):
                    parts = stripped.split()
                    if len(parts) >= 4:
                        try:
                            x, y, z = (
                                float(parts[1]),
                                float(parts[2]),
                                float(parts[3]),
                            )
                        except ValueError:
                            continue
                        min_bounds[0] = min(min_bounds[0], x)
                        min_bounds[1] = min(min_bounds[1], y)
                        min_bounds[2] = min(min_bounds[2], z)
                        max_bounds[0] = max(max_bounds[0], x)
                        max_bounds[1] = max(max_bounds[1], y)
                        max_bounds[2] = max(max_bounds[2], z)

        if triangles:
            metadata["Triangles"] = f"{triangles:,}"
        if _has_valid_bounds(min_bounds, max_bounds):
            metadata["Bounds"] = (
                f"X[{min_bounds[0]:.2f}, {max_bounds[0]:.2f}] • "
                f"Y[{min_bounds[1]:.2f}, {max_bounds[1]:.2f}] • "
                f"Z[{min_bounds[2]:.2f}, {max_bounds[2]:.2f}]"
            )
    else:
        metadata["Encoding"] = "Binary"
        with path.open("rb") as handle:
            handle.seek(80)
            count_bytes = handle.read(4)
            if len(count_bytes) == 4:
                triangles = int.from_bytes(count_bytes, byteorder="little")
                metadata["Triangles"] = f"{triangles:,}"

    return metadata


def _parse_ply_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped == "end_header":
                break
            if stripped.startswith("format "):
                parts = stripped.split()
                if len(parts) >= 2:
                    metadata["Encoding"] = parts[1]
            elif stripped.startswith("element "):
                parts = stripped.split()
                if len(parts) >= 3:
                    name = parts[1].capitalize()
                    metadata[f"{name} count"] = f"{int(parts[2]):,}"
    return metadata


def _has_valid_bounds(min_bounds: Sequence[float], max_bounds: Sequence[float]) -> bool:
    return all(
        mn != float("inf") and mx != float("-inf")
        for mn, mx in zip(min_bounds, max_bounds, strict=False)
    )


def _generate_model_placeholder(size: int = 360) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (28, 32, 43, 255))
    draw = ImageDraw.Draw(canvas)

    top = [
        (int(size * 0.3), int(size * 0.25)),
        (int(size * 0.5), int(size * 0.15)),
        (int(size * 0.7), int(size * 0.25)),
        (int(size * 0.5), int(size * 0.35)),
    ]
    front = [
        (int(size * 0.3), int(size * 0.25)),
        (int(size * 0.5), int(size * 0.35)),
        (int(size * 0.5), int(size * 0.7)),
        (int(size * 0.3), int(size * 0.6)),
    ]
    side = [
        (int(size * 0.5), int(size * 0.35)),
        (int(size * 0.7), int(size * 0.25)),
        (int(size * 0.7), int(size * 0.6)),
        (int(size * 0.5), int(size * 0.7)),
    ]

    draw.polygon(top, fill=(92, 123, 209, 255), outline=(220, 233, 255, 255))
    draw.polygon(front, fill=(65, 96, 175, 255), outline=(220, 233, 255, 255))
    draw.polygon(side, fill=(54, 80, 150, 255), outline=(220, 233, 255, 255))

    return canvas


def _human_readable_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _format_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

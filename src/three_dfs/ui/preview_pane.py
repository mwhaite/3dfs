"""Preview widget that renders thumbnails and metadata for repository assets."""

from __future__ import annotations

import html
import io
import logging
import mimetypes
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..customizer import ParameterSchema
from ..customizer.openscad import OpenSCADBackend
from ..customizer.pipeline import PipelineResult
from ..customizer.status import (
    CustomizationStatus,
    evaluate_customization_status,
)
from ..thumbnails import (
    DEFAULT_THUMBNAIL_SIZE,
    ThumbnailCache,
    ThumbnailGenerationError,
    ThumbnailResult,
)
from .customizer_dialog import CustomizerDialog, CustomizerSessionConfig
from .customizer_panel import CustomizerPanel
from .model_viewer import ModelViewer

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from ..storage import AssetRecord, AssetService, CustomizationRecord

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

_TEXT_PREVIEW_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".cfg",
        ".csv",
        ".ini",
        ".json",
        ".log",
        ".md",
        ".markdown",
        ".py",
        ".rst",
        ".scad",
        ".txt",
        ".yaml",
        ".yml",
    }
)

DEFAULT_TEXT_PREVIEW_MAX_BYTES = 200_000


@dataclass(slots=True)
class PreviewOutcome:
    """Container describing the result of a thumbnail extraction."""

    path: Path
    metadata: list[tuple[str, str]]
    thumbnail_bytes: bytes | None = None
    thumbnail_message: str | None = None
    thumbnail_info: dict[str, Any] | None = None
    asset_record: AssetRecord | None = None
    text_content: str | None = None
    text_role: str | None = None
    text_truncated: bool = False


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
        text_preview_limit: int = DEFAULT_TEXT_PREVIEW_MAX_BYTES,
    ) -> None:
        super().__init__()
        self._token = token
        self._path = path
        self._metadata = dict(asset_metadata) if asset_metadata else {}
        self._asset_service = asset_service
        self._asset_record = asset_record
        self._thumbnail_cache = thumbnail_cache
        self._size = size
        self._text_preview_limit = text_preview_limit
        self.signals = PreviewWorkerSignals()

    def run(self) -> None:  # pragma: no cover - exercised indirectly via signals
        try:
            outcome = _build_preview_outcome(
                self._path,
                asset_metadata=self._metadata,
                asset_service=self._asset_service,
                asset_record=self._asset_record,
                thumbnail_cache=self._thumbnail_cache,
                size=self._size,
                text_preview_limit=self._text_preview_limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to generate preview for %s", self._path)
            message = str(exc) or exc.__class__.__name__
            self.signals.error.emit(self._token, message)
        else:
            self.signals.result.emit(self._token, outcome)


class PreviewPane(QWidget):
    """Widget responsible for rendering previews of repository assets."""

    navigationRequested = Signal(str)
    """Emitted when the preview requests navigation to another asset."""

    customizationGenerated = Signal(object)
    """Emitted when a customization pipeline run completes successfully."""

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
        self._text_preview_limit = DEFAULT_TEXT_PREVIEW_MAX_BYTES

        self._current_task_id: int | None = None
        self._task_counter = 0
        self._current_raw_path: str | None = None
        self._current_absolute_path: str | None = None
        self._current_pixmap: QPixmap | None = None
        self._current_thumbnail_message: str | None = None
        self._asset_metadata: dict[str, Any] = {}
        self._asset_record: AssetRecord | None = None
        self._workers: dict[int, PreviewWorker] = {}
        self._customizer_context: CustomizerSessionConfig | None = None
        self._customizer_dialog: CustomizerDialog | None = None
        self._customization_action_buttons: list[QPushButton] = []
        self._viewer_error_message: str | None = None
        self._text_unavailable_message: str | None = None

        self._title_label = QLabel("Preview", self)
        self._title_label.setObjectName("previewTitle")
        self._title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._customize_button = QPushButton("Customize…", self)
        self._customize_button.setObjectName("previewCustomizeButton")
        self._customize_button.setVisible(False)
        self._customize_button.setEnabled(False)
        self._customize_button.clicked.connect(self.launch_customizer)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        header_row.addWidget(self._title_label)
        header_row.addStretch(1)
        header_row.addWidget(self._customize_button)

        self._path_label = QLabel("", self)
        self._path_label.setObjectName("previewPath")
        self._path_label.setWordWrap(True)
        self._path_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self._description_label = QLabel("", self)
        self._description_label.setObjectName("previewDescription")
        self._description_label.setWordWrap(True)
        self._description_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._description_label.setVisible(False)

        self._customization_frame = QFrame(self)
        self._customization_frame.setObjectName("previewCustomizationFrame")
        self._customization_frame.setFrameShape(QFrame.StyledPanel)
        self._customization_frame.setVisible(False)
        customization_layout = QVBoxLayout(self._customization_frame)
        customization_layout.setContentsMargins(8, 6, 8, 6)
        customization_layout.setSpacing(6)

        self._customization_summary_label = QLabel("", self._customization_frame)
        self._customization_summary_label.setWordWrap(True)
        customization_layout.addWidget(self._customization_summary_label)

        self._customization_parameters_label = QLabel("", self._customization_frame)
        self._customization_parameters_label.setWordWrap(True)
        self._customization_parameters_label.setObjectName(
            "previewCustomizationParameters"
        )
        self._customization_parameters_label.setVisible(False)
        customization_layout.addWidget(self._customization_parameters_label)

        self._customization_actions_widget = QWidget(self._customization_frame)
        self._customization_actions_layout = QHBoxLayout(
            self._customization_actions_widget
        )
        self._customization_actions_layout.setContentsMargins(0, 0, 0, 0)
        self._customization_actions_layout.setSpacing(6)
        self._customization_actions_widget.setVisible(False)
        customization_layout.addWidget(self._customization_actions_widget)

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

        # Viewer tab: allow switching between Thumbnail and 3D Viewer
        self._viewer = ModelViewer(self)
        self._viewer.setMinimumHeight(220)
        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("previewTabs")
        thumb_container = QWidget(self)
        thumb_layout = QVBoxLayout(thumb_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        thumb_layout.addWidget(self._thumbnail_label)
        self._thumbnail_tab_index = self._tabs.addTab(thumb_container, "Thumbnail")
        self._viewer_tab_index = self._tabs.addTab(self._viewer, "3D Viewer")
        self._readme_view = QTextEdit(self)
        self._readme_view.setReadOnly(True)
        self._readme_tab_index = self._tabs.addTab(self._readme_view, "README")
        self._text_view = QTextEdit(self)
        self._text_view.setObjectName("previewText")
        self._text_view.setReadOnly(True)
        self._text_tab_index = self._tabs.addTab(self._text_view, "Text")
        self._customizer_panel = CustomizerPanel(
            asset_service=self._asset_service,
            parent=self,
        )
        self._customizer_panel.customizationSucceeded.connect(
            self._handle_customizer_success
        )
        self._customizer_tab_index = self._tabs.addTab(
            self._customizer_panel,
            "Customizer",
        )
        self._tabs.setTabEnabled(self._viewer_tab_index, False)
        self._tabs.setTabEnabled(self._readme_tab_index, False)
        self._tabs.setTabEnabled(self._text_tab_index, False)
        self._tabs.setTabEnabled(self._customizer_tab_index, False)

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
        preview_layout.addWidget(self._tabs, 3)
        preview_layout.addWidget(self._metadata_title)
        preview_layout.addWidget(self._metadata_list, 2)
        self._stack.addWidget(self._preview_container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header_row)
        layout.addWidget(self._path_label)
        layout.addWidget(self._description_label)
        layout.addWidget(self._customization_frame)
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
        self._viewer_error_message = None
        self._text_unavailable_message = None
        self._tabs.setTabEnabled(self._viewer_tab_index, False)
        self._tabs.setTabEnabled(self._readme_tab_index, False)
        self._tabs.setTabEnabled(self._text_tab_index, False)
        self._tabs.setTabToolTip(self._viewer_tab_index, "")
        self._tabs.setTabToolTip(self._text_tab_index, "")
        self._text_view.clear()
        self._tabs.setTabText(self._text_tab_index, "Text")
        self._tabs.setTabEnabled(self._customizer_tab_index, False)
        self._customizer_panel.clear()
        self._customizer_context = None
        self._customize_button.setVisible(False)
        self._customize_button.setEnabled(False)
        self._customization_summary_label.clear()
        self._customization_parameters_label.clear()
        self._customization_parameters_label.setVisible(False)
        self._customization_frame.setVisible(False)
        self._clear_customization_actions()
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
        
        # Safely resolve the path with comprehensive error handling
        try:
            absolute_path = self._resolve_path(path)
            self._current_absolute_path = absolute_path
        except (ValueError, RecursionError, OSError) as e:
            logger.error("Failed to resolve path %s: %s", path, e)
            self._show_message(f"Unable to resolve path: {path}\nError: {e}")
            return

        # Safely create Path object with fallback
        path_obj = None
        display_label = label or path
        suffix = ""
        
        try:
            path_obj = Path(absolute_path)
            display_label = label or path_obj.name
            suffix = path_obj.suffix.lower()
        except (RecursionError, AttributeError, OSError, ValueError) as e:
            logger.warning("Failed to create Path object for %s: %s", absolute_path, e)
            # Extract filename and extension manually as fallback
            if '/' in absolute_path:
                filename = absolute_path.split('/')[-1]
            elif '\\' in absolute_path:
                filename = absolute_path.split('\\')[-1]
            else:
                filename = absolute_path
            
            display_label = label or filename
            if '.' in filename:
                suffix = '.' + filename.split('.')[-1].lower()
            path_obj = None
        
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
        self._viewer_error_message = None
        self._text_unavailable_message = None
        self._customizer_context = None
        self._customize_button.setVisible(False)
        self._customize_button.setEnabled(False)
        self._customization_summary_label.clear()
        self._customization_parameters_label.clear()
        self._customization_parameters_label.setVisible(False)
        self._customization_frame.setVisible(False)
        self._clear_customization_actions()
        self._text_view.clear()
        self._tabs.setTabText(self._text_tab_index, "Text")
        self._tabs.setTabEnabled(self._text_tab_index, False)

        # Prepare viewer tab
        self._tabs.setTabToolTip(self._viewer_tab_index, "")
        if suffix in _MODEL_EXTENSIONS and path_obj is not None:
            try:
                self._viewer.set_path(path_obj)
                self._tabs.setTabEnabled(self._viewer_tab_index, True)
                self._tabs.setTabToolTip(
                    self._viewer_tab_index,
                    "Interactive 3D viewer for supported meshes.",
                )
            except Exception:
                self._tabs.setTabEnabled(self._viewer_tab_index, False)
                message = (
                    getattr(self._viewer, "last_error_message", None)
                    or "3D preview is unavailable for this file."
                )
                self._viewer_error_message = message
                self._tabs.setTabToolTip(self._viewer_tab_index, message)
        else:
            self._tabs.setCurrentIndex(0)
            self._tabs.setTabEnabled(self._viewer_tab_index, False)
            self._tabs.setTabToolTip(
                self._viewer_tab_index,
                "3D viewer is only available for supported model formats.",
            )

        # Load README tab from the asset's folder if present
        if path_obj is not None and self._load_readme_for(path_obj):
            self._tabs.setTabEnabled(self._readme_tab_index, True)
        else:
            self._tabs.setTabEnabled(self._readme_tab_index, False)

        if suffix not in _TEXT_PREVIEW_EXTENSIONS:
            self._text_unavailable_message = (
                "Text preview is unavailable for this file."
            )
        else:
            self._text_unavailable_message = None
        self._tabs.setTabToolTip(self._text_tab_index, "")

        self._show_message(f"Loading preview for {display_label}…")
        if path_obj is not None:
            self._enqueue_preview(path_obj)
            self._prepare_customizer(path_obj)
        else:
            # Use string path as fallback
            self._enqueue_preview(absolute_path)
            try:
                fallback_path = Path(absolute_path)
                self._prepare_customizer(fallback_path)
            except (RecursionError, ValueError, OSError):
                logger.warning("Could not prepare customizer for path: %s", absolute_path)

    def _load_readme_for(self, path: Path) -> bool:
        try:
            base_dir = path if path.is_dir() else path.parent
        except (RecursionError, AttributeError, OSError):
            return False
        allowed = {"", ".md", ".markdown", ".txt", ".rst"}
        candidates = []
        try:
            for entry in base_dir.iterdir():
                try:
                    if not entry.is_file():
                        continue
                    stem = entry.stem.lower()
                    suffix = entry.suffix.lower()
                except Exception:
                    continue
                if suffix in allowed:
                    score = 0 if "readme" in stem else 1
                    candidates.append((score, entry.name.lower(), entry))
            if candidates:
                candidates.sort()
                chosen = candidates[0][2]
                try:
                    content = chosen.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                if content:
                    try:
                        self._readme_view.setMarkdown(content)
                    except Exception:
                        self._readme_view.setPlainText(content)
                    return True
        except Exception:
            pass
        self._readme_view.clear()
        return False

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_thumbnail_display()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def set_base_path(self, base_path: str | Path) -> None:
        """Update the base path used to resolve relative asset references."""

        self._base_path = Path(base_path).expanduser().resolve()

    def text_preview_limit(self) -> int:
        return self._text_preview_limit

    def set_text_preview_limit(self, limit: int) -> None:
        """Adjust how much text content is loaded for previews."""

        minimum = 10_240
        self._text_preview_limit = max(minimum, int(limit))

    def reload_current_preview(self) -> None:
        if self._current_absolute_path is None:
            return
        label = self._title_label.text() or "item"
        self._show_message(f"Loading preview for {label}…")
        self._enqueue_preview(Path(self._current_absolute_path))

    def _resolve_path(self, raw_path: str) -> str:
        # Validate and sanitize the input path first
        if not raw_path or not isinstance(raw_path, str):
            raise ValueError("Invalid path: empty or non-string")
        
        # Check for obvious circular references or malformed paths
        if len(raw_path) > 4096:  # Reasonable path length limit
            raise ValueError("Path too long, possible circular reference")
        
        # Check for repeated patterns that might indicate circular references
        if '..' in raw_path:
            parts = raw_path.split('/')
            if len(parts) > 100:  # Too many path components
                raise ValueError("Path has too many components, possible circular reference")
        
        try:
            if not os.path.isabs(raw_path):
                candidate = os.path.join(str(self._base_path), raw_path)
            else:
                candidate = raw_path
            
            # Use abspath instead of realpath to avoid symlink resolution issues
            resolved = os.path.abspath(candidate)
            
            # Final validation - ensure the resolved path is reasonable
            if len(resolved) > 4096:
                raise ValueError("Resolved path too long")
                
            return resolved
        except (RecursionError, OSError) as e:
            raise ValueError(f"Path resolution failed: {e}")

    def _enqueue_preview(self, absolute_path: Path | str) -> None:
        self._task_counter += 1
        task_id = self._task_counter
        self._current_task_id = task_id

        cache = self._thumbnail_cache
        if cache is None and self._asset_service is None:
            cache = ThumbnailCache()
            self._thumbnail_cache = cache

        # Convert string path to Path object safely
        if isinstance(absolute_path, str):
            try:
                path_obj = Path(absolute_path)
            except (RecursionError, ValueError, OSError) as e:
                logger.error("Failed to create Path object in _enqueue_preview: %s", e)
                self._show_message(f"Unable to process path: {absolute_path}")
                return
        else:
            path_obj = absolute_path

        worker = PreviewWorker(
            task_id,
            path_obj,
            asset_metadata=self._asset_metadata,
            asset_service=self._asset_service,
            asset_record=self._asset_record,
            thumbnail_cache=cache,
            size=DEFAULT_THUMBNAIL_SIZE,
            text_preview_limit=self._text_preview_limit,
        )
        worker.signals.result.connect(self._handle_worker_result)
        worker.signals.error.connect(self._handle_worker_error)
        self._workers[task_id] = worker
        self._thread_pool.start(worker)

    def _prepare_customizer(self, absolute_path: Path) -> None:
        self._tabs.setTabEnabled(self._customizer_tab_index, False)
        self._customizer_panel.clear()
        
        try:
            context = self._build_customizer_context(absolute_path)
            self._customizer_context = context

            if context is not None:
                try:
                    self._customizer_panel.set_session(
                        backend=context.backend,
                        schema=context.schema,
                        source_path=context.source_path,
                        base_asset=context.base_asset,
                        values=context.values,
                    )
                except Exception:
                    logger.exception(
                        "Failed to initialise customizer for %s", absolute_path
                    )
                    self._customizer_context = None
                else:
                    self._tabs.setTabEnabled(self._customizer_tab_index, True)

            self._refresh_customization_summary(absolute_path)
        except (RecursionError, ValueError, OSError) as e:
            logger.error("Failed to prepare customizer due to path issues: %s", e)
            self._customizer_context = None

    def _build_customizer_context(
        self, absolute_path: Path
    ) -> CustomizerSessionConfig | None:
        if self._asset_service is None:
            return None

        suffix = absolute_path.suffix.lower()
        if suffix == ".scad" and self._asset_record is not None:
            return self._build_context_for_source(absolute_path, self._asset_record)

        metadata = self._asset_metadata
        customization_meta = (
            metadata.get("customization") if isinstance(metadata, Mapping) else None
        )
        if isinstance(customization_meta, Mapping):
            return self._build_context_for_derivative(customization_meta)
        return None

    def _build_context_for_source(
        self, absolute_path: Path, asset: AssetRecord
    ) -> CustomizerSessionConfig | None:
        backend = OpenSCADBackend()
        try:
            schema = backend.load_schema(absolute_path)
        except Exception:
            logger.exception("Failed to load OpenSCAD schema for %s", absolute_path)
            return None

        if not schema.parameters:
            return None

        latest = self._latest_customization_for_path(asset.path)
        values: Mapping[str, Any] | None = None
        customization_id = None
        if latest is not None:
            values = dict(latest.parameter_values)
            customization_id = latest.id
        else:
            metadata = (
                asset.metadata.get("customization")
                if isinstance(asset.metadata, Mapping)
                else None
            )
            if isinstance(metadata, Mapping):
                maybe_values = metadata.get("parameters")
                if isinstance(maybe_values, Mapping):
                    values = dict(maybe_values)

        return CustomizerSessionConfig(
            backend=backend,
            schema=schema,
            source_path=absolute_path,
            base_asset=asset,
            values=values,
            customization_id=customization_id,
        )

    def _build_context_for_derivative(
        self, customization_meta: Mapping[str, Any]
    ) -> CustomizerSessionConfig | None:
        if self._asset_service is None:
            return None

        backend_identifier = str(customization_meta.get("backend") or "")
        backend = self._backend_from_identifier(backend_identifier)
        if backend is None:
            return None

        base_path = customization_meta.get("base_asset_path")
        if not isinstance(base_path, str) or not base_path.strip():
            return None
        base_path = base_path.strip()
        base_asset = self._asset_service.get_asset_by_path(base_path)
        if base_asset is None:
            return None

        source_path = Path(base_asset.path).expanduser()
        record: CustomizationRecord | None = None
        customization_id = customization_meta.get("id")
        if customization_id is not None:
            try:
                record = self._fetch_customization_record(int(customization_id))
            except Exception:
                record = None

        schema: ParameterSchema | None = None
        values: Mapping[str, Any] | None = None
        if record is not None:
            schema = ParameterSchema.from_dict(record.parameter_schema)
            values = dict(record.parameter_values)
            customization_id = record.id
        else:
            try:
                schema = backend.load_schema(source_path)
            except Exception:
                logger.exception(
                    "Failed to load customizer schema for base %s", source_path
                )
                return None
            maybe_values = customization_meta.get("parameters")
            if isinstance(maybe_values, Mapping):
                values = dict(maybe_values)

        if schema is None or not schema.parameters:
            return None

        normalized_id = (
            int(customization_id)
            if customization_id is not None and str(customization_id).strip()
            else None
        )

        return CustomizerSessionConfig(
            backend=backend,
            schema=schema,
            source_path=source_path,
            base_asset=base_asset,
            values=values,
            customization_id=normalized_id,
        )

    def _backend_from_identifier(self, identifier: str) -> OpenSCADBackend | None:
        normalized = identifier.strip().casefold()
        if normalized in {"openscad", "three_dfs.customizer.openscad"}:
            return OpenSCADBackend()
        return None

    def _latest_customization_for_path(
        self, base_path: str
    ) -> CustomizationRecord | None:
        if self._asset_service is None:
            return None
        try:
            records = self._asset_service.list_customizations_for_asset(base_path)
        except Exception:
            return None
        if not records:
            return None
        return max(records, key=lambda record: record.updated_at)

    def _fetch_customization_record(
        self, customization_id: int
    ) -> CustomizationRecord | None:
        if self._asset_service is None:
            return None
        try:
            return self._asset_service.get_customization(customization_id)
        except Exception:
            return None

    def _list_derivatives_for_path(self, base_path: str) -> list[AssetRecord]:
        if self._asset_service is None:
            return []
        try:
            return self._asset_service.list_derivatives_for_asset(base_path)
        except Exception:
            return []

    def _refresh_customization_summary(self, absolute_path: Path) -> None:
        self._clear_customization_actions()

        summary_parts: list[str] = []
        parameter_html = ""

        suffix = absolute_path.suffix.lower()
        if suffix == ".scad" and self._asset_record is not None:
            summary_parts, parameter_html = self._summarize_base_asset(
                self._asset_record
            )
        else:
            summary_parts, parameter_html = self._summarize_derivative()

        if self._customizer_context is not None:
            button_label = "Customize…" if suffix == ".scad" else "Reopen Customizer…"
            self._customize_button.setText(button_label)
            self._customize_button.setEnabled(True)
            self._customize_button.setVisible(True)
            self._customize_button.setToolTip("")
        else:
            self._customize_button.setEnabled(False)
            self._customize_button.setVisible(False)

        if summary_parts:
            self._customization_summary_label.setText(" ".join(summary_parts))
        else:
            self._customization_summary_label.clear()

        if parameter_html:
            self._customization_parameters_label.setText(parameter_html)
            self._customization_parameters_label.setVisible(True)
        else:
            self._customization_parameters_label.clear()
            self._customization_parameters_label.setVisible(False)

        actions_visible = bool(self._customization_action_buttons)
        self._customization_actions_widget.setVisible(actions_visible)
        self._customization_frame.setVisible(
            bool(summary_parts or parameter_html or actions_visible)
        )

    def _summarize_base_asset(self, asset: AssetRecord) -> tuple[list[str], str]:
        parts: list[str] = []
        parameter_html = ""

        derivatives = self._list_derivatives_for_path(asset.path)
        if derivatives:
            count = len(derivatives)
            plural = "s" if count != 1 else ""
            parts.append(f"{count} customized artifact{plural} available.")
            for derivative in derivatives[:3]:
                label = derivative.label or Path(derivative.path).name
                self._add_customization_action_button(f"Open {label}", derivative.path)
        else:
            parts.append("No customized artifacts recorded yet.")

        latest = self._latest_customization_for_path(asset.path)
        if latest is not None:
            parts.append(
                "Last run on "
                f"{_format_datetime(latest.updated_at)} via "
                f"{latest.backend_identifier}."
            )
            parameter_html = self._format_parameter_summary(latest.parameter_values)
        elif (
            self._customizer_context is not None
            and self._customizer_context.values is not None
        ):
            parameter_html = self._format_parameter_summary(
                self._customizer_context.values
            )

        return parts, parameter_html

    def _summarize_derivative(self) -> tuple[list[str], str]:
        parts: list[str] = []
        parameter_html = ""

        metadata = self._asset_metadata
        customization_meta = (
            metadata.get("customization") if isinstance(metadata, Mapping) else None
        )
        if not isinstance(customization_meta, Mapping):
            return parts, parameter_html

        base_path_raw = customization_meta.get("base_asset_path")
        base_label_raw = customization_meta.get("base_asset_label")
        base_path = base_path_raw.strip() if isinstance(base_path_raw, str) else None
        descriptor = (
            base_label_raw.strip()
            if isinstance(base_label_raw, str) and base_label_raw.strip()
            else None
        )
        if descriptor is None and base_path:
            descriptor = Path(base_path).name

        generated_at = _parse_iso_datetime(customization_meta.get("generated_at"))
        if descriptor:
            if generated_at is not None:
                parts.append(
                    f"Derived from {descriptor} on {_format_datetime(generated_at)}."
                )
            else:
                parts.append(f"Derived from {descriptor}.")
        elif generated_at is not None:
            parts.append(f"Customized on {_format_datetime(generated_at)}.")

        try:
            status = evaluate_customization_status(
                customization_meta,
                base_path=Path(base_path) if base_path else None,
            )
        except Exception:
            status_text = None
        else:
            status_text = _format_customization_status(status)
        if status_text:
            parts.append(status_text)

        relationship = customization_meta.get("relationship")
        if isinstance(relationship, str) and relationship.strip():
            parts.append(f"Relationship: {relationship.strip()}.")

        parameters = customization_meta.get("parameters")
        if isinstance(parameters, Mapping):
            parameter_html = self._format_parameter_summary(parameters)

        if base_path:
            descriptor_text = descriptor or base_path
            self._add_customization_action_button(
                f"View base: {descriptor_text}", base_path
            )
            for derivative in self._list_derivatives_for_path(base_path):
                if (
                    self._asset_record is not None
                    and derivative.path == self._asset_record.path
                ):
                    continue
                label = derivative.label or Path(derivative.path).name
                self._add_customization_action_button(f"Open {label}", derivative.path)
                if len(self._customization_action_buttons) >= 3:
                    break

        return parts, parameter_html

    def _format_parameter_summary(self, parameters: Mapping[str, Any] | None) -> str:
        if not isinstance(parameters, Mapping) or not parameters:
            return ""

        items = []
        for name in sorted(parameters):
            value = parameters[name]
            escaped_name = html.escape(str(name))
            escaped_value = html.escape(self._format_parameter_value(value))
            items.append(f"<li><b>{escaped_name}</b>: {escaped_value}</li>")
        return f"<ul>{''.join(items)}</ul>"

    def _format_parameter_value(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.4g}"
        return str(value)

    # ------------------------------------------------------------------
    # Text preview helpers (instance level)
    # ------------------------------------------------------------------

    def _add_customization_action_button(self, text: str, target_path: str) -> None:
        if not target_path:
            return
        button = QPushButton(text, self._customization_actions_widget)
        button.setObjectName("previewCustomizationAction")
        button.clicked.connect(partial(self._handle_navigation, target_path))
        self._customization_actions_layout.addWidget(button)
        self._customization_action_buttons.append(button)
        self._customization_actions_widget.setVisible(True)

    def _clear_customization_actions(self) -> None:
        while self._customization_actions_layout.count():
            item = self._customization_actions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._customization_action_buttons.clear()
        self._customization_actions_widget.setVisible(False)

    def _handle_navigation(self, target_path: str) -> None:
        self.navigationRequested.emit(target_path)

    def _ensure_customizer_dialog(self) -> CustomizerDialog:
        if self._customizer_dialog is None:
            if self._asset_service is None:
                raise RuntimeError("Customization requires an AssetService instance")
            dialog = CustomizerDialog(asset_service=self._asset_service, parent=self)
            dialog.customizationSucceeded.connect(self._handle_customizer_success)
            self._customizer_dialog = dialog
        return self._customizer_dialog

    def launch_customizer(self) -> None:
        context = self._customizer_context
        if context is None:
            return
        dialog = self._ensure_customizer_dialog()
        dialog.set_session(context)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _handle_customizer_success(self, result: object) -> None:
        pipeline_result = result if isinstance(result, PipelineResult) else None
        if pipeline_result is None:
            return

        if self._asset_service is not None:
            try:
                refreshed = self._asset_service.get_asset_by_path(
                    pipeline_result.base_asset.path
                )
            except Exception:
                refreshed = None
            if (
                refreshed is not None
                and self._asset_record is not None
                and refreshed.path == self._asset_record.path
            ):
                self._asset_record = refreshed
                self._asset_metadata = dict(refreshed.metadata)

        if self._current_absolute_path is not None:
            self._prepare_customizer(Path(self._current_absolute_path))

        self.customizationGenerated.emit(pipeline_result)

    @property
    def can_customize(self) -> bool:
        return self._customizer_context is not None

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
            or str(outcome.path) != self._current_absolute_path
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

        self._configure_text_preview(outcome)
        metadata_entries = list(outcome.metadata)
        if self._viewer_error_message:
            metadata_entries.append(("3D Viewer", self._viewer_error_message))
        if outcome.text_content is None and self._text_unavailable_message:
            metadata_entries.append(("Text Preview", self._text_unavailable_message))
        self._populate_metadata(metadata_entries)
        if self._current_absolute_path is not None:
            self._prepare_customizer(Path(self._current_absolute_path))

    def _configure_text_preview(self, outcome: PreviewOutcome) -> None:
        if outcome.text_content is None:
            self._text_view.clear()
            self._tabs.setTabText(self._text_tab_index, "Text")
            self._tabs.setTabEnabled(self._text_tab_index, False)
            message = self._text_unavailable_message
            if not message:
                message = "Text preview is unavailable for this file."
            self._text_unavailable_message = message
            self._tabs.setTabToolTip(self._text_tab_index, message)
            self._text_view.setToolTip(message)
            return

        tab_label = _text_tab_label(outcome.text_role)
        self._text_view.setPlainText(outcome.text_content)
        self._tabs.setTabText(self._text_tab_index, tab_label)
        self._tabs.setTabEnabled(self._text_tab_index, True)
        self._tabs.setTabToolTip(self._text_tab_index, "")
        if outcome.text_truncated:
            self._text_view.setToolTip("Preview truncated for large file")
        else:
            self._text_view.setToolTip("")
        self._text_unavailable_message = None

        if self._current_pixmap is None and not self._tabs.isTabEnabled(
            self._viewer_tab_index
        ):
            self._tabs.setCurrentIndex(self._text_tab_index)

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
            display_value = self._stringify_metadata_value(value)
            item = QListWidgetItem(f"{key}: {display_value}")
            item.setFlags(Qt.ItemIsEnabled)
            if "\n" in display_value:
                item.setToolTip(display_value)
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
                display_value = self._stringify_metadata_value(value)
                item = QListWidgetItem(f"{key}: {display_value}")
                item.setFlags(Qt.ItemIsEnabled)
                if "\n" in display_value:
                    item.setToolTip(display_value)
                self._metadata_list.addItem(item)

    def _stringify_metadata_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list | tuple | set):
            parts: list[str] = []
            for entry in value:
                if isinstance(entry, Mapping):
                    label = str(entry.get("label") or "").strip()
                    target = entry.get("url") or entry.get("href") or entry.get("path")
                    target_str = str(target or "").strip()
                    if label and target_str:
                        parts.append(f"{label}: {target_str}")
                    elif target_str:
                        parts.append(target_str)
                    elif label:
                        parts.append(label)
                    else:
                        parts.append(self._stringify_metadata_value(entry))
                else:
                    parts.append(self._stringify_metadata_value(entry))
            return "\n".join(part for part in parts if part)
        if isinstance(value, Mapping):
            mapped: list[str] = []
            for key, entry_value in value.items():
                formatted = self._stringify_metadata_value(entry_value)
                label = str(key)
                mapped.append(f"{label}: {formatted}" if formatted else label)
            return "\n".join(mapped)
        return str(value)


# ----------------------------------------------------------------------
# Text preview helpers
# ----------------------------------------------------------------------


def _text_tab_label(role: str | None) -> str:
    if role == "openscad":
        return "OpenSCAD"
    if role == "build123d":
        return "Build123D"
    if role == "python":
        return "Script"
    return "Text"


def _textual_thumbnail_message(role: str | None) -> str:
    if role is None:
        return "No thumbnail available for this file type."
    tab_label = _text_tab_label(role)
    return f"View content in the {tab_label} tab."


def _extract_text_preview(
    path: Path, mime_type: str | None, *, max_bytes: int
) -> tuple[str | None, str | None, bool]:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS or suffix in _MODEL_EXTENSIONS:
        return None, None, False

    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        return None, None, False

    if not raw:
        role = _detect_text_role(path, mime_type, "")
        return "", role, False

    if b"\x00" in raw:
        return None, None, False

    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]

    text = raw.decode("utf-8", errors="replace")
    role = _detect_text_role(path, mime_type, text)
    if role is None:
        return None, None, False

    if truncated:
        text += "\n… (preview truncated)"

    return text, role, truncated


def _detect_text_role(path: Path, mime_type: str | None, text: str) -> str | None:
    suffix = path.suffix.lower()

    if suffix == ".scad":
        return "openscad"

    lowered = text.lower()
    if suffix == ".py":
        if "build123d" in lowered:
            return "build123d"
        return "python"

    if mime_type and mime_type.startswith("text/"):
        return "text"

    if suffix in _TEXT_PREVIEW_EXTENSIONS:
        return "text"

    if lowered and lowered.strip():
        return "text"

    return None


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
    text_preview_limit: int = DEFAULT_TEXT_PREVIEW_MAX_BYTES,
) -> PreviewOutcome:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    mime_type, _ = mimetypes.guess_type(path.name)

    text_content, text_role, truncated = _extract_text_preview(
        path, mime_type, max_bytes=text_preview_limit
    )

    metadata: list[tuple[str, str]] = []
    stat = path.stat()
    metadata.append(("Kind", _classify_kind(path, text_role)))
    metadata.append(("Size", _format_size(stat.st_size)))
    metadata.append(("Modified", _format_timestamp(stat.st_mtime)))
    metadata.append(("Location", str(path)))

    if mime_type:
        metadata.append(("MIME Type", mime_type))

    suffix = path.suffix.lower()

    if suffix == ".scad":
        metadata.extend(
            _build_customizer_metadata_for_source(
                path,
                asset_record,
                asset_service,
            )
        )

    if text_content is not None:
        preview_status = "Truncated" if truncated else "Complete"
        metadata.append(("Text Preview", f"{preview_status} content available"))

    if suffix in _IMAGE_EXTENSIONS:
        image_metadata, thumbnail_bytes = _build_image_preview(path)
        metadata.extend(image_metadata)
        return PreviewOutcome(
            path=path,
            metadata=metadata,
            thumbnail_bytes=thumbnail_bytes,
            text_content=text_content,
            text_role=text_role,
            text_truncated=truncated,
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
            text_content=text_content,
            text_role=text_role,
            text_truncated=truncated,
        )

    metadata.append(("Type", path.suffix or "Unknown"))
    return PreviewOutcome(
        path=path,
        metadata=metadata,
        thumbnail_bytes=None,
        thumbnail_message=_textual_thumbnail_message(text_role),
        text_content=text_content,
        text_role=text_role,
        text_truncated=truncated,
    )


def _build_customizer_metadata_for_source(
    source_path: Path,
    asset_record: AssetRecord | None,
    asset_service: AssetService | None,
) -> list[tuple[str, str]]:
    if asset_service is None or asset_record is None:
        return []

    try:
        derivatives = asset_service.list_derivatives_for_asset(asset_record.path)
    except Exception:
        return []

    entries: list[tuple[str, str]] = []
    if not derivatives:
        return [("Customized Outputs", "No customized artifacts recorded yet.")]

    for derivative in derivatives:
        label = derivative.label or Path(derivative.path).name
        customization_meta = derivative.metadata.get("customization")
        status_text = "Metadata unavailable."
        if isinstance(customization_meta, Mapping):
            status = evaluate_customization_status(
                customization_meta,
                base_path=source_path,
            )
            status_text = _format_customization_status(status)
        entries.append(("Customized Output", f"{label} - {status_text}"))

    return entries


def _format_customizer_source(
    base_asset: AssetRecord | None,
    status: CustomizationStatus,
    customization_metadata: Mapping[str, Any],
) -> str:
    label = None
    if base_asset is not None and base_asset.label:
        label = base_asset.label
    else:
        candidate = customization_metadata.get("base_asset_label")
        if isinstance(candidate, str) and candidate.strip():
            label = candidate.strip()

    path_text = None
    if status.base_path is not None:
        path_text = str(status.base_path)
    else:
        candidate_path = customization_metadata.get("base_asset_path")
        if isinstance(candidate_path, str) and candidate_path.strip():
            path_text = candidate_path.strip()

    if label and path_text:
        return f"{label} ({path_text})"
    if label:
        return label
    if path_text:
        return path_text
    return "Unknown source"


def _format_customization_status(status: CustomizationStatus) -> str:
    if status.is_outdated:
        if status.current_source_mtime is not None:
            return (
                "Out of date (source updated "
                f"{_format_datetime(status.current_source_mtime)})"
            )
        return "Out of date (source unavailable)"

    if status.reason == "In sync with base source." and status.recorded_source_mtime:
        return (
            "In sync (source timestamp "
            f"{_format_datetime(status.recorded_source_mtime)})"
        )

    return status.reason


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _format_datetime(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return _format_timestamp(aware.timestamp())


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
    elif isinstance(previews, Iterable) and not isinstance(previews, str | bytes):
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

    base_asset: AssetRecord | None = None
    if asset_service is not None and asset_record is not None:
        try:
            base_asset = asset_service.get_base_for_derivative(asset_record.path)
        except Exception:
            base_asset = None

    customization_metadata: Mapping[str, Any] | None = None
    if isinstance(asset_metadata, Mapping):
        candidate = asset_metadata.get("customization")
        if isinstance(candidate, Mapping):
            customization_metadata = candidate

    if customization_metadata is not None:
        status = evaluate_customization_status(
            customization_metadata,
            base_path=base_asset.path if base_asset is not None else None,
        )
        source_display = _format_customizer_source(
            base_asset,
            status,
            customization_metadata,
        )
        metadata.append(("Customizer Source", source_display))
        metadata.append(("Customizer Status", _format_customization_status(status)))

        generated_at = _parse_iso_datetime(customization_metadata.get("generated_at"))
        if generated_at is not None:
            metadata.append(("Customized On", _format_datetime(generated_at)))

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
        except TypeError as exc:
            # Backward-compat: older cache implementations may not accept
            # the "metadata" keyword. Retry without it.
            if "metadata" in str(exc):
                try:
                    thumbnail_result = cache.get_or_render(
                        path,
                        existing_info=existing_info,
                        size=size,
                    )
                except ThumbnailGenerationError:
                    thumbnail_result = None
            else:
                raise
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


def _classify_kind(path: Path, text_role: str | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "Image"
    if suffix in _MODEL_EXTENSIONS:
        return "3D Model"
    if text_role == "openscad":
        return "OpenSCAD Source"
    if text_role == "build123d":
        return "Build123D Script"
    if text_role == "python":
        return "Python Script"
    if text_role == "text":
        return "Text Document"
    if suffix == ".scad":
        return "OpenSCAD Source"
    if suffix == ".py":
        return "Python Script"
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

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..storage import AssetRecord, AssetService, ContainerVersionRecord
from ..thumbnails import ThumbnailCache, ThumbnailGenerationError
from ..utils.undo import ActionHistory
from .preview_pane import PreviewPane

logger = logging.getLogger(__name__)

_METADATA_ROLE = Qt.UserRole + 2
_ASSET_ID_ROLE = Qt.UserRole + 3
_PRIMARY_ROLE = Qt.UserRole + 4


@dataclass(slots=True)
class ContainerComponent:
    path: str
    label: str
    kind: str = "component"  # "component", "attachment", "link", etc.
    metadata: dict[str, Any] | None = None
    asset_id: int | None = None


class ContainerPane(QWidget):
    """Show container metadata and component list with live preview."""

    navigationRequested = Signal(str)
    """Emitted when the pane requests navigation to another asset."""

    addAttachmentsRequested = Signal()
    """Emitted when the user requests to add attachments to the current container."""

    openFolderRequested = Signal()
    """Emitted when the user requests to open the current container folder."""

    openItemFolderRequested = Signal(str)
    """Emitted when the user requests to open the folder of a specific item."""

    backRequested = Signal()
    """Emitted when the user requests to navigate back in history."""

    linkContainerRequested = Signal()
    """Emitted when the user requests to link another container."""

    importLinkedComponentRequested = Signal()
    """Emitted when the user wants to import a component from a linked container."""

    navigateToPathRequested = Signal(str)
    """Emitted when the user requests to navigate to a specific path."""

    filesDropped = Signal(list)
    """Emitted when files are dropped onto the pane."""

    refreshRequested = Signal()
    """Emitted when the user requests to refresh the current container."""

    versionSelected = Signal(object)
    """Emitted when the user selects a specific container version."""

    createVersionRequested = Signal()
    """Emitted when the user requests to snapshot the container state."""

    manageVersionsRequested = Signal()
    """Emitted when the user wants to rename or delete versions."""

    setPrimaryComponentRequested = Signal(str)
    """Emitted when the user requests to set a component as primary."""

    tagFilterRequested = Signal(str)
    """Emitted when the user requests filtering by a specific tag."""

    def __init__(
        self,
        parent: QWidget | None = None,
        asset_service: AssetService | None = None,
        undo_history: ActionHistory | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_service = asset_service
        self._undo_history: ActionHistory | None = undo_history
        self._container_path: str | None = None
        self._container_asset_id: int | None = None
        self._selected_version_id: int | None = None
        self._versions: list[ContainerVersionRecord] = []
        self._suspend_version_signal = False

        self._components = QListWidget(self)
        self._attachments = QListWidget(self)
        self._linked_here = QListWidget(self)
        self._links = QListWidget(self)
        self._attachments.currentItemChanged.connect(self._handle_component_selected)
        self._attachments.setContextMenuPolicy(Qt.CustomContextMenu)
        self._attachments.customContextMenuRequested.connect(self._show_attachments_context_menu)
        self._linked_here.currentItemChanged.connect(self._handle_component_selected)
        self._links.currentItemChanged.connect(self._handle_component_selected)
        self._components.setObjectName("containerComponents")
        self._components.setSelectionMode(QAbstractItemView.SingleSelection)
        self._attachments.setSelectionMode(QAbstractItemView.SingleSelection)
        self._linked_here.setSelectionMode(QAbstractItemView.SingleSelection)
        self._links.setSelectionMode(QAbstractItemView.SingleSelection)
        self._components.currentItemChanged.connect(self._handle_component_selected)
        self._suppress_link_navigation = False
        self._icon_size = QSize(48, 48)
        self._components.setIconSize(self._icon_size)
        self._components.setContextMenuPolicy(Qt.CustomContextMenu)
        self._components.customContextMenuRequested.connect(self._show_components_context_menu)
        self._has_linked_containers = False
        self._links.setContextMenuPolicy(Qt.CustomContextMenu)
        self._links.customContextMenuRequested.connect(self._show_links_context_menu)
        self._components.itemActivated.connect(self._handle_component_activated)

        self._preview = PreviewPane(
            parent=self,
            asset_service=self._asset_service,
        )
        self._preview.tagFilterRequested.connect(self.tagFilterRequested)

        self._components_group = QGroupBox("Components")
        components_layout = QVBoxLayout()
        components_layout.addWidget(self._components)
        self._components_group.setLayout(components_layout)

        self._attachments_group = QGroupBox("Attachments")
        attachments_layout = QVBoxLayout()
        attachments_layout.addWidget(self._attachments)
        self._attachments_group.setLayout(attachments_layout)

        self._linked_here_group = QGroupBox("Linked Here")
        linked_here_layout = QVBoxLayout()
        linked_here_layout.addWidget(self._linked_here)
        self._linked_here_group.setLayout(linked_here_layout)

        self._links_group = QGroupBox("Links")
        links_layout = QVBoxLayout()
        links_layout.addWidget(self._links)
        self._links_group.setLayout(links_layout)

        left_pane_layout = QVBoxLayout()
        left_pane_layout.addWidget(self._components_group)
        left_pane_layout.addWidget(self._attachments_group)
        left_pane_layout.addWidget(self._linked_here_group)
        left_pane_layout.addWidget(self._links_group)

        left_pane_widget = QWidget()
        left_pane_widget.setLayout(left_pane_layout)

        splitter = QSplitter(self)
        splitter.addWidget(left_pane_widget)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # Actions + Search
        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(8)
        self._btn_back = QPushButton("Back", self)
        self._btn_back.setToolTip("Return to previous container")
        self._btn_back.clicked.connect(self.backRequested)
        self._btn_refresh = QPushButton("Refresh", self)
        self._btn_refresh.setToolTip("Rescan this container folder")
        self._btn_refresh.clicked.connect(self.refreshRequested)
        self._btn_add_attachments = QPushButton("Upload File(s)", self)
        self._btn_add_attachments.setToolTip("Upload files to the current container")
        self._btn_add_attachments.clicked.connect(self.addAttachmentsRequested)
        self._btn_link_container = QPushButton("Link Container", self)
        self._btn_link_container.setToolTip("Create a link to another container")
        self._btn_link_container.clicked.connect(self.linkContainerRequested)
        self._btn_open_folder = QPushButton("Open Folder", self)
        self._btn_open_folder.clicked.connect(self.openFolderRequested)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search components and uploads…  (Ctrl+F)")
        self._search.textChanged.connect(self._apply_filter)
        actions_row.addWidget(self._btn_back)
        actions_row.addWidget(self._btn_refresh)
        actions_row.addWidget(self._btn_add_attachments)
        actions_row.addWidget(self._btn_link_container)
        actions_row.addWidget(self._btn_open_folder)
        actions_row.addStretch(1)
        actions_row.addWidget(self._search, 2)

        self._title = QLabel(self)
        self._title.setObjectName("containerTitle")
        self._path_label = QLabel(self)
        self._path_label.setObjectName("containerPath")

        self._version_selector = QComboBox(self)
        self._version_selector.setObjectName("containerVersionSelector")
        self._version_selector.currentIndexChanged.connect(self._handle_version_selector_changed)
        self._btn_create_version = QPushButton("Create Version", self)
        self._btn_create_version.setToolTip("Snapshot the current container as a named version")
        self._btn_create_version.clicked.connect(self.createVersionRequested)
        self._btn_manage_versions = QPushButton("Manage…", self)
        self._btn_manage_versions.setToolTip("Rename or delete saved versions")
        self._btn_manage_versions.clicked.connect(self.manageVersionsRequested)
        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.setSpacing(6)
        version_label = QLabel("Version:", self)
        version_row.addWidget(version_label)
        version_row.addWidget(self._version_selector, 1)
        version_row.addWidget(self._btn_create_version)
        version_row.addWidget(self._btn_manage_versions)
        self._version_summary = QLabel(self)
        self._version_summary.setObjectName("containerVersionSummary")
        self._version_summary.setText("Showing working copy")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._title)
        layout.addWidget(self._path_label)
        layout.addLayout(version_row)
        layout.addWidget(self._version_summary)
        layout.addLayout(actions_row)
        layout.addWidget(splitter, 1)

        self._thread_pool = QThreadPool.globalInstance()
        self._current_signature: tuple[Any, ...] | None = None
        # Shortcut to focus search
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)

        self.set_container_versions([], selected_version_id=None)

    def set_container(
        self,
        path: str,
        *,
        asset_id: int | None = None,
        label: str | None = None,
        components: Iterable[ContainerComponent] = (),
        attachments: Iterable[ContainerComponent] = (),
        linked_here: Iterable[ContainerComponent] = (),
        links: Iterable[ContainerComponent] = (),
        version_id: int | None = None,
    ) -> None:
        """Populate the pane with data for the container at *path*."""

        self._container_path = path
        self._container_asset_id = asset_id
        self._selected_version_id = version_id

        self._title.setText(label or Path(path).name)
        self._path_label.setText(path)

        component_list = list(components)
        attachment_list = list(attachments)
        linked_here_list = list(linked_here)
        link_list = list(links)
        self._has_linked_containers = bool(link_list)

        self._components.clear()
        self._attachments.clear()
        self._linked_here.clear()
        self._links.clear()

        for component in component_list:
            self._add_component_to_list(self._components, component)
        for attachment in attachment_list:
            self._add_component_to_list(self._attachments, attachment)
        for linked_item in linked_here_list:
            self._add_component_to_list(self._linked_here, linked_item)
        for link in link_list:
            self._add_component_to_list(self._links, link)

        self._components.setVisible(bool(component_list))
        self._attachments.setVisible(bool(attachment_list))
        self._linked_here.setVisible(bool(linked_here_list))
        self._links.setVisible(bool(link_list))

        self._components_group.setVisible(bool(component_list))
        self._attachments_group.setVisible(bool(attachment_list))
        self._linked_here_group.setVisible(bool(linked_here_list))
        self._links_group.setVisible(bool(link_list))

        self._update_action_states()
        self._version_summary.setText(self._describe_version_selection())

    def focus_matching_item(self, targets: Iterable[str]) -> None:
        """Select the first list item whose normalized path matches *targets*."""

        normalized_targets: set[Path] = set()
        for value in targets:
            if not isinstance(value, str) or not value:
                continue
            candidate = self._coerce_path(value)
            if candidate is None:
                continue
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:
                try:
                    resolved = candidate.expanduser()
                except Exception:
                    continue
            normalized_targets.add(resolved)
        if not normalized_targets:
            return

        for row in range(self._components.count()):
            item = self._components.item(row)
            if not (item.flags() & Qt.ItemIsSelectable):
                continue
            raw_path = item.data(Qt.UserRole)
            if raw_path is None:
                continue
            candidate = self._coerce_path(str(raw_path))
            if candidate is None:
                continue
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:
                continue
            if resolved in normalized_targets:
                self._components.setCurrentItem(item)
                self._components.scrollToItem(item)
                break

    def set_container_versions(
        self,
        versions: Sequence[ContainerVersionRecord],
        *,
        selected_version_id: int | None = None,
    ) -> None:
        """Populate the version selector for the active container."""

        self._versions = list(versions)
        self._suspend_version_signal = True
        self._version_selector.clear()
        self._version_selector.addItem("Working Copy")
        self._version_selector.setItemData(0, None, Qt.UserRole)
        self._version_selector.setItemData(
            0,
            "Displays the latest changes in this container",
            Qt.ToolTipRole,
        )
        for index, record in enumerate(self._versions, start=1):
            label = self._format_version_label(record)
            self._version_selector.addItem(label)
            self._version_selector.setItemData(index, record.id, Qt.UserRole)
            self._version_selector.setItemData(
                index,
                self._format_version_tooltip(record),
                Qt.ToolTipRole,
            )

        self._selected_version_id = (
            selected_version_id
            if selected_version_id is None or any(version.id == selected_version_id for version in self._versions)
            else None
        )

        target_index = 0
        if self._selected_version_id is not None:
            for row in range(1, self._version_selector.count()):
                data = self._version_selector.itemData(row, Qt.UserRole)
                if data == self._selected_version_id:
                    target_index = row
                    break
        self._version_selector.setCurrentIndex(target_index)
        self._version_summary.setText(self._describe_version_selection())
        self._suspend_version_signal = False
        self._update_action_states()

    def _render_linked_here_section(self, entries: Iterable[ContainerComponent]) -> list[str]:
        components = list(entries)
        if not components:
            return []

        header = QListWidgetItem("Linked Here")
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        header.setFlags(Qt.ItemIsEnabled)
        self._components.addItem(header)

        icon_paths: list[str] = []
        for component in components:
            metadata = dict(component.metadata or {})
            source_is_container = bool(metadata.get("source_is_container"))
            display_text = component.label or component.path or "Linked Container"
            display_text = self._decorate_link_source_label(
                display_text,
                is_container=source_is_container,
            )
            source_label = metadata.get("source_label") or display_text
            if isinstance(source_label, str):
                source_label = self._decorate_link_source_label(
                    source_label,
                    is_container=source_is_container,
                )
            metadata["source_is_container"] = source_is_container
            metadata["source_label"] = source_label

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, component.path or "")
            kind = component.kind or "linked_here"
            if not kind:
                kind = "linked_here"
            item.setData(Qt.UserRole + 1, kind)
            item.setData(_METADATA_ROLE, metadata or None)
            item.setData(_PRIMARY_ROLE, False)

            tooltip_parts: list[str] = []
            if display_text:
                tooltip_parts.append(display_text)
            if isinstance(source_label, str) and source_label and source_label != display_text:
                tooltip_parts.append(source_label)
            target = metadata.get("link_target") or metadata.get("source_path")
            if isinstance(target, str) and target:
                tooltip_parts.append(target)
            tooltip = "\n".join(part for part in tooltip_parts if part)
            if tooltip:
                item.setToolTip(tooltip)

            self._components.addItem(item)

            if isinstance(target, str) and target and target not in icon_paths:
                icon_paths.append(target)

        return icon_paths

    def _components_from_linked_from_metadata(self, linked_from_meta: object) -> list[ContainerComponent]:
        entries: list[dict[str, Any]] = []
        if isinstance(linked_from_meta, Mapping):
            entries.append(dict(linked_from_meta))
        elif isinstance(linked_from_meta, list):
            for entry in linked_from_meta:
                if isinstance(entry, Mapping):
                    entries.append(dict(entry))
        if not entries:
            return []

        components: list[ContainerComponent] = []
        seen: set[tuple[Any, Any, str]] = set()
        asset_cache: dict[int, AssetRecord | None] = {}
        for entry in entries:
            link_id = entry.get("link_id")
            source_container_id = self._safe_int(entry.get("source_container_id"))
            source_path = str(entry.get("source_path") or "").strip()
            source_label = str(entry.get("source_label") or "").strip()

            source_container: AssetRecord | None = None
            if source_container_id is not None:
                if source_container_id in asset_cache:
                    source_container = asset_cache[source_container_id]
                else:
                    try:
                        source_container = self._asset_service.get_asset(source_container_id)
                    except Exception:
                        source_container = None
                    asset_cache[source_container_id] = source_container

            if source_container is not None:
                if not source_path:
                    source_path = source_container.path
                if not source_label:
                    source_label = source_container.metadata.get("display_name") or source_container.label

            if not source_label and source_path:
                try:
                    source_label = Path(source_path).name
                except Exception:
                    source_label = source_path

            if not source_label:
                if source_container_id is not None:
                    source_label = f"Container {source_container_id}"
                else:
                    source_label = "Linked Container"

            component_path = source_path or source_label

            metadata: dict[str, Any] = {
                "link_target": source_path or "",
                "link_direction": "incoming",
            }
            if link_id is not None:
                metadata["link_id"] = link_id
            if source_container_id is not None:
                metadata["link_container_id"] = source_container_id
            if source_path:
                metadata["source_path"] = source_path

            if source_label:
                metadata["source_label"] = source_label

            key = (link_id, source_container_id, metadata.get("link_target"))
            if key in seen:
                continue
            seen.add(key)

            components.append(
                ContainerComponent(
                    path=component_path,
                    label=source_label,
                    kind="linked_here",
                    metadata=metadata,
                )
            )

        return components

    @staticmethod
    def _decorate_link_source_label(label: str, *, is_container: bool) -> str:
        try:
            text = str(label or "").strip()
        except Exception:
            text = ""
        if not text:
            return text
        # The `is_container` flag is now deprecated, as all top-level containers are treated uniformly.
        # The UI will no longer distinguish between "Container:" and other labels.
        return text

    def set_container_from_asset(self, container_asset: AssetRecord) -> None:
        """Set container display from a container asset."""
        previous_selection = self.selected_item()
        self._container_asset_id = container_asset.id
        self._current_container_root = self._coerce_path(container_asset.path)
        self._container_path = container_asset.path

        metadata = container_asset.metadata or {}
        label = metadata.get("display_name") or container_asset.label

        components = [ContainerComponent(**c) for c in metadata.get("components", [])]
        attachments = [ContainerComponent(**a) for a in metadata.get("files", [])]
        links = [ContainerComponent(**link_entry) for link_entry in metadata.get("links", [])]
        linked_here = self._components_from_linked_from_metadata(metadata.get("linked_from"))

        self.set_container(
            path=container_asset.path,
            asset_id=container_asset.id,
            label=label,
            components=components,
            attachments=attachments,
            links=links,
            linked_here=linked_here,
            version_id=None,
        )

        if previous_selection:
            target_path, _ = previous_selection
            if target_path:
                try:
                    self.select_item(target_path)
                except Exception:
                    pass

    @Slot()
    def _handle_component_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            self._preview.clear()
            return

        sender = self.sender()
        self._clear_other_list_selections(sender)

        comp_path = str(current.data(Qt.UserRole) or current.text())
        metadata_obj = current.data(_METADATA_ROLE)
        metadata = metadata_obj if isinstance(metadata_obj, dict) else None
        kind_value = current.data(Qt.UserRole + 1)
        kind = str(kind_value or "component").strip().casefold()
        if kind in {"link", "linked_here"}:
            if self._suppress_link_navigation:
                pass
            elif self._navigate_to_component(current):
                return
        preview_path = comp_path
        self._preview.set_item(
            preview_path,
            label=current.text(),
            metadata=metadata,
            asset_record=None,
            entry_kind=kind,
        )
        # Keep selection visible if filtered
        self._ensure_visible(current)

    def _clear_other_list_selections(self, sender: QObject | None) -> None:
        """Clear other panes without emitting signals that would blank the preview."""
        for list_widget in (
            self._components,
            self._attachments,
            self._linked_here,
            self._links,
        ):
            if list_widget is sender:
                continue
            self._clear_selection_silently(list_widget)

    @staticmethod
    def _clear_selection_silently(list_widget: QListWidget) -> None:
        was_blocked = list_widget.blockSignals(True)
        try:
            list_widget.clearSelection()
            list_widget.setCurrentRow(-1)
        finally:
            list_widget.blockSignals(was_blocked)

    # ------------------------------------------------------------------
    # Thumbnail icon worker
    # ------------------------------------------------------------------
    def _enqueue_icons(self, comp_paths: list[str], attach_paths: list[str]) -> None:
        for path in comp_paths:
            worker = _IconWorker(path, self._icon_size)
            worker.signals.result.connect(self._apply_icon)
            # errors are ignored silently to avoid noisy UI
            self._thread_pool.start(worker)
        for path in attach_paths:
            worker = _IconWorker(path, self._icon_size)
            worker.signals.result.connect(self._apply_icon)
            self._thread_pool.start(worker)

    @Slot(str, object)
    def _apply_icon(self, path: str, pixmap_obj: object) -> None:
        pixmap = pixmap_obj if isinstance(pixmap_obj, QPixmap) else None
        if pixmap is None or pixmap.isNull():
            return
        for row in range(self._components.count()):
            item = self._components.item(row)
            if str(item.data(Qt.UserRole) or "") == path:
                # Scale to icon size for crisp display
                icon_pm = pixmap.scaled(
                    self._icon_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                item.setIcon(QIcon(icon_pm))
                break

    # ------------------------------------------------------------------
    # Filtering & UX helpers
    # ------------------------------------------------------------------
    def _add_component_to_list(self, list_widget: QListWidget, component: ContainerComponent) -> None:
        item = QListWidgetItem(component.label)
        item.setData(Qt.UserRole, component.path)
        item.setData(Qt.UserRole + 1, component.kind)
        metadata = component.metadata if isinstance(component.metadata, Mapping) else None
        item.setData(_METADATA_ROLE, metadata)
        if component.kind == "linked_component":
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
            item.setForeground(QColor(64, 128, 192))
            tooltip_bits: list[str] = [component.label]
            link_meta = metadata.get("link_import") if isinstance(metadata, Mapping) else None
            if isinstance(link_meta, Mapping):
                source_label = str(link_meta.get("source_container_label") or "").strip()
                if source_label:
                    tooltip_bits.append(f"Source container: {source_label}")
                source_path = str(link_meta.get("source_component_path") or "").strip()
                if source_path:
                    tooltip_bits.append(source_path)
            item.setToolTip("\n".join(bit for bit in tooltip_bits if bit))
        list_widget.addItem(item)

    def _update_action_states(self) -> None:
        has_container = self._container_path is not None
        self._btn_back.setEnabled(True)  # TODO: This should be based on history
        self._btn_refresh.setEnabled(has_container)
        self._btn_add_attachments.setEnabled(has_container)
        self._btn_link_container.setEnabled(has_container)
        self._btn_open_folder.setEnabled(has_container)
        self._version_selector.setEnabled(has_container)
        self._btn_create_version.setEnabled(has_container and self._container_asset_id is not None)
        self._btn_manage_versions.setEnabled(has_container and bool(self._versions))

    def _handle_version_selector_changed(self, index: int) -> None:
        if self._suspend_version_signal:
            return
        raw_id = self._version_selector.itemData(index, Qt.UserRole)
        version_id: int | None
        try:
            version_id = int(raw_id)
        except (TypeError, ValueError):
            version_id = None
        self._selected_version_id = version_id
        self._version_summary.setText(self._describe_version_selection())
        self.versionSelected.emit(version_id)

    def _describe_version_selection(self) -> str:
        if self._selected_version_id is None:
            return "Showing working copy"
        version = self._resolve_version_by_id(self._selected_version_id)
        if version is None:
            return "Showing saved version"
        return f"Showing {self._format_version_label(version)}"

    def _format_version_tooltip(self, record: ContainerVersionRecord) -> str:
        timestamp = self._format_version_timestamp(record.created_at)
        details = [f"Created {timestamp}"]
        if record.notes:
            details.append(record.notes)
        return "\n".join(details)

    def _format_version_label(self, record: ContainerVersionRecord) -> str:
        timestamp = self._format_version_timestamp(record.created_at)
        return f"{record.name} – {timestamp}"

    @staticmethod
    def _format_version_timestamp(value: datetime) -> str:
        try:
            localized = value.astimezone()
        except Exception:
            localized = value
        return localized.strftime("%Y-%m-%d %H:%M")

    def _resolve_version_by_id(self, version_id: int | None) -> ContainerVersionRecord | None:
        if version_id is None:
            return None
        for record in self._versions:
            if record.id == version_id:
                return record
        return None

    def _focus_search(self) -> None:
        self._search.setFocus(Qt.TabFocusReason)
        self._search.selectAll()

    @Slot(str)
    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        for row in range(self._components.count()):
            item = self._components.item(row)
            # Always show headers
            if not item.flags() & Qt.ItemIsSelectable:
                item.setHidden(False)
                continue
            label = (item.text() or "").casefold()
            path = str(item.data(Qt.UserRole) or "").casefold()
            hide = bool(needle) and (needle not in label and needle not in path)
            item.setHidden(hide)

    def _ensure_visible(self, item: QListWidgetItem) -> None:
        list_widget = item.listWidget()
        if list_widget:
            list_widget.scrollToItem(item)

    def _delete_file_item(self, item: QListWidgetItem) -> None:
        raw_path = item.data(Qt.UserRole)
        path_str = str(raw_path or "").strip()
        if not path_str:
            return

        path = self._coerce_path(path_str)
        if path is None:
            return

        raw_kind = item.data(Qt.UserRole + 1)
        kind = str(raw_kind or "file").strip().casefold()
        metadata_obj = item.data(_METADATA_ROLE)
        entry_metadata = metadata_obj if isinstance(metadata_obj, Mapping) else {}
        link_import_meta = entry_metadata.get("link_import") if isinstance(entry_metadata, Mapping) else None
        entry_asset_id = self._safe_int(entry_metadata.get("asset_id")) if entry_metadata else None
        link_part_id = self._safe_int(entry_metadata.get("link_part_id")) if entry_metadata else None
        normalized_path = self._normalized_path_string(path)
        metadata_key: str | None
        if kind == "linked_here":
            return
        if kind == "component":
            metadata_key = "components"
        elif kind == "linked_component":
            metadata_key = "components"
        elif kind == "file":
            metadata_key = "files"
        elif kind == "attachment":
            metadata_key = "attachments"
        elif kind == "link":
            metadata_key = "links"
        else:
            metadata_key = None

        container_metadata_before: dict[str, Any] | None = None
        asset_snapshot: dict[str, Any] | None = None
        if self._undo_history is not None and self._asset_service is not None:
            container_record = self._current_container_asset()
            if container_record and isinstance(container_record.metadata, Mapping):
                container_metadata_before = dict(container_record.metadata)
            entry_asset = None
            if entry_asset_id is not None:
                try:
                    entry_asset = self._asset_service.get_asset(int(entry_asset_id))
                except Exception:
                    entry_asset = None
            if entry_asset is None:
                try:
                    entry_asset = self._asset_service.get_asset_by_path(str(path))
                except Exception:
                    entry_asset = None
            if entry_asset is not None:
                asset_snapshot = self._serialize_asset(entry_asset)

        dialog_title = "Delete Link" if kind == "link" else "Delete File"
        if kind == "link":
            prompt = f"Remove the link '{path.name}'?" " The target container will remain on disk."
        elif kind == "linked_component":
            dialog_title = "Remove Linked Component"
            source_container = "another container"
            if isinstance(link_import_meta, Mapping):
                label = str(link_import_meta.get("source_container_label") or "").strip()
                if label:
                    source_container = label
            prompt = (
                f"Remove '{path.name}' from this container?"
                f" The original file lives in {source_container} and will be preserved."
            )
        else:
            prompt = f"Permanently delete '{path.name}'?" " You can undo this from File > Undo."
        answer = QMessageBox.question(
            self,
            dialog_title,
            prompt,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        # Try to delete the physical file if it exists
        file_existed = True
        should_remove_from_disk = kind not in {"link", "linked_component"}
        trash_path: Path | None = None
        file_bytes: bytes | None = None
        if should_remove_from_disk and path.exists():
            if self._undo_history is not None and self._undo_history.uses_versions:
                try:
                    file_bytes = path.read_bytes()
                except Exception:
                    file_bytes = None
            if self._undo_history is not None and not self._undo_history.uses_versions:
                try:
                    trash_path = self._undo_history.trash_file(path)
                except Exception:
                    trash_path = None
            if trash_path is None:
                try:
                    path.unlink()
                except OSError:
                    file_existed = False

        # Attempt to remove supplementary preview artifacts (e.g., captured thumbnails).
        if should_remove_from_disk:
            preview_candidates: set[Path] = set()
            try:
                if path.suffix:
                    preview_candidates.add(path.with_suffix(path.suffix + ".png"))
            except Exception:
                pass
            try:
                stem_pattern = f"{path.name}.*.png"
                for candidate in path.parent.glob(stem_pattern):
                    preview_candidates.add(candidate)
            except Exception:
                pass
            for candidate in preview_candidates:
                if candidate == path:
                    continue
                try:
                    if candidate.is_file():
                        candidate.unlink()
                except OSError:
                    # Ignore failures; stale previews will be pruned on next refresh.
                    pass

        # Report to user if file didn't exist
        if not file_existed and should_remove_from_disk:
            missing_title = "File Already Missing"
            missing_body = f"The file '{path}' was not found on disk but its record will be removed from the container."
            QMessageBox.information(
                self,
                missing_title,
                missing_body,
            )

        if kind == "link" and self._container_asset_id is not None and link_part_id is not None:
            try:
                target_asset = self._asset_service.get_asset(link_part_id)
            except Exception:
                target_asset = None

            if target_asset is not None:
                # Remove from current container
                current_asset = self._asset_service.get_asset(self._container_asset_id)
                if current_asset and current_asset.metadata:
                    current_meta = dict(current_asset.metadata)
                    links = list(current_meta.get("links", []))
                    updated_links = [link for link in links if link.get("target_container_id") != target_asset.id]
                    current_meta["links"] = updated_links
                    self._asset_service.update_asset(current_asset.id, metadata=current_meta)

                # Remove from target container
                if target_asset.metadata:
                    target_meta = dict(target_asset.metadata)
                    linked_from = list(target_meta.get("linked_from", []))
                    updated_linked_from = [
                        lf for lf in linked_from if lf.get("source_container_id") != self._container_asset_id
                    ]
                    target_meta["linked_from"] = updated_linked_from
                    self._asset_service.update_asset(target_asset.id, metadata=target_meta)

        # Update container asset metadata to drop the stale entry for files/attachments
        if metadata_key is not None and self._asset_service is not None:
            asset_record = None
            if self._container_asset_id is not None:
                try:
                    asset_record = self._asset_service.get_asset(self._container_asset_id)
                except Exception:
                    asset_record = None
            if asset_record is None and self._container_path:
                try:
                    asset_record = self._asset_service.get_asset_by_path(self._container_path)
                except Exception:
                    asset_record = None
                else:
                    if asset_record is not None:
                        try:
                            self._container_asset_id = int(asset_record.id)
                        except (TypeError, ValueError):
                            pass
            if asset_record is not None:
                try:
                    asset_metadata_source = asset_record.metadata or {}
                except Exception:
                    asset_metadata_source = {}
                asset_metadata = dict(asset_metadata_source)
                existing_entries = list(asset_metadata.get(metadata_key) or [])
                updated_entries, removed = self._remove_metadata_entry(
                    existing_entries,
                    target_asset_id=entry_asset_id,
                    target_path=normalized_path,
                )
                if removed:
                    if updated_entries:
                        asset_metadata[metadata_key] = updated_entries
                    else:
                        asset_metadata.pop(metadata_key, None)
                    try:
                        updated_asset = self._asset_service.update_asset(
                            asset_record.id,
                            metadata=asset_metadata,
                        )
                    except Exception:
                        logger.warning("Could not update asset metadata after deleting %s", path, exc_info=True)
                    else:
                        if updated_asset is not None:
                            try:
                                self._container_asset_id = int(updated_asset.id)
                            except (TypeError, ValueError):
                                pass

        # Also try to remove the asset record from the database if asset service is available
        asset_id = item.data(_ASSET_ID_ROLE)
        if asset_id is not None and self._asset_service is not None:
            try:
                self._asset_service.delete_asset(int(asset_id))
            except Exception as e:
                logger.warning(f"Could not delete asset record for {path}: {e}")

        if self._undo_history is not None:
            self._undo_history.record_deletion(
                kind=kind,
                original_path=path,
                trash_path=trash_path,
                container_asset_id=self._safe_int(self._container_asset_id),
                container_asset_path=self._container_path,
                container_metadata=container_metadata_before,
                asset_snapshot=asset_snapshot,
                file_bytes=file_bytes,
                asset_service=self._asset_service,
            )

        row = self._components.row(item)
        if row >= 0:
            self._components.takeItem(row)

        self.refreshRequested.emit()

    # ------------------------------------------------------------------
    # Breadcrumb helpers
    # ------------------------------------------------------------------
    def _update_breadcrumb(self, folder: Path | None, *, raw_path: str | None = None) -> None:
        del folder, raw_path

    @Slot(str)
    def _handle_breadcrumb_link(self, href: str) -> None:
        del href

    # ------------------------------------------------------------------
    # Selection + Context Menu (class methods)
    # ------------------------------------------------------------------
    @Slot(QListWidgetItem)
    def _handle_component_activated(self, item: QListWidgetItem | None) -> None:
        if not self._navigate_to_component(item):
            self._open_component_with_handler(item)

    def selected_item(self) -> tuple[str, str] | None:
        item = self._components.currentItem()
        if item is None:
            return None
        path = str(item.data(Qt.UserRole) or "").strip()
        kind = str(item.data(Qt.UserRole + 1) or "component")
        return (path, kind)

    def select_item(self, target_path: str) -> None:
        target = str(target_path)
        for row in range(self._components.count()):
            item = self._components.item(row)
            # Skip non-selectable headers
            if not (item.flags() & Qt.ItemIsSelectable):
                continue
            if str(item.data(Qt.UserRole) or "") == target:
                self._components.setCurrentItem(item)
                self._components.scrollToItem(item)
                return

    @Slot()
    def _show_components_context_menu(self, pos) -> None:
        self._show_component_list_context_menu(self._components, pos)

    @Slot()
    def _show_attachments_context_menu(self, pos) -> None:
        self._show_component_list_context_menu(self._attachments, pos)

    def _show_links_context_menu(self, pos) -> None:
        self._show_component_list_context_menu(self._links, pos)

    def _show_component_list_context_menu(self, list_widget: QListWidget, pos) -> None:
        current_item = list_widget.currentItem()
        try:
            clicked_item = list_widget.itemAt(pos)
        except Exception:
            clicked_item = None
        item = clicked_item or current_item
        self._suppress_link_navigation = True
        action = None
        link_container_act = None
        import_linked_act = None
        add_act = None
        set_primary_act = None
        delete_file_act = None
        open_item_act = None
        open_source_act = None
        open_act = None
        related_actions: dict[Any, dict[str, Any]] = {}
        try:
            if clicked_item is not None and clicked_item is not current_item:
                list_widget.setCurrentItem(clicked_item)
                item = clicked_item

            menu = QMenu(self)
            has_container = bool(self._container_path)
            link_container_act = menu.addAction("Link Container…")
            link_container_act.setEnabled(has_container)
            if list_widget in {self._components, self._links}:
                import_linked_act = menu.addAction("Import From Linked Container…")
                import_linked_act.setEnabled(has_container and self._has_linked_containers)
            add_act = menu.addAction("Upload File(s) Here…")
            add_act.setEnabled(has_container)
            if item is not None:
                raw_kind = item.data(Qt.UserRole + 1)
                kind = str(raw_kind or "component").strip().casefold()
                if kind == "component":
                    set_primary_act = menu.addAction("Set as Primary Model")
                    set_primary_act.setEnabled(has_container and not bool(item.data(_PRIMARY_ROLE)))
                if kind in {"file", "component", "attachment", "link"}:
                    delete_label = "Delete Link…" if kind == "link" else "Delete File…"
                    delete_file_act = menu.addAction(delete_label)
            open_item_act = menu.addAction("Open Item")
            open_item_act.setEnabled(self._can_open_with_handler(item))
            upstream_links = self._extract_upstream_links(item)
            if upstream_links:
                open_source_act = menu.addAction("Open Upstream Source")
            related_items = self._extract_related_items(item)
            if related_items:
                related_menu = menu.addMenu("Related Items")
                for related in related_items:
                    label = related.get("label") or related.get("path") or "Related"
                    action = related_menu.addAction(label)
                    action.setData(related)
                    related_actions[action] = related
            open_act = menu.addAction("Open Containing Folder")
            open_act.setEnabled(has_container or self._absolute_path_for_item(item) is not None)
            action = menu.exec(list_widget.mapToGlobal(pos))
        finally:
            self._suppress_link_navigation = False
        if action is None:
            return
        if action == link_container_act:
            self.linkContainerRequested.emit()
        elif import_linked_act is not None and action == import_linked_act:
            self.importLinkedComponentRequested.emit()
        elif action == add_act:
            self.addAttachmentsRequested.emit()
        elif set_primary_act is not None and action == set_primary_act:
            target = str(item.data(Qt.UserRole) or "").strip() if item is not None else ""
            if target:
                self.setPrimaryComponentRequested.emit(target)
        elif delete_file_act is not None and action == delete_file_act:
            if item is not None:
                self._delete_file_item(item)
        elif action == open_item_act:
            raw_kind = item.data(Qt.UserRole + 1) if item is not None else None
            kind = str(raw_kind or "").strip().casefold()
            if kind in {"link", "linked_here"}:
                target = self._resolve_component_navigation_target(item)
                if target:
                    self.navigateToPathRequested.emit(target)
            else:
                self._open_component_with_handler(item)
        elif action == open_act:
            target = None
            if item is not None:
                target = str(item.data(Qt.UserRole) or "").strip()
            if target:
                self.openItemFolderRequested.emit(target)
            else:
                self.openFolderRequested.emit()
        elif open_source_act is not None and action == open_source_act:
            self._open_upstream_link(upstream_links)
        elif action in related_actions:
            self._open_related_item(related_actions[action])

    def _navigate_to_component(self, item: QListWidgetItem | None) -> bool:
        target = self._resolve_component_navigation_target(item)
        if not target:
            return False
        self.navigateToPathRequested.emit(target)
        return True

    def _item_metadata(self, item: QListWidgetItem | None) -> dict[str, Any]:
        if item is None:
            return {}
        metadata_obj = item.data(_METADATA_ROLE)
        return metadata_obj if isinstance(metadata_obj, dict) else {}

    def _absolute_path_for_item(self, item: QListWidgetItem | None) -> str | None:
        if item is None:
            return None
        raw_path = item.data(Qt.UserRole)
        if raw_path is None:
            raw_path = item.text()
        path_str = str(raw_path or "").strip()
        if not path_str:
            return None
        candidate = self._coerce_path(path_str)
        if candidate is None:
            return None
        base = self._coerce_path(self._container_path)
        try:
            if base is not None and not candidate.is_absolute():
                candidate = base / candidate
        except Exception:
            pass
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            resolved = candidate.expanduser()
        return str(resolved)

    def _can_open_with_handler(self, item: QListWidgetItem | None) -> bool:
        kind = str(item.data(Qt.UserRole + 1) if item is not None else "").strip().casefold()
        if kind in {"link", "linked_here"}:
            return False
        path = self._absolute_path_for_item(item)
        if not path:
            return False
        candidate = self._coerce_path(path)
        if candidate is not None:
            try:
                if candidate.exists():
                    if candidate.is_dir():
                        return False
                    if candidate.is_file():
                        return True
            except Exception:
                pass
        metadata = self._item_metadata(item)
        handler = str(metadata.get("handler") or "").strip().lower()
        return bool(handler and handler != "none")

    def _open_component_with_handler(self, item: QListWidgetItem | None) -> bool:
        path = self._absolute_path_for_item(item)
        if not path:
            return False
        metadata = self._item_metadata(item)
        handler = str(metadata.get("handler") or "system").strip().lower()
        if handler in {"", "none"}:
            return False
        if handler not in {"system", "openscad"}:
            handler = "system"
        url = QUrl.fromLocalFile(path)
        return bool(QDesktopServices.openUrl(url))

    def _extract_upstream_links(self, item: QListWidgetItem | None) -> list[dict[str, str]]:
        metadata = self._item_metadata(item)
        raw_links = metadata.get("upstream_links")
        links: list[dict[str, str]] = []
        if isinstance(raw_links, dict):
            raw_links = [raw_links]
        if isinstance(raw_links, list):
            for entry in raw_links:
                if not isinstance(entry, dict):
                    try:
                        url = str(entry or "").strip()
                    except Exception:
                        continue
                    if url:
                        links.append({"url": url, "label": ""})
                    continue
                try:
                    url = str(entry.get("url") or entry.get("href") or "").strip()
                except Exception:
                    url = ""
                if not url:
                    continue
                label = str(entry.get("label") or "").strip()
                links.append({"url": url, "label": label})
        elif isinstance(raw_links, str):
            url = raw_links.strip()
            if url:
                links.append({"url": url, "label": ""})
        return links

    def _open_upstream_link(self, links: list[dict[str, str]] | None) -> None:
        if not links:
            return
        link = links[0]
        url = str(link.get("url") or "").strip()
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))

    def _extract_related_items(self, item: QListWidgetItem | None) -> list[dict[str, Any]]:
        metadata = self._item_metadata(item)
        raw_related = metadata.get("related_items")
        related: list[dict[str, Any]] = []
        if isinstance(raw_related, dict):
            raw_related = [raw_related]
        if isinstance(raw_related, list):
            for entry in raw_related:
                if isinstance(entry, dict):
                    path = str(entry.get("path") or "").strip()
                    if not path:
                        continue
                    label = str(entry.get("label") or self._safe_name(path))
                    relationship = str(entry.get("relationship") or "").strip()
                    related.append({"path": path, "label": label, "relationship": relationship})
                else:
                    path = str(entry or "").strip()
                    if not path:
                        continue
                    related.append({"path": path, "label": self._safe_name(path)})
        elif isinstance(raw_related, str):
            path = raw_related.strip()
            if path:
                related.append({"path": path, "label": self._safe_name(path)})
        return related

    def _open_related_item(self, payload: Mapping[str, Any] | dict[str, Any]) -> None:
        path_value = payload.get("path") if isinstance(payload, Mapping) else None
        try:
            target = str(path_value or "").strip()
        except Exception:
            target = ""
        if target:
            self.navigateToPathRequested.emit(target)

    def _resolve_component_navigation_target(self, item: QListWidgetItem | None) -> str | None:
        if item is None:
            return None
        if not (item.flags() & Qt.ItemIsSelectable):
            return None
        raw_path = item.data(Qt.UserRole)
        if raw_path is None:
            raw_path = item.text()
        path_str = str(raw_path or "").strip()
        if not path_str:
            return None
        raw_kind = item.data(Qt.UserRole + 1)
        kind = str(raw_kind or "component").strip().casefold()
        if kind in {"attachment", "arrangement"}:
            return None
        if kind in {"link", "linked_here"}:
            metadata = self._item_metadata(item)
            target = metadata.get("link_target")
            if isinstance(target, str):
                target = target.strip()
                if target:
                    return target
            return None
        is_placeholder = kind == "placeholder"
        base_path = self._coerce_path(self._container_path)

        candidate_path = self._coerce_path(path_str)
        if candidate_path is not None and base_path is not None:
            try:
                if not candidate_path.is_absolute():
                    candidate_path = base_path / candidate_path
                candidate_path = candidate_path.expanduser()
            except Exception:
                candidate_path = None

        def _resolve_path(path_obj: Path) -> str:
            try:
                resolved = path_obj.expanduser().resolve()
            except Exception:
                resolved = path_obj.expanduser()
            return str(resolved)

        if candidate_path is None:
            if not is_placeholder:
                return None
            fallback = self._coerce_path(path_str)
            if fallback is None:
                return None
            if base_path is not None:
                try:
                    if not fallback.is_absolute():
                        fallback = base_path / fallback
                except Exception:
                    return None
            return _resolve_path(fallback)

        try:
            if candidate_path.is_dir():
                return _resolve_path(candidate_path)
        except Exception:
            pass

        if is_placeholder:
            return _resolve_path(candidate_path)
        return None

    # ------------------------------------------------------------------
    # Drag & drop support for attachments (class methods)
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is None or not md.hasUrls():
            super().dropEvent(event)
            return
        paths: list[str] = []
        for url in md.urls():
            try:
                local = url.toLocalFile()
            except Exception:
                local = ""
            if local:
                paths.append(local)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _coerce_path(self, raw: str | Path | None) -> Path | None:
        if raw is None:
            return None
        try:
            value = str(raw).strip()
        except Exception:
            return None
        if not value:
            return None
        try:
            candidate = Path(value)
            _ = candidate.parts
        except (RecursionError, ValueError, OSError):
            return None
        return candidate

    def _safe_int(self, value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _current_container_asset(self) -> AssetRecord | None:
        if self._asset_service is None:
            return None
        if self._container_asset_id is not None:
            try:
                return self._asset_service.get_asset(self._container_asset_id)
            except Exception:
                return None
        if self._container_path:
            try:
                return self._asset_service.get_asset_by_path(self._container_path)
            except Exception:
                return None
        return None

    def _serialize_asset(self, asset: AssetRecord) -> dict[str, Any]:
        return {
            "id": asset.id,
            "path": asset.path,
            "label": asset.label,
            "metadata": asset.metadata or {},
            "tags": asset.tags or [],
        }

    def _safe_name(self, raw_path: str) -> str:
        candidate = self._coerce_path(raw_path)
        if candidate is None:
            return raw_path
        try:
            name = candidate.name
        except Exception:
            return raw_path
        return name or raw_path

    def _placeholder_icon(self) -> QPixmap:
        """Return an icon for placeholder components."""

        # Red exclamation badge icon for parts without a model
        w, h = self._icon_size.width(), self._icon_size.height()
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        painter = QPainter(img)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Background circle
            painter.setBrush(QColor(255, 64, 64))
            painter.setPen(QColor(0, 0, 0, 0))
            margin = int(min(w, h) * 0.12)
            painter.drawEllipse(margin, margin, w - 2 * margin, h - 2 * margin)
            # Exclamation mark
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setBold(True)
            font.setPointSize(int(min(w, h) * 0.6))
            painter.setFont(font)
            painter.drawText(0, int(h * 0.08), w, h, Qt.AlignCenter, "!")
        finally:
            painter.end()
        return QPixmap.fromImage(img)


class _IconWorkerSignals(QObject):
    result = Signal(str, object)
    error = Signal(str, str)


class _IconWorker(QRunnable):
    _IMAGE_EXTENSIONS = frozenset(
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

    def __init__(self, path: str, size: QSize) -> None:
        super().__init__()
        self._path = path
        self._size = size
        self.signals = _IconWorkerSignals()

    def run(self) -> None:  # pragma: no cover - async
        try:
            p = Path(self._path)
            suffix = p.suffix.lower()
            if suffix in self._IMAGE_EXTENSIONS:
                img = QImage(str(p))
                if img.isNull():
                    self.signals.error.emit(self._path, "bad-image")
                    return
                self.signals.result.emit(self._path, img)
                return

            # Non-image: handle known attachment types with generated icons
            ctype, _ = mimetypes.guess_type(str(p))
            label, color = self._label_color_for(suffix, ctype or "")
            if label:
                img = self._render_label_icon(label, color, self._size)
                self.signals.result.emit(self._path, img)
                return

            cache = ThumbnailCache()
            result = cache.get_or_render(
                p,
                size=(
                    max(64, self._size.width()),
                    max(64, self._size.height()),
                ),
            )
            data = result.image_bytes
            img = QImage()
            if not data or not img.loadFromData(data):
                self.signals.error.emit(self._path, "no-bytes")
                return
            self.signals.result.emit(self._path, img)
        except ThumbnailGenerationError as exc:
            self.signals.error.emit(self._path, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(self._path, exc.__class__.__name__)

    def _render_label_icon(
        self,
        text: str,
        color: tuple[int, int, int],
        size: QSize,
    ) -> QImage:
        w, h = max(16, size.width()), max(16, size.height())
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(QColor(32, 38, 46, 255))
        painter = QPainter(img)
        try:
            # Rounded rectangle with accent color
            painter.setRenderHint(QPainter.Antialiasing, True)
            bg = QColor(*color)
            painter.setBrush(bg)
            painter.setPen(QColor(0, 0, 0, 0))
            margin = int(min(w, h) * 0.08)
            painter.drawRoundedRect(
                margin,
                margin,
                w - 2 * margin,
                h - 2 * margin,
                10,
                10,
            )
            # Text
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setBold(True)
            font.setPointSize(int(min(w, h) * 0.28))
            painter.setFont(font)
            painter.drawText(0, 0, w, h, Qt.AlignCenter, text[:4])
        finally:
            painter.end()
        return img

    def _label_color_for(
        self,
        suffix: str,
        ctype: str,
    ) -> tuple[str, tuple[int, int, int]]:
        s = suffix.lower()
        c = ctype.lower()
        # Map by extension first for specificity
        if s in {".pdf"} or c == "application/pdf":
            return ("PDF", (192, 48, 48))
        if s in {".md", ".markdown"} or c.endswith("markdown"):
            return ("MD", (36, 153, 99))
        if s in {".txt"} or c.startswith("text/"):
            return ("TXT", (60, 120, 200))
        if s in {".json"} or c == "application/json":
            return ("JSON", (0, 140, 140))
        if s in {".csv"} or c.endswith("csv"):
            return ("CSV", (0, 140, 140))
        if s in {".zip", ".7z", ".tar", ".gz", ".bz2", ".xz"} or "zip" in c or "compressed" in c:
            return ("ZIP", (215, 140, 0))
        if s in {".ppt", ".pptx"}:
            return ("PPT", (209, 72, 54))
        if s in {".doc", ".docx", ".odt"}:
            return ("DOC", (30, 100, 200))
        if s in {".xls", ".xlsx", ".ods"}:
            return ("XLS", (16, 124, 16))
        if s in {".py", ".sh", ".js", ".ts"}:
            return (s.lstrip(".").upper(), (120, 80, 180))
        if s == ".scad":
            return ("SCAD", (186, 109, 42))
        # Unknown file type
        if c and not c.startswith("image/"):
            return (c.split("/")[-1][:4].upper(), (110, 110, 110))
        return ("", (0, 0, 0))

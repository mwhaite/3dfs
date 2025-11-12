"""Sidebar widget for managing repository tags."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..container import is_container_metadata
from ..customizer.status import evaluate_customization_status
from ..data import TagStore
from ..storage import AssetService

__all__ = ["TagSidebar"]


class TagSidebar(QWidget):
    """Widget exposing CRUD utilities for tags.

    The sidebar keeps the tag list for an "active" repository item in sync with
    :class:`~three_dfs.data.tags.TagStore` and emits signals whenever the user
    performs an operation so that other application components can react.
    """

    activeItemChanged = Signal(int)
    """Emitted whenever the focused repository item changes."""

    tagAdded = Signal(int, str)
    """Emitted when a tag is created for the active item."""

    tagRemoved = Signal(int, str)
    """Emitted when a tag is deleted from the active item."""

    tagRenamed = Signal(int, str, str)
    """Emitted when a tag is renamed for the active item."""

    tagsChanged = Signal(int, list)
    """Emitted for any change that affects the active item's tag collection."""

    searchRequested = Signal(str)
    """Emitted whenever the search query text changes."""

    tagFilterRequested = Signal(str)
    """Emitted when the user enters a tag filter (prefixed with '#')."""

    derivativeActivated = Signal(str)
    """Emitted when a derivative asset is activated from the sidebar."""

    tagWebRequested = Signal()
    """Emitted when the user wants to view the tag web."""

    def __init__(
        self,
        store: TagStore | None = None,
        *,
        asset_service: AssetService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store or TagStore()
        self._asset_service = asset_service or getattr(self._store, "_service", None)
        self._active_asset_id: int | None = None
        self._active_asset_path: str | None = None
        self._active_display_name: str | None = None
        self._all_tags_for_item: list[str] = []
        self._known_tags: list[str] = []
        self._active_is_taggable = False

        self._title_label = QLabel("Tags", self)
        self._title_label.setObjectName("tagSidebarTitle")

        self._active_label = QLabel("No item selected", self)
        self._active_label.setObjectName("tagSidebarActiveItem")

        self._search_input = QLineEdit(self)
        self._search_input.setPlaceholderText("Search tags…")
        self._search_input.textChanged.connect(self._handle_search_changed)

        self._tag_list = QListWidget(self)
        self._tag_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tag_list.itemSelectionChanged.connect(self._update_ui_state)
        self._tag_list.itemClicked.connect(self._handle_tag_activated)
        self._tag_list.itemDoubleClicked.connect(self._handle_item_double_clicked)
        self._tag_list.itemActivated.connect(self._handle_tag_activated)

        self._add_button = QPushButton("Add", self)
        self._add_button.clicked.connect(self._handle_add_tag)

        self._edit_button = QPushButton("Edit", self)
        self._edit_button.clicked.connect(self._handle_edit_tag)

        self._delete_button = QPushButton("Delete", self)
        self._delete_button.clicked.connect(self._handle_delete_tag)
        self._graph_button = QPushButton("Tag Web", self)
        self._graph_button.setToolTip("Visualize tag relationships")
        self._graph_button.clicked.connect(self.tagWebRequested.emit)

        self._derivatives_label = QLabel("Derived assets", self)
        self._derivatives_label.setObjectName("tagSidebarDerivativesTitle")
        self._derivatives_label.setVisible(False)

        self._derivative_list = QListWidget(self)
        self._derivative_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._derivative_list.itemActivated.connect(self._handle_derivative_activated)
        self._derivative_list.itemDoubleClicked.connect(self._handle_derivative_activated)
        self._derivative_list.setVisible(False)
        self._derivative_list.setObjectName("tagSidebarDerivativesList")

        self._build_layout()
        self._update_ui_state()
        self._refresh_available_tags()

    # ------------------------------------------------------------------
    # Qt API surface
    # ------------------------------------------------------------------
    @Slot(object)
    def set_active_item(self, item_id: object | None) -> None:
        """Switch the sidebar context to the asset identified by *item_id*."""

        if item_id is None:
            asset_id = None
        else:
            try:
                asset_id = int(item_id)
            except (TypeError, ValueError):
                asset_id = None

        if asset_id == self._active_asset_id:
            self._load_tags_for_active_item()
            self._refresh_derivatives()
            self._emit_tags_changed()
            return

        self._active_asset_id = asset_id
        self._active_asset_path = None
        self._active_display_name = None
        self._active_is_taggable = False
        if asset_id is not None and self._asset_service is not None:
            try:
                record = self._asset_service.get_asset(asset_id)
            except Exception:
                record = None
            if record is not None:
                self._active_asset_path = record.path
                self._active_display_name = self._friendly_asset_label(record)
                self._active_is_taggable = self._is_taggable_asset(record)

        self._load_tags_for_active_item()
        self._emit_tags_changed()
        self._refresh_derivatives()
        if asset_id is not None:
            self.activeItemChanged.emit(asset_id)

    def active_item(self) -> str | None:
        """Return the path of the item currently being edited."""

        return self._active_asset_path

    def active_asset_id(self) -> int | None:
        """Return the active asset identifier."""

        return self._active_asset_id

    def tags(self) -> list[str]:
        """Return the list of tags for the active item."""

        return list(self._all_tags_for_item)

    def search_text(self) -> str:
        """Return the current search query."""

        return self._search_input.text()

    # ------------------------------------------------------------------
    # Internal wiring
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(self._title_label)
        layout.addWidget(self._active_label)
        layout.addWidget(self._search_input)
        layout.addWidget(self._tag_list, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        button_row.addWidget(self._add_button)
        button_row.addWidget(self._edit_button)
        button_row.addWidget(self._delete_button)
        button_row.addWidget(self._graph_button)

        layout.addLayout(button_row)
        layout.addWidget(self._derivatives_label)
        layout.addWidget(self._derivative_list)

    def _handle_search_changed(self, text: str) -> None:
        self._refresh_visible_tags()
        self.searchRequested.emit(text)
        search = text.strip()
        if search.startswith("#") and len(search) > 1:
            self.tagFilterRequested.emit(search[1:].strip())
        elif not search:
            self.tagFilterRequested.emit("")

    def _handle_item_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        # Double-clicking a tag should act like activation (filter by tag)
        self._handle_tag_activated(item)

    def _handle_add_tag(self) -> None:
        if self._active_asset_id is None or not self._active_is_taggable:
            return

        suggestions = [tag for tag in self._known_tags if tag not in self._all_tags_for_item]

        if suggestions:
            new_tag, accepted = QInputDialog.getItem(
                self,
                "Create tag",
                "Tag name:",
                suggestions,
                0,
                True,
            )
        else:
            new_tag, accepted = QInputDialog.getText(self, "Create tag", "Tag name:")
        if not accepted:
            return

        new_tag = new_tag.strip()
        if not new_tag:
            return

        try:
            normalized = self._store.add_tag_to_asset(self._active_asset_id, new_tag)
        except ValueError:
            return

        if normalized is None:
            return

        self.tagAdded.emit(self._active_asset_id, normalized)
        self._load_tags_for_active_item()
        self._emit_tags_changed()

    def _handle_edit_tag(self) -> None:
        if self._active_asset_id is None or not self._active_is_taggable:
            return

        current_item = self._tag_list.currentItem()
        if current_item is None:
            return

        current_value = current_item.text()
        new_tag, accepted = QInputDialog.getText(
            self,
            "Rename tag",
            "New tag name:",
            text=current_value,
        )

        if not accepted:
            return

        new_tag = new_tag.strip()
        if not new_tag or new_tag == current_value:
            return

        try:
            normalized = self._store.rename_tag_for_asset(self._active_asset_id, current_value, new_tag)
        except ValueError:
            return

        if normalized is None:
            return

        self.tagRenamed.emit(self._active_asset_id, current_value, normalized)
        self._load_tags_for_active_item()
        self._emit_tags_changed()

    def _handle_delete_tag(self) -> None:
        if self._active_asset_id is None or not self._active_is_taggable:
            return

        current_item = self._tag_list.currentItem()
        if current_item is None:
            return

        tag_value = current_item.text()
        try:
            removed = self._store.remove_tag_from_asset(self._active_asset_id, tag_value)
        except ValueError:
            return

        if not removed:
            return

        self.tagRemoved.emit(self._active_asset_id, tag_value)
        self._load_tags_for_active_item()
        self._emit_tags_changed()

    def _load_tags_for_active_item(self) -> None:
        if self._active_asset_id is None or not self._active_is_taggable:
            if self._active_asset_id is None:
                self._active_label.setText("No item selected")
            else:
                self._active_label.setText("Tags unavailable for this item")
            self._active_label.setToolTip("")
            self._all_tags_for_item = []
        else:
            friendly = self._active_display_name or self._active_asset_path
            if friendly:
                self._active_label.setText(f"Tags for {friendly}")
            else:
                self._active_label.setText("Tags")
            if self._active_asset_path and friendly and friendly != self._active_asset_path:
                self._active_label.setToolTip(self._active_asset_path)
            else:
                self._active_label.setToolTip("")
            try:
                self._all_tags_for_item = self._store.tags_for_asset(self._active_asset_id)
            except (ValueError, RecursionError):
                self._all_tags_for_item = []

        self._refresh_available_tags()
        self._refresh_visible_tags()
        self._update_ui_state()

    def _refresh_visible_tags(self) -> None:
        self._tag_list.clear()
        search_text = self._search_input.text().strip().casefold()

        if search_text:
            tags = [tag for tag in self._all_tags_for_item if search_text in tag.casefold()]
        else:
            tags = list(self._all_tags_for_item)

        for tag in tags:
            self._tag_list.addItem(tag)

    def _refresh_available_tags(self) -> None:
        try:
            self._known_tags = self._store.all_tags()
        except Exception:
            self._known_tags = []

    def _refresh_derivatives(self) -> None:
        self._derivative_list.clear()
        if self._asset_service is None or self._active_asset_id is None:
            self._derivatives_label.setVisible(False)
            self._derivative_list.setVisible(False)
            return

        try:
            asset = self._asset_service.get_asset(self._active_asset_id)
        except Exception:
            asset = None

        if asset is None:
            self._derivatives_label.setVisible(False)
            self._derivative_list.setVisible(False)
            return

        try:
            derivatives = self._asset_service.list_derivatives_for_asset(asset.path)
        except (ValueError, RecursionError, TypeError):
            derivatives = []

        if not derivatives:
            self._derivatives_label.setVisible(False)
            self._derivative_list.setVisible(False)
            return

        base_path = Path(asset.path)
        for record in derivatives:
            label = record.label or Path(record.path).name
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, record.path)
            tooltip_parts = [record.path]
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            customization_meta = metadata.get("customization") if isinstance(metadata, dict) else None
            if isinstance(customization_meta, dict):
                try:
                    status = evaluate_customization_status(customization_meta, base_path=base_path)
                except Exception:
                    status = None
                if status is not None:
                    tooltip_parts.append(status.reason)
            item.setToolTip("\n".join(tooltip_parts))
            self._derivative_list.addItem(item)

        count = self._derivative_list.count()
        self._derivatives_label.setText(f"Derived assets ({count})")
        self._derivatives_label.setVisible(True)
        self._derivative_list.setVisible(True)

    def _handle_derivative_activated(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        target = item.data(Qt.UserRole) or item.text()
        if target:
            self.derivativeActivated.emit(str(target))

    def _update_ui_state(self) -> None:
        has_item = self._active_asset_id is not None and self._active_is_taggable
        has_selection = self._tag_list.currentItem() is not None

        self._tag_list.setEnabled(has_item)
        self._add_button.setEnabled(has_item)
        self._edit_button.setEnabled(has_item and has_selection)
        self._delete_button.setEnabled(has_item and has_selection)

    def _emit_tags_changed(self) -> None:
        if self._active_asset_id is None or not self._active_is_taggable:
            return

        self._refresh_available_tags()
        try:
            tags = self._store.tags_for_asset(self._active_asset_id)
        except (ValueError, RecursionError):
            tags = []
        self.tagsChanged.emit(self._active_asset_id, tags)

    def _friendly_asset_label(self, asset) -> str:
        metadata = getattr(asset, "metadata", {}) or {}
        for key in ("display_name", "name", "label", "title"):
            value = metadata.get(key)
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    return trimmed

        label = getattr(asset, "label", "")
        if isinstance(label, str) and label.strip():
            return label.strip()

        path = getattr(asset, "path", "")
        if isinstance(path, str) and path:
            try:
                return Path(path).name
            except Exception:
                return path

        identifier = getattr(asset, "id", None)
        return f"Asset {identifier}" if identifier is not None else "Asset"

    def _is_taggable_asset(self, record) -> bool:
        metadata = getattr(record, "metadata", {}) or {}
        if is_container_metadata(metadata):
            return True

        name = Path(record.path).name
        try:
            from uuid import UUID

            UUID(name)
            return True
        except Exception:
            return False

    def _handle_tag_activated(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        tag_value = item.text().strip()
        if tag_value:
            self.tagFilterRequested.emit(tag_value)

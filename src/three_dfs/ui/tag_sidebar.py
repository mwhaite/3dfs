"""Sidebar widget for managing repository tags."""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
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

from ..data import TagStore

__all__ = ["TagSidebar"]


class TagSidebar(QWidget):
    """Widget exposing CRUD utilities for tags.

    The sidebar keeps the tag list for an "active" repository item in sync with
    :class:`~three_dfs.data.tags.TagStore` and emits signals whenever the user
    performs an operation so that other application components can react.
    """

    activeItemChanged = Signal(object)
    """Emitted whenever the focused repository item changes."""

    tagAdded = Signal(str, str)
    """Emitted when a tag is created for the active item."""

    tagRemoved = Signal(str, str)
    """Emitted when a tag is deleted from the active item."""

    tagRenamed = Signal(str, str, str)
    """Emitted when a tag is renamed for the active item."""

    tagsChanged = Signal(str, list)
    """Emitted for any change that affects the active item's tag collection."""

    searchRequested = Signal(str)
    """Emitted whenever the search query text changes."""

    def __init__(
        self,
        store: TagStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store or TagStore()
        self._active_item: str | None = None
        self._all_tags_for_item: list[str] = []

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
        self._tag_list.itemDoubleClicked.connect(self._handle_item_double_clicked)

        self._add_button = QPushButton("Add", self)
        self._add_button.clicked.connect(self._handle_add_tag)

        self._edit_button = QPushButton("Edit", self)
        self._edit_button.clicked.connect(self._handle_edit_tag)

        self._delete_button = QPushButton("Delete", self)
        self._delete_button.clicked.connect(self._handle_delete_tag)

        self._build_layout()
        self._update_ui_state()

    # ------------------------------------------------------------------
    # Qt API surface
    # ------------------------------------------------------------------
    @Slot(object)
    def set_active_item(self, item_id: str | None) -> None:
        """Switch the sidebar context to *item_id* and refresh the view."""

        if item_id == self._active_item:
            # Refresh the view even when the identifier did not change.  This
            # allows external callers to force a reload after out-of-band
            # modifications.
            self._load_tags_for_active_item()
            self._emit_tags_changed()
            return

        self._active_item = item_id
        self._load_tags_for_active_item()
        self.activeItemChanged.emit(item_id)
        self._emit_tags_changed()

    def active_item(self) -> str | None:
        """Return the identifier of the item currently being edited."""

        return self._active_item

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

        layout.addLayout(button_row)

    def _handle_search_changed(self, text: str) -> None:
        self._refresh_visible_tags()
        self.searchRequested.emit(text)

    def _handle_item_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        self._handle_edit_tag()

    def _handle_add_tag(self) -> None:
        if self._active_item is None:
            return

        new_tag, accepted = QInputDialog.getText(self, "Create tag", "Tag name:")
        if not accepted:
            return

        new_tag = new_tag.strip()
        if not new_tag:
            return

        try:
            normalized = self._store.add_tag(self._active_item, new_tag)
        except ValueError:
            return

        if normalized is None:
            return

        self.tagAdded.emit(self._active_item, normalized)
        self._load_tags_for_active_item()
        self._emit_tags_changed()

    def _handle_edit_tag(self) -> None:
        if self._active_item is None:
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
            normalized = self._store.rename_tag(
                self._active_item,
                current_value,
                new_tag,
            )
        except ValueError:
            return

        if normalized is None:
            return

        self.tagRenamed.emit(self._active_item, current_value, normalized)
        self._load_tags_for_active_item()
        self._emit_tags_changed()

    def _handle_delete_tag(self) -> None:
        if self._active_item is None:
            return

        current_item = self._tag_list.currentItem()
        if current_item is None:
            return

        tag_value = current_item.text()
        try:
            removed = self._store.remove_tag(self._active_item, tag_value)
        except ValueError:
            return

        if not removed:
            return

        self.tagRemoved.emit(self._active_item, tag_value)
        self._load_tags_for_active_item()
        self._emit_tags_changed()

    def _load_tags_for_active_item(self) -> None:
        if self._active_item is None:
            self._active_label.setText("No item selected")
            self._all_tags_for_item = []
        else:
            self._active_label.setText(f"Tags for {self._active_item}")
            self._all_tags_for_item = self._store.tags_for(self._active_item)

        self._refresh_visible_tags()
        self._update_ui_state()

    def _refresh_visible_tags(self) -> None:
        self._tag_list.clear()
        search_text = self._search_input.text().strip().casefold()

        if search_text:
            tags = [
                tag for tag in self._all_tags_for_item if search_text in tag.casefold()
            ]
        else:
            tags = list(self._all_tags_for_item)

        for tag in tags:
            self._tag_list.addItem(tag)

    def _update_ui_state(self) -> None:
        has_item = self._active_item is not None
        has_selection = self._tag_list.currentItem() is not None

        self._tag_list.setEnabled(has_item)
        self._add_button.setEnabled(has_item)
        self._edit_button.setEnabled(has_item and has_selection)
        self._delete_button.setEnabled(has_item and has_selection)

    def _emit_tags_changed(self) -> None:
        if self._active_item is None:
            return

        tags = self._store.tags_for(self._active_item)
        self.tagsChanged.emit(self._active_item, tags)

"""Repository browser widgets for the 3dfs UI."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QItemSelectionModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QListView,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


def _expand_path(path: Path | str) -> Path:
    """Resolve *path* to an absolute location, falling back to the home directory."""

    resolved = Path(path).expanduser()

    if resolved.exists():
        return resolved.resolve()

    # Default to the user's home directory when the requested root is missing.
    return Path.home().resolve()


@dataclass(slots=True)
class RepositoryBrowserSettings:
    """Configuration for the :class:`RepositoryBrowser` widget."""

    root_path: Path = field(default_factory=Path.cwd)
    filters: QDir.Filters = QDir.AllEntries | QDir.NoDotAndDotDot
    name_filters: Sequence[str] | None = None

    def normalized_root(self) -> Path:
        """Return the resolved root path, ensuring it exists on disk."""

        return _expand_path(self.root_path)


class _DirectoryOnlyProxyModel(QSortFilterProxyModel):
    """Filter out non-directory entries for the tree view."""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # type: ignore[override]
        source_model = self.sourceModel()
        if source_model is None:
            return False

        index = source_model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False

        # Allow drives and directories through the proxy to form the tree structure.
        return source_model.isDir(index)  # type: ignore[attr-defined]


class RepositoryBrowser(QWidget):
    """Composite widget exposing a directory tree and a synchronized listing."""

    directoryChanged = Signal(str)
    entrySelected = Signal(str)
    entryActivated = Signal(str)

    def __init__(
        self,
        settings: RepositoryBrowserSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._settings = settings or RepositoryBrowserSettings()
        self._fs_model = QFileSystemModel(self)
        self._fs_model.setFilter(self._settings.filters)

        if self._settings.name_filters:
            self._fs_model.setNameFilters(list(self._settings.name_filters))
            self._fs_model.setNameFilterDisables(False)
        else:
            self._fs_model.setNameFilters([])
            self._fs_model.setNameFilterDisables(False)

        self._tree_proxy = _DirectoryOnlyProxyModel(self)
        self._tree_proxy.setSourceModel(self._fs_model)

        self._tree_view = QTreeView(self)
        self._tree_view.setModel(self._tree_proxy)
        self._tree_view.setHeaderHidden(True)
        self._tree_view.setUniformRowHeights(True)
        self._tree_view.setSortingEnabled(True)
        self._tree_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tree_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tree_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree_view.sortByColumn(0, Qt.AscendingOrder)

        for column in range(1, self._fs_model.columnCount()):
            self._tree_view.hideColumn(column)

        self._list_view = QListView(self)
        self._list_view.setModel(self._fs_model)
        self._list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list_view.setUniformItemSizes(True)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self._tree_view)
        splitter.addWidget(self._list_view)
        splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._apply_root_path(self._settings.normalized_root())

        tree_selection = self._tree_view.selectionModel()
        if tree_selection is not None:
            tree_selection.currentChanged.connect(self._on_tree_current_changed)

        list_selection = self._list_view.selectionModel()
        if list_selection is not None:
            list_selection.currentChanged.connect(self._on_list_selection_changed)

        self._list_view.activated.connect(self._on_list_activated)
        self._list_view.doubleClicked.connect(self._on_list_activated)

    @property
    def settings(self) -> RepositoryBrowserSettings:
        """Return the current settings for the browser."""

        return self._settings

    def apply_settings(self, settings: RepositoryBrowserSettings) -> None:
        """Update the widget using *settings*."""

        self._settings = settings
        self._fs_model.setFilter(settings.filters)

        if settings.name_filters:
            self._fs_model.setNameFilters(list(settings.name_filters))
            self._fs_model.setNameFilterDisables(False)
        else:
            self._fs_model.setNameFilters([])
            self._fs_model.setNameFilterDisables(False)

        self._apply_root_path(settings.normalized_root())

    def _apply_root_path(self, root_path: Path) -> None:
        root_index = self._fs_model.setRootPath(str(root_path))
        proxy_index = self._tree_proxy.mapFromSource(root_index)

        self._tree_view.setRootIndex(proxy_index)
        self._list_view.setRootIndex(root_index)

        selection_model = self._tree_view.selectionModel()
        if selection_model is not None and proxy_index.isValid():
            selection_model.setCurrentIndex(
                proxy_index,
                QItemSelectionModel.ClearAndSelect,
            )
            self._tree_view.expand(proxy_index)

        list_selection = self._list_view.selectionModel()
        if list_selection is not None:
            list_selection.clearSelection()

        self.directoryChanged.emit(str(root_path))

    def _on_tree_current_changed(
        self,
        current: QModelIndex,
        previous: QModelIndex,
    ) -> None:
        del previous

        source_index = self._tree_proxy.mapToSource(current)
        if not source_index.isValid():
            return

        if not self._fs_model.isDir(source_index):
            return

        self._list_view.setRootIndex(source_index)

        directory = self._fs_model.filePath(source_index)
        self.directoryChanged.emit(directory)

    def _on_list_selection_changed(
        self,
        current: QModelIndex,
        previous: QModelIndex,
    ) -> None:
        del previous

        if not current.isValid():
            return

        entry_path = self._fs_model.filePath(current)
        self.entrySelected.emit(entry_path)

    def _on_list_activated(self, index: QModelIndex) -> None:
        if not index.isValid():
            return

        entry_path = self._fs_model.filePath(index)

        if self._fs_model.isDir(index):
            proxy_index = self._tree_proxy.mapFromSource(index)
            if proxy_index.isValid():
                selection_model = self._tree_view.selectionModel()
                if selection_model is not None:
                    selection_model.setCurrentIndex(
                        proxy_index,
                        QItemSelectionModel.ClearAndSelect,
                    )
                self._tree_view.scrollTo(proxy_index)

        self.entryActivated.emit(entry_path)

    def current_directory(self) -> Path:
        """Return the currently displayed directory."""

        root_index = self._list_view.rootIndex()
        if not root_index.isValid():
            return self._settings.normalized_root()

        return Path(self._fs_model.filePath(root_index))

    def selected_entries(self) -> Iterable[Path]:
        """Yield the entries currently selected in the list view."""

        selection_model = self._list_view.selectionModel()
        if selection_model is None:
            return []

        return [
            Path(self._fs_model.filePath(index))
            for index in selection_model.selectedIndexes()
            if index.isValid()
        ]

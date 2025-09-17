"""Application bootstrap for the 3dfs desktop shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from PySide6.QtCore import QDir, QItemSelection, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileSystemModel,
    QMainWindow,
    QSplitter,
    QTreeView,
)

from .ui import PreviewPane

WINDOW_TITLE: Final[str] = "3dfs"


class MainWindow(QMainWindow):
    """Primary window for the 3dfs shell."""

    def __init__(self, repository_root: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)

        if repository_root is None:
            repository_root = Path.cwd()

        self._repository_root = repository_root
        self._model = QFileSystemModel(self)
        self._model.setRootPath(str(repository_root))
        self._model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Files
        )

        self._browser = QTreeView(self)
        self._browser.setObjectName("repositoryBrowser")
        self._browser.setModel(self._model)
        self._browser.setRootIndex(self._model.index(str(repository_root)))
        self._browser.setSortingEnabled(True)
        self._browser.setUniformRowHeights(True)
        self._browser.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._browser.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self._browser.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._browser.setHeaderHidden(False)

        self._preview = PreviewPane(self)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._browser)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([360, 640])

        self.setCentralWidget(splitter)

        selection_model = self._browser.selectionModel()
        selection_model.selectionChanged.connect(self._handle_selection_changed)

        self._preview.set_file(None)

    def _handle_selection_changed(
        self,
        selected: QItemSelection,
        _deselected: QItemSelection,
    ) -> None:
        indexes = selected.indexes()
        file_path: Path | None = None

        for index in indexes:
            if index.column() != 0:
                continue
            if self._model.isDir(index):
                continue
            file_path = Path(self._model.filePath(index))
            break

        if file_path is None:
            self._preview.set_file(None)
        else:
            self._preview.set_file(file_path)


def main() -> int:
    """Launch the 3dfs Qt application."""

    app = QApplication.instance()
    owns_application = False

    if app is None:
        app = QApplication(sys.argv)
        owns_application = True

    window = MainWindow()
    window.show()

    if owns_application:
        return app.exec()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

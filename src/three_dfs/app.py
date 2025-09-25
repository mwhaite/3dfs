"""Application bootstrap for the 3dfs desktop shell."""

from __future__ import annotations

import sys
from typing import Final

from PySide6.QtWidgets import QApplication

from .application import MainWindow

WINDOW_TITLE: Final[str] = "3dfs"
"""Default title applied to the main Qt window."""

__all__ = ["MainWindow", "main", "WINDOW_TITLE"]


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

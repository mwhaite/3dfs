"""Application bootstrap for the 3dfs desktop shell."""

from __future__ import annotations

import sys
from typing import Final

from PySide6.QtWidgets import QApplication, QMainWindow

WINDOW_TITLE: Final[str] = "3dfs"


class MainWindow(QMainWindow):
    """Primary window for the 3dfs shell."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)


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

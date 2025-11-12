"""Application bootstrap for the 3dfs desktop shell."""

from __future__ import annotations

import sys
from typing import Final

from PySide6.QtWidgets import QApplication

from .application.main_window import MainWindow

WINDOW_TITLE: Final[str] = "3dfs"
"""Default title applied to the main Qt window."""

__all__ = ["MainWindow", "main", "WINDOW_TITLE"]


def main() -> int:
    """Launch the 3dfs Qt application."""
    import logging

    logger = logging.getLogger(__name__)

    logger.info("Starting 3dfs application")
    app = QApplication.instance()
    owns_application = False

    if app is None:
        logger.info("Creating new QApplication")
        app = QApplication(sys.argv)
        owns_application = True
    else:
        logger.info("Using existing QApplication")

    logger.info("Creating MainWindow")
    try:
        window = MainWindow()
        logger.info("MainWindow created successfully")
    except Exception as e:
        logger.error(f"Failed to create MainWindow: {e}")
        raise

    logger.info("Showing MainWindow")
    window.show()

    logger.info(f"Window shown, owns_application={owns_application}")
    if owns_application:
        logger.info("Starting Qt event loop")
        result = app.exec()
        logger.info(f"Qt event loop exited with code: {result}")
        return result

    logger.info("Not owning application, returning 0")
    return 0

"""Undo/redo stack for 3dfs actions."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal


class UndoManager(QObject):
    """A manager for the undo/redo stack."""

    stackChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._undo_stack: list[Callable] = []
        self._redo_stack: list[Callable] = []

    def add(self, undo_callback: Callable, redo_callback: Callable) -> None:
        """Add a new action to the undo stack."""
        self._undo_stack.append(undo_callback)
        self._redo_stack.clear()
        self._redo_stack.append(redo_callback)
        self.stackChanged.emit()

    def undo(self) -> None:
        """Undo the last action."""
        if not self._undo_stack:
            return
        undo_callback = self._undo_stack.pop()
        undo_callback()
        self.stackChanged.emit()

    def redo(self) -> None:
        """Redo the last undone action."""
        if not self._redo_stack:
            return
        redo_callback = self._redo_stack.pop()
        redo_callback()
        self._undo_stack.append(redo_callback)
        self.stackChanged.emit()

    def can_undo(self) -> bool:
        """Return whether there are any actions to undo."""
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        """Return whether there are any actions to redo."""
        return bool(self._redo_stack)

"""Custom delegates for Qt views."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QStyledItemDelegate


class StarDelegate(QStyledItemDelegate):
    """A delegate that draws a star on the right side of a list item."""

    def paint(self, painter: QPainter, option, index):
        """Paint the item."""
        # Let the base class paint the item (including selection background)
        super().paint(painter, option, index)

        # Get the data
        tags = index.data(Qt.UserRole + 2)  # Using UserRole+2 for the tag list

        # Define rectangles
        rect = option.rect
        star_rect = QRect(rect.right() - 30, rect.top(), 30, rect.height())

        # Draw Star
        star_char = "★" if "starred" in (tags or []) else "☆"
        painter.drawText(star_rect, Qt.AlignCenter, star_char)

"""Widget for visualizing tag relationships as a simple graph."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..storage.service import TagGraph, TagGraphLink

__all__ = ["TagGraphPane"]


class _TagNodeItem(QGraphicsEllipseItem):
    def __init__(self, rect, tag_name: str, on_click):
        super().__init__(*rect)
        self._tag_name = tag_name
        self._on_click = on_click
        self.setAcceptHoverEvents(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_click is not None:
            self._on_click(self._tag_name)
            event.accept()
            return
        super().mousePressEvent(event)


class _TagLabelItem(QGraphicsTextItem):
    def __init__(self, text: str, tag_name: str, on_click):
        super().__init__(text)
        self._tag_name = tag_name
        self._on_click = on_click
        self.setAcceptHoverEvents(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_click is not None:
            self._on_click(self._tag_name)
            event.accept()
            return
        super().mousePressEvent(event)


class _TagGraphView(QGraphicsView):
    """Lightweight ``QGraphicsView`` that renders a tag graph."""

    tagClicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignCenter)

    def _handle_node_click(self, tag: str) -> None:
        self.tagClicked.emit(tag)

    def render_graph(self, graph: TagGraph, *, focus_tag: str | None = None) -> None:
        self._scene.clear()
        if not graph.nodes:
            return

        max_count = max(node.count for node in graph.nodes) or 1
        min_node_size = 18.0
        max_node_size = 72.0

        radius = 140 + (len(graph.nodes) * 6)
        positions: dict[str, QPointF] = {}

        ordered_nodes = list(graph.nodes)
        if focus_tag:
            ordered_nodes.sort(key=lambda node: (0 if node.name == focus_tag else 1, node.name))

        if focus_tag and focus_tag in {node.name for node in ordered_nodes}:
            positions[focus_tag] = QPointF(0, 0)
            neighbors = [node for node in ordered_nodes if node.name != focus_tag]
            span = max(len(neighbors), 1)
            for index, node in enumerate(neighbors):
                angle = 2 * math.pi * index / span
                x = radius * math.cos(angle)
                y = radius * math.sin(angle)
                positions[node.name] = QPointF(x, y)
        else:
            for index, node in enumerate(ordered_nodes):
                angle = 2 * math.pi * index / max(len(ordered_nodes), 1)
                x = radius * math.cos(angle)
                y = radius * math.sin(angle)
                positions[node.name] = QPointF(x, y)

        # Draw edges first so nodes sit on top
        if graph.links:
            max_weight = max(link.weight for link in graph.links) or 1
            for link in graph.links:
                start = positions.get(link.source)
                end = positions.get(link.target)
                if start is None or end is None:
                    continue
                weight_ratio = link.weight / max_weight
                pen = QPen(QColor(160, 160, 160))
                pen.setWidthF(1.0 + weight_ratio * 3.0)
                line = QGraphicsLineItem(start.x(), start.y(), end.x(), end.y())
                line.setPen(pen)
                self._scene.addItem(line)

        font = QFont()
        font.setPointSize(9)

        for node in graph.nodes:
            center = positions[node.name]
            size_ratio = node.count / max_count
            diameter = min_node_size + (max_node_size - min_node_size) * size_ratio
            rect = (
                center.x() - diameter / 2,
                center.y() - diameter / 2,
                diameter,
                diameter,
            )
            ellipse = _TagNodeItem(
                rect,
                node.name,
                self._handle_node_click,
            )
            if focus_tag and node.name == focus_tag:
                color = QColor(255, 180, 70)
            else:
                color = QColor(70, 140, 255)
            ellipse.setBrush(color)
            ellipse.setPen(QColor(40, 90, 180))
            ellipse.setToolTip(f"{node.name}\nItems: {node.count}")
            self._scene.addItem(ellipse)

            label = _TagLabelItem(
                node.name,
                node.name,
                self._handle_node_click,
            )
            label.setFont(font)
            label.setDefaultTextColor(Qt.white)
            label_rect = label.boundingRect()
            label.setPos(
                center.x() - label_rect.width() / 2,
                center.y() - label_rect.height() / 2,
            )
            self._scene.addItem(label)

        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-80, -80, 80, 80))


class TagGraphPane(QWidget):
    """Full-screen pane that shows the tag web visualization."""

    refreshRequested = Signal()
    closeRequested = Signal()
    tagFilterRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._graph: TagGraph | None = None
        self._focused_tag: str | None = None

        title = QLabel("Tag Web", self)
        title.setObjectName("tagGraphTitle")

        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self.refreshRequested)
        self._reset_button = QPushButton("Reset View", self)
        self._reset_button.clicked.connect(self._reset_focus)
        self._close_button = QPushButton("Close", self)
        self._close_button.clicked.connect(self.closeRequested)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._refresh_button)
        header.addWidget(self._reset_button)
        header.addWidget(self._close_button)

        self._view = _TagGraphView(self)
        self._view.setObjectName("tagGraphView")
        self._view.tagClicked.connect(self._handle_view_click)

        self._message_label = QLabel("", self)
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._message_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self._stack, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def show_loading(self) -> None:
        self._message_label.setText("Building tag web…")
        self._stack.setCurrentWidget(self._message_label)

    def show_message(self, text: str) -> None:
        self._message_label.setText(text)
        self._stack.setCurrentWidget(self._message_label)

    def set_graph(self, graph: TagGraph) -> None:
        self._graph = graph
        self._focused_tag = None
        if not graph.nodes:
            self.show_message("No tags available yet.")
            return
        self._render_view(graph)

    def _render_view(self, graph: TagGraph, focus_tag: str | None = None) -> None:
        self._view.render_graph(graph, focus_tag=focus_tag)
        self._stack.setCurrentWidget(self._view)

    def _reset_focus(self) -> None:
        if self._graph is None:
            return
        self._focused_tag = None
        self._render_view(self._graph)

    def _handle_view_click(self, tag: str) -> None:
        if self._focused_tag == tag:
            self.tagFilterRequested.emit(tag)
            return
        self._focus_on_tag(tag)

    def _focus_on_tag(self, tag: str) -> None:
        if self._graph is None:
            return
        focus_graph = self._build_focus_graph(tag)
        if not focus_graph.nodes:
            return
        self._focused_tag = tag
        self._render_view(focus_graph, focus_tag=tag)

    def _build_focus_graph(self, tag: str) -> TagGraph:
        if self._graph is None:
            return TagGraph((), ())
        node_lookup = {node.name: node for node in self._graph.nodes}
        if tag not in node_lookup:
            return TagGraph((), ())
        neighbor_names = {tag}
        links: list[TagGraphLink] = []
        for link in self._graph.links:
            if link.source == tag or link.target == tag:
                neighbor_names.add(link.source)
                neighbor_names.add(link.target)
                links.append(link)
        nodes = [node_lookup[name] for name in neighbor_names if name in node_lookup]
        return TagGraph(nodes=tuple(nodes), links=tuple(links))

"""Assembly page: displays an assembly and its component parts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import Qt, Slot, Signal, QSize
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QImage,
    QPainter,
    QColor,
    QFont,
    QKeySequence,
    QShortcut,
)
from PySide6.QtCore import QObject, QRunnable, QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QHBoxLayout,
    QLineEdit,
    QTextEdit,
    QSplitter,
    QVBoxLayout,
    QMenu,
    QWidget,
)

from .preview_pane import PreviewPane
from ..thumbnails import ThumbnailCache, ThumbnailGenerationError


@dataclass(slots=True)
class AssemblyComponent:
    path: str
    label: str
    kind: str = "component"  # "component" or "attachment"


@dataclass(slots=True)
class AssemblyArrangement:
    path: str
    label: str
    description: str | None = None
    rel_path: str | None = None
    metadata: dict[str, Any] | None = None


class AssemblyPane(QWidget):
    """Show assembly metadata and component list with live preview."""

    addAttachmentsRequested = Signal()
    filesDropped = Signal(list)
    openFolderRequested = Signal()
    openItemFolderRequested = Signal(str)
    navigateUpRequested = Signal()
    navigateToPathRequested = Signal(str)
    newPartRequested = Signal()
    refreshRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._title = QLabel("Assembly", self)
        self._title.setObjectName("assemblyTitle")
        self._title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self._path_label = QLabel("", self)
        self._path_label.setObjectName("assemblyPath")
        self._path_label.setWordWrap(True)
        self._breadcrumb = QLabel("", self)
        self._breadcrumb.setObjectName("assemblyBreadcrumb")
        self._breadcrumb.setTextFormat(Qt.RichText)
        self._breadcrumb.setOpenExternalLinks(False)
        self._breadcrumb.linkActivated.connect(self._handle_breadcrumb_link)

        self._readme = QTextEdit(self)
        self._readme.setObjectName("assemblyReadme")
        self._readme.setReadOnly(True)
        self._readme.setVisible(False)

        self._components = QListWidget(self)
        self._components.setObjectName("assemblyComponents")
        self._components.setSelectionMode(QAbstractItemView.SingleSelection)
        self._components.currentItemChanged.connect(self._handle_component_selected)
        self._icon_size = QSize(48, 48)
        self._components.setIconSize(self._icon_size)
        self._components.setContextMenuPolicy(Qt.CustomContextMenu)
        self._components.customContextMenuRequested.connect(self._show_components_context_menu)

        self._preview = PreviewPane(parent=self)

        splitter = QSplitter(self)
        splitter.addWidget(self._components)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # Actions + Search
        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(8)
        self._btn_up = QPushButton("Up", self)
        self._btn_up.setToolTip("Go to parent assembly folder")
        self._btn_up.clicked.connect(self.navigateUpRequested)
        self._btn_refresh = QPushButton("Refresh", self)
        self._btn_refresh.setToolTip("Rescan this assembly folder")
        self._btn_refresh.clicked.connect(self.refreshRequested)
        self._btn_add_attachments = QPushButton("Add Attachment(s)", self)
        self._btn_add_attachments.clicked.connect(self.addAttachmentsRequested)
        self._btn_open_folder = QPushButton("Open Folder", self)
        self._btn_open_folder.clicked.connect(self.openFolderRequested)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search parts and attachments…  (Ctrl+F)")
        self._search.textChanged.connect(self._apply_filter)
        actions_row.addWidget(self._btn_up)
        actions_row.addWidget(self._btn_refresh)
        actions_row.addWidget(self._btn_add_attachments)
        actions_row.addWidget(self._btn_open_folder)
        actions_row.addStretch(1)
        actions_row.addWidget(self._search, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._title)
        layout.addWidget(self._path_label)
        layout.addWidget(self._breadcrumb)
        layout.addLayout(actions_row)
        layout.addWidget(self._readme)
        layout.addWidget(splitter, 1)

        self._assembly_path: str | None = None
        self._thread_pool = QThreadPool.globalInstance()
        # Shortcut to focus search
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)

    def set_assembly(
        self,
        path: str,
        *,
        label: str,
        components: Iterable[AssemblyComponent],
        arrangements: Iterable[AssemblyArrangement] = (),
        attachments: Iterable[AssemblyComponent] = (),
    ) -> None:
        self._assembly_path = path
        self._title.setText(label)
        self._path_label.setText(path)
        self._update_breadcrumb(Path(path))
        self._btn_add_attachments.setEnabled(True)
        self._btn_open_folder.setEnabled(True)
        self._btn_up.setEnabled(True)
        self._components.clear()
        self._search.clear()
        comp_paths: list[str] = []
        arrangement_paths: list[str] = []
        attach_paths: list[str] = []
        for comp in components:
            item = QListWidgetItem(comp.label or comp.path)
            item.setData(Qt.UserRole, comp.path)
            item.setData(Qt.UserRole + 1, comp.kind)
            item.setToolTip(comp.path)
            if comp.kind == "placeholder":
                item.setIcon(QIcon(self._placeholder_icon()))
            self._components.addItem(item)
            if comp.kind == "component":
                comp_paths.append(comp.path)

        arrangements = list(arrangements)
        if arrangements:
            header = QListWidgetItem("Arrangements")
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            header.setFlags(Qt.ItemIsEnabled)
            self._components.addItem(header)
            for arrangement in arrangements:
                display = arrangement.label or arrangement.path
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, arrangement.path)
                item.setData(Qt.UserRole + 1, "arrangement")
                tooltip_parts = []
                if arrangement.description:
                    tooltip_parts.append(str(arrangement.description))
                if arrangement.rel_path:
                    tooltip_parts.append(str(arrangement.rel_path))
                tooltip_parts.append(arrangement.path)
                item.setToolTip("\n".join(part for part in tooltip_parts if part))
                self._components.addItem(item)
                arrangement_paths.append(arrangement.path)

        attachments = list(attachments)
        if attachments:
            header = QListWidgetItem("Attachments")
            font = header.font(); font.setBold(True)
            header.setFont(font)
            header.setFlags(Qt.ItemIsEnabled)
            self._components.addItem(header)
            for att in attachments:
                text = att.label or att.path
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, att.path)
                item.setData(Qt.UserRole + 1, att.kind)
                item.setToolTip(att.path)
                self._components.addItem(item)
                attach_paths.append(att.path)

        # Queue thumbnail icon generation for parts
        if comp_paths or attach_paths or arrangement_paths:
            self._enqueue_icons(comp_paths, attach_paths + arrangement_paths)
        # Load README for the assembly folder
        self._load_readme(Path(path))

        if self._components.count():
            self._components.setCurrentRow(0)
        else:
            self._preview.clear()

    @Slot()
    def _handle_component_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            self._preview.clear()
            return
        comp_path = str(current.data(Qt.UserRole) or current.text())
        self._preview.set_item(comp_path, label=current.text(), metadata=None, asset_record=None)
        # Keep selection visible if filtered
        self._ensure_visible(current)

    # ------------------------------------------------------------------
    # Thumbnail icon worker
    # ------------------------------------------------------------------
    def _enqueue_icons(self, comp_paths: list[str], attach_paths: list[str]) -> None:
        for path in comp_paths:
            worker = _IconWorker(path, self._icon_size)
            worker.signals.result.connect(self._apply_icon)
            # errors are ignored silently to avoid noisy UI
            self._thread_pool.start(worker)
        for path in attach_paths:
            worker = _IconWorker(path, self._icon_size)
            worker.signals.result.connect(self._apply_icon)
            self._thread_pool.start(worker)

    @Slot(str, object)
    def _apply_icon(self, path: str, pixmap_obj: object) -> None:
        pixmap = pixmap_obj if isinstance(pixmap_obj, QPixmap) else None
        if pixmap is None or pixmap.isNull():
            return
        for row in range(self._components.count()):
            item = self._components.item(row)
            if str(item.data(Qt.UserRole) or "") == path:
                # Scale to icon size for crisp display
                icon_pm = pixmap.scaled(self._icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                item.setIcon(QIcon(icon_pm))
                break

    # ------------------------------------------------------------------
    # Filtering & UX helpers
    # ------------------------------------------------------------------
    def _focus_search(self) -> None:
        self._search.setFocus(Qt.TabFocusReason)
        self._search.selectAll()

    @Slot(str)
    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        for row in range(self._components.count()):
            item = self._components.item(row)
            # Always show headers
            if not item.flags() & Qt.ItemIsSelectable:
                item.setHidden(False)
                continue
            label = (item.text() or "").casefold()
            path = str(item.data(Qt.UserRole) or "").casefold()
            item.setHidden(bool(needle) and (needle not in label and needle not in path))

    def _ensure_visible(self, item: QListWidgetItem) -> None:
        row = self._components.row(item)
        self._components.scrollToItem(item)

    # ------------------------------------------------------------------
    # Breadcrumb helpers
    # ------------------------------------------------------------------
    def _update_breadcrumb(self, folder: Path) -> None:
        parts = []
        # Build clickable breadcrumb from root to leaf
        acc = Path(folder.anchor) if folder.anchor else Path('/')
        for comp in folder.parts:
            # Skip empty or root duplicate
            if comp in ("/", folder.anchor):
                continue
            acc = acc / comp
            href = acc.as_posix()
            parts.append(f'<a href="{href}">{comp}</a>')
        self._breadcrumb.setText(" / ".join(parts) or folder.as_posix())

    @Slot(str)
    def _handle_breadcrumb_link(self, href: str) -> None:
        if href:
            self.navigateToPathRequested.emit(href)

    # ------------------------------------------------------------------
    # Selection + Context Menu (class methods)
    # ------------------------------------------------------------------
    def selected_item(self) -> tuple[str, str] | None:
        item = self._components.currentItem()
        if item is None:
            return None
        path = str(item.data(Qt.UserRole) or "").strip()
        kind = str(item.data(Qt.UserRole + 1) or "component")
        return (path, kind)

    def select_item(self, target_path: str) -> None:
        target = str(target_path)
        for row in range(self._components.count()):
            item = self._components.item(row)
            # Skip non-selectable headers
            if not (item.flags() & Qt.ItemIsSelectable):
                continue
            if str(item.data(Qt.UserRole) or "") == target:
                self._components.setCurrentItem(item)
                self._components.scrollToItem(item)
                return

    @Slot()
    def _show_components_context_menu(self, pos) -> None:
        try:
            item = self._components.itemAt(pos)
        except Exception:
            item = None
        if item is not None:
            self._components.setCurrentItem(item)
        menu = QMenu(self)
        new_part_act = menu.addAction("New Part…")
        add_act = menu.addAction("Add Attachment(s) Here…")
        open_act = menu.addAction("Open Containing Folder")
        action = menu.exec(self._components.mapToGlobal(pos))
        if action is None:
            return
        if action == new_part_act:
            self.newPartRequested.emit()
        elif action == add_act:
            self.addAttachmentsRequested.emit()
        elif action == open_act:
            target = None
            if item is not None:
                target = str(item.data(Qt.UserRole) or "").strip()
            if target:
                self.openItemFolderRequested.emit(target)
            else:
                self.openFolderRequested.emit()

    # ------------------------------------------------------------------
    # Drag & drop support for attachments (class methods)
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is None or not md.hasUrls():
            super().dropEvent(event)
            return
        paths: list[str] = []
        for url in md.urls():
            try:
                local = url.toLocalFile()
            except Exception:
                local = ""
            if local:
                paths.append(local)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _load_readme(self, folder: Path) -> None:
        base = folder if folder.is_dir() else folder.parent
        allowed = {"", ".md", ".markdown", ".txt", ".rst"}
        candidates = []
        try:
            for entry in base.iterdir():
                try:
                    if not entry.is_file():
                        continue
                    stem = entry.stem.lower()
                    suffix = entry.suffix.lower()
                except Exception:
                    continue
                if suffix in allowed:
                    score = 0 if "readme" in stem else 1
                    candidates.append((score, entry.name.lower(), entry))
            if candidates:
                candidates.sort()
                chosen = candidates[0][2]
                try:
                    content = chosen.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                if content:
                    try:
                        self._readme.setMarkdown(content)
                    except Exception:
                        self._readme.setPlainText(content)
                    self._readme.setVisible(True)
                    return
        except Exception:
            pass
        self._readme.clear()
        self._readme.setVisible(False)


class _IconWorkerSignals(QObject):
    result = Signal(str, object)
    error = Signal(str, str)


class _IconWorker(QRunnable):
    _IMAGE_EXTENSIONS = frozenset({
        ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tga", ".tiff", ".webp",
    })

    def __init__(self, path: str, size: QSize) -> None:
        super().__init__()
        self._path = path
        self._size = size
        self.signals = _IconWorkerSignals()

    def run(self) -> None:  # pragma: no cover - async
        try:
            from pathlib import Path as _P
            import mimetypes as _mt
            p = _P(self._path)
            suffix = p.suffix.lower()
            if suffix in self._IMAGE_EXTENSIONS:
                img = QImage(str(p))
                if img.isNull():
                    self.signals.error.emit(self._path, "bad-image")
                    return
                self.signals.result.emit(self._path, img)
                return

            # Non-image: handle known attachment types with generated icons
            ctype, _ = _mt.guess_type(str(p))
            label, color = self._label_color_for(suffix, ctype or "")
            if label:
                img = self._render_label_icon(label, color, self._size)
                self.signals.result.emit(self._path, img)
                return

            cache = ThumbnailCache()
            result = cache.get_or_render(p, size=(max(64, self._size.width()), max(64, self._size.height())))
            data = result.image_bytes
            img = QImage()
            if not data or not img.loadFromData(data):
                self.signals.error.emit(self._path, "no-bytes")
                return
            self.signals.result.emit(self._path, img)
        except ThumbnailGenerationError as exc:
            self.signals.error.emit(self._path, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(self._path, exc.__class__.__name__)

    def _render_label_icon(self, text: str, color: tuple[int, int, int], size: QSize) -> QImage:
        w, h = max(16, size.width()), max(16, size.height())
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(QColor(32, 38, 46, 255))
        painter = QPainter(img)
        try:
            # Rounded rectangle with accent color
            painter.setRenderHint(QPainter.Antialiasing, True)
            bg = QColor(*color)
            painter.setBrush(bg)
            painter.setPen(QColor(0, 0, 0, 0))
            margin = int(min(w, h) * 0.08)
            painter.drawRoundedRect(margin, margin, w - 2 * margin, h - 2 * margin, 10, 10)
            # Text
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setBold(True)
            font.setPointSize(int(min(w, h) * 0.28))
            painter.setFont(font)
            painter.drawText(0, 0, w, h, Qt.AlignCenter, text[:4])
        finally:
            painter.end()
        return img

    def _label_color_for(self, suffix: str, ctype: str) -> tuple[str, tuple[int, int, int]]:
        s = suffix.lower()
        c = ctype.lower()
        # Map by extension first for specificity
        if s in {".pdf"} or c == "application/pdf":
            return ("PDF", (192, 48, 48))
        if s in {".md", ".markdown"} or c.endswith("markdown"):
            return ("MD", (36, 153, 99))
        if s in {".txt"} or c.startswith("text/"):
            return ("TXT", (60, 120, 200))
        if s in {".json"} or c == "application/json":
            return ("JSON", (0, 140, 140))
        if s in {".csv"} or c.endswith("csv"):
            return ("CSV", (0, 140, 140))
        if s in {".zip", ".7z", ".tar", ".gz", ".bz2", ".xz"} or "zip" in c or "compressed" in c:
            return ("ZIP", (215, 140, 0))
        if s in {".ppt", ".pptx"}:
            return ("PPT", (209, 72, 54))
        if s in {".doc", ".docx", ".odt"}:
            return ("DOC", (30, 100, 200))
        if s in {".xls", ".xlsx", ".ods"}:
            return ("XLS", (16, 124, 16))
        if s in {".py", ".sh", ".js", ".ts"}:
            return (s.lstrip(".").upper(), (120, 80, 180))
        if s == ".scad":
            return ("SCAD", (186, 109, 42))
        # Unknown file type
        if c and not c.startswith("image/"):
            return (c.split("/")[-1][:4].upper(), (110, 110, 110))
        return ("", (0, 0, 0))

    # Selection helpers
    def selected_item(self) -> tuple[str, str] | None:
        item = self._components.currentItem()
        if item is None:
            return None
        path = str(item.data(Qt.UserRole) or "").strip()
        kind = str(item.data(Qt.UserRole + 1) or "component")
        return (path, kind)

    @Slot()
    def _show_components_context_menu(self, pos) -> None:
        try:
            item = self._components.itemAt(pos)
        except Exception:
            item = None
        if item is not None:
            self._components.setCurrentItem(item)
        menu = QMenu(self)
        add_act = menu.addAction("Add Attachment(s) Here…")
        open_act = menu.addAction("Open Containing Folder")
        action = menu.exec(self._components.mapToGlobal(pos))
        if action is None:
            return
        if action == add_act:
            self.addAttachmentsRequested.emit()
        elif action == open_act:
            target = None
            if item is not None:
                target = str(item.data(Qt.UserRole) or "").strip()
            if target:
                self.openItemFolderRequested.emit(target)
            else:
                self.openFolderRequested.emit()

    # ------------------------------------------------------------------
    # Drag & drop support for attachments
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is not None and md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        md = event.mimeData()
        if md is None or not md.hasUrls():
            super().dropEvent(event)
            return
        paths: list[str] = []
        for url in md.urls():
            try:
                local = url.toLocalFile()
            except Exception:
                local = ""
            if local:
                paths.append(local)
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _load_readme(self, folder: Path) -> None:
        base = folder if folder.is_dir() else folder.parent
        candidates = (
            "README.md",
            "Readme.md",
            "readme.md",
            "README.txt",
            "readme.txt",
            "README",
        )
        for name in candidates:
            p = base / name
            try:
                if not p.exists() or not p.is_file():
                    continue
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                self._readme.setMarkdown(content)
            except Exception:
                self._readme.setPlainText(content)
            self._readme.setVisible(True)
            return
        self._readme.clear()
        self._readme.setVisible(False)

    def _placeholder_icon(self) -> QPixmap:
        # Red exclamation badge icon for parts without a model
        w, h = self._icon_size.width(), self._icon_size.height()
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            # Background circle
            bg = QColor(255, 64, 64)
            p.setBrush(bg)
            p.setPen(QColor(0, 0, 0, 0))
            margin = int(min(w, h) * 0.12)
            p.drawEllipse(margin, margin, w - 2 * margin, h - 2 * margin)
            # Exclamation mark
            p.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setBold(True)
            font.setPointSize(int(min(w, h) * 0.6))
            p.setFont(font)
            p.drawText(0, int(h * 0.08), w, h, Qt.AlignCenter, "!")
        finally:
            p.end()
        return QPixmap.fromImage(img)

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QListWidgetItem, QMessageBox

from ..config import get_config
from ..container import is_container_asset
from ..customizer.pipeline import PipelineResult
from ..ui.version_manager_dialog import VersionManagerDialog

if TYPE_CHECKING:
    from ..storage import ContainerVersionRecord
    from .main_window import MainWindow


logger = logging.getLogger(__name__)


class UIManager:
    """Handles UI-related actions for the main window."""

    def __init__(self, main_window: MainWindow) -> None:
        """Initialize the UI manager."""
        self._main_window = main_window

    def handle_selection_change(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        prev_container = self._main_window._current_container_path
        self._main_window._current_container_version_id = None

        if current is None:
            self._main_window._preview_pane.clear()
            self._main_window._current_asset = None
            self._main_window._current_container_path = None
            if hasattr(self._main_window, "_tag_panel"):
                self._main_window._tag_panel.set_active_item(None)
            self._main_window._suppress_history = False
            return

        asset_id_data = current.data(Qt.UserRole)
        asset_path_data = current.data(Qt.UserRole + 1)

        asset_id: int | None
        try:
            asset_id = int(asset_id_data) if asset_id_data is not None else None
        except (TypeError, ValueError):
            asset_id = None

        asset_path = str(asset_path_data or current.text() or "").strip()

        if asset_id is None and not asset_path:
            self._main_window._preview_pane.clear()
            self._main_window._current_asset = None
            self._main_window._current_container_path = None
            self._main_window._suppress_history = False
            return

        if asset_path and not self._main_window._is_safe_path_string(asset_path):
            try:
                safe_sample = repr(asset_path[:100]) if len(asset_path) > 100 else repr(asset_path)
                print(
                    f"CORRUPTED PATH DETECTED: len={len(asset_path)}, sample={safe_sample}",
                    flush=True,
                )
            except Exception:
                print(
                    "CORRUPTED PATH DETECTED: len=?, repr failed",
                    flush=True,
                )
            self._main_window._preview_pane.clear()
            self._main_window._current_asset = None
            self._main_window._current_container_path = None
            self._main_window.statusBar().showMessage("Invalid path data detected - skipping selection", 5000)
            self._main_window._suppress_history = False
            return

        asset = None
        if asset_id is not None:
            try:
                asset = self._main_window._asset_service.get_asset(asset_id)
            except Exception:
                asset = None
        if asset is None and asset_path:
            asset = self._main_window._asset_service.get_asset_by_path(asset_path)
            if asset is not None:
                asset_id = asset.id

        if hasattr(self._main_window, "_tag_panel"):
            self._main_window._tag_panel.set_active_item(asset_id)

        metadata = asset.metadata if asset is not None and isinstance(asset.metadata, dict) else None
        is_container = False
        if metadata is not None:
            kind_value = str(metadata.get("kind") or "").strip().lower()
            if kind_value in {"container"}:
                is_container = True
        if asset is not None and not is_container:
            try:
                candidate = Path(asset.path).expanduser()
                is_container = candidate.is_dir()
            except Exception:
                is_container = False

        if asset is not None and is_container:
            try:
                canonical_path = str(Path(asset.path).expanduser().resolve())
            except Exception:
                canonical_path = str(asset.path)
            focus_components: list[str] = []
            if not self._main_window._suppress_history and prev_container and canonical_path != prev_container:
                self._main_window._container_history.append(prev_container)
            if self._main_window._tag_filter:
                try:
                    container_id = int(asset.id)
                except Exception:
                    container_id = None
                if container_id is not None:
                    focus_components = self._main_window._tag_filter_focus_map.get(container_id, [])
            self.show_container(asset)
            if focus_components:
                try:
                    self._main_window._container_pane.focus_matching_item(focus_components)
                except Exception:
                    pass
            self._main_window._suppress_history = False
            return

        # Unify: if selection is a file, show its folder as container context
        target_path = asset.path if asset is not None else asset_path
        try:
            target = Path(str(target_path)).expanduser()
        except Exception:
            target = None

        if target is not None and target.exists() and target.is_file():
            parent = target.parent
            try:
                parent.relative_to(get_config().library_root)
            except Exception:
                # Outside library root → preview only
                self._main_window._preview_pane.set_item(
                    str(target), label=current.text(), metadata=None, asset_record=asset
                )
                self._main_window._detail_stack.setCurrentWidget(self._main_window._preview_pane)
                self._main_window._current_asset = asset
                self._main_window._current_container_path = None
                self._main_window._suppress_history = False
                return

            # Load/show container for parent and select this file inside
            self._main_window._suppress_history = True
            self._main_window._container_manager.create_or_update_container(
                parent,
                select_in_repo=True,
                focus_component=str(target),
                show_container=True,
            )
            for row in range(self._main_window._repository_list.count()):
                it = self._main_window._repository_list.item(row)
                raw_path = it.data(Qt.UserRole + 1) or it.text()
                if str(raw_path) == str(parent):
                    self._main_window._repository_list.setCurrentItem(it)
                    break
            parent_asset = self._main_window._asset_service.get_asset_by_path(str(parent))
            if parent_asset is not None:
                self.show_container(parent_asset)
                try:
                    self._main_window._container_pane.select_item(str(target))
                except Exception:
                    pass
                self._main_window._current_asset = parent_asset
                self._main_window._suppress_history = False
                return

        # Default: preview pane
        if asset is None:
            self._main_window._preview_pane.set_item(
                asset_path,
                label=current.text(),
                metadata=None,
                asset_record=None,
            )
            self._main_window._current_asset = None
            self._main_window._current_container_path = None
        else:
            self._main_window._preview_pane.set_item(
                asset.path,
                label=asset.label,
                metadata=asset.metadata,
                asset_record=asset,
            )
            self._main_window._current_asset = asset
        self._main_window._detail_stack.setCurrentWidget(self._main_window._preview_pane)
        self._main_window._container_manager.update_container_watchers()
        self._main_window._suppress_history = False

    def show_container(self, asset, *, version_id: int | None = None) -> None:
        from ..ui.container_pane import ContainerComponent

        versions = self._main_window._asset_service.list_container_versions(asset.id)
        selected_version: ContainerVersionRecord | None = None
        if version_id is not None:
            selected_version = next((v for v in versions if v.id == version_id), None)
            if selected_version is None:
                candidate = self._main_window._asset_service.get_container_version(version_id)
                if candidate is not None and candidate.container_asset_id == asset.id:
                    selected_version = candidate
                    if all(entry.id != candidate.id for entry in versions):
                        versions = [candidate, *versions]
                else:
                    self._main_window.statusBar().showMessage(
                        "Selected version is unavailable for this container.",
                        4000,
                    )
                    version_id = None

        versions = sorted(versions, key=lambda record: record.created_at, reverse=True)
        meta_source = selected_version.metadata if selected_version else asset.metadata
        meta = dict(meta_source or {})
        comps_raw = meta.get("components") or []

        comp_objs: list[ContainerComponent] = []
        for entry in comps_raw:
            if not isinstance(entry, dict):
                continue
            try:
                raw_path = entry.get("path")
                path = str(raw_path or "").strip()
            except Exception:
                continue
            if not path:
                continue
            raw_label = entry.get("label")
            try:
                label = str(raw_label).strip() if raw_label is not None else ""
            except Exception:
                label = ""
            if not label:
                display_candidate = entry.get("display_name")
                if isinstance(display_candidate, str) and display_candidate.strip():
                    label = display_candidate.strip()
            if not label:
                try:
                    label = Path(path).name
                except Exception:
                    label = path
            try:
                kind = str(entry.get("kind") or "component")
            except Exception:
                kind = "component"
            metadata_entry = entry.get("metadata")
            metadata_dict = metadata_entry if isinstance(metadata_entry, dict) else None
            asset_id_value = entry.get("asset_id")
            try:
                asset_id = int(asset_id_value)
            except Exception:
                asset_id = None
            resolved_kind = kind if kind in {"component", "placeholder", "linked_component"} else "component"
            comp_objs.append(
                ContainerComponent(
                    path=path,
                    label=label,
                    kind=resolved_kind,
                    metadata=metadata_dict,
                    asset_id=asset_id,
                )
            )
        atts_raw = meta.get("attachments") or []
        att_objs = []
        for a in atts_raw:
            if not isinstance(a, dict):
                continue
            path_value = str(a.get("path") or "").strip()
            if not path_value:
                continue
            raw_label = a.get("label")
            label_text = str(raw_label).strip() if isinstance(raw_label, str) else ""
            if not label_text:
                display_candidate = a.get("display_name")
                if isinstance(display_candidate, str) and display_candidate.strip():
                    label_text = display_candidate.strip()
            if not label_text:
                try:
                    label_text = Path(path_value).name
                except Exception:
                    label_text = path_value
            metadata_entry = a.get("metadata")
            metadata_dict = metadata_entry if isinstance(metadata_entry, dict) else None
            att_objs.append(
                ContainerComponent(
                    path=path_value,
                    label=label_text,
                    kind="attachment",
                    metadata=metadata_dict,
                )
            )

        attachment_paths = {component.path for component in att_objs}

        file_objs: list[ContainerComponent] = []
        for entry in meta.get("files") or []:
            if not isinstance(entry, dict):
                continue
            try:
                raw_path = entry.get("path")
                path_value = str(raw_path or "").strip()
            except Exception:
                continue
            if not path_value:
                continue
            if path_value in attachment_paths:
                continue
            raw_label = entry.get("label")
            try:
                label_text = str(raw_label).strip() if raw_label is not None else ""
            except Exception:
                label_text = ""
            if not label_text:
                display_candidate = entry.get("display_name")
                if isinstance(display_candidate, str) and display_candidate.strip():
                    label_text = display_candidate.strip()
            if not label_text:
                try:
                    label_text = Path(path_value).name
                except Exception:
                    label_text = path_value
            metadata_entry = entry if isinstance(entry, dict) else None
            asset_id_value = entry.get("asset_id")
            try:
                asset_id = int(asset_id_value) if asset_id_value is not None else None
            except Exception:
                asset_id = None
            file_objs.append(
                ContainerComponent(
                    path=path_value,
                    label=label_text,
                    kind="file",
                    metadata=metadata_entry,
                    asset_id=asset_id,
                )
            )

        link_objs: list[ContainerComponent] = []
        linked_here_objs: list[ContainerComponent] = []

        for entry in meta.get("links", []) or []:
            if not isinstance(entry, dict):
                continue
            path_value = str(entry.get("path") or "").strip()
            target_value = str(entry.get("target") or "").strip()
            if not path_value and target_value:
                path_value = target_value
            if not path_value:
                continue
            if not target_value:
                target_value = path_value
            label_value = str(entry.get("label") or Path(path_value).name)
            link_meta: dict[str, Any] = {
                "link_target": target_value,
            }
            metadata_entry = entry.get("metadata")
            if isinstance(metadata_entry, dict):
                link_meta.update(metadata_entry)
            link_type = entry.get("link_type")
            if isinstance(link_type, str) and link_type:
                link_meta["link_type"] = link_type
            version_name = entry.get("target_version_name")
            if isinstance(version_name, str) and version_name:
                link_meta.setdefault("target_version_name", version_name)
            link_objs.append(
                ContainerComponent(
                    path=path_value,
                    label=label_value,
                    kind="link",
                    metadata=link_meta,
                )
            )

        for entry in meta.get("linked_from", []) or []:
            if not isinstance(entry, dict):
                continue
            source_path = str(entry.get("source_path") or "").strip()
            source_label = str(entry.get("source_label") or "").strip()
            if not source_path or not source_label:
                continue
            link_meta: dict[str, Any] = dict(entry)
            link_meta["link_target"] = source_path
            link_meta["link_direction"] = "incoming"
            link_meta["source_label"] = source_label
            linked_here_objs.append(
                ContainerComponent(
                    path=source_path,
                    label=source_label,
                    kind="linked_here",
                    metadata=link_meta,
                )
            )

        friendly_label = self._main_window._library_manager.friendly_asset_label(asset)
        selected_id = selected_version.id if selected_version else None
        pane = self._main_window._container_pane
        pane.set_container(
            asset.path,
            asset_id=asset.id,
            label=friendly_label,
            components=comp_objs,
            attachments=att_objs + file_objs,
            linked_here=linked_here_objs,
            links=link_objs,
            version_id=selected_id,
        )
        pane.set_container_versions(versions, selected_version_id=selected_id)
        self._main_window._detail_stack.setCurrentWidget(pane)
        self._main_window._current_asset = asset
        self._main_window._current_container_version_id = selected_id
        self._main_window._container_manager.update_container_watchers()
        try:
            resolved_path = str(Path(asset.path).expanduser().resolve())
        except Exception:
            resolved_path = asset.path
        self._main_window._current_container_path = resolved_path

    def handle_preview_navigation(self, target: str) -> None:
        path = Path(target)
        if path.is_dir():
            self._main_window._container_manager.create_or_update_container(
                path,
                show_container=True,
                select_in_repo=True,
            )
            asset = self._main_window._asset_service.get_asset_by_path(str(path))
            if asset is not None:
                self.show_container(asset)
            return
        asset = self._main_window._asset_service.get_asset_by_path(str(path))
        if asset is not None:
            self._main_window._preview_pane.set_item(
                asset.path,
                label=asset.label,
                metadata=asset.metadata,
                asset_record=asset,
            )
            self._main_window._detail_stack.setCurrentWidget(self._main_window._preview_pane)
            self._main_window._current_asset = asset
            self._main_window._container_manager.update_container_watchers()

    def handle_container_version_selected(self, version_id: int | None) -> None:
        asset = self._main_window._current_asset
        if asset is None or not is_container_asset(asset):
            return
        if version_id is None:
            self.show_container(asset, version_id=None)
            return
        version = self._main_window._asset_service.get_container_version(version_id)
        if version is None or version.container_asset_id != asset.id:
            self._main_window.statusBar().showMessage(
                "Selected version is unavailable.",
                4000,
            )
            self.show_container(asset, version_id=None)
            return
        self.show_container(asset, version_id=version_id)

    def create_container_version_snapshot(self) -> None:
        asset = self._main_window._current_asset
        if asset is None or not is_container_asset(asset):
            QMessageBox.information(
                self._main_window,
                "Create Version",
                "Select a container before creating a version.",
            )
            return

        versions = self._main_window._asset_service.list_container_versions(asset.id)
        default_name = self._derive_next_version_name(versions)
        version_name, accepted = QInputDialog.getText(
            self._main_window,
            "Create Version",
            "Version name:",
            text=default_name,
        )
        if not accepted:
            return

        normalized_name = version_name.strip()
        if not normalized_name:
            QMessageBox.warning(
                self._main_window,
                "Create Version",
                "Version name cannot be empty.",
            )
            return

        try:
            record = self._main_window._asset_service.create_container_version(
                asset.id,
                name=normalized_name,
            )
        except ValueError as exc:
            QMessageBox.warning(
                self._main_window,
                "Create Version",
                str(exc) or "Unable to create version.",
            )
            return

        self._main_window.statusBar().showMessage(
            f"Created version '{record.name}'.",
            4000,
        )
        self.show_container(asset, version_id=record.id)

    def _derive_next_version_name(self, versions: list[ContainerVersionRecord]) -> str:
        existing = {version.name for version in versions}
        index = len(versions) + 1
        while True:
            candidate = f"v{index}"
            if candidate not in existing:
                return candidate
            index += 1

    def manage_container_versions(self) -> None:
        asset = self._main_window._current_asset
        if asset is None or not is_container_asset(asset):
            QMessageBox.information(
                self._main_window,
                "Manage Versions",
                "Select a container to manage versions.",
            )
            return

        versions = self._main_window._asset_service.list_container_versions(asset.id)
        if not versions:
            QMessageBox.information(
                self._main_window,
                "Manage Versions",
                "No versions exist for this container yet.",
            )
            return

        dialog = VersionManagerDialog(asset, self._main_window._asset_service, parent=self._main_window)
        dialog.versionsChanged.connect(lambda: self._handle_versions_updated(asset))
        dialog.exec()

    def _handle_versions_updated(self, asset) -> None:
        selected_id = self._main_window._current_container_version_id
        self.show_container(asset, version_id=selected_id)
        self._main_window.statusBar().showMessage(
            "Version list updated.",
            3000,
        )

    def show_tag_graph(self) -> None:
        pane = getattr(self._main_window, "_tag_graph_pane", None)
        if pane is None:
            return
        pane.show_loading()
        graph = self._main_window._asset_service.build_tag_graph()
        pane.set_graph(graph)
        self._main_window._detail_stack.setCurrentWidget(pane)

    def close_tag_graph(self) -> None:
        if self._main_window._current_asset and is_container_asset(self._main_window._current_asset):
            target = self._main_window._container_pane
        else:
            target = self._main_window._preview_pane
        self._main_window._detail_stack.setCurrentWidget(target)

    def handle_tag_graph_tag_selected(self, tag: str) -> None:
        self.handle_tag_filter_request(tag)
        self.close_tag_graph()
        self._main_window.statusBar().showMessage(
            f"Filtering by tag '{tag.strip()}'.",
            4000,
        )

    def handle_customization_generated(self, result: PipelineResult) -> None:
        asset_path = result.output_path
        asset = self._main_window._asset_service.ensure_asset(asset_path, label=Path(asset_path).name)
        metadata = dict(asset.metadata or {})
        metadata.setdefault("kind", "generated")
        metadata.setdefault("source_customization", result.customization_id)
        metadata.setdefault("parameters", result.parameters)
        metadata.setdefault("generated_at", result.generated_at.isoformat())
        updated = self._main_window._asset_service.update_asset(asset.id, metadata=metadata)
        logger.info("Customization generated, triggering library refresh")

        # Refresh the container for the new customized asset if we know which container it belongs to
        if result.container_path:
            self._main_window._container_manager.create_or_update_container(result.container_path)

        # Then populate the repository to ensure the new container appears in the library browser
        self._main_window._library_manager.populate_repository()
        self._main_window._container_manager.refresh_current_container()
        self._main_window._preview_pane.set_item(
            updated.path,
            label=updated.label,
            metadata=updated.metadata,
            asset_record=updated,
        )
        self._main_window._detail_stack.setCurrentWidget(self._main_window._preview_pane)
        self._main_window._current_asset = updated
        self._main_window._container_manager.update_container_watchers()
        self._main_window.statusBar().showMessage("Customization output recorded", 4000)

    def handle_back_requested(self) -> None:
        if self._main_window._container_history:
            target = self._main_window._container_history.pop()
            self._main_window._suppress_history = True
            self.select_repository_path(target)
            self._main_window._suppress_history = False
            return

        self._main_window._repository_list.clearSelection()
        self._main_window._current_asset = None
        self._main_window._current_container_path = None
        self._main_window._preview_pane.clear()
        self._main_window._detail_stack.setCurrentWidget(self._main_window._preview_pane)
        if hasattr(self._main_window, "_tag_panel"):
            self._main_window._tag_panel.set_active_item(None)
        self._main_window._container_manager.update_container_watchers()
        self._main_window._suppress_history = False

    def toggle_repository_sidebar(self, visible: bool) -> None:
        container = getattr(self._main_window, "_repo_container", None)
        if container is None:
            return
        container.setVisible(bool(visible))

    def toggle_tag_panel(self, visible: bool) -> None:
        panel = getattr(self._main_window, "_tag_panel", None)
        if panel is None:
            return
        panel.setVisible(bool(visible))

    def clear_library_search(self) -> None:
        self._main_window._tag_filter = None
        self._main_window._tag_filter_container_ids.clear()
        self._main_window._tag_filter_order_ids.clear()
        self._main_window._tag_filter_container_paths.clear()
        self._main_window._tag_filter_focus_map.clear()
        if hasattr(self._main_window, "_repo_search_input"):
            self._main_window._repo_search_input.blockSignals(True)
            self._main_window._repo_search_input.clear()
            self._main_window._repo_search_input.blockSignals(False)
        self._main_window._library_manager.apply_library_filters()

    def handle_tag_filter_request(self, tag: str) -> None:
        normalized = tag.strip()
        self._main_window._tag_filter = normalized or None
        self._main_window._tag_filter_container_ids.clear()
        self._main_window._tag_filter_order_ids.clear()
        self._main_window._tag_filter_container_paths.clear()
        self._main_window._tag_filter_focus_map.clear()
        if normalized:
            try:
                tagged_paths = sorted(self._main_window._asset_service.paths_for_tag(normalized))
            except Exception:
                tagged_paths = []
            for raw_path in tagged_paths:
                asset = self._main_window._asset_service.get_asset_by_path(raw_path)
                container_path = None
                if asset is not None and isinstance(asset.metadata, dict):
                    metadata = asset.metadata
                    container_candidate = metadata.get("container_path")
                    if isinstance(container_candidate, str) and container_candidate.strip():
                        container_path = container_candidate.strip()
                if container_path is None:
                    try:
                        container_path = str(Path(raw_path).expanduser().resolve().parent)
                    except Exception:
                        container_path = None
                if not container_path:
                    continue
                container_asset = self._main_window._asset_service.get_asset_by_path(container_path)
                if container_asset is None:
                    continue
                try:
                    container_id = int(container_asset.id)
                except Exception:
                    continue
                self._main_window._tag_filter_container_ids.add(container_id)
                if container_id not in self._main_window._tag_filter_order_ids:
                    self._main_window._tag_filter_order_ids.append(container_id)
                self._main_window._tag_filter_container_paths[container_id] = container_asset.path

                focus_list = self._main_window._tag_filter_focus_map.setdefault(container_id, [])
                if raw_path not in focus_list:
                    focus_list.append(raw_path)
        if hasattr(self._main_window, "_repo_search_input"):
            self._main_window._repo_search_input.blockSignals(True)
            if normalized:
                self._main_window._repo_search_input.setText(f"#{normalized}")
            else:
                if self._main_window._repo_search_input.text().startswith("#"):
                    self._main_window._repo_search_input.clear()
            self._main_window._repo_search_input.blockSignals(False)
        self._main_window._library_manager.apply_library_filters()

    def find_repository_item_by_id(self, target_id: int) -> QListWidgetItem | None:
        for row in range(self._main_window._repository_list.count()):
            item = self._main_window._repository_list.item(row)
            try:
                candidate_id = int(item.data(Qt.UserRole))
            except (TypeError, ValueError):
                continue
            if candidate_id == target_id:
                return item
        return None

    def focus_tag_filter_target(self) -> None:
        if not self._main_window._tag_filter_focus_map:
            return
        for container_id in self._main_window._tag_filter_order_ids:
            item = self.find_repository_item_by_id(container_id)
            if item is None or item.isHidden():
                continue
            self._main_window._repository_list.setCurrentItem(item)
            container_path = self._main_window._tag_filter_container_paths.get(container_id)
            if not container_path:
                continue
            focus_list = self._main_window._tag_filter_focus_map.setdefault(container_id, [])
            focus_component = focus_list[0] if focus_list else None
            try:
                folder = Path(container_path)
            except Exception:
                continue
            try:
                resolved_folder = folder.expanduser().resolve()
            except Exception:
                resolved_folder = folder.expanduser()
            if not resolved_folder.exists() or not resolved_folder.is_dir():
                continue
            self._main_window._container_manager.create_or_update_container(
                resolved_folder,
                select_in_repo=True,
                show_container=True,
                focus_component=focus_component,
            )
            break

    def select_repository_path(self, path: str) -> None:
        for row in range(self._main_window._repository_list.count()):
            item = self._main_window._repository_list.item(row)
            raw_path = item.data(Qt.UserRole + 1) or item.text()
            item_path = str(raw_path) if raw_path is not None else ""
            if item_path == path:
                self._main_window._repository_list.setCurrentItem(item)
                break

    def open_item_folder(self, item_path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder = Path(item_path).expanduser().parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def navigate_to_path(self, target: str) -> None:
        try:
            folder = Path(target).expanduser().resolve()
        except Exception:
            return
        try:
            folder.relative_to(get_config().library_root)
        except Exception:
            return
        self._main_window._container_manager.create_or_update_container(
            folder,
            select_in_repo=True,
            show_container=True,
        )
        for row in range(self._main_window._repository_list.count()):
            item = self._main_window._repository_list.item(row)
            raw_path = item.data(Qt.UserRole + 1) or item.text()
            item_path = str(raw_path) if raw_path is not None else ""
            if item_path == str(folder):
                self._main_window._repository_list.setCurrentItem(item)
                break

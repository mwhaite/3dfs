"""Persistence helpers for user-configurable application settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QSettings

from ..utils.paths import coerce_optional_path

__all__ = [
    "APPLICATION_NAME",
    "AppSettings",
    "ORGANIZATION_NAME",
    "load_app_settings",
    "save_app_settings",
]


ORGANIZATION_NAME: Final[str] = "Open3DFS"
"""Organization identifier used when storing Qt settings."""

APPLICATION_NAME: Final[str] = "3dfs"
"""Application identifier used when storing Qt settings."""


@dataclass(slots=True)
class AppSettings:
    """Collection of end-user preferences for the desktop shell."""

    library_root: Path
    show_repository_sidebar: bool = True
    auto_refresh_containers: bool = True
    bootstrap_demo_data: bool = False
    text_preview_limit: int = 200_000


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _settings_storage() -> QSettings:
    return QSettings(ORGANIZATION_NAME, APPLICATION_NAME)


def load_app_settings(*, fallback_root: Path) -> AppSettings:
    """Return persisted settings falling back to *fallback_root* when needed."""

    store = _settings_storage()

    raw_library = store.value("general/libraryRoot")
    library_root = coerce_optional_path(raw_library) or fallback_root

    show_repo = _coerce_bool(store.value("interface/showRepositorySidebar"), True)
    auto_refresh = _coerce_bool(store.value("containers/autoRefresh"), True)
    bootstrap_demo = _coerce_bool(store.value("general/bootstrapDemoData"), False)

    text_limit_value = store.value("preview/textLimit")
    if isinstance(text_limit_value, int):
        text_limit = max(10_240, text_limit_value)
    elif isinstance(text_limit_value, str):
        stripped = text_limit_value.strip()
        text_limit = int(stripped) if stripped.isdigit() else 200_000
        text_limit = max(10_240, text_limit)
    else:
        text_limit = 200_000

    return AppSettings(
        library_root=library_root,
        show_repository_sidebar=show_repo,
        auto_refresh_containers=auto_refresh,
        bootstrap_demo_data=bootstrap_demo,
        text_preview_limit=text_limit,
    )


def save_app_settings(settings: AppSettings) -> None:
    """Persist *settings* using Qt's :class:`~PySide6.QtCore.QSettings`."""

    store = _settings_storage()

    store.beginGroup("general")
    store.setValue("libraryRoot", str(settings.library_root))
    store.setValue("bootstrapDemoData", settings.bootstrap_demo_data)
    store.endGroup()

    store.beginGroup("interface")
    store.setValue("showRepositorySidebar", settings.show_repository_sidebar)
    store.endGroup()

    store.beginGroup("containers")
    store.setValue("autoRefresh", settings.auto_refresh_containers)
    store.endGroup()

    store.beginGroup("preview")
    store.setValue("textLimit", int(settings.text_preview_limit))
    store.endGroup()

    store.sync()

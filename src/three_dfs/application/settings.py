"""Persistence helpers for user-configurable application settings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from PySide6.QtCore import QSettings

from ..utils.paths import coerce_optional_path

__all__ = [
    "APPLICATION_NAME",
    "AppSettings",
    "DEFAULT_THEME_COLORS",
    "DEFAULT_THEME_NAME",
    "ORGANIZATION_NAME",
    "ThemeColors",
    "load_app_settings",
    "save_app_settings",
]


ORGANIZATION_NAME: Final[str] = "Open3DFS"
"""Organization identifier used when storing Qt settings."""

APPLICATION_NAME: Final[str] = "3dfs"
"""Application identifier used when storing Qt settings."""

ThemeColors = dict[str, str]
"""Mapping of color role names ("window", "panel", "accent", "text") to hex codes."""

DEFAULT_THEME_NAME: Final[str] = "Default"
"""Human-friendly label for the stock application theme."""

DEFAULT_THEME_COLORS: Final[ThemeColors] = {
    "window": "#202124",
    "panel": "#2b2c30",
    "accent": "#5c9cff",
    "text": "#f0f0f0",
}
"""Baseline palette used for the default application appearance."""


@dataclass(slots=True)
class AppSettings:
    """Collection of end-user preferences for the desktop shell."""

    library_root: Path
    show_repository_sidebar: bool = True
    auto_refresh_containers: bool = True
    bootstrap_demo_data: bool = False
    text_preview_limit: int = 200_000
    theme_name: str = DEFAULT_THEME_NAME
    theme_colors: ThemeColors = field(default_factory=lambda: DEFAULT_THEME_COLORS.copy())
    custom_themes: dict[str, ThemeColors] = field(default_factory=dict)

    def resolved_theme_colors(self) -> ThemeColors:
        """Return the theme colors that should be applied to the UI."""

        available_themes = {DEFAULT_THEME_NAME: DEFAULT_THEME_COLORS}
        available_themes.update(self.custom_themes)
        selected = available_themes.get(self.theme_name, DEFAULT_THEME_COLORS)
        merged = DEFAULT_THEME_COLORS | _coerce_color_map(selected, DEFAULT_THEME_COLORS)
        return _coerce_color_map(self.theme_colors or merged, merged)


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


def _coerce_color(value: object, fallback: str) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if len(candidate) == 7 and candidate.startswith("#"):
            try:
                int(candidate[1:], 16)
                return candidate.lower()
            except ValueError:
                pass
    return fallback


def _coerce_color_map(value: Mapping[str, object], fallback: ThemeColors) -> ThemeColors:
    colors = DEFAULT_THEME_COLORS.copy() if fallback is DEFAULT_THEME_COLORS else dict(fallback)
    for key in ("window", "panel", "accent", "text"):
        raw = value.get(key) if isinstance(value, Mapping) else None
        colors[key] = _coerce_color(raw, colors[key])
    return colors


def _decode_theme_store(value: object) -> dict[str, ThemeColors]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            result: dict[str, ThemeColors] = {}
            for name, palette in parsed.items():
                if isinstance(name, str) and isinstance(palette, Mapping):
                    result[name] = _coerce_color_map(palette, DEFAULT_THEME_COLORS)
            return result
    return {}


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

    theme_name_raw = store.value("appearance/themeName")
    theme_name = theme_name_raw.strip() if isinstance(theme_name_raw, str) else DEFAULT_THEME_NAME

    theme_colors_raw = store.value("appearance/themeColors")
    if isinstance(theme_colors_raw, str):
        try:
            parsed_theme = json.loads(theme_colors_raw)
        except json.JSONDecodeError:
            parsed_theme = DEFAULT_THEME_COLORS
        theme_colors = _coerce_color_map(
            parsed_theme if isinstance(parsed_theme, Mapping) else {},
            DEFAULT_THEME_COLORS,
        )
    else:
        theme_colors = DEFAULT_THEME_COLORS.copy()

    custom_themes = _decode_theme_store(store.value("appearance/customThemes"))
    if theme_name not in custom_themes and theme_name != DEFAULT_THEME_NAME:
        theme_name = DEFAULT_THEME_NAME

    base_theme = custom_themes.get(theme_name, DEFAULT_THEME_COLORS)
    theme_colors = _coerce_color_map(theme_colors, base_theme)

    return AppSettings(
        library_root=library_root,
        show_repository_sidebar=show_repo,
        auto_refresh_containers=auto_refresh,
        bootstrap_demo_data=bootstrap_demo,
        text_preview_limit=text_limit,
        theme_name=theme_name,
        theme_colors=theme_colors,
        custom_themes=custom_themes,
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

    store.beginGroup("appearance")
    store.setValue("themeName", settings.theme_name)
    store.setValue("themeColors", json.dumps(settings.theme_colors))
    store.setValue("customThemes", json.dumps(settings.custom_themes))
    store.endGroup()

    store.sync()

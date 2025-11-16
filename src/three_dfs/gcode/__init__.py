"""Utilities for parsing and rendering G-code toolpaths."""

from .preview import (
    DEFAULT_GCODE_PREVIEW_ROOT,
    DEFAULT_GCODE_PREVIEW_SIZE,
    GCodeAnalysis,
    GCodePreviewCache,
    GCodePreviewError,
    GCodePreviewRenderer,
    GCodePreviewResult,
    GCodeSegment,
    analyze_gcode_program,
    extract_render_hints,
)

__all__ = [
    "DEFAULT_GCODE_PREVIEW_ROOT",
    "DEFAULT_GCODE_PREVIEW_SIZE",
    "GCodeAnalysis",
    "GCodePreviewCache",
    "GCodePreviewError",
    "GCodePreviewRenderer",
    "GCodePreviewResult",
    "GCodeSegment",
    "analyze_gcode_program",
    "extract_render_hints",
]

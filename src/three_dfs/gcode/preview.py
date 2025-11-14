from __future__ import annotations

import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image, ImageDraw, ImageFont

Color = tuple[int, int, int, int]

DEFAULT_GCODE_PREVIEW_SIZE: tuple[int, int] = (768, 512)
"""Default pixel dimensions for generated G-code previews."""

DEFAULT_GCODE_PREVIEW_ROOT: Path = Path.home() / ".3dfs" / "gcode_previews"
"""Filesystem location where cached G-code previews are stored."""

_GCODE_WORD_PATTERN = re.compile(r"([A-Za-z])([-+]?\d+(?:\.\d+)?)")
_PAREN_COMMENT_PATTERN = re.compile(r"\([^)]*\)")


class GCodePreviewError(RuntimeError):
    """Raised when a G-code preview cannot be produced."""


@dataclass(slots=True)
class GCodeSegment:
    """Describe a motion segment extracted from a G-code program."""

    start: tuple[float, float, float]
    end: tuple[float, float, float]
    mode: str  # ``"rapid"`` or ``"cut"``
    feed: float | None


@dataclass(slots=True)
class GCodeAnalysis:
    """Summary describing the parsed contents of a G-code program."""

    segments: list[GCodeSegment]
    command_count: int
    rapid_moves: int
    cutting_moves: int
    travel_distance: float
    cutting_distance: float
    bounds_xy: tuple[float, float, float, float]
    bounds_z: tuple[float, float]
    feed_rates: tuple[float, ...]
    units: str

    @property
    def total_moves(self) -> int:
        return self.rapid_moves + self.cutting_moves

    @property
    def has_motion(self) -> bool:
        return bool(self.segments)


@dataclass(slots=True)
class GCodePreviewResult:
    """Describe the outcome of generating a cached G-code preview."""

    path: Path
    info: dict[str, object]
    image_bytes: bytes
    updated: bool


class GCodePreviewRenderer:
    """Render G-code toolpaths into 2D preview images."""

    def __init__(
        self,
        *,
        background: Color = (12, 16, 22, 255),
        travel_color: Color = (100, 120, 160, 255),
        cut_color: Color = (240, 120, 80, 255),
        workpiece_color: Color = (60, 70, 80, 120),
        axis_color: Color = (200, 200, 210, 160),
    ) -> None:
        self._background = background
        self._travel_color = travel_color
        self._cut_color = cut_color
        self._workpiece_color = workpiece_color
        self._axis_color = axis_color

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def render(
        self,
        analysis: GCodeAnalysis,
        *,
        hints: Mapping[str, str] | None = None,
        size: tuple[int, int] = DEFAULT_GCODE_PREVIEW_SIZE,
    ) -> Image.Image:
        """Return a Pillow image representing *analysis* of a toolpath."""

        if not analysis.segments:
            raise GCodePreviewError("No motion commands were detected in this program.")

        hint_map = {str(key).lower(): str(value) for key, value in (hints or {}).items()}
        background = _resolve_color(hint_map.get("background"), self._background)
        travel_color = _resolve_color(hint_map.get("travel_color"), self._travel_color)
        cut_color = _resolve_color(hint_map.get("cut_color"), self._cut_color)
        axis_color = _resolve_color(hint_map.get("axis_color"), self._axis_color)
        workpiece_color = _resolve_color(hint_map.get("workpiece_color"), self._workpiece_color)
        line_width_hint = hint_map.get("line_width")
        try:
            line_width = max(1, int(float(line_width_hint))) if line_width_hint else 0
        except ValueError:
            line_width = 0

        width, height = size
        width = max(1, int(width))
        height = max(1, int(height))
        image = Image.new("RGBA", (width, height), background)
        draw = ImageDraw.Draw(image, "RGBA")

        min_x, min_y, max_x, max_y = analysis.bounds_xy
        min_z, max_z = analysis.bounds_z
        workpiece = _parse_workpiece_hint(hint_map.get("workpiece"))

        if workpiece is not None:
            wx, wy = workpiece
            min_x = min(min_x, -wx / 2.0)
            max_x = max(max_x, wx / 2.0)
            min_y = min(min_y, -wy / 2.0)
            max_y = max(max_y, wy / 2.0)

        if math.isclose(max_x, min_x):
            max_x += 1.0
            min_x -= 1.0
        if math.isclose(max_y, min_y):
            max_y += 1.0
            min_y -= 1.0

        padding = max(24, min(width, height) // 12)
        available_width = max(1.0, width - padding * 2)
        available_height = max(1.0, height - padding * 2)
        span_x = max_x - min_x
        span_y = max_y - min_y
        scale = available_width / span_x
        if span_y * scale > available_height:
            scale = available_height / span_y
        scale = max(scale, 1e-6)

        def project(point: tuple[float, float, float]) -> tuple[float, float]:
            x, y, _ = point
            px = padding + (x - min_x) * scale
            py = height - (padding + (y - min_y) * scale)
            return px, py

        # Draw workpiece outline when provided.
        if workpiece is not None:
            wx, wy = workpiece
            outline = [
                project((-wx / 2.0, -wy / 2.0, 0.0)),
                project((wx / 2.0, -wy / 2.0, 0.0)),
                project((wx / 2.0, wy / 2.0, 0.0)),
                project((-wx / 2.0, wy / 2.0, 0.0)),
                project((-wx / 2.0, -wy / 2.0, 0.0)),
            ]
            draw.line(outline, fill=workpiece_color, width=max(1, int(scale * 0.75)))

        # Draw axes for reference.
        axis_width = max(1, int(scale * 0.75))
        if min_x <= 0 <= max_x:
            draw.line([project((0.0, min_y, 0.0)), project((0.0, max_y, 0.0))], fill=axis_color, width=axis_width)
        if min_y <= 0 <= max_y:
            draw.line([project((min_x, 0.0, 0.0)), project((max_x, 0.0, 0.0))], fill=axis_color, width=axis_width)

        computed_line_width = max(1, int(scale * 1.25))
        if line_width:
            computed_line_width = line_width

        # Draw toolpath segments.
        for segment in analysis.segments:
            start = project(segment.start)
            end = project(segment.end)
            color = cut_color if segment.mode == "cut" else travel_color
            draw.line([start, end], fill=color, width=computed_line_width)

        # Mark the program origin and starting point.
        origin = project((0.0, 0.0, 0.0))
        start_point = project(analysis.segments[0].start)
        radius = max(2, computed_line_width + 1)
        draw.ellipse(
            [origin[0] - radius, origin[1] - radius, origin[0] + radius, origin[1] + radius],
            outline=axis_color,
            width=max(1, computed_line_width // 2),
        )
        draw.ellipse(
            [
                start_point[0] - radius,
                start_point[1] - radius,
                start_point[0] + radius,
                start_point[1] + radius,
            ],
            fill=cut_color,
        )

        font = ImageFont.load_default()
        lines: list[str] = []
        lines.append(
            f"Moves: {analysis.total_moves} (cut {analysis.cutting_moves}, rapid {analysis.rapid_moves})"
        )
        lines.append(
            f"Travel: {analysis.travel_distance:.1f} {analysis.units}  |  Cut: {analysis.cutting_distance:.1f} {analysis.units}"
        )
        lines.append(f"Z range: {min_z:.2f} to {max_z:.2f} {analysis.units}")

        tool_hint = hint_map.get("tool")
        if tool_hint:
            lines.append(f"Tool: {tool_hint}")
        material_hint = hint_map.get("material")
        if material_hint:
            lines.append(f"Material: {material_hint}")

        text_color = (240, 240, 240, 255) if background[0] < 200 else (20, 20, 20, 255)
        x = padding
        y = padding
        for line in lines:
            draw.text((x, y), line, fill=text_color, font=font)
            y += _measure_text_height(font, line) + 2

        return image


class GCodePreviewCache:
    """Persist rendered G-code previews to disk and reuse them when possible."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        renderer: GCodePreviewRenderer | None = None,
    ) -> None:
        self._root = Path(root or DEFAULT_GCODE_PREVIEW_ROOT).expanduser()
        self._renderer = renderer or GCodePreviewRenderer()

    def get_or_render(
        self,
        source_path: Path,
        *,
        hints: Mapping[str, str] | None = None,
        existing_info: Mapping[str, object] | None = None,
        size: tuple[int, int] = DEFAULT_GCODE_PREVIEW_SIZE,
        analysis: GCodeAnalysis | None = None,
    ) -> GCodePreviewResult:
        """Return the preview for *source_path* generating it when needed."""

        if not source_path.exists():
            raise GCodePreviewError(f"{source_path!s} does not exist")

        signature = self._hash_source(source_path)
        hints = dict(hints or {})
        hint_hash = self._hash_hints(hints)
        cache_path = self._cache_path(signature, hint_hash, size)

        if cache_path.exists():
            image_bytes = cache_path.read_bytes()
            if self._info_matches(existing_info, cache_path, signature, hint_hash, hints, size):
                info = dict(existing_info)  # type: ignore[arg-type]
                return GCodePreviewResult(cache_path, info, image_bytes, updated=False)

            info = self._build_info(cache_path, signature, hint_hash, hints, size, analysis)
            return GCodePreviewResult(cache_path, info, image_bytes, updated=True)

        program = analysis or analyze_gcode_program(source_path)
        image = self._renderer.render(program, hints=hints, size=size)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = buffer.getvalue()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
        info = self._build_info(cache_path, signature, hint_hash, hints, size, program)

        return GCodePreviewResult(cache_path, info, payload, updated=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _cache_path(self, signature: str, hint_hash: str, size: tuple[int, int]) -> Path:
        width, height = int(size[0]), int(size[1])
        filename = f"{signature}_{hint_hash}_{width}x{height}.png"
        return self._root / filename

    def _hash_source(self, source_path: Path) -> str:
        digest = hashlib.blake2s(digest_size=16)

        try:
            with source_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError as exc:  # pragma: no cover - surface descriptive error
            raise GCodePreviewError(f"Unable to read {source_path!s}: {exc}") from exc

        return digest.hexdigest()

    def _hash_hints(self, hints: Mapping[str, str]) -> str:
        if not hints:
            return "nohint"
        encoded = json.dumps({str(k): str(v) for k, v in sorted(hints.items())}, sort_keys=True)
        return hashlib.blake2s(encoded.encode("utf-8"), digest_size=8).hexdigest()

    def _info_matches(
        self,
        info: Mapping[str, object] | None,
        cache_path: Path,
        signature: str,
        hint_hash: str,
        hints: Mapping[str, str],
        size: tuple[int, int],
    ) -> bool:
        if info is None:
            return False

        try:
            stored_hash = info.get("source_hash")  # type: ignore[assignment]
            stored_hint_hash = info.get("hint_hash")  # type: ignore[assignment]
            stored_size = info.get("size")  # type: ignore[assignment]
            stored_path = info.get("path")  # type: ignore[assignment]
            stored_hints = info.get("hints")  # type: ignore[assignment]
        except AttributeError:
            return False

        if stored_hash != signature or stored_hint_hash != hint_hash:
            return False

        try:
            size_values = list(stored_size or [])
            if len(size_values) != 2:
                return False
            width = int(size_values[0])
            height = int(size_values[1])
        except (TypeError, ValueError):
            return False

        if [width, height] != [int(size[0]), int(size[1])]:
            return False

        if not stored_path:
            return False

        try:
            resolved = Path(str(stored_path)).expanduser().resolve(strict=False)
        except Exception:
            return False

        if resolved != cache_path.resolve(strict=False):
            return False

        normalized_hints = {str(k): str(v) for k, v in (stored_hints or {}).items()}
        if normalized_hints != {str(k): str(v) for k, v in hints.items()}:
            return False

        return True

    def _build_info(
        self,
        cache_path: Path,
        signature: str,
        hint_hash: str,
        hints: Mapping[str, str],
        size: tuple[int, int],
        analysis: GCodeAnalysis | None,
    ) -> dict[str, object]:
        info: dict[str, object] = {
            "path": cache_path.as_posix(),
            "source_hash": signature,
            "hint_hash": hint_hash,
            "hints": {str(k): str(v) for k, v in hints.items()},
            "size": [int(size[0]), int(size[1])],
            "generated_at": datetime.now(UTC).isoformat(),
        }

        if analysis is not None:
            info["analysis"] = {
                "command_count": int(analysis.command_count),
                "rapid_moves": int(analysis.rapid_moves),
                "cutting_moves": int(analysis.cutting_moves),
                "travel_distance": float(analysis.travel_distance),
                "cutting_distance": float(analysis.cutting_distance),
                "bounds_xy": [float(value) for value in analysis.bounds_xy],
                "bounds_z": [float(value) for value in analysis.bounds_z],
                "feed_rates": [float(rate) for rate in analysis.feed_rates],
                "units": analysis.units,
            }

        return info


def analyze_gcode_program(path: Path) -> GCodeAnalysis:
    """Parse *path* and return a :class:`GCodeAnalysis` summary."""

    segments: list[GCodeSegment] = []
    command_count = 0
    rapid_moves = 0
    cutting_moves = 0
    travel_distance = 0.0
    cutting_distance = 0.0
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    feed_rates: set[float] = set()
    units = "mm"
    absolute_mode = True
    current = [0.0, 0.0, 0.0]
    last_feed: float | None = None

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        raise GCodePreviewError(f"Unable to read {path!s}: {exc}") from exc

    for raw_line in lines:
        stripped = _strip_gcode_line(raw_line)
        if not stripped:
            continue

        words = dict((letter.upper(), float(value)) for letter, value in _GCODE_WORD_PATTERN.findall(stripped))
        if not words:
            continue

        command_value = words.get("G")
        command = int(command_value) if command_value is not None else None

        if command in {20}:
            units = "inch"
            continue
        if command in {21}:
            units = "mm"
            continue
        if command in {90}:
            absolute_mode = True
            continue
        if command in {91}:
            absolute_mode = False
            continue

        if "F" in words:
            last_feed = words["F"]
            if math.isfinite(last_feed):
                feed_rates.add(last_feed)

        if command not in {0, 1, 2, 3}:
            continue

        target = list(current)
        for axis_index, axis in enumerate("XYZ"):
            if axis not in words:
                continue
            value = words[axis]
            if not math.isfinite(value):
                continue
            if absolute_mode:
                target[axis_index] = value
            else:
                target[axis_index] += value

        start = tuple(current)
        end = tuple(target)
        if start == end:
            continue

        distance = math.dist(start, end)
        if math.isfinite(distance):
            if command == 0:
                travel_distance += distance
                rapid_moves += 1
                mode = "rapid"
            else:
                cutting_distance += distance
                cutting_moves += 1
                mode = "cut"
        else:
            mode = "cut"

        segments.append(GCodeSegment(start, end, mode, last_feed))
        command_count += 1
        current = target

        min_x = min(min_x, start[0], end[0])
        min_y = min(min_y, start[1], end[1])
        min_z = min(min_z, start[2], end[2])
        max_x = max(max_x, start[0], end[0])
        max_y = max(max_y, start[1], end[1])
        max_z = max(max_z, start[2], end[2])

    if not segments:
        raise GCodePreviewError("No motion commands detected in program.")

    bounds_xy = (
        min_x if math.isfinite(min_x) else 0.0,
        min_y if math.isfinite(min_y) else 0.0,
        max_x if math.isfinite(max_x) else 0.0,
        max_y if math.isfinite(max_y) else 0.0,
    )
    bounds_z = (
        min_z if math.isfinite(min_z) else 0.0,
        max_z if math.isfinite(max_z) else 0.0,
    )

    sorted_feeds = tuple(sorted(rate for rate in feed_rates if math.isfinite(rate)))

    return GCodeAnalysis(
        segments=segments,
        command_count=command_count,
        rapid_moves=rapid_moves,
        cutting_moves=cutting_moves,
        travel_distance=travel_distance,
        cutting_distance=cutting_distance,
        bounds_xy=bounds_xy,
        bounds_z=bounds_z,
        feed_rates=sorted_feeds,
        units=units,
    )


def extract_render_hints(tags: Iterable[str]) -> dict[str, str]:
    """Return a mapping of rendering hints extracted from *tags*."""

    hints: dict[str, str] = {}
    for tag in tags:
        if not isinstance(tag, str):
            continue
        raw = tag.strip()
        if not raw:
            continue
        lower = raw.casefold()
        if not lower.startswith("gcodehint:"):
            continue
        body = raw.split(":", 1)[1]
        key: str
        value: str
        if "=" in body:
            key, value = body.split("=", 1)
        elif ":" in body:
            key, value = body.split(":", 1)
        else:
            key, value = body, "true"
        normalized_key = key.strip().lower().replace(" ", "_")
        if not normalized_key:
            continue
        hints[normalized_key] = value.strip()
    return hints


def _strip_gcode_line(line: str) -> str:
    line = _PAREN_COMMENT_PATTERN.sub("", line)
    if ";" in line:
        line = line.split(";", 1)[0]
    return line.strip()


def _resolve_color(value: str | None, default: Color) -> Color:
    if not value:
        return default
    text = value.strip()
    if not text:
        return default
    if text.startswith("#"):
        hex_value = text[1:]
        if len(hex_value) in {6, 8}:
            try:
                r = int(hex_value[0:2], 16)
                g = int(hex_value[2:4], 16)
                b = int(hex_value[4:6], 16)
                a = int(hex_value[6:8], 16) if len(hex_value) == 8 else default[3]
                return (r, g, b, a)
            except ValueError:
                return default
    separators = [",", " "]
    for separator in separators:
        if separator in text:
            parts = [part for part in text.replace(" ", separator).split(separator) if part]
            if len(parts) in {3, 4}:
                try:
                    values = [float(part) for part in parts]
                except ValueError:
                    return default
                if all(0.0 <= component <= 1.0 for component in values):
                    scaled = [int(component * 255) for component in values]
                else:
                    scaled = [int(component) for component in values]
                while len(scaled) < 4:
                    scaled.append(default[len(scaled)])
                return tuple(max(0, min(255, component)) for component in scaled)  # type: ignore[return-value]
    NAMED = {
        "white": (255, 255, 255, 255),
        "black": (0, 0, 0, 255),
        "red": (220, 60, 60, 255),
        "green": (80, 200, 120, 255),
        "blue": (80, 140, 220, 255),
        "orange": (230, 140, 60, 255),
        "purple": (160, 100, 200, 255),
        "gray": (160, 160, 160, 255),
    }
    key = text.lower()
    return NAMED.get(key, default)


def _parse_workpiece_hint(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    text = value.strip().lower().replace("mm", "")
    if not text:
        return None
    separators = ["x", "×", "*"]
    for separator in separators:
        if separator in text:
            parts = [part for part in text.split(separator) if part]
            if len(parts) >= 2:
                try:
                    width = float(parts[0])
                    height = float(parts[1])
                except ValueError:
                    return None
                return abs(width), abs(height)
    try:
        value_float = float(text)
    except ValueError:
        return None
    return abs(value_float), abs(value_float)


def _measure_text_height(font: ImageFont.ImageFont, text: str) -> int:
    try:
        bbox = font.getbbox(text)
    except AttributeError:  # pragma: no cover - Pillow < 8 fallback
        _, height = font.getsize(text)
        return height
    return bbox[3] - bbox[1]

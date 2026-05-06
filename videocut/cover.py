from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageColor, ImageDraw, ImageFont

CoverPosition = Literal["top", "center", "bottom"]

_FONT_CANDIDATES = [
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/STHeiti Light.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
]


def compose_cover_with_title(
    source_path: Path,
    output_path: Path,
    title: str,
    target_size: tuple[int, int] | None = None,
    *,
    font_path: Path | None = None,
    font_size: int | None = None,
    position: CoverPosition = "top",
    offset_y: int = 0,
    box_color: str = "#000000",
    box_alpha: int = 176,
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int | None = None,
) -> Path:
    resolved_source = source_path.expanduser().resolve()
    resolved_output = output_path.expanduser().resolve()
    if not resolved_source.exists():
        raise FileNotFoundError(f"Cover source image not found: {resolved_source}")
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("Cover title cannot be empty.")
    if target_size is not None and (target_size[0] <= 0 or target_size[1] <= 0):
        raise ValueError("target_size must contain positive width and height values.")

    with Image.open(resolved_source) as original:
        source_rgb = original.convert("RGB")
        if target_size is None:
            composed = source_rgb.copy()
        else:
            composed = _resize_to_fill(source_rgb, target_size)

    canvas = composed.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    resolved_font_size = max(18, font_size or int(round(canvas.height * 0.085)))
    font = _load_font(font_path, resolved_font_size)
    resolved_stroke_width = max(1, stroke_width or max(2, resolved_font_size // 14))
    spacing = max(8, resolved_font_size // 4)
    max_text_width = max(240, int(canvas.width * 0.82))
    wrapped_title = _wrap_title(normalized_title, draw, font, max_text_width)

    text_bbox = draw.multiline_textbbox(
        (0, 0),
        wrapped_title,
        font=font,
        align="center",
        spacing=spacing,
        stroke_width=resolved_stroke_width,
    )
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    padding_x = max(24, resolved_font_size // 2)
    padding_y = max(16, resolved_font_size // 3)

    x = (canvas.width - text_width) / 2
    y = _resolve_anchor_y(position, canvas.height, text_height, resolved_font_size) + offset_y
    min_y = int(canvas.height * 0.02)
    max_y = max(min_y, canvas.height - text_height - int(canvas.height * 0.02))
    y = float(min(max_y, max(min_y, int(round(y)))))

    box = (
        int(max(0, x - padding_x)),
        int(max(0, y - padding_y)),
        int(min(canvas.width, x + text_width + padding_x)),
        int(min(canvas.height, y + text_height + padding_y)),
    )
    box_rgb = _parse_color(box_color)
    text_rgb = _parse_color(text_color)
    stroke_rgb = _parse_color(stroke_color)
    box_opacity = max(0, min(255, box_alpha))

    draw.rounded_rectangle(
        box,
        radius=max(12, resolved_font_size // 3),
        fill=(box_rgb[0], box_rgb[1], box_rgb[2], box_opacity),
    )
    draw.multiline_text(
        (x, y),
        wrapped_title,
        font=font,
        fill=(text_rgb[0], text_rgb[1], text_rgb[2], 255),
        align="center",
        spacing=spacing,
        stroke_width=resolved_stroke_width,
        stroke_fill=(stroke_rgb[0], stroke_rgb[1], stroke_rgb[2], 255),
    )

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    _save_image(canvas, resolved_output)
    return resolved_output


def _parse_color(value: str) -> tuple[int, int, int]:
    try:
        color = ImageColor.getrgb(value.strip())
    except ValueError as error:
        raise ValueError(f"Invalid color value: {value!r}") from error
    if len(color) == 4:
        return color[0], color[1], color[2]
    return color


def _load_font(font_path: Path | None, font_size: int) -> ImageFont.FreeTypeFont:
    if font_path is not None:
        resolved_path = font_path.expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Font file not found: {resolved_path}")
        return ImageFont.truetype(str(resolved_path), size=font_size)

    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=font_size)
    raise RuntimeError(
        "No usable font file found for rendering title text. "
        "Pass --font-path to specify a font manually."
    )


def _resolve_anchor_y(position: CoverPosition, height: int, text_height: int, font_size: int) -> int:
    if position == "top":
        return max(20, int(height * 0.1))
    if position == "center":
        return (height - text_height) // 2
    return height - text_height - max(20, int(height * 0.1), font_size)


def _wrap_title(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    lines: list[str] = []
    for block in text.splitlines():
        normalized = block.strip()
        if not normalized:
            continue
        lines.extend(_wrap_line(normalized, draw, font, max_width))
    return "\n".join(lines) if lines else text


def _wrap_line(
    line: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    if _text_width(draw, line, font) <= max_width:
        return [line]

    if " " in line and not _contains_cjk(line):
        wrapped = _wrap_by_words(line, draw, font, max_width)
        if wrapped:
            return wrapped

    wrapped_lines: list[str] = []
    current = ""
    for char in line:
        candidate = f"{current}{char}"
        if current and _text_width(draw, candidate, font) > max_width:
            wrapped_lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        wrapped_lines.append(current)
    return wrapped_lines


def _wrap_by_words(
    line: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = [word for word in line.split(" ") if word]
    if not words:
        return []

    wrapped_lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            wrapped_lines.append(current)
            current = word
            continue
        fragments = _wrap_line(word, draw, font, max_width)
        wrapped_lines.extend(fragments[:-1])
        current = fragments[-1]
    if current:
        wrapped_lines.append(current)
    return wrapped_lines


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _resize_to_fill(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_width, target_height = target_size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize(
        (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_width) // 2)
    top = max(0, (resized.height - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def _save_image(canvas: Image.Image, output_path: Path) -> None:
    suffix = output_path.suffix.lower()
    if suffix == ".png":
        canvas.save(output_path, format="PNG", optimize=True)
        return
    if suffix == ".webp":
        canvas.convert("RGB").save(output_path, format="WEBP", quality=95, method=6)
        return
    canvas.convert("RGB").save(output_path, format="JPEG", quality=95, optimize=True)

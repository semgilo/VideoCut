from __future__ import annotations

import html
import re
from pathlib import Path

from videocut.models import Segment


TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
TAG_RE = re.compile(r"</?[^>]+>")
INLINE_TIMESTAMP_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")


def load_segments_from_vtt(path: Path) -> list[Segment]:
    raw_cues = _parse_vtt_cues(path)
    collapsed = _collapse_cues(raw_cues)
    progressive = _strip_progressive_overlap(collapsed)
    merged = _merge_short_cues(progressive)
    return [
        Segment(index=index, start=start, end=end, english=text)
        for index, (start, end, text) in enumerate(merged, start=1)
    ]


def overlay_chinese_from_vtt(segments: list[Segment], path: Path) -> None:
    chinese_segments = load_segments_from_vtt(path)
    if not chinese_segments:
        raise RuntimeError(f"No usable subtitle segments found in {path}")

    for segment in segments:
        translated_text = _find_best_overlap_text(segment.start, segment.end, chinese_segments)
        if translated_text:
            segment.chinese = translated_text

    missing = [segment.index for segment in segments if not segment.chinese]
    if missing:
        raise RuntimeError(
            f"Chinese subtitle alignment failed for {len(missing)} segments. "
            f"First missing ids: {missing[:10]}"
        )


def load_chinese_segments_from_vtt(path: Path) -> list[Segment]:
    segments = load_segments_from_vtt(path)
    for segment in segments:
        segment.chinese = segment.english
        segment.english = ""
    return segments


def write_srt(path: Path, segments: list[Segment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        subtitle_text = _wrap_text(segment.chinese or segment.english)
        lines.extend(
            [
                str(index),
                f"{_format_srt_time(segment.render_start)} --> {_format_srt_time(segment.render_end)}",
                subtitle_text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_vtt_cues(path: Path) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start: float | None = None
    end: float | None = None
    cue_lines: list[str] = []

    def flush() -> None:
        nonlocal start, end, cue_lines
        if start is None or end is None:
            cue_lines = []
            return
        text = _merge_progressive_lines(cue_lines)
        if text:
            cues.append((start, end, text))
        start = None
        end = None
        cue_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        match = TIMESTAMP_RE.match(stripped)
        if match:
            flush()
            start = _parse_timestamp(match.group("start"))
            end = _parse_timestamp(match.group("end"))
            continue
        if start is None:
            continue
        cue_lines.append(stripped)
    flush()
    return cues


def _collapse_cues(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    collapsed: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        if not collapsed:
            collapsed.append((start, end, text))
            continue
        prev_start, prev_end, prev_text = collapsed[-1]
        overlap = start <= prev_end + 0.35
        if overlap and text == prev_text:
            collapsed[-1] = (prev_start, max(prev_end, end), prev_text)
            continue
        if overlap and (text.endswith(prev_text) or prev_text.endswith(text)):
            better_text = text if len(text) >= len(prev_text) else prev_text
            collapsed[-1] = (prev_start, max(prev_end, end), better_text)
            continue
        collapsed.append((start, end, text))
    return collapsed


def _strip_progressive_overlap(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    trimmed: list[tuple[float, float, str]] = []
    previous_text = ""
    for start, end, text in cues:
        text = _collapse_immediate_repetition(text)
        if previous_text:
            text = _strip_leading_word_overlap(text, previous_text)
            text = _collapse_immediate_repetition(text)
        if not text:
            continue
        trimmed.append((start, end, text))
        previous_text = text
    return trimmed


def _merge_short_cues(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    if not cues:
        return []
    merged: list[list[float | str]] = [[cues[0][0], cues[0][1], cues[0][2]]]
    for start, end, text in cues[1:]:
        prev_start, prev_end, prev_text = merged[-1]
        joinable = (
            start - float(prev_end) <= 0.35
            and len(str(prev_text)) + len(text) <= 140
            and not str(prev_text).endswith((".", "?", "!", ",")) 
        )
        if joinable and text.lower() not in str(prev_text).lower():
            merged[-1] = [prev_start, end, f"{prev_text} {text}".strip()]
            continue
        merged.append([start, end, text])
    return [(float(start), float(end), str(text)) for start, end, text in merged]


def _clean_vtt_text(text: str) -> str:
    text = INLINE_TIMESTAMP_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def _merge_progressive_lines(lines: list[str]) -> str:
    merged = ""
    for raw_line in lines:
        cleaned = _clean_vtt_text(raw_line)
        if not cleaned:
            continue
        if not merged:
            merged = cleaned
            continue
        if _normalized_startswith(cleaned, merged):
            merged = cleaned
            continue
        if _normalized_startswith(merged, cleaned):
            continue
        remainder = _strip_leading_word_overlap(cleaned, merged)
        if remainder != cleaned:
            merged = f"{merged} {remainder}".strip()
        else:
            merged = f"{merged} {cleaned}".strip()
        merged = _collapse_immediate_repetition(merged)
    return merged


def _parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _format_srt_time(value: float) -> str:
    total_ms = int(round(value * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _wrap_text(text: str, line_length: int = 18) -> str:
    text = text.strip()
    if len(text) <= line_length:
        return text
    chunks = [text[i : i + line_length] for i in range(0, len(text), line_length)]
    return "\n".join(chunks[:2])


def _normalized_startswith(text: str, prefix: str) -> bool:
    return _normalize_for_overlap(text).startswith(_normalize_for_overlap(prefix))


def _strip_leading_word_overlap(text: str, previous_text: str, min_words: int = 2) -> str:
    text_words = text.split()
    previous_words = previous_text.split()
    if not text_words or not previous_words:
        return text

    normalized_text_words = [_normalize_word(word) for word in text_words]
    normalized_previous_words = [_normalize_word(word) for word in previous_words]

    max_overlap = min(len(text_words), len(previous_words))
    for overlap in range(max_overlap, min_words - 1, -1):
        if normalized_text_words[:overlap] == normalized_previous_words[-overlap:]:
            return " ".join(text_words[overlap:]).strip()
    return text


def _collapse_immediate_repetition(text: str, min_words: int = 3) -> str:
    words = text.split()
    if len(words) < min_words * 2:
        return text

    changed = True
    while changed:
        changed = False
        max_span = len(words) // 2
        for span in range(max_span, min_words - 1, -1):
            for index in range(0, len(words) - span * 2 + 1):
                left = [_normalize_word(word) for word in words[index : index + span]]
                right = [_normalize_word(word) for word in words[index + span : index + span * 2]]
                if left != right:
                    continue
                words = words[: index + span] + words[index + span * 2 :]
                changed = True
                break
            if changed:
                break
    return " ".join(words).strip()


def _normalize_for_overlap(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_word(word: str) -> str:
    return re.sub(r"^\W+|\W+$", "", word).lower()


def _find_best_overlap_text(start: float, end: float, candidates: list[Segment]) -> str:
    overlaps: list[str] = []
    for candidate in candidates:
        overlap = min(end, candidate.end) - max(start, candidate.start)
        if overlap <= 0:
            continue
        if overlap >= min(end - start, candidate.end - candidate.start) * 0.3:
            overlaps.append(candidate.english)
    if not overlaps:
        return ""
    deduped: list[str] = []
    for text in overlaps:
        if text not in deduped:
            deduped.append(text)
    return " ".join(deduped).strip()

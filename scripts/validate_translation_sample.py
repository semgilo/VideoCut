#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videocut.config import PipelineConfig
from videocut.media import ffprobe_duration, render_final_video
from videocut.models import Segment
from videocut.shell import resolve_tool_binary
from videocut.subtitles import (
    _find_best_overlap_text,
    load_chinese_segments_from_vtt,
    load_segments_from_vtt,
)
from videocut.translate import (
    OpenAICompatibleTranslator,
    ensure_endpoint_reachable,
    is_local_base_url,
    load_protected_terms,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "runs" / "fd4k16REDOU-20260321-dub"
DEFAULT_SOURCE_VIDEO = (
    DEFAULT_RUN_DIR
    / "source"
    / "I fixed OpenClaw so it actually works (full setup) [fd4k16REDOU].mp4"
)
DEFAULT_ENGLISH_VTT = (
    DEFAULT_RUN_DIR
    / "source"
    / "I fixed OpenClaw so it actually works (full setup) [fd4k16REDOU].en-orig.vtt"
)
DEFAULT_YOUTUBE_ZH_VTT = (
    DEFAULT_RUN_DIR
    / "source"
    / "I fixed OpenClaw so it actually works (full setup) [fd4k16REDOU].zh-Hans.vtt"
)
DEFAULT_CURRENT_ZH_SRT = DEFAULT_RUN_DIR / "subtitles" / "zh.srt"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "runs" / "fd4k16REDOU-validate-01"
DEFAULT_START_SECONDS = 0.0
DEFAULT_DURATION_SECONDS = 300.0
TRANSLATION_BATCH_SIZE = 10
ISSUE_TAGS = (
    "proper_noun",
    "term_mismatch",
    "literal_translation",
    "awkward_cn",
    "alignment_gap",
    "duplicate_or_broken",
)
REVIEWER_DECISIONS = ("accept", "edit", "replace")
FILE_TERM_RE = re.compile(r"\b[\w./-]+\.(?:md|json|yaml|yml|txt|py|ts|tsx|js|jsx|sh)\b")
TITLE_TERM_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
CAMEL_TERM_RE = re.compile(r"\b(?:[A-Z]{2,}(?:[A-Z0-9-]*[A-Z0-9])?|[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b")
ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
SRT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
WORD_REPEAT_RE = re.compile(r"\b([A-Za-z0-9_.-]{2,})\b(?:\s+\1\b)+", re.IGNORECASE)
CJK_REPEAT_RE = re.compile(r"([\u4e00-\u9fff]{2,10})\1+")


@dataclass(slots=True)
class GlossaryEntry:
    term: str
    category: str
    occurrences: int
    guidance: str


@dataclass(slots=True)
class ReviewRow:
    index: int
    time_range: str
    english: str
    youtube_zh: str
    current_zh_srt: str
    llm_zh: str
    issue_tags: list[str]
    reviewer_decision: str


def main() -> None:
    args = _parse_args()
    english_vtt = args.english_vtt.expanduser().resolve()
    youtube_zh_vtt = args.youtube_zh_vtt.expanduser().resolve()
    current_zh_srt = args.current_zh_srt.expanduser().resolve()
    source_video = args.source_video.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    for path in (english_vtt, youtube_zh_vtt, current_zh_srt, source_video):
        if not path.exists():
            raise FileNotFoundError(f"Required input does not exist: {path}")

    config = PipelineConfig()
    _ensure_llm_ready(config)
    try:
        ensure_endpoint_reachable(config.llm_base_url)
    except OSError as error:
        raise RuntimeError(
            "LLM endpoint is not reachable. "
            "Start the local OpenAI-compatible server or update VIDEOCUT_LLM_BASE_URL."
        ) from error
    protected_terms = load_protected_terms(config.protected_terms_path)

    all_english_segments = load_segments_from_vtt(english_vtt)
    window_start = float(args.start)
    window_end = float(args.start + args.duration)
    sample_segments = _select_sample_segments(all_english_segments, window_start, window_end)
    if not sample_segments:
        raise RuntimeError(
            f"No English subtitle segments intersect the requested window: "
            f"{_format_clock(window_start)}-{_format_clock(window_end)}"
        )

    translator = OpenAICompatibleTranslator(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        timeout=config.llm_timeout,
        batch_size=TRANSLATION_BATCH_SIZE,
        concurrency=config.translation_concurrency,
        protected_terms=protected_terms,
    )
    print(
        f"Translating {len(sample_segments)} segments "
        f"from {_format_clock(window_start)} to {_format_clock(window_end)}..."
    )
    translator.translate(sample_segments)
    if config.translation_timing_adapt:
        adapted_count = translator.adapt_subtitles_for_timing(
            segments=sample_segments,
            target_compact_cps=config.translation_target_compact_cps,
            slack_chars=config.translation_adapt_slack_chars,
            passes=config.translation_adapt_passes,
            min_compact_chars=config.translation_adapt_min_chars,
        )
        if adapted_count:
            print(
                "Adapted translated lines for dubbing timing: "
                f"{adapted_count}/{len(sample_segments)} segments"
            )
    missing_translations = [segment.index for segment in sample_segments if not segment.chinese.strip()]
    if missing_translations:
        raise RuntimeError(
            "LLM translation returned empty text for sample segments: "
            f"{missing_translations[:10]}"
        )

    youtube_segments = load_chinese_segments_from_vtt(youtube_zh_vtt)
    current_srt_segments = _load_segments_from_srt(current_zh_srt)
    current_by_index = {segment.index: segment.chinese for segment in current_srt_segments}
    glossary = _build_glossary(sample_segments)
    glossary_terms = {term for segment in sample_segments for term in _extract_terms(segment.english)}

    rows: list[ReviewRow] = []
    issue_counts = Counter({tag: 0 for tag in ISSUE_TAGS})
    high_risk_counter: Counter[str] = Counter()
    for segment in sample_segments:
        youtube_zh = _find_best_overlap_text(segment.start, segment.end, youtube_segments)
        current_zh = current_by_index.get(segment.index, "")
        if not current_zh:
            current_zh = _find_best_overlap_text(segment.start, segment.end, current_srt_segments)

        issue_tags = _suggest_issue_tags(
            segment=segment,
            youtube_zh=youtube_zh,
            current_zh=current_zh,
            glossary_terms=glossary_terms,
        )
        reviewer_decision = _default_reviewer_decision(issue_tags)
        for tag in issue_tags:
            issue_counts[tag] += 1
        if issue_tags:
            for term in _extract_terms(segment.english):
                high_risk_counter[term] += 1

        rows.append(
            ReviewRow(
                index=segment.index,
                time_range=f"{_format_clock(segment.start)}-{_format_clock(segment.end)}",
                english=segment.english,
                youtube_zh=youtube_zh,
                current_zh_srt=current_zh,
                llm_zh=segment.chinese,
                issue_tags=issue_tags,
                reviewer_decision=reviewer_decision,
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / "review.md"
    bilingual_srt_path = output_dir / "bilingual.srt"
    preview_video_path = output_dir / "preview_bilingual.mp4"
    review_path.write_text(
        _render_review_markdown(
            source_video=source_video,
            english_vtt=english_vtt,
            youtube_zh_vtt=youtube_zh_vtt,
            current_zh_srt=current_zh_srt,
            output_dir=output_dir,
            window_start=window_start,
            window_end=window_end,
            config=config,
            protected_terms=protected_terms,
            sample_segments=sample_segments,
            glossary=glossary,
            rows=rows,
            issue_counts=issue_counts,
            high_risk_counter=high_risk_counter,
        ),
        encoding="utf-8",
    )
    _write_bilingual_srt(
        path=bilingual_srt_path,
        segments=sample_segments,
        clip_start=window_start,
    )
    _render_bilingual_preview_video(
        source_video=source_video,
        subtitle_path=bilingual_srt_path,
        output_path=preview_video_path,
        clip_start=window_start,
        clip_duration=args.duration,
        config=config,
    )
    print(f"Review written to {review_path}")
    print(f"Bilingual SRT written to {bilingual_srt_path}")
    print(f"Bilingual preview video written to {preview_video_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a translated subtitle sample without running dubbing.")
    parser.add_argument("--source-video", type=Path, default=DEFAULT_SOURCE_VIDEO)
    parser.add_argument("--english-vtt", type=Path, default=DEFAULT_ENGLISH_VTT)
    parser.add_argument("--youtube-zh-vtt", type=Path, default=DEFAULT_YOUTUBE_ZH_VTT)
    parser.add_argument("--current-zh-srt", type=Path, default=DEFAULT_CURRENT_ZH_SRT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", type=float, default=DEFAULT_START_SECONDS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    return parser.parse_args()


def _ensure_llm_ready(config: PipelineConfig) -> None:
    base_url = config.llm_base_url.strip()
    model = config.llm_model.strip()
    api_key = config.llm_api_key.strip()
    if not base_url or not model:
        raise RuntimeError(
            "LLM translation is not configured. Set VIDEOCUT_LLM_BASE_URL and VIDEOCUT_LLM_MODEL."
        )
    if api_key or _is_local_base_url(base_url):
        return
    raise RuntimeError(
        "LLM translation requires VIDEOCUT_LLM_API_KEY unless the base URL is local."
    )


def _select_sample_segments(
    segments: list[Segment],
    start: float,
    end: float,
) -> list[Segment]:
    selected: list[Segment] = []
    for segment in segments:
        if segment.end <= start or segment.start >= end:
            continue
        selected.append(
            Segment(
                index=segment.index,
                start=segment.start,
                end=segment.end,
                english=segment.english,
            )
        )
    return selected


def _load_segments_from_srt(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip())
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        timestamp_line = lines[1] if lines[0].isdigit() else lines[0]
        match = SRT_TIMESTAMP_RE.match(timestamp_line)
        if not match:
            continue
        text_start_index = 2 if lines[0].isdigit() else 1
        subtitle_text = " ".join(lines[text_start_index:]).strip()
        if not subtitle_text:
            continue
        segments.append(
            Segment(
                index=len(segments) + 1,
                start=_parse_timestamp(match.group("start")),
                end=_parse_timestamp(match.group("end")),
                english="",
                chinese=subtitle_text,
            )
        )
    return segments


def _parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _build_glossary(segments: list[Segment]) -> list[GlossaryEntry]:
    counts = Counter()
    categories: dict[str, str] = {}
    for segment in segments:
        for term in _extract_terms(segment.english):
            counts[term] += 1
            categories.setdefault(term, _categorize_term(term))

    entries = [
        GlossaryEntry(
            term=term,
            category=categories[term],
            occurrences=occurrences,
            guidance=_guidance_for_term(categories[term]),
        )
        for term, occurrences in counts.items()
    ]
    entries.sort(key=lambda entry: (-entry.occurrences, entry.term.lower()))
    return entries[:20]


def _extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for pattern in (FILE_TERM_RE, TITLE_TERM_RE, CAMEL_TERM_RE):
        for match in pattern.finditer(text):
            term = match.group(0).strip()
            if not term or term.lower() in {"this", "that", "therefore"}:
                continue
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return terms


def _categorize_term(term: str) -> str:
    if FILE_TERM_RE.fullmatch(term):
        return "file"
    if " " in term:
        return "proper noun"
    if term.isupper():
        return "acronym"
    return "product/term"


def _guidance_for_term(category: str) -> str:
    if category == "file":
        return "Keep the filename literal and unchanged."
    if category == "proper noun":
        return "Preserve the person or product name consistently."
    if category == "acronym":
        return "Keep the acronym unchanged unless a standard Chinese form exists."
    return "Keep the product or technical term stable across the sample."


def _suggest_issue_tags(
    segment: Segment,
    youtube_zh: str,
    current_zh: str,
    glossary_terms: set[str],
) -> list[str]:
    tags: list[str] = []
    risky_terms = [term for term in _extract_terms(segment.english) if term in glossary_terms]
    translations = [youtube_zh, current_zh, segment.chinese]
    if risky_terms:
        tags.append("proper_noun")
    if not youtube_zh or not current_zh:
        tags.append("alignment_gap")
    if _has_term_mismatch(translations):
        tags.append("term_mismatch")
    if _looks_literal(segment.english, segment.chinese, youtube_zh, current_zh):
        tags.append("literal_translation")
    if _looks_awkward(segment.chinese):
        tags.append("awkward_cn")
    if any(_has_duplicate_or_broken_text(text) for text in translations if text):
        tags.append("duplicate_or_broken")
    return [tag for tag in ISSUE_TAGS if tag in tags]


def _has_term_mismatch(translations: list[str]) -> bool:
    ascii_sets = {
        tuple(sorted({token.lower() for token in ASCII_TOKEN_RE.findall(text)}))
        for text in translations
        if text
    }
    ascii_sets.discard(tuple())
    return len(ascii_sets) > 1


def _looks_literal(english: str, llm_zh: str, youtube_zh: str, current_zh: str) -> bool:
    comparison_lengths = [len(text) for text in (youtube_zh, current_zh) if text]
    if not comparison_lengths:
        return False
    baseline = min(comparison_lengths)
    if baseline == 0:
        return False
    english_words = max(1, len(english.split()))
    return len(llm_zh) >= baseline * 1.6 and len(llm_zh) >= english_words * 2.5


def _looks_awkward(text: str) -> bool:
    if not text:
        return False
    if "  " in text:
        return True
    if re.search(r"[\u4e00-\u9fff]\s+[，。！？；：]", text):
        return True
    if re.search(r"[\u4e00-\u9fff]\s+[A-Za-z0-9]", text) and len(ASCII_TOKEN_RE.findall(text)) >= 3:
        return True
    return False


def _has_duplicate_or_broken_text(text: str) -> bool:
    if WORD_REPEAT_RE.search(text):
        return True
    if CJK_REPEAT_RE.search(re.sub(r"\s+", "", text)):
        return True
    return False


def _default_reviewer_decision(issue_tags: list[str]) -> str:
    if not issue_tags:
        return REVIEWER_DECISIONS[0]
    if "alignment_gap" in issue_tags or "duplicate_or_broken" in issue_tags:
        return REVIEWER_DECISIONS[2]
    return REVIEWER_DECISIONS[1]


def _render_review_markdown(
    source_video: Path,
    english_vtt: Path,
    youtube_zh_vtt: Path,
    current_zh_srt: Path,
    output_dir: Path,
    window_start: float,
    window_end: float,
    config: PipelineConfig,
    protected_terms: list[str],
    sample_segments: list[Segment],
    glossary: list[GlossaryEntry],
    rows: list[ReviewRow],
    issue_counts: Counter[str],
    high_risk_counter: Counter[str],
) -> str:
    high_risk_terms = ", ".join(
        term for term, _ in high_risk_counter.most_common(10)
    ) or "-"
    lines = [
        "# Translation Validation Review",
        "",
        "- Scope: subtitle translation only; no dubbing, timing, or final video export.",
        f"- Window: `{_format_clock(window_start)}-{_format_clock(window_end)}`",
        f"- Sample segments: `{len(sample_segments)}`",
        f"- Source video: `{source_video}`",
        f"- English source: `{english_vtt}`",
        f"- YouTube Chinese baseline: `{youtube_zh_vtt}`",
        f"- Current Chinese SRT: `{current_zh_srt}`",
        f"- Output dir: `{output_dir}`",
        f"- Bilingual subtitle preview: `{output_dir / 'preview_bilingual.mp4'}`",
        f"- LLM endpoint: `{config.llm_base_url}`",
        f"- LLM model: `{config.llm_model}`",
        f"- Translation batch size: `{TRANSLATION_BATCH_SIZE}`",
        f"- Translation concurrency: `{config.translation_concurrency}`",
        f"- Protected terms file: `{config.protected_terms_path}`",
        f"- Protected terms loaded: `{len(protected_terms)}`",
        "- `issue_tags` and `reviewer_decision` are auto-suggested and should be confirmed by a human reviewer.",
        "",
        "## Locked Terminology",
        "",
        "| term | category | occurrences | guidance |",
        "| --- | --- | ---: | --- |",
    ]
    if glossary:
        for entry in glossary:
            lines.append(
                f"| {_md(entry.term)} | {_md(entry.category)} | {entry.occurrences} | {_md(entry.guidance)} |"
            )
    else:
        lines.append("| - | - | 0 | No high-risk terms detected in the sample. |")

    lines.extend(
        [
            "",
            "## Review Table",
            "",
            "| index | time | english | youtube_zh | current_zh_srt | llm_zh | issue_tags | reviewer_decision |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        issue_tags = ", ".join(row.issue_tags) if row.issue_tags else "-"
        lines.append(
            f"| {row.index} | {_md(row.time_range)} | {_md(row.english)} | {_md(row.youtube_zh)} | "
            f"{_md(row.current_zh_srt)} | {_md(row.llm_zh)} | {_md(issue_tags)} | {_md(row.reviewer_decision)} |"
        )

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Total sample segments: `{len(sample_segments)}`",
            "- Issue tag counts:",
            (
                f"  `proper_noun={issue_counts['proper_noun']}`, "
                f"`term_mismatch={issue_counts['term_mismatch']}`, "
                f"`literal_translation={issue_counts['literal_translation']}`, "
                f"`awkward_cn={issue_counts['awkward_cn']}`, "
                f"`alignment_gap={issue_counts['alignment_gap']}`, "
                f"`duplicate_or_broken={issue_counts['duplicate_or_broken']}`"
            ),
            f"- High-risk terms in flagged rows: `{high_risk_terms}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _md(value: str) -> str:
    if not value:
        return "-"
    return value.replace("|", "\\|").replace("\n", "<br>")


def _format_clock(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def _write_bilingual_srt(path: Path, segments: list[Segment], clip_start: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for cue_index, segment in enumerate(segments, start=1):
        start = max(0.0, segment.start - clip_start)
        end = max(start + 0.01, segment.end - clip_start)
        text_lines = [segment.english.strip(), segment.chinese.strip()]
        text = "\n".join(line for line in text_lines if line)
        if not text:
            continue
        lines.extend(
            [
                str(cue_index),
                f"{_format_srt_time(start)} --> {_format_srt_time(end)}",
                text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _format_srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _render_bilingual_preview_video(
    source_video: Path,
    subtitle_path: Path,
    output_path: Path,
    clip_start: float,
    clip_duration: float,
    config: PipelineConfig,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = max(0.01, clip_duration)
    duration = min(clip_duration, max(0.01, ffprobe_duration(source_video) - clip_start))
    clipped_video_path = output_path.with_name("preview_source_clip.mp4")
    cmd = [
        resolve_tool_binary("ffmpeg"),
        "-y",
        "-ss",
        f"{clip_start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_video),
        "-c",
        "copy",
        str(clipped_video_path),
    ]
    try:
        subprocess.run(cmd, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Failed to create clipped preview source video: "
            f"{error.stderr.strip()}"
        ) from error
    render_final_video(
        video_path=clipped_video_path,
        dubbed_track_path=clipped_video_path,
        subtitle_path=subtitle_path,
        output_path=output_path,
        burn_subtitles=True,
        subtitle_font=config.subtitle_font,
        subtitle_font_path=config.subtitle_font_path,
        subtitle_font_size=config.subtitle_font_size,
        video_preset=config.video_preset,
        video_crf=config.video_crf,
        subtitle_overlay_concurrency=config.subtitle_overlay_concurrency,
    )


if __name__ == "__main__":
    main()

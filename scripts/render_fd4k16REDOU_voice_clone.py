#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videocut.config import PipelineConfig
from videocut.media import (
    compose_dubbed_track,
    finalize_synthesized_segments,
    ffprobe_duration,
    render_final_video,
    write_manifest,
)
from videocut.models import Segment
from videocut.subtitles import write_srt
from videocut.timing import plan_dubbing_timing_with_fallback
from videocut.tts import synthesize_segments


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "runs" / "fd4k16REDOU-validate-01"
RUN_DIR = REPO_ROOT / "runs" / "fd4k16REDOU-voiceclone-02"
SOURCE_VIDEO = SAMPLE_DIR / "preview_source_clip.mp4"
BILINGUAL_SRT = SAMPLE_DIR / "bilingual.srt"
COSYVOICE_GROUP_SIZE = 10
SRT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})"
)


def main() -> None:
    if not SOURCE_VIDEO.exists():
        raise FileNotFoundError(f"Source clip not found: {SOURCE_VIDEO}")
    if not BILINGUAL_SRT.exists():
        raise FileNotFoundError(f"Bilingual subtitle file not found: {BILINGUAL_SRT}")

    segments = _load_bilingual_segments(BILINGUAL_SRT)
    if not segments:
        raise RuntimeError(f"No subtitle segments could be loaded from {BILINGUAL_SRT}")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tts_dir = RUN_DIR / "tts"
    subtitles_dir = RUN_DIR / "subtitles"
    audio_dir = RUN_DIR / "audio"
    tts_dir.mkdir(parents=True, exist_ok=True)
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    config = PipelineConfig()
    config.output_name = "voiceclone_cn.mp4"
    config.tts_provider = "cosyvoice"
    config.cosyvoice_python = str(REPO_ROOT / ".venv-cosyvoice" / "bin" / "python")
    config.cosyvoice_repo_dir = str(REPO_ROOT / ".vendor" / "CosyVoice")
    config.cosyvoice_model_dir = str(
        REPO_ROOT / ".vendor" / "CosyVoice" / "pretrained_models" / "Fun-CosyVoice3-0.5B"
    )
    config.cosyvoice_mode = "cross_lingual"
    config.cosyvoice_group_size = COSYVOICE_GROUP_SIZE
    config.original_audio_volume = 0.0
    config.dub_audio_volume = 1.0
    config.timing_mode = "fit"
    config.min_playback_rate = 0.6
    config.max_playback_rate = 1.5
    config.max_segment_lag = 0.3
    config.max_opening_silence = 0.2
    config.max_global_shift = 0.2
    config.min_segment_gap = 0.02

    print(f"Voice clone sample source: {SOURCE_VIDEO}")
    print(f"Subtitle source: {BILINGUAL_SRT}")
    print(f"Segments: {len(segments)}")
    print(f"CosyVoice batching: {config.cosyvoice_group_size} segments/job")
    print(
        "Timing constraints: "
        f"mode={config.timing_mode}, "
        f"playback_rate={config.min_playback_rate:.2f}-{config.max_playback_rate:.2f}, "
        f"max_segment_lag={config.max_segment_lag:.2f}"
    )

    synthesize_segments(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=SOURCE_VIDEO,
    )
    trimmed_segments, total_leading_trim, total_trailing_trim = finalize_synthesized_segments(
        segments=segments,
        trim_silence=config.trim_tts_silence,
        silence_threshold_db=config.tts_silence_threshold_db,
        min_silence_duration=config.tts_silence_min_duration,
        keep_silence=config.tts_keep_silence,
    )
    if trimmed_segments:
        print(
            "Trimmed TTS silence: "
            f"{trimmed_segments} segments, "
            f"{total_leading_trim:.2f}s leading and {total_trailing_trim:.2f}s trailing removed"
        )

    video_duration = ffprobe_duration(SOURCE_VIDEO)
    used_timing_mode, used_max_playback_rate, used_max_segment_lag = plan_dubbing_timing_with_fallback(
        segments=segments,
        video_duration=video_duration,
        timing_mode=config.timing_mode,
        max_opening_silence=config.max_opening_silence,
        max_global_shift=config.max_global_shift,
        min_segment_gap=config.min_segment_gap,
        min_playback_rate=config.min_playback_rate,
        max_playback_rate=config.max_playback_rate,
        max_segment_lag=config.max_segment_lag,
    )
    print(
        "Timing used: "
        f"mode={used_timing_mode}, "
        f"max_playback_rate={used_max_playback_rate:.2f}, "
        f"max_segment_lag={used_max_segment_lag:.2f}"
    )

    subtitle_path = subtitles_dir / "zh.srt"
    write_srt(subtitle_path, segments)
    dubbed_track = compose_dubbed_track(
        video_path=SOURCE_VIDEO,
        segments=segments,
        output_path=audio_dir / "dubbed_track.m4a",
        original_volume=config.original_audio_volume,
        dub_volume=config.dub_audio_volume,
    )
    final_video = render_final_video(
        video_path=SOURCE_VIDEO,
        dubbed_track_path=dubbed_track,
        subtitle_path=subtitle_path,
        output_path=RUN_DIR / config.output_name,
        burn_subtitles=config.burn_subtitles,
        subtitle_font=config.subtitle_font,
        subtitle_font_path=config.subtitle_font_path,
        subtitle_font_size=config.subtitle_font_size,
        video_preset=config.video_preset,
        video_crf=config.video_crf,
        subtitle_overlay_concurrency=config.subtitle_overlay_concurrency,
    )
    write_manifest(
        path=RUN_DIR / "manifest.json",
        source_video=SOURCE_VIDEO,
        subtitle_source=BILINGUAL_SRT,
        thumbnail_source=None,
        generated_srt=subtitle_path,
        dubbed_track=dubbed_track,
        final_video=final_video,
        segments=segments,
        source_metadata=None,
        localized_metadata=None,
        publish_assets=None,
    )
    print(f"Chinese subtitle file generated: {subtitle_path}")
    print(f"Dubbed audio track generated: {dubbed_track}")
    print(f"Voice clone preview video generated: {final_video}")


def _load_bilingual_segments(path: Path) -> list[Segment]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip())
    segments: list[Segment] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 4:
            continue
        timestamp_line = lines[1] if lines[0].isdigit() else lines[0]
        match = SRT_TIMESTAMP_RE.match(timestamp_line)
        if not match:
            continue
        text_start_index = 2 if lines[0].isdigit() else 1
        text_lines = lines[text_start_index:]
        if len(text_lines) < 2:
            continue
        english = text_lines[0].strip()
        chinese = " ".join(line.strip() for line in text_lines[1:] if line.strip())
        if not english or not chinese:
            continue
        segments.append(
            Segment(
                index=len(segments) + 1,
                start=_parse_timestamp(match.group("start")),
                end=_parse_timestamp(match.group("end")),
                english=english,
                chinese=chinese,
            )
        )
    return segments


def _parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


if __name__ == "__main__":
    main()

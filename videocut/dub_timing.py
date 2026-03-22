from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from videocut.config import PipelineConfig
from videocut.media import finalize_synthesized_segments
from videocut.models import Segment
from videocut.translate import OpenAICompatibleTranslator
from videocut.tts import synthesize_segments


def repair_segments_for_audio_timing(
    segments: list[Segment],
    output_dir: Path,
    config: PipelineConfig,
    source_video: Path | None,
    translator: OpenAICompatibleTranslator | None,
) -> tuple[int, int]:
    if translator is None or not config.translation_audio_repair:
        return 0, 0

    total_rewritten = 0
    total_resynthesized = 0
    max_passes = max(1, config.translation_audio_repair_passes)
    repair_config = _repair_tts_config(config)
    effective_target_rate = max(0.01, config.translation_audio_target_playback_rate)
    slack_seconds = max(0.0, config.translation_audio_repair_slack_seconds)

    for pass_index in range(1, max_passes + 1):
        candidates = _collect_audio_repair_candidates(
            segments=segments,
            target_playback_rate=effective_target_rate,
            slack_seconds=slack_seconds,
        )
        if not candidates:
            break

        worst_ratio = max(
            (segment.synthetic_duration or segment.duration) / max(segment.duration, 0.01)
            for segment in candidates
        )
        print(
            "Audio timing repair pass "
            f"{pass_index}/{max_passes}: {len(candidates)} segments exceed the local dubbing budget "
            f"(worst natural/local ratio {worst_ratio:.2f}x)"
        )
        rewritten_segments = translator.adapt_subtitles_for_audio_timing(
            candidates=candidates,
            target_playback_rate=effective_target_rate,
            slack_seconds=slack_seconds,
            min_compact_chars=config.translation_adapt_min_chars,
        )
        if not rewritten_segments:
            print("Audio timing repair could not shorten any remaining segments further.")
            break

        for segment in rewritten_segments:
            if segment.audio_path is not None:
                segment.audio_path.unlink(missing_ok=True)
            segment.synthetic_duration = None
            segment.leading_silence = 0.0
            segment.trailing_silence = 0.0

        synthesize_segments(
            segments=segments,
            output_dir=output_dir,
            config=repair_config,
            source_video=source_video,
        )
        trimmed_segments, total_leading_trim, total_trailing_trim = finalize_synthesized_segments(
            segments=rewritten_segments,
            trim_silence=config.trim_tts_silence,
            silence_threshold_db=config.tts_silence_threshold_db,
            min_silence_duration=config.tts_silence_min_duration,
            keep_silence=config.tts_keep_silence,
        )
        if trimmed_segments:
            print(
                "Trimmed repaired TTS silence: "
                f"{trimmed_segments} segments, "
                f"{total_leading_trim:.2f}s leading and {total_trailing_trim:.2f}s trailing removed"
            )

        total_rewritten += len(rewritten_segments)
        total_resynthesized += len(rewritten_segments)

    remaining = _collect_audio_repair_candidates(
        segments=segments,
        target_playback_rate=effective_target_rate,
        slack_seconds=slack_seconds,
    )
    if remaining:
        worst_remaining = max(
            (segment.synthetic_duration or segment.duration) / max(segment.duration, 0.01)
            for segment in remaining
        )
        print(
            "Audio timing repair stopped with "
            f"{len(remaining)} segments still above the local target "
            f"(worst natural/local ratio {worst_remaining:.2f}x)"
        )
    return total_rewritten, total_resynthesized


def _collect_audio_repair_candidates(
    segments: list[Segment],
    target_playback_rate: float,
    slack_seconds: float,
) -> list[Segment]:
    candidates: list[Segment] = []
    for segment in segments:
        if not segment.english.strip() or not segment.chinese.strip():
            continue
        if segment.synthetic_duration is None:
            continue
        target_audio_duration = max(0.01, segment.duration * target_playback_rate + slack_seconds)
        if segment.synthetic_duration > target_audio_duration + 0.01:
            candidates.append(segment)
    return candidates


def _repair_tts_config(config: PipelineConfig) -> PipelineConfig:
    provider = config.tts_provider.strip().lower()
    if provider != "cosyvoice":
        return config
    return replace(
        config,
        cosyvoice_group_size=max(1, config.translation_audio_repair_group_size),
    )

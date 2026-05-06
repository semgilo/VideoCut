from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videocut.config import PipelineConfig
from videocut.dub_timing import repair_segments_for_audio_timing
from videocut.media import (
    compose_dubbed_track,
    finalize_synthesized_segments,
    ffprobe_duration,
    render_final_video,
    write_manifest,
)
from videocut.models import Segment
from videocut.publish import export_publish_assets, metadata_from_dict
from videocut.subtitles import write_srt
from videocut.timing import plan_dubbing_timing_with_fallback
from videocut.translate import OpenAICompatibleTranslator, load_protected_terms
from videocut.tts import synthesize_segments


def main() -> None:
    args = _parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    config = PipelineConfig()
    config.tts_provider = args.tts_provider or config.tts_provider
    config.tts_voice = args.voice or config.tts_voice
    config.minimax_voice_id = args.voice or config.minimax_voice_id
    config.tts_rate = args.tts_rate or config.tts_rate
    config.tts_command = args.tts_command or config.tts_command
    if args.tts_command_audio_format:
        config.tts_command_audio_format = args.tts_command_audio_format
    config.minimax_base_url = args.minimax_base_url or config.minimax_base_url
    config.minimax_api_key = args.minimax_api_key or config.minimax_api_key
    config.minimax_model = args.minimax_model or config.minimax_model
    if args.minimax_speed is not None:
        config.minimax_speed = args.minimax_speed
    if args.minimax_volume is not None:
        config.minimax_volume = args.minimax_volume
    if args.minimax_pitch is not None:
        config.minimax_pitch = args.minimax_pitch
    if args.minimax_concurrency is not None:
        config.minimax_concurrency = args.minimax_concurrency
    if args.minimax_voice_clone:
        config.minimax_voice_clone = True
    config.cosyvoice_python = args.cosyvoice_python or config.cosyvoice_python
    config.cosyvoice_mode = args.cosyvoice_mode or config.cosyvoice_mode
    if args.cosyvoice_group_size is not None:
        config.cosyvoice_group_size = max(1, args.cosyvoice_group_size)
    if args.cosyvoice_repo:
        config.cosyvoice_repo_dir = str(Path(args.cosyvoice_repo).expanduser().resolve())
    if args.cosyvoice_model:
        config.cosyvoice_model_dir = str(Path(args.cosyvoice_model).expanduser().resolve())
    if args.reference_audio:
        config.reference_audio_path = str(Path(args.reference_audio).expanduser().resolve())
    if args.reference_text:
        config.reference_text = args.reference_text
    if args.original_volume is not None:
        config.original_audio_volume = args.original_volume
    if args.dub_volume is not None:
        config.dub_audio_volume = args.dub_volume
    if args.timing_mode:
        config.timing_mode = args.timing_mode
    if args.min_playback_rate is not None:
        config.min_playback_rate = args.min_playback_rate
    if args.max_playback_rate is not None:
        config.max_playback_rate = args.max_playback_rate
    if args.max_segment_lag is not None:
        config.max_segment_lag = args.max_segment_lag
    if args.max_opening_silence is not None:
        config.max_opening_silence = args.max_opening_silence
    if args.max_global_shift is not None:
        config.max_global_shift = args.max_global_shift
    if args.min_segment_gap is not None:
        config.min_segment_gap = args.min_segment_gap
    if args.no_burn_subtitles:
        config.burn_subtitles = False
    if args.subtitle_font:
        config.subtitle_font = args.subtitle_font
    if args.subtitle_font_path:
        config.subtitle_font_path = args.subtitle_font_path
    if args.subtitle_size is not None:
        config.subtitle_font_size = args.subtitle_size
    if args.tts_provider is None and config.tts_provider.strip().lower() == "edge":
        if _has_local_cosyvoice_assets(config):
            print(
                "Configured TTS provider 'edge' does not support voice cloning. "
                "Switching to local CosyVoice."
            )
            config.tts_provider = "cosyvoice"

    source_video = Path(payload["source_video"]).expanduser().resolve()
    subtitle_source = payload.get("subtitle_source")
    subtitle_source_path = Path(subtitle_source).expanduser().resolve() if subtitle_source else None
    thumbnail_source = payload.get("thumbnail_source")
    thumbnail_source_path = Path(thumbnail_source).expanduser().resolve() if thumbnail_source else None
    source_metadata = metadata_from_dict(payload.get("source_metadata"))
    localized_metadata = metadata_from_dict(payload.get("localized_metadata"))
    segments = [
        Segment(
            index=int(item["index"]),
            start=float(item["start"]),
            end=float(item["end"]),
            english=str(item.get("english", "")),
            chinese=str(item.get("chinese", "")),
        )
        for item in payload["segments"]
    ]

    tts_dir = output_dir / "tts"
    subtitles_dir = output_dir / "subtitles"
    audio_dir = output_dir / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    protected_terms = load_protected_terms(config.protected_terms_path)
    translator = None
    if config.llm_base_url.strip() and config.llm_model.strip():
        translator = OpenAICompatibleTranslator(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
            timeout=config.llm_timeout,
            batch_size=config.translation_batch_size,
            concurrency=config.translation_concurrency,
            target_cps=config.translation_target_cps,
            min_playback_rate=config.min_playback_rate,
            max_playback_rate=config.max_playback_rate,
            enforce_char_budget=config.translation_enforce_char_budget,
            budget_refine_passes=config.translation_budget_refine_passes,
            protected_terms=protected_terms,
        )

    print(f"Rendering {len(segments)} manifest segments into {output_dir}")
    synthesize_segments(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=source_video,
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
    repaired_lines, resynthesized_lines = repair_segments_for_audio_timing(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=source_video,
        translator=translator,
    )
    if repaired_lines:
        print(
            "Audio timing repair completed: "
            f"{repaired_lines} subtitle rewrites, {resynthesized_lines} segment re-syntheses"
        )

    video_duration = ffprobe_duration(source_video)
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
    if (
        used_timing_mode != config.timing_mode
        or abs(used_max_playback_rate - config.max_playback_rate) > 0.001
        or abs(used_max_segment_lag - config.max_segment_lag) > 0.001
    ):
        print(
            "Timing fallback applied: "
            f"mode={used_timing_mode}, "
            f"max_playback_rate={used_max_playback_rate:.2f}, "
            f"max_segment_lag={used_max_segment_lag:.2f}"
        )

    subtitle_path = subtitles_dir / "zh.srt"
    write_srt(subtitle_path, segments)
    dubbed_track = compose_dubbed_track(
        video_path=source_video,
        segments=segments,
        output_path=audio_dir / "dubbed_track.m4a",
        original_volume=config.original_audio_volume,
        dub_volume=config.dub_audio_volume,
    )
    final_video = render_final_video(
        video_path=source_video,
        dubbed_track_path=dubbed_track,
        subtitle_path=subtitle_path,
        output_path=output_dir / config.output_name,
        burn_subtitles=config.burn_subtitles,
        subtitle_font=config.subtitle_font,
        subtitle_font_path=config.subtitle_font_path,
        subtitle_font_size=config.subtitle_font_size,
    )
    publish_assets = export_publish_assets(
        output_dir=output_dir,
        source_metadata=source_metadata,
        localized_metadata=localized_metadata,
        cover_image_path=thumbnail_source_path,
        final_video=final_video,
    )
    write_manifest(
        path=output_dir / "manifest.json",
        source_video=source_video,
        subtitle_source=subtitle_source_path,
        thumbnail_source=thumbnail_source_path,
        generated_srt=subtitle_path,
        dubbed_track=dubbed_track,
        final_video=final_video,
        segments=segments,
        source_metadata=source_metadata,
        localized_metadata=localized_metadata,
        publish_assets=publish_assets,
    )
    print(f"Final video: {final_video}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-render a VideoCut manifest with a different TTS setup.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tts-provider", choices=("edge", "minimax", "cosyvoice", "command"))
    parser.add_argument("--voice")
    parser.add_argument("--tts-rate")
    parser.add_argument("--tts-command")
    parser.add_argument("--tts-command-audio-format", choices=("wav", "mp3", "m4a", "aac", "flac"))
    parser.add_argument("--minimax-base-url")
    parser.add_argument("--minimax-api-key")
    parser.add_argument("--minimax-model")
    parser.add_argument("--minimax-speed", type=float)
    parser.add_argument("--minimax-volume", type=float)
    parser.add_argument("--minimax-pitch", type=float)
    parser.add_argument("--minimax-concurrency", type=int)
    parser.add_argument("--minimax-voice-clone", action="store_true")
    parser.add_argument("--cosyvoice-python")
    parser.add_argument("--cosyvoice-repo")
    parser.add_argument("--cosyvoice-model")
    parser.add_argument("--cosyvoice-mode", choices=("cross_lingual", "zero_shot"))
    parser.add_argument("--cosyvoice-group-size", type=int)
    parser.add_argument("--reference-audio")
    parser.add_argument("--reference-text")
    parser.add_argument("--original-volume", type=float)
    parser.add_argument("--dub-volume", type=float)
    parser.add_argument("--timing-mode", choices=("natural", "fit"))
    parser.add_argument("--min-playback-rate", type=float)
    parser.add_argument("--max-playback-rate", type=float)
    parser.add_argument("--max-segment-lag", type=float)
    parser.add_argument("--max-opening-silence", type=float)
    parser.add_argument("--max-global-shift", type=float)
    parser.add_argument("--min-segment-gap", type=float)
    parser.add_argument("--subtitle-font")
    parser.add_argument("--subtitle-font-path")
    parser.add_argument("--subtitle-size", type=int)
    parser.add_argument("--no-burn-subtitles", action="store_true")
    return parser.parse_args()


def _has_local_cosyvoice_assets(config: PipelineConfig) -> bool:
    repo_dir = (
        Path(config.cosyvoice_repo_dir).expanduser().resolve()
        if config.cosyvoice_repo_dir
        else Path(__file__).resolve().parents[1] / ".vendor" / "CosyVoice"
    )
    model_dir = (
        Path(config.cosyvoice_model_dir).expanduser().resolve()
        if config.cosyvoice_model_dir
        else repo_dir / "pretrained_models" / "Fun-CosyVoice3-0.5B"
    )
    return repo_dir.exists() and model_dir.exists()


if __name__ == "__main__":
    main()

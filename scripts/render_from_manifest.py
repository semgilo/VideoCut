from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videocut.config import PipelineConfig
from videocut.media import (
    compose_dubbed_track,
    ffprobe_duration,
    render_final_video,
    write_manifest,
)
from videocut.models import Segment
from videocut.subtitles import write_srt
from videocut.timing import plan_dubbing_timing
from videocut.tts import synthesize_segments


def main() -> None:
    args = _parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    config = PipelineConfig()
    config.tts_provider = args.tts_provider
    config.tts_voice = args.voice or config.tts_voice
    config.tts_rate = args.tts_rate or config.tts_rate
    config.cosyvoice_python = args.cosyvoice_python or config.cosyvoice_python
    config.cosyvoice_mode = args.cosyvoice_mode or config.cosyvoice_mode
    if args.cosyvoice_repo:
        config.cosyvoice_repo_dir = str(Path(args.cosyvoice_repo).expanduser().resolve())
    if args.cosyvoice_model:
        config.cosyvoice_model_dir = str(Path(args.cosyvoice_model).expanduser().resolve())
    if args.reference_audio:
        config.reference_audio_path = str(Path(args.reference_audio).expanduser().resolve())
    if args.reference_text:
        config.reference_text = args.reference_text
    config.original_audio_volume = args.original_volume
    config.dub_audio_volume = args.dub_volume
    config.max_playback_rate = args.max_playback_rate
    config.max_segment_lag = args.max_segment_lag
    config.max_opening_silence = args.max_opening_silence
    config.max_global_shift = args.max_global_shift
    config.min_segment_gap = args.min_segment_gap
    config.burn_subtitles = not args.no_burn_subtitles
    if args.subtitle_font:
        config.subtitle_font = args.subtitle_font
    if args.subtitle_size is not None:
        config.subtitle_font_size = args.subtitle_size

    source_video = Path(payload["source_video"]).expanduser().resolve()
    subtitle_source = payload.get("subtitle_source")
    subtitle_source_path = Path(subtitle_source).expanduser().resolve() if subtitle_source else None
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

    print(f"Rendering {len(segments)} manifest segments into {output_dir}")
    synthesize_segments(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=source_video,
    )
    for segment in segments:
        if segment.audio_path is None:
            raise RuntimeError(f"Segment {segment.index} did not produce audio")
        segment.synthetic_duration = ffprobe_duration(segment.audio_path)

    video_duration = ffprobe_duration(source_video)
    plan_dubbing_timing(
        segments=segments,
        video_duration=video_duration,
        max_opening_silence=config.max_opening_silence,
        max_global_shift=config.max_global_shift,
        min_segment_gap=config.min_segment_gap,
        max_playback_rate=config.max_playback_rate,
        max_segment_lag=config.max_segment_lag,
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
        subtitle_font_size=config.subtitle_font_size,
    )
    write_manifest(
        path=output_dir / "manifest.json",
        source_video=source_video,
        subtitle_source=subtitle_source_path,
        generated_srt=subtitle_path,
        dubbed_track=dubbed_track,
        final_video=final_video,
        segments=segments,
    )
    print(f"Final video: {final_video}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-render a VideoCut manifest with a different TTS setup.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tts-provider", choices=("edge", "cosyvoice"), required=True)
    parser.add_argument("--voice")
    parser.add_argument("--tts-rate")
    parser.add_argument("--cosyvoice-python")
    parser.add_argument("--cosyvoice-repo")
    parser.add_argument("--cosyvoice-model")
    parser.add_argument("--cosyvoice-mode", choices=("cross_lingual", "zero_shot"))
    parser.add_argument("--reference-audio")
    parser.add_argument("--reference-text")
    parser.add_argument("--original-volume", type=float, default=0.0)
    parser.add_argument("--dub-volume", type=float, default=1.0)
    parser.add_argument("--max-playback-rate", type=float, default=1.15)
    parser.add_argument("--max-segment-lag", type=float, default=1.0)
    parser.add_argument("--max-opening-silence", type=float, default=0.15)
    parser.add_argument("--max-global-shift", type=float, default=2.8)
    parser.add_argument("--min-segment-gap", type=float, default=0.03)
    parser.add_argument("--subtitle-font")
    parser.add_argument("--subtitle-size", type=int)
    parser.add_argument("--no-burn-subtitles", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()

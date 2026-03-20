#!/usr/bin/env python3
from __future__ import annotations

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
from videocut.models import Segment, VideoMetadata
from videocut.publish import export_publish_assets, load_video_metadata
from videocut.shell import run_command
from videocut.subtitles import load_segments_from_vtt, write_srt
from videocut.timing import plan_dubbing_timing, validate_source_segment_coverage
from videocut.tts import synthesize_segments


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN_DIR = REPO_ROOT / "runs" / "69XGdiMR17I-20260320"
RUN_DIR = REPO_ROOT / "runs" / "69XGdiMR17I-cosyvoice-fixed-v4"


SEGMENT_DATA: list[tuple[float, float, str, str]] = [
    (
        0.32,
        2.80,
        "OpenClaw plus Nvidia Nemotron 3 Super.",
        "OpenClaw 加 Nvidia Nemotron 3 Super。",
    ),
    (
        2.80,
        15.44,
        (
            "Plus Ollama is insane. "
            "Nvidia just released the 120-billion-parameter Nemotron 3 Super, "
            "and it runs free with OpenClaw. It also has a 256,000-token context window."
        ),
        (
            "再加上 Ollama，简直离谱。"
            "Nvidia 刚发布 1200 亿参数的 Nemotron 3 Super，"
            "在 OpenClaw 里就能免费跑，还带 25.6 万 token 上下文。"
        ),
    ),
    (
        15.44,
        20.72,
        (
            "It runs fast because only 12 billion parameters are active at a time, "
            "and it's built for AI agent applications."
        ),
        "它速度快，因为每次只激活 120 亿参数，而且就是为 AI 智能体应用打造的。",
    ),
    (
        20.72,
        29.84,
        (
            "Setup takes under 10 minutes. Copy one command into your terminal and "
            "OpenClaw connects to Nemotron 3 Super instantly."
        ),
        "配置不到 10 分钟。把一条命令复制进终端，OpenClaw 就会立刻连上 Nemotron 3 Super。",
    ),
    (
        29.84,
        38.16,
        (
            "Once it's running, your AI agent can live inside WhatsApp, Telegram, or Discord, "
            "browse the web, summarize news, and automate tasks."
        ),
        "跑起来后，AI 智能体能进驻 WhatsApp、Telegram、Discord，会上网、看新闻、自动干活。",
    ),
    (
        38.16,
        40.72,
        "No expensive API costs, no complex setup.",
        "不用贵 API，也不用复杂配置。",
    ),
    (
        40.72,
        47.51,
        (
            "Just copy, paste, and run. OpenClaw is the brain, Ollama is the engine."
        ),
        (
            "复制、粘贴、运行。OpenClaw 是大脑，Ollama 是引擎。"
        ),
    ),
    (
        47.51,
        53.44,
        (
            "Nemotron 3 Super is the intelligence. Stack them together and you've got "
            "a fully personal AI agent running for free on your own device."
        ),
        "Nemotron 3 Super 负责智能。三者合体，本机免费跑私有智能体。",
    ),
]


def main() -> None:
    source_dir = SOURCE_RUN_DIR / "source"
    source_video = next(source_dir.glob("*.mp4"))
    subtitle_source = next(source_dir.glob("*.en-orig.vtt"))
    thumbnail_source = next(source_dir.glob("*.jpg"))
    source_metadata = load_video_metadata(next(source_dir.glob("*.info.json")))

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tts_dir = RUN_DIR / "tts"
    subtitles_dir = RUN_DIR / "subtitles"
    audio_dir = RUN_DIR / "audio"
    reference_dir = RUN_DIR / "reference"
    tts_dir.mkdir(parents=True, exist_ok=True)
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)

    reference_audio = reference_dir / "prompt.wav"
    _extract_reference_audio(source_video, reference_audio)

    segments = [
        Segment(index=index, start=start, end=end, english=english, chinese=chinese)
        for index, (start, end, english, chinese) in enumerate(SEGMENT_DATA, start=1)
    ]
    source_segments = load_segments_from_vtt(subtitle_source)
    validate_source_segment_coverage(
        source_segments=source_segments,
        target_segments=segments,
        max_uncovered_duration=0.25,
    )

    localized_metadata = VideoMetadata(
        title="OpenClaw + Nvidia Nemotron 3 Super + Ollama，强到离谱！",
        description=(
            "想用 AI 赚钱、省时间？获取 AI 辅导、支持与课程 👉 "
            "https://www.skool.com/ai-profit-lab-7462/about\n\n"
            "视频笔记和工具链接 → https://www.skool.com/ai-profit-lab-7462/about\n\n"
            "获取免费 AI 课程 + 1000 个全新 AI Agents 👉 "
            "https://www.skool.com/ai-seo-with-julian-goldie-1553/about\n\n"
            "想知道我是怎么做这种视频的？加入 AI Profit Boardroom → "
            "https://www.skool.com/ai-profit-lab-7462/about\n\n"
            "免费 AI SEO 策略咨询：https://go.juliangoldie.com/strategy-session?utm=julian"
        ),
        tags=["OpenClaw", "Nemotron 3 Super", "Ollama", "AI Agent", "本地 AI"],
        uploader=source_metadata.uploader,
        channel=source_metadata.channel,
        video_id=source_metadata.video_id,
        webpage_url=source_metadata.webpage_url,
        upload_date=source_metadata.upload_date,
    )

    config = PipelineConfig(
        tts_provider="cosyvoice",
        cosyvoice_python=str(REPO_ROOT / ".venv-cosyvoice" / "bin" / "python"),
        cosyvoice_repo_dir=str(REPO_ROOT / ".vendor" / "CosyVoice"),
        cosyvoice_model_dir=str(
            REPO_ROOT / ".vendor" / "CosyVoice" / "pretrained_models" / "Fun-CosyVoice3-0.5B"
        ),
        cosyvoice_mode="cross_lingual",
        cosyvoice_group_size=1,
        reference_audio_path=str(reference_audio),
        burn_subtitles=True,
        subtitle_font_path="/System/Library/Fonts/Hiragino Sans GB.ttc",
        subtitle_font_size=18,
        original_audio_volume=0.0,
        dub_audio_volume=1.0,
        timing_mode="fit",
        min_playback_rate=0.75,
        max_playback_rate=1.6,
        max_segment_lag=0.25,
        max_opening_silence=2.8,
        max_global_shift=0.0,
        min_segment_gap=0.0,
    )

    print(f"Source video: {source_video}")
    print(f"Output dir: {RUN_DIR}")
    print(f"Reference audio: {reference_audio}")
    print(f"Segments: {len(segments)}")

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

    video_duration = ffprobe_duration(source_video)
    plan_dubbing_timing(
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
    first_spoken_at = min(segment.render_start for segment in segments)
    max_rate = max(segment.playback_rate for segment in segments)
    print(f"Dub timing scheduled: first speech at {first_spoken_at:.2f}s, max playback rate {max_rate:.2f}x")

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
        output_path=RUN_DIR / "final_cn.mp4",
        burn_subtitles=config.burn_subtitles,
        subtitle_font=config.subtitle_font,
        subtitle_font_path=config.subtitle_font_path,
        subtitle_font_size=config.subtitle_font_size,
    )
    publish_assets = export_publish_assets(
        output_dir=RUN_DIR,
        source_metadata=source_metadata,
        localized_metadata=localized_metadata,
        cover_image_path=thumbnail_source,
        final_video=final_video,
    )
    write_manifest(
        path=RUN_DIR / "manifest.json",
        source_video=source_video,
        subtitle_source=subtitle_source,
        thumbnail_source=thumbnail_source,
        generated_srt=subtitle_path,
        dubbed_track=dubbed_track,
        final_video=final_video,
        segments=segments,
        source_metadata=source_metadata,
        localized_metadata=localized_metadata,
        publish_assets=publish_assets,
    )
    print(f"Final video exported: {final_video}")


def _extract_reference_audio(source_video: Path, output_path: Path) -> None:
    if output_path.exists() and output_path.stat().st_size > 0:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "2.800",
            "-to",
            "10.800",
            "-i",
            str(source_video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )


if __name__ == "__main__":
    main()

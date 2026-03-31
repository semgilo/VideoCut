#!/usr/bin/env python3
"""合成字幕到视频"""
from pathlib import Path
from videocut.config import load_pipeline_config
from videocut.media import compose_dubbed_track, render_final_video

run_dir = Path("runs/UlC7pTdH_y4-subtitle-only-20260325")
source_dir = run_dir / "source"
subtitles_dir = run_dir / "subtitles"
audio_dir = run_dir / "audio"
audio_dir.mkdir(exist_ok=True)

config = load_pipeline_config()

video_path = next(source_dir.glob("*.mp4"))
srt_path = subtitles_dir / "zh.srt"
output_path = run_dir / config.output_name

print(f"视频: {video_path}")
print(f"字幕: {srt_path}")
print(f"输出: {output_path}")

original_track = compose_dubbed_track(
    video_path=video_path,
    segments=[],
    output_path=audio_dir / "original_audio.m4a",
    original_volume=1.0,
    dub_volume=0.0,
)
print(f"音频轨道: {original_track}")

print("开始合成字幕视频...")
final_video = render_final_video(
    video_path=video_path,
    dubbed_track_path=original_track,
    subtitle_path=srt_path,
    output_path=output_path,
    burn_subtitles=config.burn_subtitles,
    subtitle_font=config.subtitle_font,
    subtitle_font_path=config.subtitle_font_path,
    subtitle_font_size=config.subtitle_font_size,
)
print(f"✅ 完成: {final_video}")

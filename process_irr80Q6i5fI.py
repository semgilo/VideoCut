#!/usr/bin/env python3
"""处理 irr80Q6i5fI 视频 - 翻译 + 渲染 + 平台材料"""
import os
import sys
os.chdir('/Users/semgilo/Documents/git/VideoCut')

from pathlib import Path
from videocut.config import load_pipeline_config
from videocut.translate import OpenAICompatibleTranslator, load_protected_terms
from videocut.subtitles import load_segments_from_vtt, write_srt
from videocut.media import compose_dubbed_track, render_final_video, write_manifest, ffprobe_duration, ffprobe_video_size
from videocut.publish import export_publish_assets, load_video_metadata
from videocut.subtitle_only import _export_platform_kits, _collect_video_profile, _write_delivery_summary

run_dir = Path("runs/irr80Q6i5fI-final-20250326")
source_dir = run_dir / "source"
subtitles_dir = run_dir / "subtitles"
audio_dir = run_dir / "audio"
platforms_dir = run_dir / "platforms"

subtitles_dir.mkdir(exist_ok=True)
audio_dir.mkdir(exist_ok=True)

config = load_pipeline_config()

# 1. Load source files
vtt_file = next(source_dir.glob("*.en-orig.vtt"), None) or next(source_dir.glob("*.en.vtt"))
video_path = next(source_dir.glob("*.mp4"))
info_json = next(source_dir.glob("*.info.json"), None)
thumbnail = next(source_dir.glob("*.jpg"), None)
print(f"[1/5] 字幕文件: {vtt_file.name}")
print(f"      视频文件: {video_path.name}")

# 2. Load segments
from videocut.subtitles import load_segments_from_vtt
segments = load_segments_from_vtt(vtt_file)
print(f"[2/5] 字幕段数: {len(segments)}")

# 3. Translate
protected_terms = load_protected_terms(config.protected_terms_path)
translator = OpenAICompatibleTranslator(
    base_url=config.llm_base_url,
    api_key=config.llm_api_key,
    model=config.llm_model,
    timeout=config.llm_timeout,
    batch_size=config.translation_batch_size,
    concurrency=config.translation_concurrency,
    protected_terms=protected_terms,
)
print(f"[3/5] 开始翻译 {len(segments)} 段字幕 (batch={config.translation_batch_size}, concurrency={config.translation_concurrency})...")
translator.translate(segments)
srt_path = subtitles_dir / "zh.srt"
write_srt(srt_path, segments, bilingual=True)
print(f"      ✅ 字幕生成: {srt_path}")

# 4. Translate metadata
source_metadata = None
localized_metadata = None
if info_json:
    try:
        source_metadata = load_video_metadata(info_json)
        print(f"      标题: {source_metadata.title}")
        localized_metadata = translator.translate_metadata(source_metadata)
        print(f"      翻译标题: {localized_metadata.title}")
    except Exception as e:
        print(f"      Warning: metadata translation failed: {e}")

# 5. Render video
print(f"[4/5] 合成字幕视频...")
original_track = compose_dubbed_track(
    video_path=video_path,
    segments=[],
    output_path=audio_dir / "original_audio.m4a",
    original_volume=1.0,
    dub_volume=0.0,
)
output_path = run_dir / "final_subtitled.mp4"
final_video = render_final_video(
    video_path=video_path,
    dubbed_track_path=original_track,
    subtitle_path=srt_path,
    output_path=output_path,
    burn_subtitles=config.burn_subtitles,
    subtitle_font=config.subtitle_font,
    subtitle_font_path=config.subtitle_font_path,
    subtitle_font_size=config.subtitle_font_size,
    video_preset=config.video_preset,
    video_crf=config.video_crf,
    subtitle_overlay_concurrency=config.subtitle_overlay_concurrency,
)
print(f"      ✅ 视频生成: {final_video}")

# 6. Export platform materials
print(f"[5/5] 生成平台发布材料...")
publish_assets = export_publish_assets(
    output_dir=run_dir,
    source_metadata=source_metadata,
    localized_metadata=localized_metadata,
    cover_image_path=thumbnail,
    final_video=final_video,
)
video_profile = _collect_video_profile(final_video)
_export_platform_kits(
    output_dir=platforms_dir,
    final_video=final_video,
    subtitle_path=srt_path,
    publish_assets=publish_assets,
    source_metadata=source_metadata,
    localized_metadata=localized_metadata,
    video_profile=video_profile,
)
_write_delivery_summary(
    output_dir=run_dir,
    final_video=final_video,
    subtitle_path=srt_path,
    original_track=original_track,
    publish_assets=publish_assets,
    video_profile=video_profile,
)
write_manifest(
    path=run_dir / "manifest.json",
    source_video=video_path,
    subtitle_source=vtt_file,
    thumbnail_source=thumbnail,
    generated_srt=srt_path,
    dubbed_track=original_track,
    final_video=final_video,
    segments=segments,
    source_metadata=source_metadata,
    localized_metadata=localized_metadata,
    publish_assets=publish_assets,
)
print(f"\n✅ 全部完成！")
print(f"   视频: {final_video}")
print(f"   字幕: {srt_path}")
print(f"   平台材料: {platforms_dir}")
print(f"   manifest: {run_dir}/manifest.json")

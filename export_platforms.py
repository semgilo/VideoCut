#!/usr/bin/env python3
"""补全平台发布套件"""
import json
import math
from pathlib import Path
from videocut.config import load_pipeline_config
from videocut.downloader import load_video_metadata
from videocut.media import ffprobe_video_size, ffprobe_duration
from videocut.subtitle_only import (
    PLATFORM_SPECS,
    _collect_video_profile,
    _export_platform_kits,
    _derive_keywords,
    _build_cover_text,
    _evaluate_compatibility,
    _export_platform_cover_assets,
    _render_platform_requirements,
    _render_upload_checklist,
    _build_compression_command,
)

run_dir = Path("runs/UlC7pTdH_y4-subtitle-only-20260325")
source_dir = run_dir / "source"
platforms_dir = run_dir / "platforms"

# 加载元数据
info_json = next(source_dir.glob("*.info.json"))
source_metadata = load_video_metadata(info_json)

# 加载已翻译的发布材料
publish_dir = run_dir / "publish"
title_zh = (publish_dir / "title.txt").read_text(encoding='utf-8').strip()
desc_zh = (publish_dir / "description.txt").read_text(encoding='utf-8').strip()
tags_zh = [t.strip() for t in (publish_dir / "tags.txt").read_text(encoding='utf-8').split(',') if t.strip()]

localized_metadata = source_metadata
localized_metadata.title = title_zh
localized_metadata.description = desc_zh
localized_metadata.tags = tags_zh

# 视频信息
final_video = run_dir / "final_subtitled.mp4"
subtitle_path = run_dir / "subtitles" / "zh.srt"
video_profile = _collect_video_profile(final_video)

# 缩略图
cover_path = next(source_dir.glob("*.jpg"), None)
if cover_path:
    cover_path = str(cover_path)

# 构建 publish_assets
publish_assets = {
    "cover_image": cover_path,
    "title_text": str(publish_dir / "title.txt"),
    "tags_text": str(publish_dir / "tags.txt"),
    "description_text": str(publish_dir / "description.txt"),
}

# 导出平台套件
print("导出平台发布套件...")
_export_platform_kits(
    output_dir=platforms_dir,
    final_video=final_video,
    subtitle_path=subtitle_path,
    publish_assets=publish_assets,
    source_metadata=source_metadata,
    localized_metadata=localized_metadata,
    video_profile=video_profile,
)

# 列出结果
print("\n✅ 平台套件已生成:")
for platform_dir in platforms_dir.iterdir():
    if platform_dir.is_dir():
        print(f"\n{platform_dir.name}:")
        for f in sorted(platform_dir.iterdir()):
            print(f"  - {f.name}")

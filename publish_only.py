#!/usr/bin/env python3
"""生成发布材料"""
from pathlib import Path
from videocut.config import load_pipeline_config
from videocut.downloader import load_video_metadata
from videocut.publish import export_publish_assets
from deep_translator import GoogleTranslator

run_dir = Path("runs/UlC7pTdH_y4-subtitle-only-20260325")
source_dir = run_dir / "source"
publish_dir = run_dir / "publish"
publish_dir.mkdir(exist_ok=True)

# 加载元数据
info_json = next(source_dir.glob("*.info.json"))
metadata = load_video_metadata(info_json)

# 用 Google Translate 翻译元数据
translator = GoogleTranslator(source='en', target='zh-CN')

title_zh = translator.translate(metadata.title) if metadata.title else ""
desc_zh = translator.translate(metadata.description) if metadata.description else ""

# 简单标签转换
tags_zh = [translator.translate(tag) for tag in (metadata.tags or [])[:10]]

# 保存
(publish_dir / "title.txt").write_text(title_zh, encoding='utf-8')
(publish_dir / "description.txt").write_text(desc_zh, encoding='utf-8')
(publish_dir / "tags.txt").write_text(", ".join(tags_zh), encoding='utf-8')

print(f"标题: {title_zh[:50]}...")
print(f"标签: {', '.join(tags_zh[:5])}...")
print(f"✅ 发布材料已保存到 {publish_dir}")

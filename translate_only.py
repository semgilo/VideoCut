#!/usr/bin/env python3
"""直接翻译字幕，跳过下载步骤"""
from pathlib import Path
from videocut.config import load_pipeline_config
from videocut.translate import OpenAICompatibleTranslator, load_protected_terms
from videocut.subtitles import load_segments_from_vtt, write_srt

run_dir = Path("runs/UlC7pTdH_y4-subtitle-only-20260325")
source_dir = run_dir / "source"
subtitles_dir = run_dir / "subtitles"
subtitles_dir.mkdir(exist_ok=True)

config = load_pipeline_config()

vtt_file = next(source_dir.glob("*.en-orig.vtt"), None) or next(source_dir.glob("*.en.vtt"))
print(f"字幕文件: {vtt_file}")

segments = load_segments_from_vtt(vtt_file)
print(f"字幕段数: {len(segments)}")

protected_terms = load_protected_terms(config.protected_terms_path)
print(f"保护词: {len(protected_terms)}")

translator = OpenAICompatibleTranslator(
    base_url=config.llm_base_url,
    api_key=config.llm_api_key,
    model=config.llm_model,
    timeout=config.llm_timeout,
    batch_size=config.translation_batch_size,
    concurrency=config.translation_concurrency,
    protected_terms=protected_terms,
)

print("开始翻译...")
translator.translate(segments)

srt_path = subtitles_dir / "zh.srt"
write_srt(srt_path, segments)
print(f"✅ 字幕生成: {srt_path}")

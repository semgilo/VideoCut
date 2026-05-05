from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from deep_translator import GoogleTranslator

from videocut.asr import extract_audio_for_asr, transcribe_with_faster_whisper
from videocut.config import PipelineConfig
from videocut.downloader import download_youtube_assets
from videocut.media import (
    compose_dubbed_track,
    compress_for_publish,
    ffprobe_duration,
    ffprobe_video_size,
    render_final_video,
    write_manifest,
)
from videocut.models import Segment, VideoMetadata
from videocut.publish import export_publish_assets, load_video_metadata
from videocut.shell import stage
from videocut.subtitles import (
    load_chinese_segments_from_vtt,
    load_segments_from_vtt,
    overlay_chinese_from_vtt,
    overlay_english_from_vtt,
    write_srt,
)
from videocut.translate import (
    OpenAICompatibleTranslator,
    ensure_endpoint_reachable,
    load_protected_terms,
    llm_translation_enabled,
)



def run_subtitle_only_pipeline(
    url: str,
    config: PipelineConfig,
    workdir: Path | None = None,
) -> Path:
    run_dir = workdir.expanduser().resolve() if workdir else _default_run_dir(url, config.runs_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    source_dir = run_dir / "source"
    subtitles_dir = run_dir / "subtitles"
    audio_dir = run_dir / "audio"
    print(f"Working directory: {run_dir}")
    llm_enabled = llm_translation_enabled(
        base_url=config.llm_base_url,
        model=config.llm_model,
        api_key=config.llm_api_key,
    )
    if config.translation_backend == "llm" and not llm_enabled:
        raise RuntimeError("LLM translation was requested, but the configured endpoint is unavailable.")
    if llm_enabled and config.translation_backend != "google":
        try:
            ensure_endpoint_reachable(config.llm_base_url)
        except OSError as error:
            if config.translation_backend == "llm":
                raise RuntimeError(f"LLM endpoint is unreachable: {error}") from error
            print(f"Warning: LLM endpoint is unreachable, falling back to Google Translate: {error}")
            llm_enabled = False

    with stage("download-youtube-assets"):
        download = download_youtube_assets(
            url,
            source_dir,
            include_chinese_subtitles=False,
        )
        _recover_download_artifacts(download, source_dir)
        print(f"Downloaded source video: {download.video_path}")

    with stage("load-or-build-segments"):
        prefer_translation = download.english_subtitle_path is not None
        segments = _load_or_build_segments(download, config, prefer_translation)
        print(f"Subtitle segments ready: {len(segments)}")

    if len(segments) == 0:
        raise RuntimeError(
            "Failed to generate any subtitle segments. "
            "The video has no embedded subtitle track and automatic speech recognition (ASR) "
            "produced no results. Cannot proceed with subtitle burn-in."
        )

    translator: OpenAICompatibleTranslator | None = None
    subtitle_translation_backend = "none"
    protected_terms = load_protected_terms(config.protected_terms_path)
    if protected_terms:
        print(
            f"Loaded {len(protected_terms)} protected terms from {config.protected_terms_path}"
        )
    with stage("translate-subtitles"):
        if not _segments_need_translation(segments):
            print("Subtitle source is already Chinese. Skipping subtitle translation.")
        elif config.translation_backend == "google":
            print("Translating subtitles with Google Translate fallback...")
            _translate_segments_with_google(segments)
            subtitle_translation_backend = "google"
        elif llm_enabled:
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
            print("Translating subtitles to Simplified Chinese...")
            try:
                translator.translate(segments)
                subtitle_translation_backend = "llm"
            except Exception as error:
                if config.translation_backend == "llm":
                    raise
                print(f"Warning: LLM translation failed, falling back to Google Translate: {error}")
                _translate_segments_with_google(segments)
                subtitle_translation_backend = "google"
        elif download.chinese_subtitle_path is not None:
            if download.english_subtitle_path is not None and any(segment.english for segment in segments):
                print(
                    "No LLM endpoint configured. Reusing YouTube Chinese subtitle track for final subtitles."
                )
                overlay_chinese_from_vtt(segments, download.chinese_subtitle_path)
            else:
                print("Using downloaded Chinese subtitle track directly.")
            subtitle_translation_backend = "native_zh"
        else:
            print("No LLM endpoint or Chinese subtitle track available. Falling back to Google Translate.")
            _translate_segments_with_google(segments)
            subtitle_translation_backend = "google"

    with stage("translate-metadata"):
        localized_metadata = download.source_metadata
        if (
            download.source_metadata is not None
            and subtitle_translation_backend == "llm"
            and translator is not None
        ):
            print("Translating title, description, and tags...")
            try:
                localized_metadata = _translate_metadata_with_llm(translator, download.source_metadata)
            except Exception as error:
                if config.translation_backend == "llm":
                    raise
                print(f"Warning: metadata LLM translation failed, falling back to Google Translate: {error}")
                localized_metadata = _translate_metadata_with_google(download.source_metadata)
        elif download.source_metadata is not None and (
            config.translation_backend == "google" or subtitle_translation_backend == "google"
        ):
            print("Translating title, description, and tags with Google Translate fallback...")
            localized_metadata = _translate_metadata_with_google(download.source_metadata)
        elif download.source_metadata is None:
            print("Warning: no source metadata was downloaded.")

    with stage("write-srt"):
        generated_srt = subtitles_dir / "zh.srt"
        write_srt(generated_srt, segments, bilingual=True)
        print(f"Bilingual subtitle file generated: {generated_srt}")

    with stage("compose-original-audio"):
        original_track = compose_dubbed_track(
            video_path=download.video_path,
            segments=[],
            output_path=audio_dir / "original_audio.m4a",
            original_volume=1.0,
            dub_volume=0.0,
        )
        print(f"Original audio track exported: {original_track}")

    with stage("render-final-video"):
        final_video = render_final_video(
            video_path=download.video_path,
            dubbed_track_path=original_track,
            subtitle_path=generated_srt,
            output_path=run_dir / config.output_name,
            burn_subtitles=config.burn_subtitles,
            subtitle_font=config.subtitle_font,
            subtitle_font_path=config.subtitle_font_path,
            subtitle_font_size=config.subtitle_font_size,
            video_preset=config.video_preset,
            video_crf=config.video_crf,
            subtitle_overlay_concurrency=config.subtitle_overlay_concurrency,
        )
        print(f"Final subtitle-only video exported: {final_video}")

    with stage("compress-for-publish"):
        if config.compress_to_max_mb > 0:
            compressed_path = run_dir / "final_compressed.mp4"
            compress_for_publish(
                input_path=final_video,
                output_path=compressed_path,
                target_size_mb=config.compress_to_max_mb,
                max_width=1920,
                max_height=1080,
            )
            final_video.unlink()
            final_video = compressed_path
            print(f"Compressed for publish ({config.compress_to_max_mb}MB target): {final_video.name}")
        else:
            print("Skipping compression (compress_to_max_mb is 0).")

    with stage("export-publish-assets"):
        publish_assets = export_publish_assets(
            output_dir=run_dir,
            source_metadata=download.source_metadata,
            localized_metadata=localized_metadata,
            cover_image_path=download.thumbnail_path,
            final_video=final_video,
        )

    with stage("write-delivery-summary"):
        video_profile = _collect_video_profile(final_video)
        _write_delivery_summary(
            output_dir=run_dir,
            final_video=final_video,
            subtitle_path=generated_srt,
            original_track=original_track,
            publish_assets=publish_assets,
            video_profile=video_profile,
        )

    write_manifest(
        path=run_dir / "manifest.json",
        source_video=download.video_path,
        subtitle_source=download.english_subtitle_path or download.chinese_subtitle_path,
        thumbnail_source=download.thumbnail_path,
        generated_srt=generated_srt,
        dubbed_track=original_track,
        final_video=final_video,
        segments=segments,
        source_metadata=download.source_metadata,
        localized_metadata=localized_metadata,
        publish_assets=publish_assets,
    )
    print(f"Manifest written to {run_dir / 'manifest.json'}")

    with stage("cleanup-intermediate-files"):
        if config.cleanup_source_after_publish:
            _cleanup_intermediate_files(run_dir)

    return final_video


def _default_run_dir(url: str, runs_dir: Path) -> Path:
    video_id = _extract_video_id(url)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (runs_dir / f"mc-{stamp}").resolve()


def _extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/")
        if video_id:
            return video_id
    query = parse_qs(parsed.query)
    candidate = query.get("v", [""])[0].strip()
    if candidate:
        return candidate
    compact = re.sub(r"[^A-Za-z0-9_-]+", "-", url).strip("-")
    return compact or "video"


def _load_or_build_segments(
    download,
    config: PipelineConfig,
    prefer_translation: bool,
) -> list[Segment]:
    english_segments = None
    chinese_segments = None
    if download.english_subtitle_path is not None:
        print(f"English subtitle track found: {download.english_subtitle_path}")
        english_segments = load_segments_from_vtt(download.english_subtitle_path)
    if download.chinese_subtitle_path is not None:
        print(f"Chinese subtitle track found: {download.chinese_subtitle_path}")
        chinese_segments = load_chinese_segments_from_vtt(download.chinese_subtitle_path)

    if prefer_translation and english_segments is not None:
        return english_segments

    if chinese_segments is not None:
        if english_segments is not None and download.english_subtitle_path is not None:
            overlay_english_from_vtt(chinese_segments, download.english_subtitle_path)
        return chinese_segments

    if english_segments is not None:
        return english_segments

    print("No subtitle track found. Falling back to faster-whisper ASR.")
    extracted_audio = extract_audio_for_asr(download.video_path, download.video_path.parent / "source_audio.wav")
    return transcribe_with_faster_whisper(
        extracted_audio,
        model_name=config.asr_model,
        device=config.asr_device,
        compute_type=config.asr_compute_type,
    )


def _recover_download_artifacts(download, source_dir: Path) -> None:
    if getattr(download, "info_json_path", None) is None:
        candidates = sorted(source_dir.glob("*.info.json"))
        if candidates:
            download.info_json_path = candidates[-1]
    if getattr(download, "thumbnail_path", None) is None:
        image_candidates = []
        for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            image_candidates.extend(source_dir.glob(pattern))
        image_candidates = sorted(path for path in image_candidates if path.is_file())
        if image_candidates:
            download.thumbnail_path = image_candidates[-1]
    if getattr(download, "source_metadata", None) is None and getattr(download, "info_json_path", None) is not None:
        try:
            download.source_metadata = load_video_metadata(download.info_json_path)
        except Exception as error:
            print(f"Warning: failed to recover source metadata from info.json: {error}")


def _segments_need_translation(segments: list[Segment]) -> bool:
    return any(segment.english.strip() for segment in segments)


def _translate_segments_with_google(segments: list[Segment]) -> None:
    translator = GoogleTranslator(source="en", target="zh-CN")
    batch_size = 25
    total = len(segments)
    translated = 0
    for start_index in range(0, total, batch_size):
        batch = segments[start_index : start_index + batch_size]
        texts = [segment.english for segment in batch]
        for attempt in range(3):
            try:
                results = translator.translate_batch(texts)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        for segment, chinese in zip(batch, results, strict=False):
            segment.chinese = _normalize_space(chinese or segment.english)
        translated += len(batch)
        print(f"Translated {translated}/{total} segments (Google)")


def _translate_metadata_with_google(metadata: VideoMetadata) -> VideoMetadata:
    translator = GoogleTranslator(source="en", target="zh-CN")
    tags = metadata.tags[:]
    translated_tags: list[str] = []
    if tags:
        try:
            translated_tags = [tag.strip() for tag in translator.translate_batch(tags) if tag.strip()]
        except Exception:
            translated_tags = tags
    return VideoMetadata(
        title=_normalize_space(translator.translate(metadata.title)) if metadata.title else metadata.title,
        description=(
            _normalize_space(translator.translate(metadata.description))
            if metadata.description
            else metadata.description
        ),
        tags=translated_tags or tags,
        uploader=metadata.uploader,
        channel=metadata.channel,
        video_id=metadata.video_id,
        webpage_url=metadata.webpage_url,
        upload_date=metadata.upload_date,
    )


def _translate_metadata_with_llm(
    translator: OpenAICompatibleTranslator,
    metadata: VideoMetadata,
) -> VideoMetadata:
    def _translate_field(field_name: str, text: str) -> str:
        if not text.strip():
            return text
        try:
            return translator._translate_single_metadata_field(field_name, text)
        except Exception as error:
            print(f"Warning: metadata field '{field_name}' translation failed, keeping original: {error}")
            return text

    translated_tags: list[str] = []
    for tag in metadata.tags:
        normalized = str(tag).strip()
        if not normalized:
            continue
        translated_tags.append(_translate_field("tag", normalized))

    title = _translate_field("title", metadata.title)
    try:
        description = translator._generate_description(title, translated_tags)
    except Exception as error:
        print(f"Warning: description generation failed: {error}")
        description = ""

    return VideoMetadata(
        title=title,
        description=description,
        tags=translated_tags,
        uploader=metadata.uploader,
        channel=metadata.channel,
        video_id=metadata.video_id,
        webpage_url=metadata.webpage_url,
        upload_date=metadata.upload_date,
    )


def _cleanup_intermediate_files(run_dir: Path) -> None:
    import json
    import shutil

    for dir_name in ("source", "audio", "platforms", "subtitles"):
        target = run_dir / dir_name
        if target.exists():
            shutil.rmtree(target)
            print(f"Cleaned up: {target}")

    summary = run_dir / "delivery_summary.md"
    if summary.exists():
        summary.unlink()
        print(f"Cleaned up: {summary}")

    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pruned = {
            "final_video": manifest.get("final_video"),
            "publish_assets": manifest.get("publish_assets", {}),
            "source_metadata": manifest.get("source_metadata"),
            "localized_metadata": manifest.get("localized_metadata"),
        }
        manifest_path.write_text(json.dumps(pruned, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Pruned manifest.json")

    print("Intermediate files have been cleaned up.")


def _collect_video_profile(video_path: Path) -> dict[str, object]:
    width, height = ffprobe_video_size(video_path)
    duration_seconds = ffprobe_duration(video_path)
    gcd_value = math.gcd(width, height)
    aspect_ratio = f"{width // gcd_value}:{height // gcd_value}"
    size_bytes = video_path.stat().st_size
    return {
        "path": str(video_path),
        "width": width,
        "height": height,
        "aspect_ratio": aspect_ratio,
        "duration_seconds": round(duration_seconds, 3),
        "duration_text": _format_duration(duration_seconds),
        "size_bytes": size_bytes,
        "size_text": _format_bytes(size_bytes),
        "format": video_path.suffix.lstrip(".").lower(),
    }


def _write_delivery_summary(
    output_dir: Path,
    final_video: Path,
    subtitle_path: Path,
    original_track: Path,
    publish_assets: dict[str, str | None],
    video_profile: dict[str, object],
) -> None:
    lines = [
        "# 交付摘要",
        "",
        f"- 字幕版成片：`{final_video}`",
        f"- 中文字幕文件：`{subtitle_path}`",
        f"- 原声音轨：`{original_track}`",
        f"- 成片信息：`{video_profile['width']}x{video_profile['height']}` `{video_profile['aspect_ratio']}` `{video_profile['duration_text']}` `{video_profile['size_text']}`",
        f"- 通用标题素材：`{publish_assets.get('title_text')}`",
        f"- 通用简介素材：`{publish_assets.get('description_text')}`",
        f"- 通用标签素材：`{publish_assets.get('tags_text')}`",
        f"- 通用预览页：`{publish_assets.get('preview_html')}`",
    ]
    (output_dir / "delivery_summary.md").write_text("\n".join(lines), encoding="utf-8")



def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()





def _truncate_text(text: str, limit: int) -> str:
    text = _normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    total_seconds = int(round(float(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    suffixes = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for suffix in suffixes:
        if amount < 1024 or suffix == suffixes[-1]:
            if suffix == "B":
                return f"{int(amount)}{suffix}"
            return f"{amount:.2f}{suffix}"
        amount /= 1024
    return f"{value}B"

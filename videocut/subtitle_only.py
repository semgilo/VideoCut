from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from deep_translator import GoogleTranslator
from PIL import Image, ImageEnhance, ImageFilter

from videocut.asr import extract_audio_for_asr, transcribe_with_faster_whisper
from videocut.config import PipelineConfig
from videocut.downloader import download_youtube_assets
from videocut.media import (
    compose_dubbed_track,
    ffprobe_duration,
    ffprobe_video_size,
    render_final_video,
    write_manifest,
)
from videocut.models import Segment, VideoMetadata
from videocut.publish import export_publish_assets, load_video_metadata
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


@dataclass(slots=True)
class PlatformSpec:
    slug: str
    display_name: str
    format_allowlist: tuple[str, ...]
    max_size_bytes: int | None
    max_duration_seconds: int | None
    preferred_aspect: str
    preferred_resolution: str
    cover_recommendation: str
    requirements_summary: list[str]
    publishing_notes: list[str]
    sources: list[dict[str, str]]
    conservative: bool = False
    cover_size: tuple[int, int] = (1280, 720)


@dataclass(slots=True)
class MaterialEvidence:
    base_title: str
    summary: str
    highlights: list[str]
    keywords: list[str]


PLATFORM_SPECS = {
    "douyin": PlatformSpec(
        slug="douyin",
        display_name="抖音",
        format_allowlist=("mp4", "webm"),
        max_size_bytes=4 * 1024 * 1024 * 1024,
        max_duration_seconds=15 * 60,
        preferred_aspect="9:16",
        preferred_resolution="720p（1280x720）及以上",
        cover_recommendation="竖版封面，大字标题放中上区域，主体尽量居中。",
        requirements_summary=[
            "官方开放平台上传文档写明：视频总大小需控制在 4GB 以内。",
            "超过 50MB 的视频建议分片上传；超过 300MB 的视频必须分片上传。",
            "支持常用视频格式，文档明确推荐 mp4、webm。",
            "同一页面还给出了 720p 及以上、竖版短视频优先的观看建议。",
        ],
        publishing_notes=[
            "官方页面把“16:9”“1280x720”和“竖版视频”写在同一句里，表述有冲突。因为页面明确出现了“竖版视频”，本材料按 9:16 竖屏优先来理解；这是基于原文的合理推断。",
            "横版视频仍可准备上传，但并不是抖音原生分发最偏好的画面方向。",
        ],
        sources=[
            {
                "label": "抖音开放平台 - 上传视频",
                "url": "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/upload-video",
                "kind": "官方",
            }
        ],
        cover_size=(1080, 1920),
    ),
    "bilibili": PlatformSpec(
        slug="bilibili",
        display_name="Bilibili",
        format_allowlist=("mp4", "mov", "flv", "avi", "wmv", "webm", "mpeg4"),
        max_size_bytes=8 * 1024 * 1024 * 1024,
        max_duration_seconds=None,
        preferred_aspect="16:9",
        preferred_resolution="1080p 及以上",
        cover_recommendation="横版封面，10-16 字主标题，主体清晰，重点信息直接上封面。",
        requirements_summary=[
            "Bilibili 官方专栏写明：4K 投稿已全面开放，最高支持 4K/120FPS。",
            "同一官方专栏给出 4K 参数建议：H264/AVC 码率 20000 kbps、峰值码率不超过 60000 kbps、AAC 音频最高 320 kbps、最大分辨率 4096x4096。",
            "公开创作向资料普遍把 mp4/H.264/AAC 视为最稳妥的投稿组合，并提到单文件 8GB 这一常见上限。",
        ],
        publishing_notes=[
            "文件大小上限这里采用的是公开实操资料，而不是当前可直接访问到的静态官方帮助页。",
            "在这批平台里，当前这份横版、只加字幕的成片与 Bilibili 的观看习惯最匹配。",
        ],
        sources=[
            {
                "label": "Bilibili专栏 - 最高4K120帧！B站全面开放4K视频投稿",
                "url": "https://www.bilibili.com/read/cv6230364/",
                "kind": "官方",
            },
            {
                "label": "CSDN - B站上传视频时各分辨率最佳的码率及格式参数",
                "url": "https://blog.csdn.net/yufeiluo/article/details/139549229",
                "kind": "公开资料",
            },
            {
                "label": "PHP中文网 - B站上传视频格式要求",
                "url": "https://www.php.cn/faq/2104021.html",
                "kind": "公开资料",
            },
        ],
        cover_size=(1280, 720),
    ),
    "xiaohongshu": PlatformSpec(
        slug="xiaohongshu",
        display_name="小红书",
        format_allowlist=("mp4", "mov"),
        max_size_bytes=1 * 1024 * 1024 * 1024,
        max_duration_seconds=15 * 60,
        preferred_aspect="9:16 优先，3:4 兼容",
        preferred_resolution="1080x1920 优先，1080x1440 兼容",
        cover_recommendation="优先 3:4 封面图，建议画布约 1242x1660，标题放中上安全区。",
        requirements_summary=[
            "公开创作资料普遍把 9:16 视为首选比例，3:4 视为信息流展示更稳妥的兼容比例。",
            "公开设计参考常用 1080x1920 作为竖版视频尺寸、1080x1440 作为 3:4 尺寸、1242x1660 作为封面图尺寸。",
            "当前公开实操资料里，15 分钟是最常见的视频上传时长上限口径。",
            "新榜公开资料写到支持 1080P/720P/480P 且大小限制 1GB。本材料按 1GB 这个更保守的值来判断，因为公开资料之间存在差异。",
        ],
        publishing_notes=[
            "我没有找到一个无需登录即可稳定访问的当前官方静态上传规格页，所以这里采用近期公开创作者资料，并明确标注为公开口径。",
            "这次按你的要求不额外做平台专用视频版本，所以小红书目录里给的是封面和摆位建议，不会另做竖版重剪。",
        ],
        sources=[
            {
                "label": "新榜小豆芽 - 小红书视频发布尺寸大小设置全攻略",
                "url": "https://d.newrank.cn/creative/4512",
                "kind": "公开资料",
            },
            {
                "label": "PHP中文网 - 小红书视频比例要多少",
                "url": "https://www.php.cn/faq/2109163.html",
                "kind": "公开资料",
            },
            {
                "label": "晓观点 - 小红书最长能传15分钟还是30分钟",
                "url": "https://insight.xiaoduoai.com/commerce-knowledge/xiaohongshu-information/xiaohongshu-can-upload-videos-up-to-15-minutes-or-30-minutes-where-is-the-upload-button-hidden-official-duration-limit-analysis-complete-guide-to-upload-entry-even-newbies-can-get-started-quickly.html",
                "kind": "公开资料",
            },
        ],
        conservative=True,
        cover_size=(1242, 1660),
    ),
    "tecent": PlatformSpec(
        slug="tecent",
        display_name="微信视频号",
        format_allowlist=("mp4", "mov"),
        max_size_bytes=2 * 1024 * 1024 * 1024,
        max_duration_seconds=60 * 60,
        preferred_aspect="16:9 / 9:16 兼容",
        preferred_resolution="1080p 优先（横竖屏均可）",
        cover_recommendation="封面标题控制在 12-16 字，主体居中，避免边缘信息被裁切。",
        requirements_summary=[
            "视频号支持横屏和竖屏内容，优先保证 1080p 清晰度与字幕可读性。",
            "素材建议使用 mp4（H.264/AAC）或 mov，降低转码失败概率。",
            "封面建议保持主体居中，标题尽量简洁，避免边缘信息被裁切。",
            "平台规则会动态调整，最终以上传页面实时提示为准。",
        ],
        publishing_notes=[
            "视频号审核更看重封面与首屏信息密度，建议标题直接点出核心价值。",
            "这次仍使用共享成片，不额外生成视频号专用重剪版本。",
        ],
        sources=[
            {
                "label": "微信视频号创作中心",
                "url": "https://channels.weixin.qq.com/platform",
                "kind": "官方入口",
            }
        ],
        conservative=True,
        cover_size=(1080, 1260),
    ),
}

PLATFORM_TITLE_LIMITS = {
    "douyin": 36,
    "bilibili": 60,
    "xiaohongshu": 50,
    "tecent": 30,
}

PLATFORM_HASHTAG_LIMITS = {
    "douyin": 5,
    "bilibili": 6,
    "xiaohongshu": 10,
    "tecent": 8,
}


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
    platforms_dir = run_dir / "platforms"

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

    download = download_youtube_assets(
        url,
        source_dir,
        include_chinese_subtitles=False,
    )
    _recover_download_artifacts(download, source_dir)
    print(f"Downloaded source video: {download.video_path}")

    prefer_translation = download.english_subtitle_path is not None
    segments = _load_or_build_segments(download, config, prefer_translation)
    print(f"Subtitle segments ready: {len(segments)}")

    translator: OpenAICompatibleTranslator | None = None
    subtitle_translation_backend = "none"
    protected_terms = load_protected_terms(config.protected_terms_path)
    if protected_terms:
        print(
            f"Loaded {len(protected_terms)} protected terms from {config.protected_terms_path}"
        )
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

    generated_srt = subtitles_dir / "zh.srt"
    write_srt(generated_srt, segments, bilingual=True)
    print(f"Bilingual subtitle file generated: {generated_srt}")

    original_track = compose_dubbed_track(
        video_path=download.video_path,
        segments=[],
        output_path=audio_dir / "original_audio.m4a",
        original_volume=1.0,
        dub_volume=0.0,
    )
    print(f"Original audio track exported: {original_track}")

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

    publish_assets = export_publish_assets(
        output_dir=run_dir,
        source_metadata=download.source_metadata,
        localized_metadata=localized_metadata,
        cover_image_path=download.thumbnail_path,
        final_video=final_video,
    )

    video_profile = _collect_video_profile(final_video)
    if config.export_platform_materials:
        _export_platform_kits(
            output_dir=platforms_dir,
            final_video=final_video,
            subtitle_path=generated_srt,
            subtitle_segments=segments,
            publish_assets=publish_assets,
            source_metadata=download.source_metadata,
            localized_metadata=localized_metadata,
            video_profile=video_profile,
        )
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
    return final_video


def _default_run_dir(url: str, runs_dir: Path) -> Path:
    video_id = _extract_video_id(url)
    stamp = datetime.now().strftime("%Y%m%d")
    return (runs_dir / f"{video_id}-subtitle-only-{stamp}").resolve()


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

    return VideoMetadata(
        title=_translate_field("title", metadata.title),
        description=_translate_field("description", metadata.description),
        tags=translated_tags,
        uploader=metadata.uploader,
        channel=metadata.channel,
        video_id=metadata.video_id,
        webpage_url=metadata.webpage_url,
        upload_date=metadata.upload_date,
    )


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


def _export_platform_kits(
    output_dir: Path,
    final_video: Path,
    subtitle_path: Path,
    publish_assets: dict[str, str | None],
    source_metadata: VideoMetadata | None,
    localized_metadata: VideoMetadata | None,
    video_profile: dict[str, object],
    subtitle_segments: list[Segment] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_metadata = localized_metadata or source_metadata or VideoMetadata(title="未命名视频")
    evidence = _build_material_evidence(
        base_metadata=base_metadata,
        source_metadata=source_metadata,
        subtitle_segments=subtitle_segments,
        subtitle_path=subtitle_path,
    )
    keyword_pool = evidence.keywords
    cover_path = publish_assets.get("cover_image")

    for slug, spec in PLATFORM_SPECS.items():
        platform_dir = output_dir / slug
        platform_dir.mkdir(parents=True, exist_ok=True)

        title = _build_platform_title(spec, evidence.base_title)
        hashtags = _build_hashtags(spec, keyword_pool)
        description = _build_platform_description(
            spec=spec,
            title=title,
            source_metadata=source_metadata,
            evidence=evidence,
            hashtags=hashtags,
        )
        cover_text = _build_cover_text(evidence.base_title, spec)
        compatibility = _evaluate_compatibility(spec, video_profile)
        generated_cover_path = _export_platform_cover_assets(
            spec=spec,
            platform_dir=platform_dir,
            shared_cover_path=Path(cover_path) if cover_path else None,
        )

        (platform_dir / "title.txt").write_text(f"{title}\n", encoding="utf-8")
        (platform_dir / "description.txt").write_text(description, encoding="utf-8")
        (platform_dir / "hashtags.txt").write_text("\n".join(hashtags) + "\n", encoding="utf-8")
        (platform_dir / "cover_text.txt").write_text(f"{cover_text}\n", encoding="utf-8")
        (platform_dir / "requirements.md").write_text(
            _render_platform_requirements(
                spec=spec,
                compatibility=compatibility,
                video_profile=video_profile,
                source_metadata=source_metadata,
                final_video=final_video,
                subtitle_path=subtitle_path,
                cover_path=cover_path,
            ),
            encoding="utf-8",
        )
        (platform_dir / "upload_checklist.md").write_text(
            _render_upload_checklist(spec, compatibility),
            encoding="utf-8",
        )

        manifest_payload = {
            "platform": slug,
            "display_name": spec.display_name,
            "shared_final_video": str(final_video),
            "shared_subtitle": str(subtitle_path),
            "shared_cover": cover_path,
            "platform_cover": str(generated_cover_path) if generated_cover_path else None,
            "compatibility": compatibility,
            "title": title,
            "hashtags": hashtags,
        }
        (platform_dir / "asset_refs.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if compatibility["needs_compression"]:
            (platform_dir / "compression_ffmpeg.sh").write_text(
                _build_compression_command(final_video, slug, spec.max_size_bytes),
                encoding="utf-8",
            )

    (output_dir / "README.md").write_text(
        _render_platform_index(output_dir, video_profile),
        encoding="utf-8",
    )


def _build_material_evidence(
    base_metadata: VideoMetadata,
    source_metadata: VideoMetadata | None,
    subtitle_segments: list[Segment] | None,
    subtitle_path: Path,
) -> MaterialEvidence:
    subtitle_lines = _collect_subtitle_lines(subtitle_segments, subtitle_path)
    highlights = _pick_subtitle_highlights(subtitle_lines, max_items=3)
    summary = _pick_summary_line(base_metadata, source_metadata, highlights)
    base_title = _resolve_base_title(base_metadata, source_metadata, highlights)
    keywords = _derive_keywords(base_metadata, source_metadata, subtitle_lines)
    return MaterialEvidence(
        base_title=base_title,
        summary=summary,
        highlights=highlights,
        keywords=keywords,
    )


def _resolve_base_title(
    base_metadata: VideoMetadata,
    source_metadata: VideoMetadata | None,
    highlights: list[str],
) -> str:
    for candidate in (
        base_metadata.title,
        source_metadata.title if source_metadata is not None else "",
    ):
        normalized = _sanitize_base_title(candidate)
        if normalized:
            return normalized
    for line in highlights:
        normalized = _sanitize_base_title(line)
        if normalized:
            return _truncate_text(normalized, 42)
    return "未命名视频"


def _pick_summary_line(
    base_metadata: VideoMetadata,
    source_metadata: VideoMetadata | None,
    highlights: list[str],
) -> str:
    for candidate in (
        _first_sentence(base_metadata.description),
        _first_sentence(source_metadata.description if source_metadata is not None else ""),
    ):
        if candidate:
            return candidate
    if highlights:
        return highlights[0]
    return ""


def _collect_subtitle_lines(
    subtitle_segments: list[Segment] | None,
    subtitle_path: Path,
    max_lines: int = 320,
) -> list[str]:
    raw_lines: list[str] = []
    if subtitle_segments:
        for segment in subtitle_segments:
            text = _normalize_space(segment.chinese or segment.english)
            if _subtitle_line_usable(text):
                raw_lines.append(text)
    elif subtitle_path.exists() and subtitle_path.suffix.lower() == ".srt":
        raw_lines.extend(_load_subtitle_lines_from_srt(subtitle_path))

    deduped: list[str] = []
    seen: set[str] = set()
    for line in raw_lines:
        normalized = _normalize_space(line)
        if not _subtitle_line_usable(normalized):
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
        if len(deduped) >= max_lines:
            break
    return deduped


def _load_subtitle_lines_from_srt(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", content)
    lines: list[str] = []
    for block in blocks:
        raw_lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not raw_lines:
            continue
        timestamp_index = next((index for index, line in enumerate(raw_lines) if "-->" in line), -1)
        if timestamp_index < 0:
            continue
        text_lines = [_normalize_space(line) for line in raw_lines[timestamp_index + 1 :] if _normalize_space(line)]
        if not text_lines:
            continue
        chinese_candidates = [line for line in text_lines if _contains_cjk(line)]
        candidate = chinese_candidates[0] if chinese_candidates else text_lines[0]
        if _subtitle_line_usable(candidate):
            lines.append(candidate)
    return lines


def _subtitle_line_usable(text: str) -> bool:
    cleaned = _normalize_space(text).strip("-*• ")
    if not cleaned:
        return False
    if re.fullmatch(r"[\d\W_]+", cleaned):
        return False
    if _contains_cjk(cleaned):
        return len(cleaned) >= 4
    return len(cleaned) >= 12


def _pick_subtitle_highlights(lines: list[str], max_items: int) -> list[str]:
    if max_items <= 0:
        return []
    candidates = [_truncate_text(line, 80) for line in lines if _subtitle_line_usable(line)]
    if not candidates:
        return []
    if len(candidates) <= max_items:
        return candidates

    selected: list[str] = []
    if max_items == 1:
        selected.append(candidates[len(candidates) // 2])
    else:
        for i in range(max_items):
            index = int(round(i * (len(candidates) - 1) / (max_items - 1)))
            candidate = candidates[index]
            if candidate not in selected:
                selected.append(candidate)
    for candidate in candidates:
        if len(selected) >= max_items:
            break
        if candidate not in selected:
            selected.append(candidate)
    return selected[:max_items]


def _derive_keywords(
    localized_metadata: VideoMetadata,
    source_metadata: VideoMetadata | None,
    subtitle_lines: list[str],
) -> list[str]:
    keywords: list[str] = []
    keyword_sources = [
        *localized_metadata.tags,
        *(source_metadata.tags if source_metadata is not None else []),
        *_extract_title_keywords(localized_metadata.title or source_metadata.title if source_metadata else ""),
        *_extract_subtitle_keywords(subtitle_lines),
    ]
    for item in keyword_sources:
        normalized = _normalize_keyword(item)
        if normalized:
            keywords.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        lowered = keyword.lower()
        if lowered in seen:
            continue
        deduped.append(keyword)
        seen.add(lowered)
    return deduped[:18]


def _extract_subtitle_keywords(lines: list[str], limit: int = 18) -> list[str]:
    if not lines:
        return []
    latin_stopwords = {
        "the",
        "and",
        "that",
        "this",
        "with",
        "for",
        "from",
        "your",
        "you",
        "are",
        "was",
        "have",
        "has",
        "not",
        "just",
        "what",
        "when",
        "where",
        "how",
        "into",
        "then",
        "than",
        "about",
        "video",
        "subtitle",
        "subtitles",
    }
    counts: Counter[str] = Counter()
    values: dict[str, str] = {}
    for line in lines:
        for token in _tokenize_keywords(line):
            cleaned = _normalize_keyword(token).replace(" ", "")
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in latin_stopwords or len(cleaned) < 2:
                continue
            values.setdefault(lowered, cleaned)
            counts[lowered] += 1

    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], len(item[0]), item[0]),
    )
    return [values[key] for key, _ in ranked[:limit]]


def _tokenize_keywords(text: str) -> list[str]:
    chunks = re.split(r"[^\w\u4e00-\u9fff+\-]+", text)
    tokens: list[str] = []
    for chunk in chunks:
        normalized = chunk.strip()
        if not normalized:
            continue
        if _contains_cjk(normalized):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9+\-]{2,24}", normalized):
            tokens.append(normalized)
    return tokens


def _build_platform_title(spec: PlatformSpec, base_title: str) -> str:
    clean = _sanitize_base_title(base_title) or "未命名视频"

    if spec.slug == "douyin":
        return _compose_title_with_suffix(clean, "｜中文字幕", PLATFORM_TITLE_LIMITS["douyin"])

    if spec.slug == "bilibili":
        clean = re.sub(r"^【\s*中文字幕\s*】", "", clean).strip()
        return _truncate_text(f"【中文字幕】{clean}", PLATFORM_TITLE_LIMITS["bilibili"])

    if spec.slug == "xiaohongshu":
        return _compose_title_with_suffix(clean, "｜中文字幕", PLATFORM_TITLE_LIMITS["xiaohongshu"])

    if spec.slug == "tecent":
        return _compose_title_with_suffix(clean, "｜中文字幕", PLATFORM_TITLE_LIMITS["tecent"])

    return _truncate_text(f"【字幕翻译】{clean}", 22)


def _build_hashtags(spec: PlatformSpec, keywords: list[str]) -> list[str]:
    limit = PLATFORM_HASHTAG_LIMITS.get(spec.slug, 5)
    hashtags: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = _normalize_keyword(keyword).replace(" ", "")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        hashtags.append(f"#{cleaned}")
        if len(hashtags) >= limit:
            break
    return hashtags


def _build_platform_description(
    spec: PlatformSpec,
    title: str,
    source_metadata: VideoMetadata | None,
    evidence: MaterialEvidence,
    hashtags: list[str],
) -> str:
    lines = [f"视频标题：{title}"]
    if evidence.summary:
        lines.append(f"简介摘录：{evidence.summary}")
    if evidence.highlights:
        lines.append("字幕摘录：")
        for item in evidence.highlights:
            lines.append(f"- {item}")
    if source_metadata is not None and source_metadata.webpage_url:
        lines.append(f"原视频：{source_metadata.webpage_url}")
    if source_metadata is not None and (source_metadata.channel or source_metadata.uploader):
        lines.append(f"来源频道：{source_metadata.channel or source_metadata.uploader}")
    # 抖音 description 不拼 hashtags——hashtags 已单独写入 hashtags.txt
    # 把 hashtags 放进 description 会导致 Playwright 输入时触发 mention 弹框，拦截后续操作
    if spec.slug != "douyin" and hashtags:
        lines.append("标签建议：" + " ".join(hashtags))
    return "\n".join(line for line in lines if line) + "\n"


def _build_cover_text(title: str, spec: PlatformSpec | None = None) -> str:
    normalized = _sanitize_base_title(title)
    compact = re.split(r"[：:|｜\-]", normalized)[0].strip() if normalized else ""
    compact = compact or "中文字幕版"

    if spec and spec.slug == "xiaohongshu":
        if not _contains_cjk(compact):
            return "中文字幕\n原声保留"
        lines = _split_cover_lines(compact, max_line_length=10, max_lines=2)
        if not lines:
            lines = ["中文字幕", "原声保留"]
        elif len(lines) == 1:
            lines.append("中文字幕")
        return "\n".join(lines[:2])

    return _truncate_text(compact, 14)


def _evaluate_compatibility(spec: PlatformSpec, video_profile: dict[str, object]) -> dict[str, object]:
    format_value = str(video_profile["format"]).lower()
    duration_seconds = float(video_profile["duration_seconds"])
    size_bytes = int(video_profile["size_bytes"])
    aspect_ratio = str(video_profile["aspect_ratio"])

    format_ok = format_value in spec.format_allowlist
    duration_ok = spec.max_duration_seconds is None or duration_seconds <= spec.max_duration_seconds
    size_ok = spec.max_size_bytes is None or size_bytes <= spec.max_size_bytes
    preferred_aspect_ok = aspect_ratio == spec.preferred_aspect or (
        spec.slug == "xiaohongshu" and aspect_ratio in {"9:16", "3:4"}
    ) or (
        spec.slug == "tecent" and aspect_ratio in {"16:9", "9:16"}
    )

    needs_compression = spec.max_size_bytes is not None and size_bytes > spec.max_size_bytes
    return {
        "format_ok": format_ok,
        "duration_ok": duration_ok,
        "size_ok": size_ok,
        "preferred_aspect_ok": preferred_aspect_ok,
        "needs_compression": needs_compression,
        "duration_limit_text": (
            _format_duration(spec.max_duration_seconds)
            if spec.max_duration_seconds is not None
            else "未抓到明确硬性上限"
        ),
        "size_limit_text": (
            _format_bytes(spec.max_size_bytes)
            if spec.max_size_bytes is not None
            else "未抓到明确硬性上限"
        ),
    }


def _render_platform_requirements(
    spec: PlatformSpec,
    compatibility: dict[str, object],
    video_profile: dict[str, object],
    source_metadata: VideoMetadata | None,
    final_video: Path,
    subtitle_path: Path,
    cover_path: str | None,
) -> str:
    source_lines = []
    for source in spec.sources:
        source_lines.append(
            f"- [{source['label']}]({source['url']})（{source['kind']}）"
        )

    notes = "\n".join(f"- {note}" for note in spec.publishing_notes)
    requirements = "\n".join(f"- {line}" for line in spec.requirements_summary)
    fit_lines = [
        f"- 当前成片：`{video_profile['width']}x{video_profile['height']}` `{video_profile['aspect_ratio']}` `{video_profile['duration_text']}` `{video_profile['size_text']}` `{video_profile['format']}`",
        f"- 格式检查：{'通过' if compatibility['format_ok'] else '需复核'}",
        f"- 时长检查：{'通过' if compatibility['duration_ok'] else '超出上限'}（上限：{compatibility['duration_limit_text']}）",
        f"- 大小检查：{'通过' if compatibility['size_ok'] else '超出上限'}（上限：{compatibility['size_limit_text']}）",
        f"- 画幅匹配：{'匹配平台偏好' if compatibility['preferred_aspect_ok'] else '可传但不属于平台偏好画幅'}",
    ]
    if source_metadata is not None and source_metadata.webpage_url:
        fit_lines.append(f"- 原视频链接：{source_metadata.webpage_url}")
    fit_lines.append(f"- 共享成片路径：`{final_video}`")
    fit_lines.append(f"- 共享字幕路径：`{subtitle_path}`")
    if cover_path:
        fit_lines.append(f"- 共享封面路径：`{cover_path}`")

    caution_line = (
        "- 保守模式：当公开资料存在冲突时，这份材料按更严格的口径来判断。"
        if spec.conservative
        else ""
    )

    return "\n".join(
        [
            f"# {spec.display_name} 上传材料",
            "",
            "## 当前公开要求",
            "",
            requirements,
            "",
            "## 这份成片的适配判断",
            "",
            *fit_lines,
            caution_line,
            "",
            "## 封面与包装方向",
            "",
            f"- 标题建议上限：{PLATFORM_TITLE_LIMITS.get(spec.slug, 22)} 字",
            f"- 标签建议上限：{PLATFORM_HASHTAG_LIMITS.get(spec.slug, 5)} 个",
            f"- 推荐比例：{spec.preferred_aspect}",
            f"- 推荐分辨率：{spec.preferred_resolution}",
            f"- 封面建议：{spec.cover_recommendation}",
            "",
            "## 说明",
            "",
            notes,
            "",
            "## 来源",
            "",
            *source_lines,
            "",
        ]
    ).replace("\n\n\n", "\n\n")


def _render_upload_checklist(spec: PlatformSpec, compatibility: dict[str, object]) -> str:
    checklist = [
        f"# {spec.display_name} 上传检查清单",
        "",
        "- 使用共享的 `final_subtitled.mp4`，除非平台实际拒稿，否则不要额外生成新的视频版本。",
        "- 直接使用本目录中的 `title.txt`、`description.txt`、`hashtags.txt`、`cover_text.txt`。",
        "- 在简介或置顶说明里保留“只做字幕翻译、保留原声、不做声音克隆”的表述。",
    ]
    if not compatibility["preferred_aspect_ok"]:
        checklist.append(
            "- 当前成片不是该平台偏好的画幅，上传时优先用本目录提供的封面标题，并确保封面主体居中，降低画幅不匹配带来的影响。"
        )
    if compatibility["needs_compression"]:
        checklist.append(
            "- 当前视频超过保守大小上限，上传前先运行 `compression_ffmpeg.sh`。"
        )
    if not compatibility["duration_ok"]:
        checklist.append(
            "- 当前视频时长超过公开口径上限，上传前需要切分或裁短。"
        )
    checklist.append("- 最终提交前，在手机端预览一次字幕可读性。")
    checklist.append("")
    return "\n".join(checklist)


def _build_compression_command(
    final_video: Path,
    slug: str,
    max_size_bytes: int | None,
) -> str:
    limit_text = _format_bytes(max_size_bytes) if max_size_bytes is not None else "platform limit"
    output_path = final_video.with_name(f"{final_video.stem}.{slug}.compressed.mp4")
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f"# {slug} 的保守压缩目标：{limit_text}",
            "ffmpeg \\",
            f"  -i '{final_video}' \\",
            "  -c:v libx264 -preset medium -crf 24 \\",
            "  -c:a aac -b:a 128k \\",
            "  -movflags +faststart \\",
            f"  '{output_path}'",
            "",
        ]
    )


def _export_platform_cover_assets(
    spec: PlatformSpec,
    platform_dir: Path,
    shared_cover_path: Path | None,
) -> Path | None:
    if shared_cover_path is None or not shared_cover_path.exists():
        return None
    source_copy_path = platform_dir / f"cover_source{shared_cover_path.suffix.lower()}"
    source_copy_path.write_bytes(shared_cover_path.read_bytes())
    output_path = platform_dir / "cover.jpg"
    _render_cover_variant(
        source_path=shared_cover_path,
        output_path=output_path,
        target_size=spec.cover_size,
    )
    return output_path


def _render_cover_variant(
    source_path: Path,
    output_path: Path,
    target_size: tuple[int, int],
) -> None:
    target_width, target_height = target_size
    with Image.open(source_path) as original:
        image = original.convert("RGB")
        background = _resize_to_fill(image, target_size)
        background = background.filter(ImageFilter.GaussianBlur(radius=22))
        background = ImageEnhance.Brightness(background).enhance(0.72)

        foreground = _resize_to_fit(image, target_size)
        canvas = background.copy()
        offset_x = (target_width - foreground.width) // 2
        offset_y = (target_height - foreground.height) // 2
        canvas.paste(foreground, (offset_x, offset_y))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="JPEG", quality=95, optimize=True)


def _resize_to_fill(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_width, target_height = target_size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize(
        (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_width) // 2)
    top = max(0, (resized.height - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def _resize_to_fit(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_width, target_height = target_size
    scale = min(target_width / image.width, target_height / image.height)
    return image.resize(
        (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
        Image.Resampling.LANCZOS,
    )


def _render_platform_index(output_dir: Path, video_profile: dict[str, object]) -> str:
    lines = [
        "# 平台材料目录",
        "",
        f"- 共享成片：`{video_profile['path']}`",
        f"- 成片信息：`{video_profile['width']}x{video_profile['height']}` `{video_profile['aspect_ratio']}` `{video_profile['duration_text']}` `{video_profile['size_text']}`",
        "- 平台目录：",
    ]
    for slug, spec in PLATFORM_SPECS.items():
        lines.append(f"  - `{slug}/` ({spec.display_name})")
    lines.append("")
    return "\n".join(lines)


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
        f"- 平台材料根目录：`{output_dir / 'platforms'}`",
        "",
    ]
    (output_dir / "delivery_summary.md").write_text("\n".join(lines), encoding="utf-8")


def _first_sentence(text: str) -> str:
    normalized = _normalize_space(text)
    if not normalized:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s+", normalized)
    candidate = parts[0].strip()
    return _truncate_text(candidate, 120)


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _sanitize_base_title(title: str) -> str:
    clean = _normalize_space(title)
    clean = re.sub(r"\s*\[[A-Za-z0-9_-]{6,}\]\s*$", "", clean).strip()
    clean = re.sub(r"\s*\((Official Video|Official Trailer)\)\s*$", "", clean, flags=re.IGNORECASE).strip()
    return clean


def _extract_title_keywords(title: str) -> list[str]:
    clean = _sanitize_base_title(title)
    if not clean:
        return []
    tokens = [token.strip() for token in re.split(r"[：:|｜\-_,，。.!?、【】\[\]()/]+", clean) if token.strip()]
    keywords: list[str] = []
    for token in tokens:
        normalized = _normalize_keyword(token)
        if not normalized:
            continue
        if normalized.isdigit():
            continue
        keywords.append(normalized)
    return keywords[:8]


def _normalize_keyword(text: str) -> str:
    cleaned = _normalize_space(text).lstrip("#")
    cleaned = re.sub(r"[\"'“”‘’`]+", "", cleaned)
    cleaned = re.sub(r"[^\w\u4e00-\u9fff+\- ]+", "", cleaned)
    cleaned = cleaned.strip(" -_")
    if not cleaned:
        return ""
    if len(cleaned) > 24:
        return cleaned[:24].strip()
    return cleaned


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _split_cover_lines(text: str, max_line_length: int, max_lines: int) -> list[str]:
    clean = _normalize_space(text)
    if not clean:
        return []
    units = [item.strip() for item in re.split(r"[：:|｜\-_,，。.!?、/]+", clean) if item.strip()]
    if not units:
        units = [clean]

    lines: list[str] = []
    for unit in units:
        wrapped = _wrap_text_to_lines(unit, max_line_length)
        for line in wrapped:
            if len(lines) >= max_lines:
                break
            lines.append(line)
        if len(lines) >= max_lines:
            break
    return [line for line in lines if line]


def _wrap_text_to_lines(text: str, max_line_length: int) -> list[str]:
    if " " in text:
        words = [word for word in text.split(" ") if word]
        if words:
            lines: list[str] = []
            current = ""
            for word in words:
                candidate = word if not current else f"{current} {word}"
                if len(candidate) <= max_line_length:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                if len(word) <= max_line_length:
                    current = word
                    continue
                lines.extend(word[index : index + max_line_length] for index in range(0, len(word), max_line_length))
                current = ""
            if current:
                lines.append(current)
            if lines:
                return lines

    return [text[index : index + max_line_length].strip() for index in range(0, len(text), max_line_length)]


def _compose_title_with_suffix(base: str, suffix: str, limit: int) -> str:
    clean_base = _normalize_space(base)
    clean_suffix = _normalize_space(suffix)
    candidate = f"{clean_base}{clean_suffix}"
    if len(candidate) <= limit:
        return candidate
    keep = limit - len(clean_suffix) - 1
    if keep <= 0:
        return _truncate_text(candidate, limit)
    return f"{clean_base[:keep].rstrip()}…{clean_suffix}"


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

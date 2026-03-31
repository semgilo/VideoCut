from __future__ import annotations

import json
import math
import re
import time
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
            "在这次三个平台里，当前这份横版、只加字幕的成片与 Bilibili 的观看习惯最匹配。",
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
            localized_metadata = translator.translate_metadata(download.source_metadata)
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
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_metadata = localized_metadata or source_metadata or VideoMetadata(title="未命名视频")
    keyword_pool = _derive_keywords(base_metadata, source_metadata)
    cover_path = publish_assets.get("cover_image")

    for slug, spec in PLATFORM_SPECS.items():
        platform_dir = output_dir / slug
        platform_dir.mkdir(parents=True, exist_ok=True)

        title = _build_platform_title(spec, base_metadata.title)
        hashtags = _build_hashtags(spec, keyword_pool)
        description = _build_platform_description(
            spec=spec,
            title=title,
            source_metadata=source_metadata,
            base_metadata=base_metadata,
            hashtags=hashtags,
        )
        cover_text = _build_cover_text(base_metadata.title, spec)
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


def _derive_keywords(
    localized_metadata: VideoMetadata,
    source_metadata: VideoMetadata | None,
) -> list[str]:
    title_text = localized_metadata.title or (source_metadata.title if source_metadata else "")
    source_text = source_metadata.title if source_metadata is not None else ""
    combined = " ".join(
        [
            title_text,
            localized_metadata.description,
            source_text,
            source_metadata.description if source_metadata is not None else "",
        ]
    ).lower()

    keywords: list[str] = []
    if "openclaw" in combined:
        keywords.append("OpenClaw")
    if "ai" in combined:
        keywords.append("AI工具")
    if "agent" in combined or "代理" in combined:
        keywords.append("AI代理")
    if "automation" in combined or "自动化" in combined:
        keywords.append("自动化")
    if "workflow" in combined or "工作流" in combined:
        keywords.append("工作流")
    if "productivity" in combined or "效率" in combined or "life" in combined:
        keywords.append("效率工具")
    keywords.extend(["字幕翻译", "原声保留"])

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if keyword in seen:
            continue
        deduped.append(keyword)
        seen.add(keyword)
    return deduped[:6]


def _build_platform_title(spec: PlatformSpec, base_title: str) -> str:
    clean = _normalize_space(base_title) or "视频字幕翻译版"
    title_lower = clean.lower()

    if spec.slug == "douyin":
        return _truncate_text(f"{clean}｜中文字幕版", 36)

    if spec.slug == "bilibili":
        return _truncate_text(f"【中文字幕】{clean}", 60)

    if spec.slug == "xiaohongshu":
        # XHS style: catchy, emoji, shorter
        if "openclaw" in title_lower:
            return "OpenClaw教程｜本地AI自动化神器 🚀"
        if "local" in title_lower or "llm" in title_lower:
            if any(x in title_lower for x in ["$", "money", "万", "花", "spent"]):
                return "花了5万刀测试后｜本地AI入门全攻略💰"
            return "本地AI模型教程｜零门槛上手 🔥"
        if "how to" in title_lower or "guide" in title_lower:
            return f"{clean[:20]}｜超详细教程 ⭐"
        return _truncate_text(f"{clean}｜中文字幕版", 22)

    return _truncate_text(f"【字幕翻译】{clean}", 22)


def _build_hashtags(spec: PlatformSpec, keywords: list[str]) -> list[str]:
    tags = [f"#{keyword}" for keyword in keywords]
    if spec.slug == "douyin":
        return tags[:5]
    if spec.slug == "bilibili":
        return tags[:6]
    if spec.slug == "xiaohongshu":
        # XHS can use up to 10 hashtags, add some general ones
        xhs_extra = ["#干货分享", "#学习笔记", "#科技数码"]
        return (tags + xhs_extra)[:10]
    return tags[:5]


def _build_platform_description(
    spec: PlatformSpec,
    title: str,
    source_metadata: VideoMetadata | None,
    base_metadata: VideoMetadata,
    hashtags: list[str],
) -> str:
    origin_line = (
        f"原视频：{source_metadata.webpage_url}\n"
        f"来源频道：{source_metadata.channel or source_metadata.uploader}\n"
        if source_metadata is not None and source_metadata.webpage_url
        else ""
    )
    base_summary = _first_sentence(base_metadata.description)
    if not base_summary:
        base_summary = "本稿仅做中文字幕翻译，保留原始音轨，不做配音或声音克隆。"

    if spec.slug == "douyin":
        lines = [
            "本条仅做中文字幕翻译，保留原声。",
            base_summary,
            origin_line.rstrip(),
            " ".join(hashtags),
        ]
        return "\n".join(line for line in lines if line) + "\n"

    if spec.slug == "bilibili":
        lines = [
            "本稿说明：仅添加中文字幕，保留英文原声，不做中文配音或声音克隆。",
            f"视频标题：{title}",
        ]
        if base_summary:
            lines.append(f"内容摘要：{base_summary}")
        if origin_line:
            lines.append(origin_line.rstrip())
        lines.append("标签建议：" + " ".join(hashtags))
        return "\n".join(line for line in lines if line) + "\n"

    if spec.slug == "xiaohongshu":
        return _build_xiaohongshu_description(base_metadata, source_metadata, hashtags)

    lines = [
        "这是一条中文字幕翻译版视频，保留原声，不做配音。",
        base_summary,
        origin_line.rstrip(),
        " ".join(hashtags),
    ]
    return "\n".join(line for line in lines if line) + "\n"


def _build_xiaohongshu_description(
    base_metadata: VideoMetadata,
    source_metadata: VideoMetadata | None,
    hashtags: list[str],
) -> str:
    """Generate Xiaohongshu-style notes based on video content."""
    title_lower = (base_metadata.title or "").lower()
    desc_lower = (base_metadata.description or "").lower()
    combined = title_lower + " " + desc_lower

    # Detect content topics
    is_ai_local = "local" in combined or "本地" in combined or "llm" in combined
    is_openclaw = "openclaw" in combined
    is_tutorial = "how to" in combined or "guide" in combined or "教程" in combined
    is_money = "money" in combined or "$" in combined or "万" in combined or "赚" in combined

    lines = []

    # Opening hook
    if is_money and is_ai_local:
        lines.append("姐妹们！今天分享一个超硬核的AI教程 🔥")
        lines.append("")
        lines.append(f"博主@{source_metadata.uploader if source_metadata else '原作者'} 花了大量时间和金钱测试各种AI工具，最终整理出这份实用指南")
    elif is_openclaw:
        lines.append("发现了一个超实用的AI自动化工具！🚀")
        lines.append("")
        lines.append("OpenClaw 可以让你用自然语言指挥AI完成各种任务，不用写代码也能搭建自动化 workflow")
    elif is_tutorial:
        lines.append("干货分享！今天这个教程值得收藏 ⭐")
        lines.append("")
        lines.append("英文字幕已翻译，内容硬核但讲得很清楚，小白也能看懂")
    else:
        lines.append("今天分享一个超赞的英文视频翻译版 🎬")
        lines.append("")
        lines.append("原视频干货满满，已经加上了中文字幕，方便大家食用～")

    lines.append("")
    lines.append("📌 视频亮点速览：")
    lines.append("")

    # Generate bullet points based on content
    if is_ai_local:
        lines.append("1️⃣ 什么是本地AI？")
        lines.append("不需要联网、数据还私密，完全在你自己设备上运行的AI")
        lines.append("")
        lines.append("2️⃣ 需要什么设备？")
        lines.append("从普通电脑到专业设备，博主会告诉你不同预算的配置方案")
        lines.append("")

    if is_openclaw:
        lines.append(f"{'3️⃣' if is_ai_local else '1️⃣'} OpenClaw 能做什么？")
        lines.append("连接各种AI模型和工具，让AI自动帮你完成复杂任务")
        lines.append("")

    if is_money:
        lines.append(f"{'4️⃣' if (is_ai_local and is_openclaw) else ('3️⃣' if (is_ai_local or is_openclaw) else '2️⃣')} 省钱/赚钱思路")
        lines.append("不用订阅一堆付费服务，本地部署一次搞定，还能接私活")
        lines.append("")

    # Add generic points if we don't have enough
    point_num = 2 if (is_ai_local and is_openclaw and is_money) else (
        3 if ((is_ai_local and is_openclaw) or (is_ai_local and is_money) or (is_openclaw and is_money)) else (
        4 if (is_ai_local or is_openclaw or is_money) else 1
    ))

    if point_num <= 3:
        lines.append(f"{point_num}️⃣ 实用技巧")
        lines.append("博主分享了很多实操经验，跟着做就能上手")
        lines.append("")
        point_num += 1

    # Target audience
    lines.append("💡 适合谁看：")
    if is_ai_local:
        lines.append("» 担心隐私泄露，不想把数据传到云端的宝子")
        lines.append("» 想省钱，不想每个月交各种AI订阅费的")
    if is_openclaw:
        lines.append("» 想用AI提效，但不想学编程的")
    lines.append("» 对AI感兴趣，想从零开始学的")
    lines.append("")

    # Closing
    lines.append("🎬 英文字幕已翻译，放心食用～")
    lines.append("")

    # Origin info
    if source_metadata and source_metadata.webpage_url:
        lines.append(f"原视频：{source_metadata.webpage_url}")
        if source_metadata.uploader or source_metadata.channel:
            lines.append(f"来源频道：{source_metadata.channel or source_metadata.uploader}")
        lines.append("")

    # Hashtags
    lines.append(" ".join(hashtags[:8]))  # XHS can use more hashtags

    return "\n".join(lines) + "\n"


def _build_cover_text(title: str, spec: PlatformSpec | None = None) -> str:
    normalized = _normalize_space(title)
    title_lower = normalized.lower()

    # XHS specific cover text
    if spec and spec.slug == "xiaohongshu":
        if "openclaw" in title_lower:
            return "OpenClaw\nAI自动化神器"
        if "local" in title_lower or "llm" in title_lower:
            if any(x in title_lower for x in ["$", "money", "万", "花", "spent"]):
                return "5万刀实测\n本地AI攻略"
            return "本地AI\n零基础入门"
        if "how to" in title_lower or "教程" in title_lower:
            return "硬核教程\n建议收藏"
        # Default XHS style
        compact = re.sub(r"^[【\[].*?[】\]]", "", normalized).strip()
        compact = re.split(r"[：:|｜\-]", compact)[0].strip()
        if len(compact) > 10:
            return _truncate_text(compact, 10) + "\n干货分享"
        return (compact or "中文字幕") + "\n建议收藏"

    # Default for other platforms
    if re.search(r"\bopenclaw\b", normalized, flags=re.IGNORECASE):
        return "OpenClaw 5个实用场景"
    compact = re.sub(r"^[【\[].*?[】\]]", "", normalized).strip()
    compact = re.split(r"[：:|｜\-]", compact)[0].strip()
    if not compact:
        return "中文字幕版"
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

from __future__ import annotations

from pathlib import Path

from videocut.config import PipelineConfig
from videocut.downloader import download_youtube_assets
from videocut.media import (
    compose_dubbed_track,
    compress_for_publish,
    measure_synthesized_segments,
    render_final_video,
    write_manifest,
)
from videocut.publish import export_publish_assets
from videocut.shell import step, step_guard
from videocut.subtitles import load_segments_from_vtt, write_srt
from videocut.timing import schedule_dubbing_timing
from videocut.translate import (
    OpenAICompatibleTranslator,
    is_local_base_url,
    load_protected_terms,
    llm_translation_enabled,
)
from videocut.tts import synthesize_segments


def run_pipeline(url: str, config: PipelineConfig, workdir: Path | None = None) -> Path:
    run_dir = workdir.expanduser().resolve() if workdir else _make_run_dir(config.runs_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    source_dir = run_dir / "source"
    subtitles_dir = run_dir / "subtitles"
    tts_dir = run_dir / "tts"
    audio_dir = run_dir / "audio"

    print(f"Working directory: {run_dir}")
    step("Step 1/10 download-assets")
    download = download_youtube_assets(
        url=url,
        source_dir=source_dir,
        include_chinese_subtitles=False,
    )
    print(f"Video downloaded: {download.video_path}")
    if download.english_subtitle_path is None:
        raise RuntimeError(
            "No English subtitle track was downloaded. "
            "This unified pipeline requires an English subtitle source."
        )

    step("Step 2/10 parse-english-subtitles")
    segments = load_segments_from_vtt(download.english_subtitle_path)
    if not segments:
        raise RuntimeError(f"No subtitle segments were parsed from {download.english_subtitle_path}")
    print(f"Segments ready: {len(segments)}")

    step("Step 3/10 translate-llm")
    if not llm_translation_enabled(
        base_url=config.llm_base_url,
        model=config.llm_model,
        api_key=config.llm_api_key,
    ):
        raise RuntimeError(
            "Local LLM translation is required for the unified pipeline. "
            "Please set VIDEOCUT_LLM_BASE_URL and VIDEOCUT_LLM_MODEL."
        )
    if not is_local_base_url(config.llm_base_url):
        raise RuntimeError(
            f"Unified pipeline requires a local LLM endpoint, got {config.llm_base_url}."
        )
    protected_terms = load_protected_terms(config.protected_terms_path)
    if protected_terms:
        print(
            f"Loaded {len(protected_terms)} protected translation terms from "
            f"{config.protected_terms_path}"
        )
    translator = OpenAICompatibleTranslator(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        timeout=config.llm_timeout,
        batch_size=config.translation_batch_size,
        concurrency=config.translation_concurrency,
        target_cps=config.translation_target_cps,
        char_tolerance=config.translation_char_tolerance,
        protected_terms=protected_terms,
    )
    translator.translate(segments)

    step("Step 4/10 synthesize-tts")
    synthesize_segments(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=download.video_path,
    )

    step("Step 5/10 measure-tts-durations")
    measure_synthesized_segments(segments)

    step("Step 6/10 schedule-timing")
    schedule_dubbing_timing(segments)
    first_spoken_at = min(segment.render_start for segment in segments)
    max_rate = max(segment.playback_rate for segment in segments)
    min_rate = min(segment.playback_rate for segment in segments)
    print(
        f"Dub timing scheduled: first speech at {first_spoken_at:.2f}s, "
        f"playback-rate range {min_rate:.2f}x ~ {max_rate:.2f}x"
    )

    step("Step 7/10 write-bilingual-srt")
    generated_srt = subtitles_dir / "zh.srt"
    write_srt(generated_srt, segments, bilingual=True)
    print(f"Bilingual subtitle file generated: {generated_srt}")

    step("Step 8/10 compose-dubbed-audio")
    dubbed_track = compose_dubbed_track(
        video_path=download.video_path,
        segments=segments,
        output_path=audio_dir / "dubbed_track.m4a",
        original_volume=config.original_audio_volume,
        dub_volume=config.dub_audio_volume,
    )
    print(f"Dubbed audio track generated: {dubbed_track}")

    step("Step 9/10 render-final-video")
    final_video = render_final_video(
        video_path=download.video_path,
        dubbed_track_path=dubbed_track,
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

    step("Step 10/11 compress-for-publish")
    compress_for_publish(
        input_path=final_video,
        output_path=run_dir / "final_compressed.mp4",
        target_size_mb=500,
        max_width=1920,
        max_height=1080,
    )

    step("Step 11/11 translate-meta-and-export")
    localized_metadata = download.source_metadata
    if download.source_metadata is not None:
        localized_metadata = _translate_metadata_partial(translator, download.source_metadata)
    else:
        print("Warning: source metadata was not downloaded, publish assets will be partial.")

    publish_assets = export_publish_assets(
        output_dir=run_dir,
        source_metadata=download.source_metadata,
        localized_metadata=localized_metadata,
        cover_image_path=download.thumbnail_path,
        final_video=final_video,
    )
    if config.export_platform_materials:
        _export_platform_materials(
            run_dir=run_dir,
            final_video=final_video,
            subtitle_path=generated_srt,
            subtitle_segments=segments,
            publish_assets=publish_assets,
            source_metadata=download.source_metadata,
            localized_metadata=localized_metadata,
        )
    write_manifest(
        path=run_dir / "manifest.json",
        source_video=download.video_path,
        subtitle_source=download.english_subtitle_path,
        thumbnail_source=download.thumbnail_path,
        generated_srt=generated_srt,
        dubbed_track=dubbed_track,
        final_video=final_video,
        segments=segments,
        source_metadata=download.source_metadata,
        localized_metadata=localized_metadata,
        publish_assets=publish_assets,
    )
    print(f"Final video exported: {final_video}")

    if config.cleanup_source_after_publish:
        import shutil

        if source_dir.exists():
            shutil.rmtree(source_dir)
            print(f"Source directory cleaned up: {source_dir}")

    return final_video


def _translate_metadata_partial(
    translator: "OpenAICompatibleTranslator",
    source: "VideoMetadata",
) -> "VideoMetadata":
    """逐字段翻译元数据，每个字段独立降级——失败时保留原文，不影响其他字段。"""
    from videocut.models import VideoMetadata

    def _try_field(field_name: str, text: str) -> str:
        if not text.strip():
            return text
        try:
            result = translator._translate_single_metadata_field(field_name, text)
            print(f"  metadata [{field_name}] translated OK")
            return result
        except Exception as err:
            print(f"  Warning: metadata [{field_name}] translation failed, keeping original: {err}")
            return text

    def _try_tags(tags: list[str]) -> list[str]:
        if not tags:
            return tags
        translated = []
        for tag in tags:
            try:
                translated.append(translator._translate_single_metadata_field("tag", tag))
            except Exception:
                translated.append(tag)
        return translated

    title = _try_field("title", source.title)
    description = _try_field("description", source.description)
    tags = _try_tags(source.tags)

    return VideoMetadata(
        title=title,
        description=description,
        tags=tags,
        uploader=source.uploader,
        channel=source.channel,
        video_id=source.video_id,
        webpage_url=source.webpage_url,
        upload_date=source.upload_date,
    )


def _export_platform_materials(
    run_dir: Path,
    final_video: Path,
    subtitle_path: Path,
    subtitle_segments: "list[Segment] | None",
    publish_assets: dict[str, str | None],
    source_metadata: "VideoMetadata | None",
    localized_metadata: "VideoMetadata | None",
) -> None:
    from videocut.subtitle_only import _collect_video_profile, _export_platform_kits

    platforms_dir = run_dir / "platforms"
    video_profile = _collect_video_profile(final_video)
    _export_platform_kits(
        output_dir=platforms_dir,
        final_video=final_video,
        subtitle_path=subtitle_path,
        publish_assets=publish_assets,
        source_metadata=source_metadata,
        localized_metadata=localized_metadata,
        video_profile=video_profile,
        subtitle_segments=subtitle_segments,
    )
    print(f"Platform kits exported: {platforms_dir}")


def _make_run_dir(runs_dir: Path) -> Path:
    import secrets
    import string
    import time

    runs_root = runs_dir.expanduser().resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    alphabet = string.ascii_lowercase + string.digits

    for _ in range(20):
        timestamp_ms = int(time.time() * 1000)
        left = _to_base36(timestamp_ms)
        right = "".join(secrets.choice(alphabet) for _ in range(6))
        candidate = runs_root / f"run_{left}_{right}-processed"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Failed to allocate a unique run directory name.")


def _to_base36(value: int) -> str:
    if value < 0:
        raise ValueError("Base36 encoding only supports non-negative integers.")
    if value == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        encoded.append(digits[remainder])
    return "".join(reversed(encoded))

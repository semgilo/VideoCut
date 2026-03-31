from __future__ import annotations

from pathlib import Path

from videocut.config import PipelineConfig
from videocut.downloader import download_youtube_assets
from videocut.media import (
    compose_dubbed_track,
    measure_synthesized_segments,
    render_final_video,
    write_manifest,
)
from videocut.publish import export_publish_assets
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
    run_dir = workdir or _make_run_dir(config.runs_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    source_dir = run_dir / "source"
    subtitles_dir = run_dir / "subtitles"
    tts_dir = run_dir / "tts"
    audio_dir = run_dir / "audio"

    print(f"Working directory: {run_dir}")
    print("Step 1/10: downloading video and subtitle assets...")
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

    print("Step 2/10: parsing English subtitles...")
    segments = load_segments_from_vtt(download.english_subtitle_path)
    if not segments:
        raise RuntimeError(f"No subtitle segments were parsed from {download.english_subtitle_path}")
    print(f"Segments ready: {len(segments)}")

    print("Step 3/10: translating with local LLM (batch=10, L/V char budget)...")
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

    print("Step 4/10: synthesizing Chinese dubbing with CosyVoice...")
    synthesize_segments(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=download.video_path,
    )

    print("Step 5/10: measuring synthesized segment durations...")
    measure_synthesized_segments(segments)

    print("Step 6/10: planning timing via per-segment stretch/compress alignment...")
    schedule_dubbing_timing(segments)
    first_spoken_at = min(segment.render_start for segment in segments)
    max_rate = max(segment.playback_rate for segment in segments)
    min_rate = min(segment.playback_rate for segment in segments)
    print(
        f"Dub timing scheduled: first speech at {first_spoken_at:.2f}s, "
        f"playback-rate range {min_rate:.2f}x ~ {max_rate:.2f}x"
    )

    print("Step 7/10: generating bilingual SRT...")
    generated_srt = subtitles_dir / "zh.srt"
    write_srt(generated_srt, segments, bilingual=True)
    print(f"Bilingual subtitle file generated: {generated_srt}")

    print("Step 8/10: composing dubbed audio track (ffmpeg-full)...")
    dubbed_track = compose_dubbed_track(
        video_path=download.video_path,
        segments=segments,
        output_path=audio_dir / "dubbed_track.m4a",
        original_volume=config.original_audio_volume,
        dub_volume=config.dub_audio_volume,
    )
    print(f"Dubbed audio track generated: {dubbed_track}")

    print("Step 9/10: rendering final video (ffmpeg-full)...")
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

    print("Step 10/10: translating metadata and exporting publish assets...")
    localized_metadata = download.source_metadata
    if download.source_metadata is not None:
        try:
            localized_metadata = translator.translate_metadata(download.source_metadata)
        except Exception as error:
            print(f"Warning: metadata translation failed, keeping original metadata: {error}")
    else:
        print("Warning: source metadata was not downloaded, publish assets will be partial.")

    publish_assets = export_publish_assets(
        output_dir=run_dir,
        source_metadata=download.source_metadata,
        localized_metadata=localized_metadata,
        cover_image_path=download.thumbnail_path,
        final_video=final_video,
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


def _make_run_dir(runs_dir: Path) -> Path:
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return runs_dir / timestamp

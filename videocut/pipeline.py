from __future__ import annotations

from pathlib import Path

from videocut.asr import extract_audio_for_asr, transcribe_with_faster_whisper
from videocut.config import PipelineConfig
from videocut.downloader import download_youtube_assets
from videocut.media import (
    compose_dubbed_track,
    ffprobe_duration,
    render_final_video,
    write_manifest,
)
from videocut.subtitles import (
    load_chinese_segments_from_vtt,
    load_segments_from_vtt,
    overlay_chinese_from_vtt,
    write_srt,
)
from videocut.timing import plan_dubbing_timing
from videocut.translate import OpenAICompatibleTranslator
from videocut.tts import synthesize_segments


def run_pipeline(url: str, config: PipelineConfig, workdir: Path | None = None) -> Path:
    run_dir = workdir or _make_run_dir(config.runs_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    source_dir = run_dir / "source"
    subtitles_dir = run_dir / "subtitles"
    tts_dir = run_dir / "tts"
    audio_dir = run_dir / "audio"

    print(f"Working directory: {run_dir}")
    download = download_youtube_assets(
        url,
        source_dir,
        include_chinese_subtitles=not bool(config.llm_api_key),
    )
    print(f"Video downloaded: {download.video_path}")

    if download.english_subtitle_path is not None:
        print(f"English subtitle track found: {download.english_subtitle_path}")
        segments = load_segments_from_vtt(download.english_subtitle_path)
    elif download.chinese_subtitle_path is not None:
        print(
            "No English subtitle track found. Using Chinese subtitle track directly: "
            f"{download.chinese_subtitle_path}"
        )
        segments = load_chinese_segments_from_vtt(download.chinese_subtitle_path)
    else:
        print("No English subtitle track found. Falling back to faster-whisper.")
        extracted_audio = extract_audio_for_asr(download.video_path, source_dir / "source_audio.wav")
        segments = transcribe_with_faster_whisper(
            extracted_audio,
            model_name=config.asr_model,
            device=config.asr_device,
            compute_type=config.asr_compute_type,
        )

    if not segments:
        raise RuntimeError("No subtitle or transcription segments were produced.")

    print(f"Segments ready: {len(segments)}")
    if config.llm_api_key:
        print("Translating subtitles...")
        translator = OpenAICompatibleTranslator(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
            timeout=config.llm_timeout,
            batch_size=config.translation_batch_size,
        )
        translator.translate(segments)
    elif download.chinese_subtitle_path is not None:
        if download.english_subtitle_path is not None:
            print(
                "No translation API key configured. Reusing YouTube Chinese subtitle track: "
                f"{download.chinese_subtitle_path}"
            )
            overlay_chinese_from_vtt(segments, download.chinese_subtitle_path)
        else:
            print(
                "No translation API key configured. Chinese subtitle track will be used directly "
                "for subtitles and dubbing."
            )
    else:
        raise RuntimeError(
            "No translation API key was configured and no Chinese subtitle track is available. "
            "Set VIDEOCUT_LLM_API_KEY or use a video that has zh-Hans subtitles."
        )

    print("Synthesizing Chinese dubbing...")
    synthesize_segments(
        segments=segments,
        output_dir=tts_dir,
        config=config,
        source_video=download.video_path,
    )
    for segment in segments:
        if segment.audio_path is None:
            raise RuntimeError(f"Segment {segment.index} did not produce an audio file")
        segment.synthetic_duration = ffprobe_duration(segment.audio_path)

    video_duration = ffprobe_duration(download.video_path)
    plan_dubbing_timing(
        segments=segments,
        video_duration=video_duration,
        max_opening_silence=config.max_opening_silence,
        max_global_shift=config.max_global_shift,
        min_segment_gap=config.min_segment_gap,
        max_playback_rate=config.max_playback_rate,
        max_segment_lag=config.max_segment_lag,
    )
    first_spoken_at = min(segment.render_start for segment in segments)
    max_rate = max(segment.playback_rate for segment in segments)
    print(
        "Dub timing scheduled: "
        f"first speech at {first_spoken_at:.2f}s, max playback rate {max_rate:.2f}x"
    )

    generated_srt = subtitles_dir / "zh.srt"
    write_srt(generated_srt, segments)
    print(f"Chinese subtitle file generated: {generated_srt}")

    dubbed_track = compose_dubbed_track(
        video_path=download.video_path,
        segments=segments,
        output_path=audio_dir / "dubbed_track.m4a",
        original_volume=config.original_audio_volume,
        dub_volume=config.dub_audio_volume,
    )
    print(f"Dubbed audio track generated: {dubbed_track}")

    final_video = render_final_video(
        video_path=download.video_path,
        dubbed_track_path=dubbed_track,
        subtitle_path=generated_srt,
        output_path=run_dir / config.output_name,
        burn_subtitles=config.burn_subtitles,
        subtitle_font=config.subtitle_font,
        subtitle_font_size=config.subtitle_font_size,
    )
    write_manifest(
        path=run_dir / "manifest.json",
        source_video=download.video_path,
        subtitle_source=download.english_subtitle_path or download.chinese_subtitle_path,
        generated_srt=generated_srt,
        dubbed_track=dubbed_track,
        final_video=final_video,
        segments=segments,
    )
    print(f"Final video exported: {final_video}")
    return final_video


def _make_run_dir(runs_dir: Path) -> Path:
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return runs_dir / timestamp

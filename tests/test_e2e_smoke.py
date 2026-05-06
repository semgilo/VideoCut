"""Smoke tests for the VideoCut pipeline.

- ``test_pipeline_until_translate`` (active): validates download reuse,
  subtitle parsing, and LLM translation by calling the public APIs directly.
- ``test_full_pipeline_smoke`` (skipped): end-to-end run via
  ``run_pipeline()``.  Re-enable once CosyVoice is installed.

Run:
    uv run pytest tests/test_e2e_smoke.py -v -s

Generate the fixture once before the first run:
    python scripts/prepare_test_fixture.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from videocut.config import load_pipeline_config
from videocut.downloader import download_youtube_assets
from videocut.pipeline import run_pipeline
from videocut.shell import resolve_tool_binary
from videocut.subtitles import load_segments_from_vtt, write_srt
from videocut.translate import OpenAICompatibleTranslator, load_protected_terms

FIXTURE_RUN_DIR = Path.home() / ".openclaw" / "tmp" / "mc-runs" / "test-fixture"
FIXTURE_SOURCE_DIR = FIXTURE_RUN_DIR / "source"
FIXTURE_HINT_CMD = "python scripts/prepare_test_fixture.py"
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm")

MAX_FINAL_SIZE = 500 * 1024 * 1024
MIN_FINAL_SIZE = 100_000
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
DURATION_SLACK_SEC = 10.0


def _pick_fixture_video() -> Path:
    if not FIXTURE_SOURCE_DIR.exists():
        pytest.fail(
            f"Test fixture directory not found: {FIXTURE_SOURCE_DIR}\n"
            f"Generate it once with: {FIXTURE_HINT_CMD}"
        )
    candidates = [
        p for p in FIXTURE_SOURCE_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]
    if not candidates:
        pytest.fail(
            f"Test fixture not found in {FIXTURE_SOURCE_DIR}\n"
            f"Generate it once with: {FIXTURE_HINT_CMD}"
        )
    return max(candidates, key=lambda p: p.stat().st_size)


def _ffprobe_json(path: Path) -> dict:
    output = subprocess.run(
        [
            resolve_tool_binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=width,height,codec_type,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(output.stdout)


def _video_stream(info: dict) -> dict:
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise AssertionError(f"No video stream in ffprobe output: {info}")
    return streams[0]


def _duration_sec(info: dict) -> float:
    fmt_duration = info.get("format", {}).get("duration")
    if fmt_duration is not None:
        return float(fmt_duration)
    stream = _video_stream(info)
    return float(stream["duration"])


def _copy_fixture_to_source(target_source_dir: Path) -> None:
    target_source_dir.mkdir(parents=True, exist_ok=True)
    for src in FIXTURE_SOURCE_DIR.iterdir():
        if src.is_file():
            shutil.copy2(src, target_source_dir / src.name)


def _prepare_test_run_dir() -> Path:
    config = load_pipeline_config()
    runs_dir = config.runs_dir.expanduser().resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="test-", dir=runs_dir))


# ---------------------------------------------------------------------------
# Active test: download → parse → translate
# ---------------------------------------------------------------------------

def test_pipeline_until_translate():
    fixture_video = _pick_fixture_video()

    config = load_pipeline_config()
    config.cleanup_source_after_publish = False
    test_run_dir = _prepare_test_run_dir()

    try:
        test_source_dir = test_run_dir / "source"
        _copy_fixture_to_source(test_source_dir)

        # Step 1/10: download-assets (reused from fixture)
        download = download_youtube_assets(
            url="https://www.youtube.com/watch?v=test",
            source_dir=test_source_dir,
            include_chinese_subtitles=False,
        )
        assert download.english_subtitle_path is not None
        assert download.video_path.exists()
        # Ensure downloader matched subtitle to the correct video stem
        assert download.english_subtitle_path.name.startswith(download.video_path.stem)

        # Step 2/10: parse-english-subtitles
        segments = load_segments_from_vtt(download.english_subtitle_path)
        assert len(segments) > 0, "No subtitle segments parsed"

        # Step 3/10: translate-llm
        protected_terms = load_protected_terms(config.protected_terms_path)
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

        for segment in segments:
            assert segment.chinese and segment.chinese.strip(), (
                f"Segment {segment.index} missing Chinese translation"
            )

        # Step 7/10 (partial): write-bilingual-srt
        subtitles_dir = test_run_dir / "subtitles"
        subtitles_dir.mkdir(parents=True, exist_ok=True)
        zh_srt = subtitles_dir / "zh.srt"
        write_srt(zh_srt, segments, bilingual=True)
        assert zh_srt.exists() and zh_srt.stat().st_size > 0
    finally:
        shutil.rmtree(test_run_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Skipped test: full pipeline via run_pipeline()
# Re-enable once CosyVoice is installed.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="CosyVoice repo not available – install CosyVoice to enable")
def test_full_pipeline_smoke():
    fixture_video = _pick_fixture_video()

    config = load_pipeline_config()
    config.mode = "voice_clone"
    config.cleanup_source_after_publish = False
    config.enable_voice_clone = False

    test_run_dir = _prepare_test_run_dir()

    try:
        _copy_fixture_to_source(test_run_dir / "source")

        run_pipeline(
            url="https://www.youtube.com/watch?v=test",
            config=config,
            workdir=test_run_dir,
        )

        expected_files = [
            test_run_dir / "subtitles" / "zh.srt",
            test_run_dir / "audio" / "dubbed_track.m4a",
            test_run_dir / "manifest.json",
            test_run_dir / config.output_name,
        ]
        for path in expected_files:
            assert path.exists(), f"Missing expected artifact: {path}"

        publish_dir = test_run_dir / "publish"
        assert publish_dir.is_dir()
        assert any(publish_dir.iterdir())

        final_video = test_run_dir / config.output_name
        size = final_video.stat().st_size
        assert MIN_FINAL_SIZE < size < MAX_FINAL_SIZE

        final_info = _ffprobe_json(final_video)
        v_stream = _video_stream(final_info)
        assert int(v_stream["width"]) <= MAX_WIDTH
        assert int(v_stream["height"]) <= MAX_HEIGHT

        final_duration = _duration_sec(final_info)
        input_duration = _duration_sec(_ffprobe_json(fixture_video))
        assert final_duration > 0
        assert final_duration <= input_duration + DURATION_SLACK_SEC
    finally:
        shutil.rmtree(test_run_dir, ignore_errors=True)

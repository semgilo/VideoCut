from __future__ import annotations

import json
import os
import random
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from videocut.models import VideoMetadata
from videocut.publish import load_video_metadata
from videocut.shell import run_command

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
INFO_EXTENSIONS = (".info.json",)
THUMBNAIL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
YTDLP_REMOTE_COMPONENTS = "ejs:github"
YTDLP_PREFERRED_VIDEO_FORMAT = (
    "bestvideo*[height=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo*[height=1080]+bestaudio/"
    "best[height=1080]/"
    "bestvideo*[height=720][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo*[height=720]+bestaudio/"
    "best[height=720]/"
    "bestvideo*[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo*[height<=1080]+bestaudio/"
    "best[height<=1080]/"
    "best"
)
YTDLP_LOCK_PATH = Path(os.getenv("VIDEOCUT_YTDLP_LOCK_PATH", "/tmp/videocut-yt-dlp.lock"))
YTDLP_RETRY_ATTEMPTS = max(1, int(os.getenv("VIDEOCUT_YTDLP_RETRY_ATTEMPTS", "3")))
YTDLP_RETRY_BASE_SLEEP_SECONDS = max(1.0, float(os.getenv("VIDEOCUT_YTDLP_RETRY_BASE_SLEEP_SECONDS", "8")))
YTDLP_REQUEST_SLEEP_SECONDS = max(0.0, float(os.getenv("VIDEOCUT_YTDLP_REQUEST_SLEEP_SECONDS", "2")))
YTDLP_SUBTITLE_REQUEST_SLEEP_SECONDS = max(0.0, float(os.getenv("VIDEOCUT_YTDLP_SUBTITLE_REQUEST_SLEEP_SECONDS", "3")))


@dataclass(slots=True)
class DownloadResult:
    video_path: Path
    english_subtitle_path: Path | None
    chinese_subtitle_path: Path | None
    info_json_path: Path | None
    thumbnail_path: Path | None
    source_metadata: VideoMetadata | None


def download_youtube_assets(url: str, source_dir: Path, include_chinese_subtitles: bool = False) -> DownloadResult:
    source_dir.mkdir(parents=True, exist_ok=True)
    existing_download = _load_existing_download(source_dir)
    if existing_download is not None:
        print(f"Reusing existing downloaded source assets from {source_dir}")
        return existing_download

    output_template = str(source_dir / "%(title).120B [%(id)s].%(ext)s")

    with _download_lock():
        print("Acquired global yt-dlp download lock")
        _download_video_with_retry(url, output_template)
        _download_subtitles_best_effort(
            url,
            output_template,
            subtitle_langs="en,en-orig",
            label="English",
        )
        if include_chinese_subtitles:
            _download_subtitles_best_effort(
                url,
                output_template,
                subtitle_langs="zh-Hans.*,zh-Hans,zh-CN.*,zh-CN,zh-Hant.*,zh-Hant",
                label="Chinese",
            )

    video_path = _pick_latest_video(source_dir)
    english_subtitle_path = _pick_best_subtitle(source_dir, video_path.stem, ("en", "en-orig"))
    chinese_subtitle_path = _pick_best_subtitle(
        source_dir,
        video_path.stem,
        ("zh-Hans", "zh-CN", "zh-Hant"),
    )
    info_json_path = _pick_related_file(source_dir, video_path.stem, INFO_EXTENSIONS)
    thumbnail_path = _pick_related_file(source_dir, video_path.stem, THUMBNAIL_EXTENSIONS)
    source_metadata = load_video_metadata(info_json_path) if info_json_path is not None else None

    if english_subtitle_path is None:
        print("Warning: no English subtitle track was downloaded.")
    if include_chinese_subtitles and chinese_subtitle_path is None:
        print("Warning: no Chinese subtitle track was downloaded.")

    return DownloadResult(
        video_path=video_path,
        english_subtitle_path=english_subtitle_path,
        chinese_subtitle_path=chinese_subtitle_path,
        info_json_path=info_json_path,
        thumbnail_path=thumbnail_path,
        source_metadata=source_metadata,
    )


def _download_video_with_retry(url: str, output_template: str) -> None:
    _run_ytdlp_with_retry(
        [
            "yt-dlp",
            "--no-playlist",
            "--remote-components",
            YTDLP_REMOTE_COMPONENTS,
            "--write-info-json",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "--sleep-requests",
            str(YTDLP_REQUEST_SLEEP_SECONDS),
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--extractor-retries",
            "5",
            "--file-access-retries",
            "3",
            "-f",
            YTDLP_PREFERRED_VIDEO_FORMAT,
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            url,
        ],
        label="video",
        required=True,
    )


def _download_subtitles_best_effort(url: str, output_template: str, subtitle_langs: str, label: str) -> None:
    try:
        _run_ytdlp_with_retry(
            [
                "yt-dlp",
                "--skip-download",
                "--no-playlist",
                "--remote-components",
                YTDLP_REMOTE_COMPONENTS,
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                subtitle_langs,
                "--convert-subs",
                "vtt",
                "--sleep-requests",
                str(YTDLP_SUBTITLE_REQUEST_SLEEP_SECONDS),
                "--retries",
                "6",
                "--fragment-retries",
                "3",
                "--extractor-retries",
                "3",
                "-o",
                output_template,
                url,
            ],
            label=f"{label.lower()} subtitles",
            required=False,
        )
    except subprocess.CalledProcessError as error:
        print(f"Warning: could not download {label} subtitle track, continuing without it: {error}")


def _run_ytdlp_with_retry(args: list[str], label: str, required: bool) -> None:
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(1, YTDLP_RETRY_ATTEMPTS + 1):
        try:
            print(f"Starting yt-dlp {label} attempt {attempt}/{YTDLP_RETRY_ATTEMPTS}")
            run_command(args)
            return
        except subprocess.CalledProcessError as error:
            last_error = error
            retryable = _should_retry_ytdlp(error)
            if attempt >= YTDLP_RETRY_ATTEMPTS or not retryable:
                if required:
                    raise
                raise error
            sleep_seconds = _compute_retry_sleep_seconds(attempt)
            print(
                f"Warning: yt-dlp {label} attempt {attempt} failed ({_summarize_ytdlp_error(error)}). "
                f"Retrying in {sleep_seconds:.1f}s..."
            )
            time.sleep(sleep_seconds)
    if last_error is not None:
        raise last_error


def _should_retry_ytdlp(error: subprocess.CalledProcessError) -> bool:
    output = _error_output_text(error).lower()
    retry_markers = (
        "429",
        "too many requests",
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
        "http error 5",
        "requested format is not available",
        "unable to download api page",
        "remote end closed connection",
        "try again",
        "rate limit",
    )
    return any(marker in output for marker in retry_markers) or error.returncode != 0


def _compute_retry_sleep_seconds(attempt: int) -> float:
    base = YTDLP_RETRY_BASE_SLEEP_SECONDS * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0, min(5.0, base * 0.25))
    return base + jitter


def _summarize_ytdlp_error(error: subprocess.CalledProcessError) -> str:
    output = _error_output_text(error).strip()
    if not output:
        return f"exit code {error.returncode}"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:240] if lines else f"exit code {error.returncode}"


def _error_output_text(error: subprocess.CalledProcessError) -> str:
    parts = []
    if getattr(error, "stdout", None):
        parts.append(str(error.stdout))
    if getattr(error, "stderr", None):
        parts.append(str(error.stderr))
    return "\n".join(parts)


@contextmanager
def _download_lock():
    YTDLP_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(YTDLP_LOCK_PATH, "w") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_existing_download(source_dir: Path) -> DownloadResult | None:
    try:
        video_path = _pick_latest_video(source_dir)
    except FileNotFoundError:
        return None

    english_subtitle_path = _pick_best_subtitle(source_dir, video_path.stem, ("en", "en-orig"))
    chinese_subtitle_path = _pick_best_subtitle(
        source_dir,
        video_path.stem,
        ("zh-Hans", "zh-CN", "zh-Hant"),
    )
    info_json_path = _pick_related_file(source_dir, video_path.stem, INFO_EXTENSIONS)
    thumbnail_path = _pick_related_file(source_dir, video_path.stem, THUMBNAIL_EXTENSIONS)
    source_metadata = load_video_metadata(info_json_path) if info_json_path is not None else None
    return DownloadResult(
        video_path=video_path,
        english_subtitle_path=english_subtitle_path,
        chinese_subtitle_path=chinese_subtitle_path,
        info_json_path=info_json_path,
        thumbnail_path=thumbnail_path,
        source_metadata=source_metadata,
    )


def _pick_latest_video(source_dir: Path) -> Path:
    candidates = [
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not candidates:
        raise FileNotFoundError("yt-dlp completed but no video file was found in source/")
    return max(candidates, key=_video_sort_key)


def _video_sort_key(path: Path) -> tuple[int, int, int, int, int, float]:
    width, height = _probe_video_size(path)
    clamped_height = min(height, 1080)
    clamped_width = min(width, 1920)
    exact_1080p = int(height == 1080)
    hd_bucket = int(height >= 720)
    mp4_priority = int(path.suffix.lower() == ".mp4")
    return (
        exact_1080p,
        clamped_height,
        clamped_width,
        hd_bucket,
        mp4_priority,
        path.stat().st_mtime,
    )


def _probe_video_size(path: Path) -> tuple[int, int]:
    try:
        output = run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            log_command=False,
        )
        payload = json.loads(output)
    except (subprocess.CalledProcessError, ValueError, KeyError, json.JSONDecodeError):
        return (0, 0)

    streams = payload.get("streams") or []
    if not streams:
        return (0, 0)
    stream = streams[0]
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return (0, 0)
    return (max(0, width), max(0, height))


def _pick_best_subtitle(source_dir: Path, video_stem: str, languages: tuple[str, ...]) -> Path | None:
    candidates = [path for path in source_dir.glob("*.vtt") if "live_chat" not in path.name.lower()]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int, int, float]:
        name = path.name.lower()
        exact_match = int(path.stem.startswith(video_stem))
        lang_score = 0
        for index, language in enumerate(languages):
            normalized = language.lower()
            if f".{normalized}." in name or f".{normalized}-" in name or f".{normalized}_" in name:
                lang_score = len(languages) - index
                break
        return (exact_match, lang_score, len(name), path.stat().st_mtime)

    matched = [path for path in candidates if score(path)[1] > 0]
    if not matched:
        return None
    return max(matched, key=score)


def _pick_related_file(source_dir: Path, video_stem: str, extensions: tuple[str, ...]) -> Path | None:
    candidates = []
    for path in source_dir.iterdir():
        if not path.is_file():
            continue
        suffix = "".join(path.suffixes).lower()
        if suffix in extensions:
            candidates.append(path)
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, int, float]:
        stem = path.name[: -len("".join(path.suffixes))] if path.suffixes else path.stem
        exact_match = int(stem == video_stem)
        prefix_match = int(stem.startswith(video_stem) or video_stem.startswith(stem))
        return (exact_match, prefix_match, path.stat().st_mtime)

    matched = [path for path in candidates if score(path)[:2] != (0, 0)]
    if not matched:
        return None
    return max(matched, key=score)

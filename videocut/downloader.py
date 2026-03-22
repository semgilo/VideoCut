from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from videocut.models import VideoMetadata
from videocut.publish import load_video_metadata
from videocut.shell import run_command


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
    run_command(
        [
            "yt-dlp",
            "--no-playlist",
            "--remote-components",
            YTDLP_REMOTE_COMPONENTS,
            "--write-subs",
            "--write-auto-subs",
            "--write-info-json",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "--sub-langs",
            "en.*,en",
            "--convert-subs",
            "vtt",
            "-f",
            YTDLP_PREFERRED_VIDEO_FORMAT,
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            url,
        ]
    )
    if include_chinese_subtitles:
        try:
            run_command(
                [
                    "yt-dlp",
                    "--skip-download",
                    "--remote-components",
                    YTDLP_REMOTE_COMPONENTS,
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    "zh-Hans.*,zh-Hans,zh-CN.*,zh-CN,zh-Hant.*,zh-Hant",
                    "--convert-subs",
                    "vtt",
                    "-o",
                    output_template,
                    url,
                ]
            )
        except subprocess.CalledProcessError as error:
            print(f"Warning: could not download Chinese subtitle track, continuing without it: {error}")

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
    return DownloadResult(
        video_path=video_path,
        english_subtitle_path=english_subtitle_path,
        chinese_subtitle_path=chinese_subtitle_path,
        info_json_path=info_json_path,
        thumbnail_path=thumbnail_path,
        source_metadata=source_metadata,
    )


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

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from videocut.shell import run_command


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")


@dataclass(slots=True)
class DownloadResult:
    video_path: Path
    english_subtitle_path: Path | None
    chinese_subtitle_path: Path | None


def download_youtube_assets(url: str, source_dir: Path, include_chinese_subtitles: bool = False) -> DownloadResult:
    source_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(source_dir / "%(title).120B [%(id)s].%(ext)s")
    run_command(
        [
            "yt-dlp",
            "--no-playlist",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en.*,en",
            "--convert-subs",
            "vtt",
            "-f",
            "bv*+ba/b",
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
    return DownloadResult(
        video_path=video_path,
        english_subtitle_path=english_subtitle_path,
        chinese_subtitle_path=chinese_subtitle_path,
    )


def _pick_latest_video(source_dir: Path) -> Path:
    candidates = [
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not candidates:
        raise FileNotFoundError("yt-dlp completed but no video file was found in source/")
    return max(candidates, key=lambda path: path.stat().st_mtime)


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

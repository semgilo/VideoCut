"""Generate the e2e smoke test fixture by trimming an existing run's source assets.

Places the fixture under ``~/.openclaw/tmp/mc-runs/test-fixture/source/``,
keeping the original yt-dlp file names so the standard run layout is preserved.

Usage:
    python scripts/prepare_test_fixture.py
    python scripts/prepare_test_fixture.py --source-run runs/<some-run>/ --duration 30
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_RUN = REPO_ROOT / "runs" / "hwuapiVeDhY-subtitle-only-20260501"
FIXTURE_RUN_DIR = Path.home() / ".openclaw" / "tmp" / "mc-runs" / "test-fixture"
FIXTURE_SOURCE_DIR = FIXTURE_RUN_DIR / "source"
DEFAULT_DURATION_SEC = 30

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm")
THUMBNAIL_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _resolve_ffmpeg() -> str:
    candidates = [
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return "ffmpeg"


def _pick_one(source_dir: Path, suffixes: tuple[str, ...]) -> Path:
    for path in source_dir.iterdir():
        if path.is_file() and path.suffix.lower() in suffixes:
            return path
    raise FileNotFoundError(f"No file with suffix {suffixes} found in {source_dir}")


def _pick_vtt(source_dir: Path) -> Path:
    candidates = sorted(
        p for p in source_dir.glob("*.en*.vtt") if "live_chat" not in p.name.lower()
    )
    if not candidates:
        raise FileNotFoundError(f"No .en*.vtt file found in {source_dir}")
    return candidates[0]


def _pick_info_json(source_dir: Path) -> Path:
    for path in source_dir.iterdir():
        if path.is_file() and path.name.endswith(".info.json"):
            return path
    raise FileNotFoundError(f"No .info.json file found in {source_dir}")


def _trim_video(ffmpeg_bin: str, src: Path, dst: Path, duration_sec: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-t",
        str(duration_sec),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def _parse_vtt_timestamp(stamp: str) -> float:
    parts = stamp.split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


_CUE_TIMING_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)


def _trim_vtt(src: Path, dst: Path, duration_sec: int) -> int:
    text = src.read_text(encoding="utf-8")
    blocks = text.split("\n\n")
    if not blocks:
        raise RuntimeError(f"VTT file {src} is empty")

    header = blocks[0]
    if not header.lstrip().startswith("WEBVTT"):
        raise RuntimeError(f"VTT file {src} missing WEBVTT header")

    kept_cues: list[str] = []
    for block in blocks[1:]:
        stripped = block.strip()
        if not stripped:
            continue
        first_line = stripped.splitlines()[0]
        m = _CUE_TIMING_RE.match(first_line)
        if not m:
            kept_cues.append(block)
            continue
        start_sec = _parse_vtt_timestamp(m.group(1))
        if start_sec < duration_sec:
            kept_cues.append(block)

    output = "\n\n".join([header, *kept_cues]) + "\n"
    dst.write_text(output, encoding="utf-8")
    return len(kept_cues)


def _ensure_clean_fixture_dir() -> None:
    if FIXTURE_SOURCE_DIR.exists():
        shutil.rmtree(FIXTURE_SOURCE_DIR, ignore_errors=True)
    FIXTURE_SOURCE_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-run",
        type=Path,
        default=DEFAULT_SOURCE_RUN,
        help=f"Existing run directory containing source/ (default: {DEFAULT_SOURCE_RUN.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_SEC,
        help=f"Trim duration in seconds (default: {DEFAULT_DURATION_SEC})",
    )
    args = parser.parse_args()

    source_run: Path = args.source_run.expanduser().resolve()
    source_dir = source_run / "source"
    if not source_dir.is_dir():
        print(f"ERROR: {source_dir} is not a directory", file=sys.stderr)
        return 1

    src_video = _pick_one(source_dir, VIDEO_EXTS)
    src_vtt = _pick_vtt(source_dir)
    src_info = _pick_info_json(source_dir)
    src_thumb = _pick_one(source_dir, THUMBNAIL_EXTS)

    _ensure_clean_fixture_dir()

    ffmpeg_bin = _resolve_ffmpeg()
    dst_video = FIXTURE_SOURCE_DIR / src_video.name
    dst_vtt = FIXTURE_SOURCE_DIR / src_vtt.name
    dst_info = FIXTURE_SOURCE_DIR / src_info.name
    dst_thumb = FIXTURE_SOURCE_DIR / src_thumb.name

    _trim_video(ffmpeg_bin, src_video, dst_video, args.duration)
    cue_count = _trim_vtt(src_vtt, dst_vtt, args.duration)
    shutil.copy2(src_info, dst_info)
    shutil.copy2(src_thumb, dst_thumb)

    # Clean up legacy repo-local fixture if it exists
    legacy_fixture = REPO_ROOT / "tests" / "fixtures"
    if legacy_fixture.exists():
        shutil.rmtree(legacy_fixture)
        print(f"Cleaned legacy fixture: {legacy_fixture}")

    print()
    print(f"Fixture written to {FIXTURE_SOURCE_DIR}:")
    print(f"  {dst_video.name}  ({dst_video.stat().st_size / 1024 / 1024:.1f} MB, trimmed to {args.duration}s)")
    print(f"  {dst_vtt.name}    ({cue_count} cues kept)")
    print(f"  {dst_info.name} (copied)")
    print(f"  {dst_thumb.name}    (copied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import subprocess
from pathlib import Path


_FFMPEG_FULL_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
]
_FFPROBE_FULL_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
    "/usr/local/opt/ffmpeg-full/bin/ffprobe",
]


def _find_binary(candidates: list[str], fallback: str) -> str:
    for path in candidates:
        if Path(path).exists():
            return path
    return fallback


_FFMPEG_BIN: str = _find_binary(_FFMPEG_FULL_CANDIDATES, "ffmpeg")
_FFPROBE_BIN: str = _find_binary(_FFPROBE_FULL_CANDIDATES, "ffprobe")


def resolve_tool_binary(name: str) -> str:
    if name == "ffmpeg":
        return _FFMPEG_BIN
    if name == "ffprobe":
        return _FFPROBE_BIN
    return name


def _normalize_command(args: list[str]) -> list[str]:
    if not args:
        return args
    return [resolve_tool_binary(args[0]), *args[1:]]


def run_command(
    args: list[str],
    cwd: Path | None = None,
    capture_output: bool = False,
    log_command: bool = True,
    env: dict[str, str] | None = None,
) -> str:
    resolved_args = _normalize_command(args)
    if log_command:
        print("$", " ".join(resolved_args))
    completed = subprocess.run(
        resolved_args,
        cwd=cwd,
        env=None if env is None else {**os.environ, **env},
        check=True,
        text=True,
        capture_output=capture_output,
    )
    if capture_output:
        return completed.stdout.strip()
    return ""

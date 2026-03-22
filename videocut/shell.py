from __future__ import annotations

import os
import subprocess
from pathlib import Path


def resolve_tool_binary(name: str) -> str:
    env_key = {
        "ffmpeg": "VIDEOCUT_FFMPEG_BIN",
        "ffprobe": "VIDEOCUT_FFPROBE_BIN",
    }.get(name)
    if env_key is None:
        return name
    configured = os.getenv(env_key, "").strip()
    return configured or name


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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import tempfile
from pathlib import Path

from videocut.shell import resolve_tool_binary


DEFAULT_VOICE = "Eddy (Chinese (China mainland))"
DEFAULT_RATE = "180"
DEFAULT_CONCURRENCY = "4"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synthesize TTS segments with macOS say from a VideoCut command-provider manifest.",
    )
    parser.add_argument("--input-json", required=True, type=Path, help="Path to tts_command_inputs.json")
    parser.add_argument(
        "--voice",
        default=os.environ.get("VIDEOCUT_SAY_VOICE", DEFAULT_VOICE),
        help="macOS say voice name",
    )
    parser.add_argument(
        "--rate",
        default=os.environ.get("VIDEOCUT_SAY_RATE", DEFAULT_RATE),
        help="macOS say rate in words per minute",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("VIDEOCUT_SAY_CONCURRENCY", DEFAULT_CONCURRENCY)),
        help="Number of concurrent say workers",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    segments = payload.get("segments") or []
    total = len(segments)
    if not isinstance(segments, list) or not segments:
        raise RuntimeError(f"No segments found in {args.input_json}")

    jobs: list[tuple[str, Path]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        output_path = Path(str(segment.get("audio_path") or "")).expanduser()
        if not text or not output_path:
            raise RuntimeError(f"Invalid segment payload: {segment}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        jobs.append((text, output_path))

    max_workers = max(1, args.concurrency)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                synthesize_with_say,
                text=text,
                output_path=output_path,
                voice=args.voice,
                rate=args.rate,
            )
            for text, output_path in jobs
        ]
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            future.result()
            completed += 1
            if completed == 1 or completed % 25 == 0 or completed == total:
                print(f"Synthesized {completed}/{total} TTS segments with macOS say")


def synthesize_with_say(text: str, output_path: Path, voice: str, rate: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_aiff = Path(temp_dir) / "segment.aiff"
        subprocess.run(
            ["say", "-v", voice, "-r", str(rate), "-o", str(temp_aiff), text],
            check=True,
        )
        subprocess.run(
            [
                resolve_tool_binary("ffmpeg"),
                "-y",
                "-v",
                "error",
                "-i",
                str(temp_aiff),
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()

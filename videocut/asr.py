from __future__ import annotations

from pathlib import Path

from videocut.models import Segment
from videocut.shell import run_command


def extract_audio_for_asr(video_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return output_path


def transcribe_with_faster_whisper(
    audio_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "No English subtitle track was found and faster-whisper is not installed. "
            "Install it with: uv pip install -e '.[asr]'"
        ) from error

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    raw_segments, _ = model.transcribe(
        str(audio_path),
        language="en",
        vad_filter=True,
    )

    segments: list[Segment] = []
    for index, segment in enumerate(raw_segments, start=1):
        text = segment.text.strip()
        if not text:
            continue
        segments.append(
            Segment(
                index=index,
                start=float(segment.start),
                end=float(segment.end),
                english=text,
            )
        )
    return segments

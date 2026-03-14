from __future__ import annotations

import json
from pathlib import Path

from videocut.models import Segment
from videocut.shell import run_command


def ffprobe_duration(path: Path) -> float:
    output = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        log_command=False,
    )
    return float(output)


def compose_dubbed_track(
    video_path: Path,
    segments: list[Segment],
    output_path: Path,
    original_volume: float,
    dub_volume: float,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    filters: list[str] = []
    include_original = original_volume > 0
    video_duration = ffprobe_duration(video_path)
    if include_original:
        filters.append(f"[0:a]volume={original_volume}[orig]")
    else:
        filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{video_duration:.3f}[base]")
    dub_labels: list[str] = []

    for input_index, segment in enumerate(segments, start=1):
        if segment.audio_path is None:
            raise RuntimeError(f"Segment {segment.index} does not have a synthesized audio file")
        if segment.synthetic_duration is None:
            raise RuntimeError(f"Segment {segment.index} is missing synthesized duration")
        cmd.extend(["-i", str(segment.audio_path)])
        atempo = _build_atempo_chain(segment.playback_rate)
        delay_ms = int(segment.render_start * 1000)
        label = f"dub{input_index}"
        filters.append(
            f"[{input_index}:a]{atempo},apad=pad_dur={segment.render_duration:.3f},"
            f"atrim=0:{segment.render_duration:.3f},adelay={delay_ms}|{delay_ms},"
            f"volume={dub_volume}[{label}]"
        )
        dub_labels.append(f"[{label}]")

    if dub_labels:
        filters.append(f"{''.join(dub_labels)}amix=inputs={len(dub_labels)}:normalize=0[dubs]")
        if include_original:
            filters.append("[orig][dubs]amix=inputs=2:normalize=0[aout]")
        else:
            filters.append("[base][dubs]amix=inputs=2:normalize=0[aout]")
    else:
        if include_original:
            filters.append("[orig]anull[aout]")
        else:
            raise RuntimeError("No dubbed audio segments were generated and original audio is muted.")

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
    )
    run_command(cmd)
    return output_path


def render_final_video(
    video_path: Path,
    dubbed_track_path: Path,
    subtitle_path: Path,
    output_path: Path,
    burn_subtitles: bool,
    subtitle_font: str,
    subtitle_font_size: int,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if burn_subtitles and _ffmpeg_has_subtitles_filter():
        subtitle_filter = (
            f"subtitles=filename='{_escape_filter_path(subtitle_path.resolve())}':"
            f"force_style='FontName={subtitle_font},Fontsize={subtitle_font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H64000000,"
            "Outline=1,Shadow=0,Alignment=2,MarginV=24'"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(dubbed_track_path),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
        run_command(cmd)
        return output_path

    if burn_subtitles:
        print("Warning: ffmpeg subtitles filter is unavailable. Falling back to soft subtitles.")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(dubbed_track_path),
        "-i",
        str(subtitle_path),
    ]
    cmd.extend(
        [
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:0",
            "-c:a",
            "aac",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=zho",
            "-shortest",
            str(output_path),
        ]
    )
    run_command(cmd)
    return output_path


def write_manifest(
    path: Path,
    source_video: Path,
    subtitle_source: Path | None,
    generated_srt: Path,
    dubbed_track: Path,
    final_video: Path,
    segments: list[Segment],
) -> None:
    payload = {
        "source_video": str(source_video),
        "subtitle_source": str(subtitle_source) if subtitle_source else None,
        "generated_srt": str(generated_srt),
        "dubbed_track": str(dubbed_track),
        "final_video": str(final_video),
        "segments": [
            {
                "index": segment.index,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "scheduled_start": round(segment.render_start, 3),
                "scheduled_end": round(segment.render_end, 3),
                "playback_rate": round(segment.playback_rate, 4),
                "english": segment.english,
                "chinese": segment.chinese,
                "audio_path": str(segment.audio_path) if segment.audio_path else None,
            }
            for segment in segments
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_atempo_chain(factor: float) -> str:
    if factor <= 0:
        raise ValueError(f"Invalid atempo factor: {factor}")
    filters: list[str] = []
    while factor < 0.5:
        filters.append("atempo=0.5")
        factor /= 0.5
    while factor > 2.0:
        filters.append("atempo=2.0")
        factor /= 2.0
    filters.append(f"atempo={factor:.6f}")
    return ",".join(filters)


def _escape_filter_path(path: Path) -> str:
    value = str(path)
    value = value.replace("\\", "\\\\")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    value = value.replace(",", r"\,")
    value = value.replace("[", r"\[")
    value = value.replace("]", r"\]")
    return value


def _ffmpeg_has_subtitles_filter() -> bool:
    output = run_command(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        log_command=False,
    )
    return " subtitles " in output or "\n... subtitles" in output

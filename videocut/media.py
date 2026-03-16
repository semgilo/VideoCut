from __future__ import annotations

import json
import subprocess
from pathlib import Path

from videocut.models import Segment, VideoMetadata
from videocut.publish import metadata_to_dict
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


def finalize_synthesized_segments(
    segments: list[Segment],
    trim_silence: bool,
    silence_threshold_db: float,
    min_silence_duration: float,
    keep_silence: float,
) -> tuple[int, float, float]:
    trimmed_segments = 0
    total_leading_trim = 0.0
    total_trailing_trim = 0.0

    for segment in segments:
        if segment.audio_path is None:
            raise RuntimeError(f"Segment {segment.index} did not produce an audio file")
        if trim_silence:
            leading_trim, trailing_trim = trim_audio_silence_in_place(
                path=segment.audio_path,
                silence_threshold_db=silence_threshold_db,
                min_silence_duration=min_silence_duration,
                keep_silence=keep_silence,
            )
            if leading_trim > 0 or trailing_trim > 0:
                trimmed_segments += 1
                total_leading_trim += leading_trim
                total_trailing_trim += trailing_trim
        segment.synthetic_duration = ffprobe_duration(segment.audio_path)
        segment.leading_silence, segment.trailing_silence = detect_audio_edge_silence(
            path=segment.audio_path,
            silence_threshold_db=silence_threshold_db,
            min_silence_duration=min_silence_duration,
        )
        segment.leading_silence = min(segment.leading_silence, segment.synthetic_duration)
        max_trailing = max(0.0, segment.synthetic_duration - segment.leading_silence)
        segment.trailing_silence = min(segment.trailing_silence, max_trailing)

    return trimmed_segments, total_leading_trim, total_trailing_trim


def trim_audio_silence_in_place(
    path: Path,
    silence_threshold_db: float,
    min_silence_duration: float,
    keep_silence: float,
) -> tuple[float, float]:
    total_leading_trim = 0.0
    total_trailing_trim = 0.0

    for _ in range(3):
        leading_silence, trailing_silence = detect_audio_edge_silence(
            path=path,
            silence_threshold_db=silence_threshold_db,
            min_silence_duration=min_silence_duration,
        )
        leading_trim = max(0.0, leading_silence - keep_silence)
        trailing_trim = max(0.0, trailing_silence - keep_silence)
        if leading_trim <= 0.0 and trailing_trim <= 0.0:
            break

        output_path = path.with_name(f"{path.stem}.trimmed{path.suffix}")
        filter_chain = (
            "silenceremove="
            f"start_periods=1:start_duration={min_silence_duration:.3f}:"
            f"start_threshold={silence_threshold_db}dB:start_silence={keep_silence:.3f},"
            "areverse,"
            "silenceremove="
            f"start_periods=1:start_duration={min_silence_duration:.3f}:"
            f"start_threshold={silence_threshold_db}dB:start_silence={keep_silence:.3f},"
            "areverse"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-af",
            filter_chain,
            *_audio_codec_args_for_path(path),
            str(output_path),
        ]
        subprocess.run(cmd, check=True, text=True, capture_output=True)

        trimmed_duration = ffprobe_duration(output_path)
        if trimmed_duration < 0.01:
            output_path.unlink(missing_ok=True)
            break

        output_path.replace(path)
        total_leading_trim += leading_trim
        total_trailing_trim += trailing_trim

    return total_leading_trim, total_trailing_trim


def detect_audio_edge_silence(
    path: Path,
    silence_threshold_db: float,
    min_silence_duration: float,
) -> tuple[float, float]:
    duration = ffprobe_duration(path)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={silence_threshold_db}dB:d={min_silence_duration:.3f}",
            "-f",
            "null",
            "-",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    output = completed.stderr

    silence_starts: list[float] = []
    silence_ends: list[float] = []
    for line in output.splitlines():
        if "silence_start:" in line:
            silence_starts.append(float(line.split("silence_start:")[1].strip().split()[0]))
        if "silence_end:" in line:
            silence_ends.append(float(line.split("silence_end:")[1].split("|")[0].strip()))

    leading_silence = 0.0
    if silence_starts and silence_ends and abs(silence_starts[0]) < 0.001:
        leading_silence = silence_ends[0]

    trailing_silence = 0.0
    if silence_starts:
        last_start = silence_starts[-1]
        last_end = silence_ends[-1] if silence_ends else -1.0
        if silence_ends and abs(last_end - duration) < 0.02:
            trailing_silence = max(0.0, duration - last_start)
        elif last_start > last_end:
            trailing_silence = max(0.0, duration - last_start)

    return leading_silence, trailing_silence


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
        render_leading_silence = min(
            max(0.0, segment.leading_silence / segment.playback_rate),
            segment.render_start,
        )
        render_trailing_silence = min(
            max(0.0, segment.trailing_silence / segment.playback_rate),
            max(0.0, segment.render_duration - render_leading_silence - 0.01),
        )
        play_duration = max(0.01, segment.render_duration - render_trailing_silence)
        delay_ms = int(max(0.0, segment.render_start - render_leading_silence) * 1000)
        label = f"dub{input_index}"
        filters.append(
            f"[{input_index}:a]{atempo},apad=pad_dur={play_duration:.3f},"
            f"atrim=0:{play_duration:.3f},adelay={delay_ms}|{delay_ms},"
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
    thumbnail_source: Path | None,
    generated_srt: Path,
    dubbed_track: Path,
    final_video: Path,
    segments: list[Segment],
    source_metadata: VideoMetadata | None = None,
    localized_metadata: VideoMetadata | None = None,
    publish_assets: dict[str, str | None] | None = None,
) -> None:
    payload = {
        "source_video": str(source_video),
        "subtitle_source": str(subtitle_source) if subtitle_source else None,
        "thumbnail_source": str(thumbnail_source) if thumbnail_source else None,
        "generated_srt": str(generated_srt),
        "dubbed_track": str(dubbed_track),
        "final_video": str(final_video),
        "source_metadata": metadata_to_dict(source_metadata),
        "localized_metadata": metadata_to_dict(localized_metadata),
        "publish_assets": publish_assets or {},
        "segments": [
            {
                "index": segment.index,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "scheduled_start": round(segment.render_start, 3),
                "scheduled_end": round(segment.render_end, 3),
                "playback_rate": round(segment.playback_rate, 4),
                "synthetic_duration": (
                    round(segment.synthetic_duration, 3) if segment.synthetic_duration is not None else None
                ),
                "leading_silence": round(segment.leading_silence, 3),
                "trailing_silence": round(segment.trailing_silence, 3),
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


def _audio_codec_args_for_path(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return ["-c:a", "pcm_s16le"]
    if suffix == ".mp3":
        return ["-c:a", "libmp3lame", "-q:a", "2"]
    if suffix in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "192k"]
    return []


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

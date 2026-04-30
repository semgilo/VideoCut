from __future__ import annotations

import json
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from videocut.models import Segment, VideoMetadata
from videocut.publish import metadata_to_dict
from videocut.shell import resolve_tool_binary, run_command


def ffprobe_duration(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                if frame_rate <= 0:
                    raise RuntimeError(f"Invalid WAV frame rate for {path}")
                return frame_count / frame_rate
        except (wave.Error, EOFError):
            # CosyVoice may emit float PCM WAVs that Python's wave module
            # does not decode; fall back to ffprobe for those files.
            pass
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


def ffprobe_video_size(path: Path) -> tuple[int, int]:
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
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"Could not determine video size for {path}")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid video size reported for {path}: {payload}")
    return width, height


def measure_synthesized_segments(segments: list[Segment]) -> None:
    """Measure and store synthetic_duration for each segment after CosyVoice synthesis."""
    if not segments:
        return
    for segment in segments:
        if segment.audio_path is None:
            raise RuntimeError(f"Segment {segment.index} did not produce an audio file")
    max_workers = min(8, len(segments))
    if max_workers <= 1:
        for segment in segments:
            segment.synthetic_duration = ffprobe_duration(segment.audio_path)
            segment.leading_silence = 0.0
            segment.trailing_silence = 0.0
        return
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_segment = {
            executor.submit(ffprobe_duration, segment.audio_path): segment
            for segment in segments
        }
        for future in as_completed(future_to_segment):
            segment = future_to_segment[future]
            segment.synthetic_duration = future.result()
            segment.leading_silence = 0.0
            segment.trailing_silence = 0.0


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
        delay_ms = int(max(0.0, segment.render_start) * 1000)
        label = f"dub{input_index}"
        filters.append(
            f"[{input_index}:a]{atempo},adelay={delay_ms}|{delay_ms},"
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
    subtitle_font_path: str,
    subtitle_font_size: int,
    video_preset: str = "medium",
    video_crf: int = 20,
    subtitle_overlay_concurrency: int = 1,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_codec_args = _build_video_codec_args(video_preset=video_preset, video_crf=video_crf)
    video_duration = ffprobe_duration(video_path)
    if burn_subtitles and _ffmpeg_has_subtitles_filter():
        # Build fontsdir argument to avoid fontconfig hangs on macOS.
        # fontconfig is not the native font system on macOS and known to hang libass
        # when cache is missing or font name cannot be resolved.
        fontsdir_arg = ""
        if subtitle_font_path:
            font_dir = str(Path(subtitle_font_path).resolve().parent)
            fontsdir_arg = f":fontsdir='{_escape_filter_path(font_dir)}'"
        else:
            # Fallback: macOS system font directory
            mac_fonts = Path("/System/Library/Fonts")
            if mac_fonts.is_dir():
                fontsdir_arg = f":fontsdir='{mac_fonts}'"

        subtitle_filter = (
            f"subtitles=filename='{_escape_filter_path(subtitle_path.resolve())}'"
            f"{fontsdir_arg}:"
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
            "-i",
            str(subtitle_path),
            "-vf",
            subtitle_filter,
            *video_codec_args,
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
            "-metadata:s:s:0",
            "title=Chinese",
            "-disposition:s:0",
            "default",
            "-t",
            f"{video_duration:.3f}",
            str(output_path),
        ]
        run_command(cmd, timeout=_render_timeout(video_path))
        _verify_video_output(output_path)
        return output_path

    if burn_subtitles:
        try:
            return _render_final_video_with_overlay_subtitles(
                video_path=video_path,
                dubbed_track_path=dubbed_track_path,
                subtitle_path=subtitle_path,
                output_path=output_path,
                subtitle_font_path=subtitle_font_path,
                subtitle_font_size=subtitle_font_size,
                video_preset=video_preset,
                video_crf=video_crf,
                subtitle_overlay_concurrency=subtitle_overlay_concurrency,
            )
        except ImportError:
            print(
                "Warning: ffmpeg subtitles filter is unavailable and Pillow is not installed. "
                "Falling back to soft subtitles."
            )
        except Exception as error:
            print(f"Warning: hard-subtitle overlay fallback failed, falling back to soft subtitles: {error}")

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
            "-metadata:s:s:0",
            "title=Chinese",
            "-disposition:s:0",
            "default",
            "-t",
            f"{video_duration:.3f}",
            str(output_path),
        ]
    )
    run_command(cmd, timeout=_render_timeout(video_path))
    _verify_video_output(output_path)
    return output_path


@dataclass(slots=True)
class SubtitleCue:
    start: float
    end: float
    text: str
    image_path: Path


def _render_final_video_with_overlay_subtitles(
    video_path: Path,
    dubbed_track_path: Path,
    subtitle_path: Path,
    output_path: Path,
    subtitle_font_path: str,
    subtitle_font_size: int,
    video_preset: str,
    video_crf: int,
    subtitle_overlay_concurrency: int,
) -> Path:
    cues = _load_srt_cues(subtitle_path)
    if not cues:
        raise RuntimeError(f"No subtitle cues were found in {subtitle_path}")

    width, height = ffprobe_video_size(video_path)
    overlay_dir = subtitle_path.parent / "burn_overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    font_path = _resolve_subtitle_font_path(subtitle_font_path)
    video_duration = ffprobe_duration(video_path)

    pending_cues: list[SubtitleCue] = []
    for index, cue in enumerate(cues, start=1):
        cue.image_path = overlay_dir / f"{index:04d}.png"
        if cue.image_path.exists() and cue.image_path.stat().st_size > 0:
            continue
        pending_cues.append(cue)

    if pending_cues:
        max_workers = max(1, min(subtitle_overlay_concurrency, len(pending_cues)))
        if max_workers == 1:
            for cue in pending_cues:
                _render_subtitle_overlay_image(
                    output_path=cue.image_path,
                    width=width,
                    height=height,
                    text=cue.text,
                    font_path=font_path,
                    subtitle_font_size=subtitle_font_size,
                )
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(
                        _render_subtitle_overlay_image,
                        output_path=cue.image_path,
                        width=width,
                        height=height,
                        text=cue.text,
                        font_path=font_path,
                        subtitle_font_size=subtitle_font_size,
                    )
                    for cue in pending_cues
                ]
                for future in futures:
                    future.result()

    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(dubbed_track_path)]
    filters: list[str] = []
    current_label = "0:v"
    for input_index, cue in enumerate(cues, start=2):
        cmd.extend(
            [
                "-loop",
                "1",
                "-t",
                f"{video_duration:.3f}",
                "-i",
                str(cue.image_path),
            ]
        )
        next_label = f"v{input_index - 1}"
        filters.append(
            f"[{current_label}][{input_index}:v]"
            f"overlay=0:0:enable='between(t,{cue.start:.3f},{cue.end:.3f})'"
            f"[{next_label}]"
        )
        current_label = next_label

    cmd.extend(["-i", str(subtitle_path)])
    subtitle_input_index = len(cues) + 2
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{current_label}]",
            "-map",
            "1:a:0",
            "-map",
            f"{subtitle_input_index}:0",
            *_build_video_codec_args(video_preset=video_preset, video_crf=video_crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=zho",
            "-metadata:s:s:0",
            "title=Chinese",
            "-disposition:s:0",
            "default",
            "-t",
            f"{video_duration:.3f}",
            str(output_path),
        ]
    )
    run_command(cmd, timeout=_render_timeout(video_path))
    _verify_video_output(output_path)
    return output_path


def _verify_video_output(path: Path) -> None:
    """Check the output video is playable and has the expected streams.

    Called after every ffmpeg render so stalls (watchdog-kill) don't silently
    produce a truncated file.  Raises RuntimeError if the file is missing,
    empty, or has no video stream.
    """
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Render output missing or empty: {path}")

    duration = ffprobe_duration(path)
    if duration <= 0:
        raise RuntimeError(
            f"Render output has zero/negative duration ({duration}s): {path}"
        )


def _render_timeout(video_path: Path) -> float:
    """Return a timeout in seconds for ffmpeg render commands, proportional to video duration.

    Shorter timeout helps detect hangs (e.g. -shortest / subtitle mux stall) early.
    Max 30 min — if encoding takes longer, something is wrong.
    """
    duration = ffprobe_duration(video_path)
    return max(300.0, min(duration * 5, 1800.0))


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


def _build_video_codec_args(video_preset: str, video_crf: int) -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        video_preset,
        "-crf",
        str(video_crf),
    ]


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


def _load_srt_cues(path: Path) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    raw_blocks = path.read_text(encoding="utf-8", errors="ignore").strip().split("\n\n")
    for block in raw_blocks:
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        timestamp_line_index = next((index for index, line in enumerate(lines) if "-->" in line), -1)
        if timestamp_line_index < 0:
            continue
        timestamp_line = lines[timestamp_line_index]
        try:
            start_raw, end_raw = [part.strip() for part in timestamp_line.split("-->", 1)]
            start = _parse_srt_timestamp(start_raw)
            end = _parse_srt_timestamp(end_raw)
        except ValueError:
            continue
        text = "\n".join(lines[timestamp_line_index + 1 :]).strip()
        if not text:
            continue
        cues.append(SubtitleCue(start=start, end=end, text=text, image_path=Path()))
    return cues


def _parse_srt_timestamp(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _render_subtitle_overlay_image(
    output_path: Path,
    width: int,
    height: int,
    text: str,
    font_path: Path,
    subtitle_font_size: int,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    resolved_font_size = max(subtitle_font_size, int(height * 0.035))
    font = ImageFont.truetype(str(font_path), size=resolved_font_size)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    spacing = max(6, resolved_font_size // 5)
    stroke_width = max(2, resolved_font_size // 14)
    text_bbox = draw.multiline_textbbox(
        (0, 0),
        text,
        font=font,
        align="center",
        spacing=spacing,
        stroke_width=stroke_width,
    )
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    padding_x = max(24, resolved_font_size // 2)
    padding_y = max(14, resolved_font_size // 4)
    x = (width - text_width) / 2
    y = height - int(height * 0.09) - text_height
    box = (
        int(max(0, x - padding_x)),
        int(max(0, y - padding_y)),
        int(min(width, x + text_width + padding_x)),
        int(min(height, y + text_height + padding_y)),
    )
    draw.rounded_rectangle(box, radius=max(12, resolved_font_size // 3), fill=(0, 0, 0, 156))
    draw.multiline_text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        align="center",
        spacing=spacing,
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 255),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _resolve_subtitle_font_path(subtitle_font_path: str) -> Path:
    if subtitle_font_path.strip():
        path = Path(subtitle_font_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Subtitle font file not found: {path}")
        return path

    candidates = [
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "No subtitle font file could be auto-detected for the Pillow overlay fallback. "
        "Set VIDEOCUT_SUBTITLE_FONT_PATH or pass --subtitle-font-path."
    )


def compress_for_publish(
    input_path: Path,
    output_path: Path,
    target_size_mb: int = 500,
    max_width: int = 1920,
    max_height: int = 1080,
) -> Path:
    """Compress video to target size and resolution using two-pass VBR.

    Uses two-pass encoding to hit the target file size precisely.
    The bitrate is calculated dynamically based on video duration:
    - Short videos get higher bitrate (better quality)
    - Long videos are capped to fit within target_size_mb

    Returns the output path (same as output_path parameter).
    """
    if output_path.exists():
        return output_path

    duration = ffprobe_duration(input_path)
    if duration <= 0:
        raise RuntimeError(f"Cannot determine duration of {input_path}")

    audio_bitrate_k = 128
    total_bitrate_k = int((target_size_mb * 8192) / duration)
    video_bitrate_k = max(total_bitrate_k - audio_bitrate_k, 100)

    # Scale filter only if needed
    in_w, in_h = ffprobe_video_size(input_path)
    if in_w > max_width or in_h > max_height:
        scale = (
            f"scale='min({max_width},iw)':'min({max_height},ih)':"
            f"force_original_aspect_ratio=decrease"
        )
    else:
        scale = "null"

    # Pass 1: analysis
    run_command(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", "libx264", "-preset", "slow",
            "-b:v", f"{video_bitrate_k}k",
            "-vf", scale,
            "-pass", "1", "-an", "-f", "mp4", "/dev/null",
        ],
        log_command=True,
    )

    # Pass 2: encoding
    run_command(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", "libx264", "-preset", "slow",
            "-b:v", f"{video_bitrate_k}k",
            "-vf", scale,
            "-c:a", "aac", "-b:a", f"{audio_bitrate_k}k",
            "-movflags", "+faststart",
            "-pass", "2", str(output_path),
        ],
        log_command=True,
    )

    # Clean up analysis files
    for f in (input_path.parent / "ffmpeg2pass-0.log",
              input_path.parent / "ffmpeg2pass-0.log.mbtree"):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    return output_path

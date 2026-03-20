from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time
from pathlib import Path
from uuid import uuid4

import requests

from videocut.config import PipelineConfig
from videocut.media import ffprobe_duration
from videocut.models import Segment
from videocut.shell import run_command


async def _synthesize_segments_with_edge_async(
    segments: list[Segment],
    output_dir: Path,
    voice: str,
    rate: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(segments)
    reused = 0
    for index, segment in enumerate(segments, start=1):
        if not segment.chinese:
            raise RuntimeError(f"Segment {segment.index} is missing Chinese text for TTS")
        segment.audio_path = output_dir / f"{segment.index:04d}.mp3"
        if segment.audio_path.exists() and segment.audio_path.stat().st_size > 0:
            reused += 1
            continue
        await _save_edge_tts_with_retries(
            text=segment.chinese,
            output_path=segment.audio_path,
            voice=voice,
            rate=rate,
        )
        if index == 1 or index % 25 == 0 or index == total:
            print(f"Synthesized {index}/{total} TTS segments with edge-tts")
    if reused:
        print(f"Reused {reused} existing edge-tts segments from {output_dir}")


async def _save_edge_tts_with_retries(
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    attempts: int = 4,
) -> None:
    try:
        import edge_tts
    except ImportError as error:
        raise RuntimeError(
            "edge-tts is not installed. Install it before using --tts-provider edge, "
            "or switch back to CosyVoice."
        ) from error
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
            await communicate.save(str(output_path))
            return
        except Exception as error:
            last_error = error
            if output_path.exists():
                output_path.unlink()
            if attempt == attempts:
                break
            wait_seconds = attempt * 2
            print(
                f"edge-tts failed (attempt {attempt}/{attempts}) for {output_path.name}: {error}. "
                f"Retrying in {wait_seconds}s."
            )
            await asyncio.sleep(wait_seconds)
    raise RuntimeError(f"edge-tts failed for {output_path.name}") from last_error


def synthesize_segments(
    segments: list[Segment],
    output_dir: Path,
    config: PipelineConfig,
    source_video: Path | None = None,
) -> None:
    provider = config.tts_provider.strip().lower()
    if provider == "edge":
        asyncio.run(
            _synthesize_segments_with_edge_async(
                segments=segments,
                output_dir=output_dir,
                voice=config.tts_voice,
                rate=config.tts_rate,
            )
        )
        return
    if provider == "minimax":
        _synthesize_segments_with_minimax(
            segments=segments,
            output_dir=output_dir,
            config=config,
            source_video=source_video,
        )
        return
    if provider == "cosyvoice":
        _synthesize_segments_with_cosyvoice(
            segments=segments,
            output_dir=output_dir,
            config=config,
            source_video=source_video,
        )
        return
    raise RuntimeError(
        f"Unsupported TTS provider: {config.tts_provider}. Expected one of: edge, minimax, cosyvoice."
    )


def _synthesize_segments_with_minimax(
    segments: list[Segment],
    output_dir: Path,
    config: PipelineConfig,
    source_video: Path | None,
) -> None:
    api_key = config.minimax_api_key.strip()
    if not api_key:
        raise RuntimeError(
            "VIDEOCUT_MINIMAX_API_KEY is empty. Set it in .env or pass --minimax-api-key."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    voice_id = _resolve_minimax_voice_id(
        config=config,
        output_dir=output_dir,
        segments=segments,
        source_video=source_video,
    )
    suffix = _minimax_audio_suffix(config.minimax_audio_format)
    pending_segments: list[Segment] = []
    reused = 0
    for segment in segments:
        if not segment.chinese:
            raise RuntimeError(f"Segment {segment.index} is missing Chinese text for TTS")
        segment.audio_path = output_dir / f"{segment.index:04d}.{suffix}"
        if segment.audio_path.exists() and segment.audio_path.stat().st_size > 0:
            reused += 1
            continue
        pending_segments.append(segment)

    if reused:
        print(f"Reused {reused} existing MiniMax segments from {output_dir}")
    if not pending_segments:
        print("All MiniMax segments already exist; skipping synthesis.")
        return

    print(
        "Synthesizing Chinese dubbing with MiniMax "
        f"({voice_id}, {len(pending_segments)} new / {len(segments)} total segments)..."
    )
    max_workers = max(1, config.minimax_concurrency)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _save_minimax_tts_with_retries,
                text=segment.chinese,
                output_path=segment.audio_path,
                config=config,
                voice_id=voice_id,
            ): segment.index
            for segment in pending_segments
        }
        completed = 0
        total = len(pending_segments)
        for future in concurrent.futures.as_completed(future_map):
            future.result()
            completed += 1
            if completed == 1 or completed % 25 == 0 or completed == total:
                print(f"Synthesized {completed}/{total} new TTS segments with MiniMax")


def _save_minimax_tts_with_retries(
    text: str,
    output_path: Path,
    config: PipelineConfig,
    voice_id: str,
    attempts: int = 4,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _save_minimax_tts(
                text=text,
                output_path=output_path,
                config=config,
                voice_id=voice_id,
            )
            return
        except (requests.RequestException, RuntimeError, ValueError) as error:
            last_error = error
            output_path.unlink(missing_ok=True)
            if attempt == attempts:
                break
            wait_seconds = attempt * 2
            print(
                f"MiniMax TTS failed (attempt {attempt}/{attempts}) for {output_path.name}: {error}. "
                f"Retrying in {wait_seconds}s."
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"MiniMax TTS failed for {output_path.name}") from last_error


def _save_minimax_tts(
    text: str,
    output_path: Path,
    config: PipelineConfig,
    voice_id: str,
) -> None:
    response = requests.post(
        _minimax_endpoint(config, "/v1/t2a_v2"),
        headers=_minimax_headers(config),
        json={
            "model": config.minimax_model,
            "text": text,
            "stream": False,
            "language_boost": config.minimax_language_boost,
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": config.minimax_speed,
                "vol": config.minimax_volume,
                "pitch": config.minimax_pitch,
            },
            "audio_setting": {
                "sample_rate": config.minimax_sample_rate,
                "bitrate": config.minimax_bitrate,
                "format": config.minimax_audio_format.strip().lower(),
                "channel": 1,
            },
        },
        timeout=config.minimax_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    _raise_for_minimax_error(payload, fallback="MiniMax TTS request failed")
    audio_hex = payload.get("data", {}).get("audio")
    if not isinstance(audio_hex, str) or not audio_hex.strip():
        raise RuntimeError(f"MiniMax TTS did not return audio data: {payload}")
    output_path.write_bytes(bytes.fromhex(audio_hex))


def _resolve_minimax_voice_id(
    config: PipelineConfig,
    output_dir: Path,
    segments: list[Segment],
    source_video: Path | None,
) -> str:
    if not config.minimax_voice_clone:
        voice_id = config.minimax_voice_id.strip() or config.tts_voice.strip()
        if not voice_id:
            raise RuntimeError("MiniMax requires a configured voice id. Set VIDEOCUT_MINIMAX_VOICE_ID.")
        return voice_id

    cache_path = output_dir / "minimax_voice.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = {}
        cached_voice_id = cached.get("voice_id")
        if isinstance(cached_voice_id, str) and cached_voice_id.strip():
            print(f"Reusing cached MiniMax cloned voice: {cached_voice_id}")
            return cached_voice_id.strip()

    voice_id = _clone_minimax_voice(
        config=config,
        output_dir=output_dir,
        segments=segments,
        source_video=source_video,
    )
    cache_path.write_text(
        json.dumps({"voice_id": voice_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return voice_id


def _clone_minimax_voice(
    config: PipelineConfig,
    output_dir: Path,
    segments: list[Segment],
    source_video: Path | None,
) -> str:
    clone_audio_path, prompt_audio_path, prompt_text = _prepare_minimax_clone_material(
        segments=segments,
        output_dir=output_dir,
        source_video=source_video,
        reference_audio_path=config.reference_audio_path,
        reference_text=config.reference_text,
    )
    clone_file_id = _upload_minimax_audio_file(
        config=config,
        audio_path=clone_audio_path,
        purpose="voice_clone",
    )
    prompt_file_id = _upload_minimax_audio_file(
        config=config,
        audio_path=prompt_audio_path,
        purpose="voice_clone",
    )
    voice_id = f"videocut_{uuid4().hex[:20]}"
    response = requests.post(
        _minimax_endpoint(config, "/v1/voice_clone"),
        headers=_minimax_headers(config),
        json={
            "file_id": clone_file_id,
            "voice_id": voice_id,
            "prompt_audio": prompt_file_id,
            "prompt_text": prompt_text,
        },
        timeout=config.minimax_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    _raise_for_minimax_error(payload, fallback="MiniMax voice clone request failed")
    print(f"Created MiniMax cloned voice: {voice_id}")
    return voice_id


def _prepare_minimax_clone_material(
    segments: list[Segment],
    output_dir: Path,
    source_video: Path | None,
    reference_audio_path: str,
    reference_text: str,
) -> tuple[Path, Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_text = reference_text.strip()
    if reference_audio_path:
        clone_audio_path = Path(reference_audio_path).expanduser().resolve()
        if not clone_audio_path.exists():
            raise FileNotFoundError(f"Reference audio file not found: {clone_audio_path}")
        if not prompt_text:
            raise RuntimeError(
                "MiniMax voice cloning with --reference-audio requires --reference-text "
                "for the prompt clip transcript."
            )
    else:
        if source_video is None:
            raise RuntimeError("MiniMax voice cloning requires a source video or --reference-audio.")
        selected_segments, clone_start, clone_end = _select_reference_window(segments)
        clone_audio_path = output_dir / "minimax_clone_source.wav"
        _extract_audio_clip(
            source_path=source_video,
            output_path=clone_audio_path,
            clip_start=clone_start,
            clip_end=clone_end,
            sample_rate=32000,
        )
        prompt_text = _prepare_minimax_prompt_text(
            segments=selected_segments,
            clip_start=clone_start,
            clip_duration=7.5,
        )

    clone_duration = ffprobe_duration(clone_audio_path)
    if clone_duration < 10.0:
        raise RuntimeError(
            "MiniMax voice cloning requires at least 10 seconds of reference audio. "
            f"Received {clone_duration:.2f}s from {clone_audio_path}."
        )
    prompt_audio_path = output_dir / "minimax_clone_prompt.wav"
    _extract_audio_clip(
        source_path=clone_audio_path,
        output_path=prompt_audio_path,
        clip_start=0.0,
        clip_end=min(7.5, clone_duration),
        sample_rate=32000,
    )
    if not prompt_text:
        raise RuntimeError(
            "MiniMax voice cloning requires prompt text from the source subtitles. "
            "Provide English subtitles or set --reference-text and use CosyVoice instead."
        )
    return clone_audio_path, prompt_audio_path, prompt_text


def _prepare_minimax_prompt_text(
    segments: list[Segment],
    clip_start: float,
    clip_duration: float,
) -> str:
    prompt_lines: list[str] = []
    clip_end = clip_start + clip_duration
    for segment in segments:
        if segment.start >= clip_end:
            break
        content = segment.english.strip() or segment.chinese.strip()
        if content:
            prompt_lines.append(content)
        if segment.end >= clip_end and prompt_lines:
            break
    return " ".join(prompt_lines).strip()


def _upload_minimax_audio_file(
    config: PipelineConfig,
    audio_path: Path,
    purpose: str,
) -> int:
    with audio_path.open("rb") as handle:
        response = requests.post(
            _minimax_endpoint(config, "/v1/files/upload"),
            headers={"Authorization": f"Bearer {config.minimax_api_key.strip()}"},
            data={"purpose": purpose},
            files={"file": (audio_path.name, handle)},
            timeout=config.minimax_timeout,
        )
    response.raise_for_status()
    payload = response.json()
    _raise_for_minimax_error(payload, fallback="MiniMax file upload failed")
    file_id = payload.get("file", {}).get("file_id")
    if not isinstance(file_id, int):
        raise RuntimeError(f"MiniMax file upload did not return a file_id: {payload}")
    return file_id


def _extract_audio_clip(
    source_path: Path,
    output_path: Path,
    clip_start: float,
    clip_end: float,
    sample_rate: int,
) -> None:
    if clip_end <= clip_start:
        raise RuntimeError(
            f"Invalid audio extraction range: start={clip_start:.3f}s end={clip_end:.3f}s"
        )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{clip_start:.3f}",
            "-to",
            f"{clip_end:.3f}",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )


def _minimax_headers(config: PipelineConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.minimax_api_key.strip()}",
        "Content-Type": "application/json",
    }


def _minimax_endpoint(config: PipelineConfig, path: str) -> str:
    return f"{config.minimax_base_url.rstrip('/')}{path}"


def _raise_for_minimax_error(payload: dict, fallback: str) -> None:
    base_resp = payload.get("base_resp")
    if not isinstance(base_resp, dict):
        return
    status_code = base_resp.get("status_code")
    if status_code in (0, "0", None):
        return
    status_msg = base_resp.get("status_msg") or fallback
    raise RuntimeError(f"{fallback}: [{status_code}] {status_msg}")


def _minimax_audio_suffix(audio_format: str) -> str:
    normalized = audio_format.strip().lower()
    if normalized in {"mp3", "wav", "flac"}:
        return normalized
    raise RuntimeError(
        "VideoCut currently supports MiniMax output formats mp3, wav, and flac. "
        f"Received: {audio_format!r}"
    )


def _synthesize_segments_with_cosyvoice(
    segments: list[Segment],
    output_dir: Path,
    config: PipelineConfig,
    source_video: Path | None,
) -> None:
    repo_dir = _resolve_cosyvoice_repo_dir(config)
    model_dir = _resolve_cosyvoice_model_dir(config, repo_dir)
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "cosyvoice_batch.py"
    prompt_audio_path, prompt_text = _prepare_reference_material(
        segments=segments,
        output_dir=output_dir,
        config=config,
        source_video=source_video,
    )

    pending_segments: list[Segment] = []
    reused = 0
    for segment in segments:
        if not segment.chinese:
            raise RuntimeError(f"Segment {segment.index} is missing Chinese text for TTS")
        segment.audio_path = output_dir / f"{segment.index:04d}.wav"
        if segment.audio_path.exists() and segment.audio_path.stat().st_size > 0:
            reused += 1
            continue
        pending_segments.append(segment)

    output_dir.mkdir(parents=True, exist_ok=True)
    group_size = max(1, config.cosyvoice_group_size)
    input_manifest = _build_cosyvoice_input_manifest(
        pending_segments=pending_segments,
        group_size=group_size,
    )
    manifest_path = output_dir / "cosyvoice_inputs.json"
    manifest_path.write_text(json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if reused:
        print(f"Reused {reused} existing CosyVoice segments from {output_dir}")
    if not input_manifest:
        print("All CosyVoice segments already exist; skipping synthesis.")
        return

    cmd = [
        _resolve_cosyvoice_python(config),
        str(script_path),
        "--repo-dir",
        str(repo_dir),
        "--model-dir",
        str(model_dir),
        "--mode",
        config.cosyvoice_mode,
        "--prompt-audio",
        str(prompt_audio_path),
        "--input-json",
        str(manifest_path),
    ]
    if prompt_text:
        cmd.extend(["--prompt-text", prompt_text])
    print(
        "Synthesizing Chinese dubbing with CosyVoice "
        f"({config.cosyvoice_mode}, {len(pending_segments)} new / {len(segments)} total segments, "
        f"group size {group_size})..."
    )
    run_command(cmd)


def _build_cosyvoice_input_manifest(
    pending_segments: list[Segment],
    group_size: int,
) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    for group in _chunked_segments(pending_segments, group_size):
        if len(group) == 1:
            segment = group[0]
            jobs.append(
                {
                    "index": segment.index,
                    "text": segment.chinese,
                    "audio_path": str(segment.audio_path),
                }
            )
            continue

        jobs.append(
            {
                "index": group[0].index,
                "text": _combine_grouped_cosyvoice_text(group),
                "split_segments": [
                    {
                        "index": segment.index,
                        "audio_path": str(segment.audio_path),
                        "target_duration": segment.duration,
                    }
                    for segment in group
                ],
            }
        )
    return jobs


def _chunked_segments(segments: list[Segment], size: int) -> list[list[Segment]]:
    return [segments[index : index + size] for index in range(0, len(segments), size)]


def _combine_grouped_cosyvoice_text(segments: list[Segment]) -> str:
    combined: list[str] = []
    for index, segment in enumerate(segments):
        text = segment.chinese.strip()
        if not text:
            raise RuntimeError(f"Segment {segment.index} is missing Chinese text for TTS")
        if index < len(segments) - 1:
            text = text.rstrip("，,、；;：:")
            if not text.endswith(("。", "！", "？", "!", "?", "…")):
                text = f"{text}。"
        combined.append(text)
    return "".join(combined)


def _prepare_reference_material(
    segments: list[Segment],
    output_dir: Path,
    config: PipelineConfig,
    source_video: Path | None,
) -> tuple[Path, str]:
    mode = config.cosyvoice_mode.strip().lower()
    prompt_text = config.reference_text.strip()

    if config.reference_audio_path:
        prompt_audio_path = Path(config.reference_audio_path).expanduser().resolve()
        if not prompt_audio_path.exists():
            raise FileNotFoundError(f"Reference audio file not found: {prompt_audio_path}")
        if mode == "zero_shot" and not prompt_text:
            raise RuntimeError(
                "CosyVoice zero_shot mode requires VIDEOCUT_REFERENCE_TEXT or --reference-text."
            )
        return prompt_audio_path, prompt_text

    if source_video is None:
        raise RuntimeError("CosyVoice requires a source video or a reference audio file.")

    selected_segments, clip_start, clip_end = _select_reference_window(segments)
    prompt_audio_path = output_dir / "reference_prompt.wav"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{clip_start:.3f}",
            "-to",
            f"{clip_end:.3f}",
            "-i",
            str(source_video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-c:a",
            "pcm_s16le",
            str(prompt_audio_path),
        ]
    )

    if not prompt_text:
        prompt_text = " ".join(segment.english.strip() for segment in selected_segments if segment.english).strip()

    if mode == "zero_shot" and not prompt_text:
        raise RuntimeError(
            "CosyVoice zero_shot mode requires reference text, but no English subtitle text was available "
            "for the extracted reference clip."
        )
    return prompt_audio_path, prompt_text


def _select_reference_window(segments: list[Segment]) -> tuple[list[Segment], float, float]:
    candidates = [segment for segment in segments if segment.end > segment.start and (segment.english or segment.chinese)]
    if not candidates:
        raise RuntimeError("No subtitle segments are available to build a CosyVoice reference clip.")

    # Voice-clone prompts work better when the reference clip is short, dense,
    # and mostly uninterrupted speech instead of a long 20s+ montage.
    target_window = 8.0
    minimum_window = 4.5
    max_window = 10.0
    best_choice: tuple[tuple[float, float, float, int], list[Segment], float, float] | None = None

    for start_index, first_segment in enumerate(candidates):
        selected: list[Segment] = []
        clip_start = max(0.0, first_segment.start - 0.15)
        clip_end = first_segment.end

        for segment in candidates[start_index:]:
            proposed_end = max(clip_end, segment.end + 0.15)
            if proposed_end - clip_start > max_window and selected:
                break
            selected.append(segment)
            clip_end = min(proposed_end, clip_start + max_window)

            window = clip_end - clip_start
            speech = sum(max(0.01, item.end - item.start) for item in selected)
            coverage = speech / max(window, 0.01)
            text_length = sum(len((item.english or item.chinese).strip()) for item in selected)
            score = (
                1.0 if window >= minimum_window else 0.0,
                -abs(window - target_window),
                coverage,
                text_length,
            )
            if best_choice is None or score > best_choice[0]:
                best_choice = (score, list(selected), clip_start, clip_end)

            if window >= target_window and len(selected) >= 2:
                break

    if best_choice is None:
        selected = [candidates[0]]
        clip_start = max(0.0, candidates[0].start - 0.15)
        clip_end = min(candidates[0].end + 0.15, clip_start + max_window)
        return selected, clip_start, clip_end

    _, selected, clip_start, clip_end = best_choice
    return selected, clip_start, clip_end


def _resolve_cosyvoice_repo_dir(config: PipelineConfig) -> Path:
    if config.cosyvoice_repo_dir:
        repo_dir = Path(config.cosyvoice_repo_dir).expanduser().resolve()
    else:
        repo_dir = Path(__file__).resolve().parents[1] / ".vendor" / "CosyVoice"
    if not repo_dir.exists():
        raise FileNotFoundError(
            f"CosyVoice repo directory not found: {repo_dir}. "
            "Set VIDEOCUT_COSYVOICE_REPO_DIR to your CosyVoice checkout."
        )
    return repo_dir


def _resolve_cosyvoice_python(config: PipelineConfig) -> str:
    python_path = config.cosyvoice_python.strip()
    if python_path:
        return python_path
    bundled_venv_python = Path(__file__).resolve().parents[1] / ".venv-cosyvoice" / "bin" / "python"
    if bundled_venv_python.exists():
        return str(bundled_venv_python)
    return "python3.11"


def _resolve_cosyvoice_model_dir(config: PipelineConfig, repo_dir: Path) -> Path:
    if config.cosyvoice_model_dir:
        model_dir = Path(config.cosyvoice_model_dir).expanduser().resolve()
    else:
        model_dir = repo_dir / "pretrained_models" / "Fun-CosyVoice3-0.5B"
    if not model_dir.exists():
        raise FileNotFoundError(
            f"CosyVoice model directory not found: {model_dir}. "
            "Set VIDEOCUT_COSYVOICE_MODEL_DIR to your downloaded model path."
        )
    return model_dir

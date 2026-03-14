from __future__ import annotations

import asyncio
import json
from pathlib import Path

import edge_tts

from videocut.config import PipelineConfig
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
    for index, segment in enumerate(segments, start=1):
        if not segment.chinese:
            raise RuntimeError(f"Segment {segment.index} is missing Chinese text for TTS")
        segment.audio_path = output_dir / f"{segment.index:04d}.mp3"
        await _save_edge_tts_with_retries(
            text=segment.chinese,
            output_path=segment.audio_path,
            voice=voice,
            rate=rate,
        )
        if index == 1 or index % 25 == 0 or index == total:
            print(f"Synthesized {index}/{total} TTS segments with edge-tts")


async def _save_edge_tts_with_retries(
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    attempts: int = 4,
) -> None:
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
    if provider == "cosyvoice":
        _synthesize_segments_with_cosyvoice(
            segments=segments,
            output_dir=output_dir,
            config=config,
            source_video=source_video,
        )
        return
    raise RuntimeError(
        f"Unsupported TTS provider: {config.tts_provider}. Expected one of: edge, cosyvoice."
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

    input_manifest: list[dict[str, str | int]] = []
    for segment in segments:
        if not segment.chinese:
            raise RuntimeError(f"Segment {segment.index} is missing Chinese text for TTS")
        segment.audio_path = output_dir / f"{segment.index:04d}.wav"
        input_manifest.append(
            {
                "index": segment.index,
                "text": segment.chinese,
                "audio_path": str(segment.audio_path),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "cosyvoice_inputs.json"
    manifest_path.write_text(json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [
        config.cosyvoice_python,
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
        f"({config.cosyvoice_mode}, {len(segments)} segments)..."
    )
    run_command(cmd)


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

    selected: list[Segment] = []
    clip_start = max(0.0, candidates[0].start - 0.2)
    clip_end = candidates[0].end
    target_window = 24.0
    max_window = 35.0

    for segment in candidates:
        proposed_end = max(clip_end, segment.end + 0.2)
        if proposed_end - clip_start > max_window and selected:
            break
        selected.append(segment)
        clip_end = proposed_end
        if clip_end - clip_start >= target_window and len(selected) >= 4:
            break

    if not selected:
        selected.append(candidates[0])
        clip_end = max(clip_end, candidates[0].end + 0.2)

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

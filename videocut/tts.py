from __future__ import annotations

import json
from pathlib import Path

from videocut.config import PipelineConfig
from videocut.media import ffprobe_duration
from videocut.models import Segment
from videocut.shell import run_command


def synthesize_segments(
    segments: list[Segment],
    output_dir: Path,
    config: PipelineConfig,
    source_video: Path | None = None,
) -> None:
    _synthesize_segments_with_cosyvoice(
        segments=segments,
        output_dir=output_dir,
        config=config,
        source_video=source_video,
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
    run_command(
        cmd,
        env={
            "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        },
    )


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

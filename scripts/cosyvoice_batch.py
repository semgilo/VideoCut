from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    args = _parse_args()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    _extend_python_path(repo_dir)

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import AutoModel

    jobs = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    cosyvoice = AutoModel(model_dir=str(Path(args.model_dir).expanduser().resolve()))
    selected_speaker = _resolve_speaker(cosyvoice, args.speaker) if args.mode == "sft" else ""
    total = len(jobs)
    if selected_speaker:
        print(f"CosyVoice speaker: {selected_speaker}")

    with torch.inference_mode():
        for index, job in enumerate(jobs, start=1):
            text = str(job["text"]).strip()
            if not text:
                raise RuntimeError(f"CosyVoice input {job['index']} is missing text")
            result = _run_inference(
                cosyvoice=cosyvoice,
                mode=args.mode,
                text=text,
                prompt_audio=args.prompt_audio,
                prompt_text=args.prompt_text,
                speaker=selected_speaker,
            )
            speech = result["tts_speech"]
            if not isinstance(speech, torch.Tensor):
                speech = torch.as_tensor(speech)
            if speech.ndim == 1:
                speech = speech.unsqueeze(0)
            sample_rate = int(result.get("sample_rate", getattr(cosyvoice, "sample_rate", 22050)))
            _save_job_audio(
                job=job,
                speech=speech.cpu(),
                sample_rate=sample_rate,
                torchaudio_module=torchaudio,
            )
            if index == 1 or index % 10 == 0 or index == total:
                print(f"CosyVoice synthesized {index}/{total} segments")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch CosyVoice synthesis helper for VideoCut.")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--mode", choices=("cross_lingual", "zero_shot", "sft"), required=True)
    parser.add_argument("--prompt-audio", default="")
    parser.add_argument("--prompt-text", default="")
    parser.add_argument("--speaker", default="")
    parser.add_argument("--input-json", required=True)
    args = parser.parse_args()
    if args.mode in {"cross_lingual", "zero_shot"} and not str(args.prompt_audio).strip():
        parser.error("--prompt-audio is required when mode is cross_lingual or zero_shot")
    return args


def _extend_python_path(repo_dir: Path) -> None:
    sys.path.insert(0, str(repo_dir))
    third_party_dir = repo_dir / "third_party"
    if not third_party_dir.exists():
        return
    for path in sorted(third_party_dir.iterdir()):
        if path.is_dir():
            sys.path.insert(0, str(path))


def _run_inference(
    cosyvoice: object,
    mode: str,
    text: str,
    prompt_audio: str,
    prompt_text: str,
    speaker: str,
) -> dict:
    model_name = cosyvoice.__class__.__name__
    prepared_text = _prepare_tts_text(model_name=model_name, text=text)
    prepared_prompt_text = _prepare_prompt_text(
        model_name=model_name,
        prompt_text=prompt_text,
    )
    if mode == "sft":
        result = cosyvoice.inference_sft(
            tts_text=prepared_text,
            spk_id=speaker,
            stream=False,
        )
    elif mode == "cross_lingual":
        result = cosyvoice.inference_cross_lingual(
            tts_text=prepared_text,
            prompt_wav=prompt_audio,
            stream=False,
        )
    else:
        if not prepared_prompt_text:
            raise RuntimeError("CosyVoice zero_shot mode requires prompt_text")
        result = cosyvoice.inference_zero_shot(
            tts_text=prepared_text,
            prompt_text=prepared_prompt_text,
            prompt_wav=prompt_audio,
            stream=False,
        )

    if isinstance(result, dict):
        return result
    for item in result:
        return item
    raise RuntimeError("CosyVoice returned no audio output")


def _resolve_speaker(cosyvoice: object, requested: str) -> str:
    speaker = requested.strip()
    if not hasattr(cosyvoice, "list_available_spks"):
        if speaker:
            return speaker
        raise RuntimeError("Current CosyVoice model does not expose built-in speaker ids.")

    available = list(getattr(cosyvoice, "list_available_spks")() or [])
    if speaker:
        if available and speaker in available:
            return speaker
        if available:
            preview = ", ".join(available[:10])
            raise RuntimeError(
                f"Requested speaker '{speaker}' was not found. "
                f"Available speakers (first 10): {preview}"
            )
        raise RuntimeError(
            "This CosyVoice model has no built-in speakers. "
            "Use voice cloning mode or switch to a model with `spk2info.pt`."
        )

    if not available:
        raise RuntimeError(
            "Voice cloning is disabled but no built-in speakers were found in the model. "
            "Use --voice-clone or provide a model with built-in speakers (`spk2info.pt`)."
        )
    return str(available[0])


def _prepare_tts_text(model_name: str, text: str) -> str:
    value = text.strip()
    if model_name == "CosyVoice" and not value.startswith("<|"):
        return f"{_guess_language_tag(value)}{value}"
    if model_name == "CosyVoice3" and "<|endofprompt|>" not in value:
        return f"You are a helpful assistant.<|endofprompt|>{value}"
    return value


def _prepare_prompt_text(model_name: str, prompt_text: str) -> str:
    value = prompt_text.strip()
    if model_name == "CosyVoice3" and value and "<|endofprompt|>" not in value:
        return f"You are a helpful assistant.<|endofprompt|>{value}"
    return value


def _guess_language_tag(text: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "<|zh|>"
    return "<|en|>"


def _save_job_audio(
    job: dict,
    speech,
    sample_rate: int,
    torchaudio_module,
) -> None:
    split_segments = job.get("split_segments")
    if isinstance(split_segments, list) and split_segments:
        _split_group_audio(
            split_segments=split_segments,
            speech=speech,
            sample_rate=sample_rate,
            torchaudio_module=torchaudio_module,
        )
        return

    output_path = Path(job["audio_path"]).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torchaudio_module.save(str(output_path), speech, sample_rate)


def _split_group_audio(
    split_segments: list[dict],
    speech,
    sample_rate: int,
    torchaudio_module,
) -> None:
    mono = speech[0] if speech.ndim > 1 else speech
    boundaries = _estimate_split_boundaries(
        audio=mono,
        sample_rate=sample_rate,
        split_segments=split_segments,
    )
    starts = [0, *boundaries]
    ends = [*boundaries, int(mono.shape[-1])]

    for item, start, end in zip(split_segments, starts, ends, strict=True):
        output_path = Path(item["audio_path"]).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        safe_start = max(0, min(int(start), int(mono.shape[-1]) - 1))
        safe_end = max(safe_start + 1, min(int(end), int(mono.shape[-1])))
        segment_audio = mono[safe_start:safe_end].unsqueeze(0)
        torchaudio_module.save(str(output_path), segment_audio, sample_rate)


def _estimate_split_boundaries(
    audio,
    sample_rate: int,
    split_segments: list[dict],
) -> list[int]:
    if len(split_segments) <= 1:
        return []

    total_samples = int(audio.shape[-1])
    durations = [max(0.01, float(item.get("target_duration", 0.01))) for item in split_segments]
    total_duration = sum(durations)
    expected_boundaries = [
        int(total_samples * sum(durations[:index]) / total_duration)
        for index in range(1, len(durations))
    ]
    silence_boundaries = _detect_silence_boundaries(audio=audio, sample_rate=sample_rate)

    chosen: list[int] = []
    minimum_gap = max(1, int(sample_rate * 0.12))
    search_window = max(int(sample_rate * 0.5), total_samples // max(8, len(split_segments) * 3))

    for index, expected in enumerate(expected_boundaries):
        lower_bound = minimum_gap if not chosen else chosen[-1] + minimum_gap
        remaining_boundaries = len(expected_boundaries) - index
        upper_bound = total_samples - (remaining_boundaries * minimum_gap)
        candidates = [
            boundary
            for boundary in silence_boundaries
            if lower_bound <= boundary <= upper_bound and abs(boundary - expected) <= search_window
        ]
        if candidates:
            chosen_boundary = min(candidates, key=lambda value: abs(value - expected))
        else:
            chosen_boundary = min(max(expected, lower_bound), upper_bound)
        chosen.append(chosen_boundary)

    return chosen


def _detect_silence_boundaries(audio, sample_rate: int) -> list[int]:
    frame_size = max(1, int(sample_rate * 0.02))
    hop_size = max(1, int(sample_rate * 0.01))
    minimum_silence_frames = max(2, int(0.08 / (hop_size / sample_rate)))
    peak = max(float(audio.abs().max().item()), 1e-4)
    threshold = peak * 0.03

    silent_midpoints: list[int] = []
    run_start: int | None = None
    frame_index = 0
    max_start = max(1, int(audio.shape[-1]) - frame_size + 1)
    for start in range(0, max_start, hop_size):
        frame = audio[start : start + frame_size]
        frame_energy = float(frame.abs().mean().item())
        if frame_energy <= threshold:
            if run_start is None:
                run_start = frame_index
        elif run_start is not None:
            if frame_index - run_start >= minimum_silence_frames:
                midpoint_frame = (run_start + frame_index) // 2
                silent_midpoints.append(midpoint_frame * hop_size + frame_size // 2)
            run_start = None
        frame_index += 1

    if run_start is not None and frame_index - run_start >= minimum_silence_frames:
        midpoint_frame = (run_start + frame_index) // 2
        silent_midpoints.append(midpoint_frame * hop_size + frame_size // 2)

    return silent_midpoints


if __name__ == "__main__":
    main()

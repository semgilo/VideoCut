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
    total = len(jobs)

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
            )
            speech = result["tts_speech"]
            if not isinstance(speech, torch.Tensor):
                speech = torch.as_tensor(speech)
            if speech.ndim == 1:
                speech = speech.unsqueeze(0)
            sample_rate = int(result.get("sample_rate", getattr(cosyvoice, "sample_rate", 22050)))
            output_path = Path(job["audio_path"]).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            torchaudio.save(str(output_path), speech.cpu(), sample_rate)
            if index == 1 or index % 10 == 0 or index == total:
                print(f"CosyVoice synthesized {index}/{total} segments")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch CosyVoice synthesis helper for VideoCut.")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--mode", choices=("cross_lingual", "zero_shot"), required=True)
    parser.add_argument("--prompt-audio", required=True)
    parser.add_argument("--prompt-text", default="")
    parser.add_argument("--input-json", required=True)
    return parser.parse_args()


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
) -> dict:
    model_name = cosyvoice.__class__.__name__
    prepared_text = _prepare_tts_text(model_name=model_name, text=text)
    prepared_prompt_text = _prepare_prompt_text(
        model_name=model_name,
        prompt_text=prompt_text,
    )
    if mode == "cross_lingual":
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


if __name__ == "__main__":
    main()

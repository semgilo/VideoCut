from __future__ import annotations

import argparse
from pathlib import Path

from videocut.config import PipelineConfig
from videocut.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="videocut",
        description="Download a YouTube video, translate it to Chinese, dub it, and export a new video.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the full download-to-export pipeline")
    run_parser.add_argument("url", help="YouTube video URL")
    run_parser.add_argument("--workdir", type=Path, help="Custom working directory for this run")
    run_parser.add_argument(
        "--tts-provider",
        choices=("edge", "minimax", "cosyvoice"),
        help="TTS backend provider (defaults to configured provider; CosyVoice by default)",
    )
    run_parser.add_argument(
        "--voice",
        help="Provider voice id, for example zh-CN-YunxiNeural or Chinese (Mandarin)_News_Anchor",
    )
    run_parser.add_argument("--tts-rate", help="Edge TTS rate, for example +10%% or -5%%")
    run_parser.add_argument("--minimax-base-url", help="MiniMax API base URL")
    run_parser.add_argument("--minimax-api-key", help="MiniMax API key")
    run_parser.add_argument("--minimax-model", help="MiniMax speech model, for example speech-2.8-turbo")
    run_parser.add_argument("--minimax-speed", type=float, help="MiniMax speech speed")
    run_parser.add_argument("--minimax-volume", type=float, help="MiniMax speech volume")
    run_parser.add_argument("--minimax-pitch", type=float, help="MiniMax speech pitch")
    run_parser.add_argument("--minimax-concurrency", type=int, help="MiniMax synthesis concurrency")
    run_parser.add_argument(
        "--minimax-voice-clone",
        action="store_true",
        help="Clone a MiniMax voice from the source audio before segment synthesis",
    )
    run_parser.add_argument("--cosyvoice-python", help="Python interpreter used for CosyVoice inference")
    run_parser.add_argument("--cosyvoice-repo", type=Path, help="Path to the local CosyVoice repository")
    run_parser.add_argument("--cosyvoice-model", type=Path, help="Path to the CosyVoice model directory")
    run_parser.add_argument(
        "--cosyvoice-mode",
        choices=("cross_lingual", "zero_shot"),
        help="CosyVoice inference mode",
    )
    run_parser.add_argument(
        "--cosyvoice-group-size",
        type=int,
        help="Optional CosyVoice batching group size for long videos; values above 1 synthesize several adjacent subtitles together",
    )
    run_parser.add_argument(
        "--reference-audio",
        type=Path,
        help="Optional reference audio for CosyVoice, or clone source audio for MiniMax",
    )
    run_parser.add_argument(
        "--reference-text",
        help="Transcript for the reference audio in CosyVoice zero-shot mode or MiniMax voice cloning",
    )
    run_parser.add_argument("--llm-base-url", help="OpenAI-compatible base URL")
    run_parser.add_argument("--llm-api-key", help="OpenAI-compatible API key")
    run_parser.add_argument("--llm-model", help="OpenAI-compatible model name")
    run_parser.add_argument("--original-volume", type=float, help="Original audio mix volume")
    run_parser.add_argument("--dub-volume", type=float, help="Dub audio mix volume")
    run_parser.add_argument("--subtitle-font", help="Subtitle font name used by ffmpeg")
    run_parser.add_argument("--subtitle-size", type=int, help="Subtitle font size")
    run_parser.add_argument(
        "--no-burn-subtitles",
        action="store_true",
        help="Do not burn subtitles into the final video; keep only the generated SRT file",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = PipelineConfig()

    if args.command == "run":
        if args.tts_provider:
            config.tts_provider = args.tts_provider
        if args.voice:
            config.tts_voice = args.voice
            config.minimax_voice_id = args.voice
        if args.tts_rate:
            config.tts_rate = args.tts_rate
        if args.minimax_base_url:
            config.minimax_base_url = args.minimax_base_url
        if args.minimax_api_key:
            config.minimax_api_key = args.minimax_api_key
        if args.minimax_model:
            config.minimax_model = args.minimax_model
        if args.minimax_speed is not None:
            config.minimax_speed = args.minimax_speed
        if args.minimax_volume is not None:
            config.minimax_volume = args.minimax_volume
        if args.minimax_pitch is not None:
            config.minimax_pitch = args.minimax_pitch
        if args.minimax_concurrency is not None:
            config.minimax_concurrency = args.minimax_concurrency
        if args.minimax_voice_clone:
            config.minimax_voice_clone = True
        if args.cosyvoice_python:
            config.cosyvoice_python = args.cosyvoice_python
        if args.cosyvoice_repo:
            config.cosyvoice_repo_dir = str(args.cosyvoice_repo)
        if args.cosyvoice_model:
            config.cosyvoice_model_dir = str(args.cosyvoice_model)
        if args.cosyvoice_mode:
            config.cosyvoice_mode = args.cosyvoice_mode
        if args.cosyvoice_group_size is not None:
            config.cosyvoice_group_size = max(1, args.cosyvoice_group_size)
        if args.reference_audio:
            config.reference_audio_path = str(args.reference_audio)
        if args.reference_text:
            config.reference_text = args.reference_text
        if args.llm_base_url:
            config.llm_base_url = args.llm_base_url
        if args.llm_api_key:
            config.llm_api_key = args.llm_api_key
        if args.llm_model:
            config.llm_model = args.llm_model
        if args.original_volume is not None:
            config.original_audio_volume = args.original_volume
        if args.dub_volume is not None:
            config.dub_audio_volume = args.dub_volume
        if args.subtitle_font:
            config.subtitle_font = args.subtitle_font
        if args.subtitle_size is not None:
            config.subtitle_font_size = args.subtitle_size
        if args.no_burn_subtitles:
            config.burn_subtitles = False
        run_pipeline(args.url, config, workdir=args.workdir)


if __name__ == "__main__":
    main()

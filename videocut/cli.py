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
        choices=("edge", "minimax", "cosyvoice", "command"),
        help="TTS backend provider (defaults to configured provider; CosyVoice by default)",
    )
    run_parser.add_argument(
        "--voice",
        help="Provider voice id, for example zh-CN-YunxiNeural or Chinese (Mandarin)_News_Anchor",
    )
    run_parser.add_argument("--tts-rate", help="Edge TTS rate, for example +10%% or -5%%")
    run_parser.add_argument(
        "--tts-command",
        help="External TTS/voice-clone adapter command used by --tts-provider command",
    )
    run_parser.add_argument(
        "--tts-command-audio-format",
        choices=("wav", "mp3", "m4a", "aac", "flac"),
        help="Expected audio format written by the external command provider",
    )
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
    run_parser.add_argument(
        "--no-translation-timing-adapt",
        action="store_true",
        help="Disable the per-line translation shortening pass that keeps Chinese dubbing lines closer to the source timing",
    )
    run_parser.add_argument(
        "--translation-target-cps",
        type=float,
        help="Target compact characters-per-second budget used when shortening overlong translated lines",
    )
    run_parser.add_argument(
        "--translation-slack-chars",
        type=int,
        help="Extra compact characters allowed above the target budget before a translated line is rewritten",
    )
    run_parser.add_argument(
        "--no-translation-audio-repair",
        action="store_true",
        help="Disable the post-TTS single-line repair pass that rewrites only segments whose real synthesized audio still runs too long",
    )
    run_parser.add_argument(
        "--translation-audio-target-playback-rate",
        type=float,
        help="Target local playback ratio for post-TTS subtitle repair, for example 1.0 keeps each line close to its own subtitle window",
    )
    run_parser.add_argument(
        "--translation-audio-repair-slack-seconds",
        type=float,
        help="Extra local audio duration allowed before a synthesized line is rewritten and re-synthesized",
    )
    run_parser.add_argument(
        "--translation-audio-repair-passes",
        type=int,
        help="Maximum post-TTS repair passes that rewrite and re-synthesize only locally overlong lines",
    )
    run_parser.add_argument(
        "--translation-audio-repair-group-size",
        type=int,
        help="CosyVoice group size used during the repair pass; use 1 for strict single-line re-synthesis",
    )
    run_parser.add_argument("--original-volume", type=float, help="Original audio mix volume")
    run_parser.add_argument("--dub-volume", type=float, help="Dub audio mix volume")
    run_parser.add_argument(
        "--timing-mode",
        choices=("natural", "fit"),
        help="Dub timing strategy: natural keeps speech closer to raw TTS speed; fit stretches/shrinks within the playback-rate range to align more tightly to subtitle windows",
    )
    run_parser.add_argument(
        "--min-playback-rate",
        type=float,
        help="Minimum playback rate allowed during timing alignment, for example 0.6",
    )
    run_parser.add_argument(
        "--max-playback-rate",
        type=float,
        help="Maximum playback rate allowed during timing alignment, for example 1.5",
    )
    run_parser.add_argument("--subtitle-font", help="Subtitle font name used by ffmpeg")
    run_parser.add_argument(
        "--subtitle-font-path",
        help="Optional subtitle font file path used by the Pillow overlay burn-in fallback",
    )
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
        if args.tts_command:
            config.tts_command = args.tts_command
        if args.tts_command_audio_format:
            config.tts_command_audio_format = args.tts_command_audio_format
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
        if args.no_translation_timing_adapt:
            config.translation_timing_adapt = False
        if args.translation_target_cps is not None:
            config.translation_target_compact_cps = args.translation_target_cps
        if args.translation_slack_chars is not None:
            config.translation_adapt_slack_chars = max(0, args.translation_slack_chars)
        if args.no_translation_audio_repair:
            config.translation_audio_repair = False
        if args.translation_audio_target_playback_rate is not None:
            config.translation_audio_target_playback_rate = max(
                0.01,
                args.translation_audio_target_playback_rate,
            )
        if args.translation_audio_repair_slack_seconds is not None:
            config.translation_audio_repair_slack_seconds = max(
                0.0,
                args.translation_audio_repair_slack_seconds,
            )
        if args.translation_audio_repair_passes is not None:
            config.translation_audio_repair_passes = max(1, args.translation_audio_repair_passes)
        if args.translation_audio_repair_group_size is not None:
            config.translation_audio_repair_group_size = max(1, args.translation_audio_repair_group_size)
        if args.original_volume is not None:
            config.original_audio_volume = args.original_volume
        if args.dub_volume is not None:
            config.dub_audio_volume = args.dub_volume
        if args.timing_mode:
            config.timing_mode = args.timing_mode
        if args.min_playback_rate is not None:
            config.min_playback_rate = args.min_playback_rate
        if args.max_playback_rate is not None:
            config.max_playback_rate = args.max_playback_rate
        if args.subtitle_font:
            config.subtitle_font = args.subtitle_font
        if args.subtitle_font_path:
            config.subtitle_font_path = args.subtitle_font_path
        if args.subtitle_size is not None:
            config.subtitle_font_size = args.subtitle_size
        if args.no_burn_subtitles:
            config.burn_subtitles = False
        if args.tts_provider is None and config.tts_provider.strip().lower() == "edge":
            if _has_local_cosyvoice_assets(config):
                print(
                    "Configured TTS provider 'edge' does not support voice cloning. "
                    "Switching to local CosyVoice."
                )
                config.tts_provider = "cosyvoice"
        run_pipeline(args.url, config, workdir=args.workdir)


def _has_local_cosyvoice_assets(config: PipelineConfig) -> bool:
    repo_dir = (
        Path(config.cosyvoice_repo_dir).expanduser().resolve()
        if config.cosyvoice_repo_dir
        else Path(__file__).resolve().parents[1] / ".vendor" / "CosyVoice"
    )
    model_dir = (
        Path(config.cosyvoice_model_dir).expanduser().resolve()
        if config.cosyvoice_model_dir
        else repo_dir / "pretrained_models" / "Fun-CosyVoice3-0.5B"
    )
    return repo_dir.exists() and model_dir.exists()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from videocut.config import PipelineConfig, load_pipeline_config
from videocut.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="videocut",
        description="Run the configured YouTube-to-Chinese video processing pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the configured pipeline")
    run_parser.add_argument("url", help="YouTube video URL")
    run_parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML config file. If omitted, videocut.toml in the current directory is loaded automatically when present.",
    )
    run_parser.add_argument("--workdir", type=Path, help="Custom working directory for this run")
    run_parser.add_argument(
        "--mode",
        choices=("dub", "subtitle_only"),
        help="Pipeline mode. dub runs the full dubbing flow; subtitle_only keeps original audio and only exports subtitles plus platform materials.",
    )
    run_parser.add_argument(
        "--platform-materials",
        dest="platform_materials",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Export platform-specific publish materials for Douyin, Bilibili, and Xiaohongshu.",
    )
    run_parser.add_argument("--output-name", help="Final output filename written inside the run directory")
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
        help="CosyVoice batching group size; values above 1 synthesize adjacent subtitles together",
    )
    run_parser.add_argument(
        "--reference-audio",
        type=Path,
        help="Reference audio file for CosyVoice voice cloning",
    )
    run_parser.add_argument(
        "--reference-text",
        help="Transcript for the reference audio (required for zero_shot mode)",
    )
    run_parser.add_argument("--llm-base-url", help="OpenAI-compatible base URL for local translation model")
    run_parser.add_argument("--llm-api-key", help="OpenAI-compatible API key")
    run_parser.add_argument("--llm-model", help="OpenAI-compatible model name")
    run_parser.add_argument("--original-volume", type=float, help="Original audio mix volume (0.0 = muted)")
    run_parser.add_argument("--dub-volume", type=float, help="Dub audio mix volume")
    run_parser.add_argument(
        "--max-playback-rate",
        type=float,
        help="Maximum playback rate when dubbed audio overflows its subtitle slot (default 1.3)",
    )
    run_parser.add_argument("--subtitle-font", help="Subtitle font name used by ffmpeg")
    run_parser.add_argument(
        "--subtitle-font-path",
        help="Optional subtitle font file path for the Pillow overlay burn-in fallback",
    )
    run_parser.add_argument("--subtitle-size", type=int, help="Subtitle font size")
    run_parser.add_argument(
        "--cleanup-source",
        dest="cleanup_source",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Delete the source/ directory after publish assets are exported.",
    )
    run_parser.add_argument(
        "--burn-subtitles",
        dest="burn_subtitles",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Burn subtitles into the final video.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        config = load_pipeline_config(args.config)
        _apply_run_overrides(config, args)
        run_pipeline(args.url, config, workdir=args.workdir)


def _apply_run_overrides(config: PipelineConfig, args: argparse.Namespace) -> None:
    for field_name, value in (
        ("pipeline_mode", args.mode),
        ("output_name", args.output_name),
        ("cosyvoice_python", args.cosyvoice_python),
        ("cosyvoice_mode", args.cosyvoice_mode),
        ("reference_text", args.reference_text),
        ("llm_base_url", args.llm_base_url),
        ("llm_api_key", args.llm_api_key),
        ("llm_model", args.llm_model),
        ("subtitle_font", args.subtitle_font),
        ("subtitle_font_path", args.subtitle_font_path),
    ):
        _apply_override(config, field_name, value)

    _apply_override(config, "export_platform_materials", args.platform_materials)
    _apply_override(config, "cleanup_source_after_publish", args.cleanup_source)
    _apply_override(config, "burn_subtitles", args.burn_subtitles)
    _apply_override(config, "cosyvoice_group_size", args.cosyvoice_group_size, lambda value: max(1, value))
    _apply_override(config, "original_audio_volume", args.original_volume)
    _apply_override(config, "dub_audio_volume", args.dub_volume)
    _apply_override(config, "max_playback_rate", args.max_playback_rate)
    _apply_override(config, "subtitle_font_size", args.subtitle_size)

    if args.cosyvoice_repo:
        config.cosyvoice_repo_dir = str(args.cosyvoice_repo)
    if args.cosyvoice_model:
        config.cosyvoice_model_dir = str(args.cosyvoice_model)
    if args.reference_audio:
        config.reference_audio_path = str(args.reference_audio)


def _apply_override(
    config: PipelineConfig,
    field_name: str,
    value,
    transform=None,
) -> None:
    if value is None:
        return
    if transform is not None:
        value = transform(value)
    setattr(config, field_name, value)


if __name__ == "__main__":
    main()

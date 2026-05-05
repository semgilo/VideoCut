from __future__ import annotations

import argparse
from pathlib import Path

from videocut.config import PipelineConfig, load_pipeline_config
from videocut.cover import compose_cover_with_title
from videocut.pipeline import run_pipeline
from videocut.shell import step_guard
from videocut.subtitle_only import run_subtitle_only_pipeline


def _add_clean_runs_parser(subparsers) -> None:
    parser = subparsers.add_parser("clean-runs", help="Clean old run directories")
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML config file.",
    )
    parser.add_argument("--runs-dir", type=Path, dest="runs_dir", help="Root directory for runs (default: ./runs)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--keep-days",
        type=int,
        default=7,
        help="Keep runs newer than this many days (default: 7). Implied when neither --all nor --keep-days given.",
    )
    mode.add_argument("--all", action="store_true", dest="all_runs", help="Clean ALL run directories")
    parser.add_argument(
        "--force", action="store_true", help="Actually delete (default is dry-run)"
    )
    return parser


def _add_doctor_parser(subparsers) -> None:
    parser = subparsers.add_parser("doctor", help="Check system configuration and status")
    parser.add_argument("--config", type=Path, help="Optional TOML config file to validate")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="videocut",
        description="Run the unified YouTube -> Chinese dubbing pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the unified pipeline")
    run_parser.add_argument("url", help="YouTube video URL")
    run_parser.add_argument(
        "--config",
        type=Path,
        help="Optional TOML config file. If omitted, videocut.toml in the current directory is loaded automatically when present.",
    )
    run_parser.add_argument("--workdir", type=Path, help="Custom working directory for this run")
    run_parser.add_argument("--runs-dir", type=Path, dest="runs_dir", help="Root directory for all runs (default: ./runs)")
    run_parser.add_argument("--output-name", help="Final output filename written inside the run directory")

    run_parser.add_argument("--llm-base-url", help="OpenAI-compatible base URL for local translation model")
    run_parser.add_argument("--llm-api-key", help="OpenAI-compatible API key")
    run_parser.add_argument("--llm-model", help="OpenAI-compatible model name")
    run_parser.add_argument("--llm-timeout", type=int, help="Translation request timeout in seconds")
    run_parser.add_argument("--translation-batch-size", type=int, help="Translation batch size")
    run_parser.add_argument("--translation-concurrency", type=int, help="Translation batch concurrency")
    run_parser.add_argument(
        "--translation-target-cps",
        type=float,
        help="Chinese target characters-per-second used for L/V budget calculation (default 4.5)",
    )
    run_parser.add_argument(
        "--translation-char-tolerance",
        type=float,
        help="Allowed character-budget tolerance ratio around L/V (default 0.2 for ±20%%)",
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
        "--voice-clone",
        dest="voice_clone",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable voice cloning. --no-voice-clone uses built-in speaker mode.",
    )
    run_parser.add_argument("--cosyvoice-speaker", help="Built-in CosyVoice speaker id used with --no-voice-clone")
    run_parser.add_argument("--cosyvoice-group-size", type=int, help="CosyVoice group size")
    run_parser.add_argument("--cosyvoice-concurrency", type=int, help="CosyVoice worker process count")
    run_parser.add_argument("--reference-audio", type=Path, help="Reference audio file for CosyVoice voice cloning")
    run_parser.add_argument("--reference-text", help="Transcript for the reference audio (required for zero_shot)")

    run_parser.add_argument("--original-volume", type=float, help="Original audio mix volume (0.0 = muted)")
    run_parser.add_argument("--dub-volume", type=float, help="Dub audio mix volume")

    run_parser.add_argument("--subtitle-font", help="Subtitle font name used by ffmpeg")
    run_parser.add_argument("--subtitle-font-path", help="Optional subtitle font file path")
    run_parser.add_argument("--subtitle-size", type=int, help="Subtitle font size")
    run_parser.add_argument(
        "--burn-subtitles",
        dest="burn_subtitles",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Burn subtitles into the final video.",
    )
    run_parser.add_argument(
        "--cleanup-source",
        dest="cleanup_source",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Delete the source/ directory after publish assets are exported.",
    )

    # ------------------------------------------------------------------
    # inpaint  – logo removal / old-film scratch repair
    # ------------------------------------------------------------------
    inpaint_parser = subparsers.add_parser(
        "inpaint",
        help="Remove logos or repair old-film scratches via inpainting",
    )
    inpaint_parser.add_argument("input", type=Path, help="Input video file")
    inpaint_parser.add_argument("output", type=Path, help="Output video file")
    inpaint_parser.add_argument(
        "--region",
        dest="regions",
        metavar="X,Y,W,H",
        action="append",
        default=[],
        help=(
            "Static region to inpaint every frame, e.g. '1720,40,180,60'. "
            "Repeat --region to specify multiple areas (useful for multiple logos)."
        ),
    )
    inpaint_parser.add_argument(
        "--mask",
        type=Path,
        metavar="MASK_IMAGE",
        help=(
            "Greyscale mask image (same resolution as video recommended). "
            "White pixels mark regions to inpaint; black pixels are kept."
        ),
    )
    inpaint_parser.add_argument(
        "--scratch",
        dest="auto_scratch",
        action="store_true",
        help="Automatically detect and repair vertical film scratches.",
    )
    inpaint_parser.add_argument(
        "--method",
        choices=["telea", "ns", "lama"],
        default="telea",
        help=(
            "Inpainting algorithm: "
            "telea (fast marching, default), "
            "ns (Navier-Stokes diffusion), "
            "lama (deep-learning, requires 'pip install simple-lama-inpainting')."
        ),
    )
    inpaint_parser.add_argument(
        "--radius",
        type=int,
        default=5,
        metavar="N",
        help="Neighbourhood radius in pixels for telea/ns (default: 5).",
    )
    inpaint_parser.add_argument(
        "--scratch-sensitivity",
        type=float,
        default=1.0,
        metavar="FLOAT",
        help=(
            "Scratch detection sensitivity for --scratch mode "
            "(default 1.0; increase to catch lighter scratches, e.g. 1.5)."
        ),
    )
    inpaint_parser.add_argument(
        "--dilate",
        type=int,
        default=2,
        metavar="PIXELS",
        help="Expand the inpaint mask by this many pixels to avoid border rings (default: 2).",
    )

    # ------------------------------------------------------------------
    # doctor – system diagnostics
    # ------------------------------------------------------------------
    _add_doctor_parser(subparsers)

    # ------------------------------------------------------------------
    # clean-runs – clean up old run directories
    # ------------------------------------------------------------------
    _add_clean_runs_parser(subparsers)

    cover_parser = subparsers.add_parser("cover", help="Compose a cover image with title and background box")
    cover_parser.add_argument("--input", type=Path, required=True, help="Source image path")
    cover_parser.add_argument("--output", type=Path, required=True, help="Output image path")
    cover_parser.add_argument("--title", required=True, help="Title text to draw")
    cover_parser.add_argument("--width", type=int, help="Optional output width")
    cover_parser.add_argument("--height", type=int, help="Optional output height")
    cover_parser.add_argument("--font-path", type=Path, help="Optional TTF/TTC font path")
    cover_parser.add_argument("--font-size", type=int, help="Optional title font size in px")
    cover_parser.add_argument(
        "--position",
        choices=("top", "center", "bottom"),
        default="top",
        help="Title box vertical anchor",
    )
    cover_parser.add_argument("--offset-y", type=int, default=0, help="Vertical offset from the selected anchor")
    cover_parser.add_argument("--box-color", default="#000000", help="Title box color")
    cover_parser.add_argument("--box-alpha", type=int, default=176, help="Title box opacity (0-255)")
    cover_parser.add_argument("--text-color", default="#FFFFFF", help="Title text color")
    cover_parser.add_argument("--stroke-color", default="#000000", help="Title stroke color")
    cover_parser.add_argument("--stroke-width", type=int, help="Title stroke width in px")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        config = load_pipeline_config(args.config)
        _apply_run_overrides(config, args)
        # Route to the correct pipeline based on mode
        with step_guard():
            if config.mode == "subtitle_only":
                run_subtitle_only_pipeline(args.url, config, workdir=args.workdir)
            else:
                run_pipeline(args.url, config, workdir=args.workdir)
        return

    if args.command == "inpaint":
        from videocut.inpaint import InpaintMethod, inpaint_video, inpaint_video_ffmpeg, parse_region

        regions = []
        for r in args.regions:
            try:
                regions.append(parse_region(r))
            except ValueError as exc:
                parser.error(str(exc))
        if not regions and args.mask is None and not args.auto_scratch:
            parser.error(
                "Provide at least one of: --region X,Y,W,H, --mask FILE, or --scratch."
            )

        use_ffmpeg_path = (
            regions
            and args.mask is None
            and not args.auto_scratch
            and args.method == InpaintMethod.TELEA.value
        )
        if use_ffmpeg_path:
            try:
                import cv2  # noqa: F401
                use_ffmpeg_path = False
            except ImportError:
                pass

        if use_ffmpeg_path:
            inpaint_video_ffmpeg(
                input_path=args.input,
                output_path=args.output,
                regions=regions,
            )
        else:
            inpaint_video(
                input_path=args.input,
                output_path=args.output,
                regions=regions or None,
                mask_path=args.mask,
                auto_scratch=args.auto_scratch,
                method=InpaintMethod(args.method),
                radius=args.radius,
                scratch_sensitivity=args.scratch_sensitivity,
                dilate_pixels=args.dilate,
            )
        return

    if args.command == "cover":
        target_size: tuple[int, int] | None = None
        if args.width is not None or args.height is not None:
            if args.width is None or args.height is None:
                parser.error("--width and --height must be set together.")
            target_size = (args.width, args.height)

        output_path = compose_cover_with_title(
            source_path=args.input,
            output_path=args.output,
            title=args.title,
            target_size=target_size,
            font_path=args.font_path,
            font_size=args.font_size,
            position=args.position,
            offset_y=args.offset_y,
            box_color=args.box_color,
            box_alpha=args.box_alpha,
            text_color=args.text_color,
            stroke_color=args.stroke_color,
            stroke_width=args.stroke_width,
        )
        print(f"Cover generated: {output_path}")
        return

    if args.command == "doctor":
        from videocut.doctor import run_doctor

        raise SystemExit(run_doctor(config_path=args.config))

    if args.command == "clean-runs":
        from videocut.clean_runs import run_clean_runs

        config = load_pipeline_config(args.config)
        runs_dir = args.runs_dir if args.runs_dir is not None else config.runs_dir
        raise SystemExit(
            run_clean_runs(
                runs_dir,
                keep_days=args.keep_days,
                all_=args.all_runs,
                force=args.force,
            )
        )


def _apply_run_overrides(config: PipelineConfig, args: argparse.Namespace) -> None:
    for field_name, value in (
        ("runs_dir", args.runs_dir),
        ("output_name", args.output_name),
        ("cosyvoice_python", args.cosyvoice_python),
        ("cosyvoice_mode", args.cosyvoice_mode),
        ("enable_voice_clone", args.voice_clone),
        ("cosyvoice_speaker", args.cosyvoice_speaker),
        ("reference_text", args.reference_text),
        ("llm_base_url", args.llm_base_url),
        ("llm_api_key", args.llm_api_key),
        ("llm_model", args.llm_model),
        ("llm_timeout", args.llm_timeout),
        ("translation_batch_size", args.translation_batch_size),
        ("translation_concurrency", args.translation_concurrency),
        ("translation_target_cps", args.translation_target_cps),
        ("translation_char_tolerance", args.translation_char_tolerance),
        ("subtitle_font", args.subtitle_font),
        ("subtitle_font_path", args.subtitle_font_path),
    ):
        _apply_override(config, field_name, value)

    _apply_override(config, "cleanup_source_after_publish", args.cleanup_source)
    _apply_override(config, "burn_subtitles", args.burn_subtitles)
    _apply_override(config, "cosyvoice_group_size", args.cosyvoice_group_size, lambda value: max(1, value))
    _apply_override(config, "cosyvoice_concurrency", args.cosyvoice_concurrency, lambda value: max(1, value))
    _apply_override(config, "original_audio_volume", args.original_volume)
    _apply_override(config, "dub_audio_volume", args.dub_volume)
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

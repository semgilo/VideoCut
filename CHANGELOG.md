# Changelog

## 2026-04-12

### Added
- `videocut inpaint` command for logo/watermark removal and old-film scratch repair.
  - Three algorithms: `telea` (Fast Marching, default), `ns` (Navier-Stokes), `lama` (deep learning via `simple-lama-inpainting`).
  - `--region X,Y,W,H`: static bounding-box mask, repeatable for multiple logos.
  - `--mask FILE`: greyscale mask image (white = inpaint).
  - `--scratch`: automatic vertical scratch detection via column-stripe analysis.
  - `--dilate`, `--radius`, `--scratch-sensitivity` for fine-tuning.
  - Falls back to ffmpeg `delogo` filter when OpenCV is absent and only static regions are requested.
- `videocut/inpaint.py`: core inpainting module with frame-level API (`inpaint_frame`, `inpaint_video`) and standalone ffmpeg helper (`inpaint_video_ffmpeg`).

## 2026-04-08 (patch)

### Added
- Added `--voice-clone/--no-voice-clone` CLI switch for controlling whether CosyVoice runs in voice-clone mode.
- Added CosyVoice config keys: `voice_clone` and `speaker` under `[cosyvoice]`.
- Added non-clone `sft` mode support in `scripts/cosyvoice_batch.py` with automatic built-in speaker selection (or explicit `--speaker`).

### Changed
- Platform title generation is now deterministic and based on source/localized title + platform length limits (no hardcoded topic-rewrite titles).
- Platform hashtag generation now enforces per-platform limits (抖音 5 / B站 6 / 小红书 10) using metadata tags + title keywords with dedup.
- Cover text generation now derives directly from title and keeps 小红书 output to two lines.
- Synced `README.md`, `README.zh-CN.md`, `USAGE.zh-CN.md`, and `OUTPUT_STANDARD.md` with the new voice-clone toggle and platform material rules.

## 2026-04-01 (patch)

### Added
- Added `_is_qwen3_model()` helper to detect Qwen3 model variants by name prefix.
- Qwen3 models now automatically receive `thinking_budget=0` in all chat/completion requests, disabling chain-of-thought reasoning and ensuring direct JSON output for translation tasks.
- Qwen3 models also receive an empty pre-filled assistant think block (`<think>\n\n</think>`) as a fallback for backends that support assistant prefill.

### Changed
- Default translation model switched from `translategemma-4b-it-mlx-4bit` to `Qwen3.5-4B-MLX-4bit` in `videocut.toml`.
- Verified optimal translation concurrency: `concurrency=4` with `batch_size=10` on Mac mini M-series gives ~2–3 min for 336 segments (~2 segs/s sustained).

## 2026-04-01

### Changed
- Unified the production path into one fixed pipeline: download -> subtitle parse -> local LLM translation -> CosyVoice TTS -> duration measure -> stretch/compress alignment -> bilingual SRT -> dub mix -> final render -> publish assets.
- Removed runtime fallbacks for ASR/chinese-track reuse from the main pipeline and made English subtitles + local LLM translation explicit requirements.
- Simplified config/CLI surface to unified-flow fields only and updated default TOML/ENV templates accordingly.
- Updated subtitle-budget calculation to `L/V` form via `target_cps=4.5` with `char_tolerance=0.2` (±20%).
- Switched timing planner behavior to strict boundary alignment with exact per-segment rate (`synthetic_duration / subtitle_duration`) without trim/crop retries.
- Completion-model translation now supports adaptive batching for compatible models, with robust JSON extraction and automatic single-segment fallback for models that do not reliably emit batch JSON.
- CosyVoice multi-worker execution now uses `as_completed` handling for quicker worker failure surfacing.
- Synthesized-segment duration probing now runs in parallel.

### Added
- Added `USAGE.zh-CN.md` as the unified usage manual.

## 2026-03-22

### Added
- Added `scripts/validate_translation_sample.py` for 5-minute subtitle validation runs that translate, compare source baselines, and generate `review.md`.
- Added `translation_protected_terms.txt` and protected-term masking so product names, filenames, commands, and other pinned terms stay untranslated.
- Added a post-TTS single-line repair loop in `videocut/dub_timing.py` that measures real synthesized audio duration, rewrites only locally overlong lines, and re-synthesizes only those lines.
- Added `scripts/say_tts_adapter.py` as a reusable `--tts-provider command` example adapter for macOS `say`.

### Changed
- Translation now supports configurable concurrency, timing-oriented line shortening, and audio-duration-aware repair prompts.
- Re-render and pipeline flows now share the same dubbing-timing fallback and post-TTS repair path.
- Video download preference now favors HD/1080p when available.
- ffmpeg and ffprobe binaries are now configurable, making `ffmpeg-full` with `libass` usable as the default high-speed hard-subtitle path.

### Cleaned Up
- Removed one-off local rerun/manual scripts that were hardcoded to personal run directories and not part of the reusable toolchain.

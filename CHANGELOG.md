# Changelog

## 2026-04-01

### Changed
- Unified the production path into one fixed pipeline: download -> subtitle parse -> local LLM translation -> CosyVoice TTS -> duration measure -> stretch/compress alignment -> bilingual SRT -> dub mix -> final render -> publish assets.
- Removed runtime fallbacks for ASR/chinese-track reuse from the main pipeline and made English subtitles + local LLM translation explicit requirements.
- Simplified config/CLI surface to unified-flow fields only and updated default TOML/ENV templates accordingly.
- Updated subtitle-budget calculation to `L/V` form via `target_cps=4.5` with `char_tolerance=0.2` (±20%).
- Switched timing planner behavior to strict boundary alignment with exact per-segment rate (`synthetic_duration / subtitle_duration`) without trim/crop retries.

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

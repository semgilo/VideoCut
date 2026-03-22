# Changelog

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

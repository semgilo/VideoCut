# VideoCut

[简体中文](README.zh-CN.md)

VideoCut is a local pipeline that turns an English YouTube video into a Chinese-dubbed video with Chinese subtitles.

It is designed for practical end-to-end processing:

1. Download a single YouTube video and subtitle tracks with `yt-dlp`
2. Reuse English subtitles when available, or fall back to ASR
3. Translate subtitle segments into spoken Simplified Chinese
4. Generate per-segment Chinese dubbing with `edge-tts`, `MiniMax`, or `CosyVoice`
5. Reschedule the dubbed speech onto a more natural timeline
6. Mix the new dub with optional original audio and export the final video with subtitles
7. Export a publish bundle with cover, translated title, tags, description, and a local preview page

## What It Can Do

- Download one YouTube video at a time
- Prefer existing English subtitle tracks for better alignment and lower cost
- Fall back to `faster-whisper` transcription if no English subtitle track exists
- Reuse YouTube Chinese subtitle tracks directly when no translation API key is configured
- Translate subtitles through an OpenAI-compatible chat-completions API
- Generate Chinese dubbing with:
  - `edge-tts` for fast cloud TTS
  - `MiniMax` for faster cloud TTS with higher-quality Chinese voices and optional voice cloning
  - `CosyVoice` for local cross-lingual or zero-shot voice cloning
- Export:
  - Chinese `.srt`
  - a mixed dubbed audio track
  - a final `.mp4` with burned subtitles or soft subtitles
  - translated publish metadata and a reusable cover image
- Save a `manifest.json` that can be re-rendered later with a different TTS setup

## How The Pipeline Works

### 1. Asset acquisition

`yt-dlp` downloads the video, source metadata, thumbnail, plus English subtitles or auto-captions. If translation is not configured, VideoCut also tries to fetch Chinese subtitle tracks so the pipeline can still finish without an LLM.

### 2. Subtitle normalization

VTT cues are cleaned, overlapping progressive captions are collapsed, and very short neighboring cues can be merged. This produces a more stable segment list for translation and dubbing.

### 3. Translation

If `VIDEOCUT_LLM_API_KEY` is set, subtitle batches are sent to an OpenAI-compatible `/chat/completions` endpoint. The translator asks for strict JSON output and automatically retries with smaller batches when a request fails.

The same translator is also used to localize the source title, tags, and description into Simplified Chinese while preserving proper nouns.

If no API key is available and a Chinese subtitle track exists, VideoCut skips subtitle translation and reuses that track. The publish metadata then stays in the original language.

### 4. TTS synthesis

Each subtitle segment is synthesized into its own audio file.

- `edge-tts` is the simplest option and works well for fast validation.
- `MiniMax` is a fast cloud option that can reuse a system voice ID or clone a voice from the source audio.
- `CosyVoice` can synthesize Chinese with a reference speaker sample:
  - `cross_lingual`: reference audio only
  - `zero_shot`: reference audio plus reference transcript

If no reference audio is provided for `CosyVoice`, VideoCut extracts a prompt clip from the source video automatically.
If `VIDEOCUT_MINIMAX_VOICE_CLONE=1`, VideoCut also extracts a short prompt clip automatically and caches the cloned voice id under `tts/minimax_voice.json`.

### 5. Natural timing scheduler

The dub is not forced to match the original subtitle windows exactly. Instead, VideoCut:

- reduces excessive opening silence with a bounded global shift
- computes a base playback rate needed to fit the whole dub into the video
- schedules each segment with a minimum inter-segment gap
- allows a limited lag relative to the next subtitle anchor
- speeds up only when necessary, up to `VIDEOCUT_MAX_PLAYBACK_RATE`

This is intentionally a natural-speech-first strategy rather than lip-sync.

If the dub still cannot fit within the configured playback-rate limit, the pipeline fails fast and tells you to shorten the translation, use a faster voice, or relax the constraints.

### 6. Mixdown and export

All synthesized segments are delayed onto the planned timeline, time-stretched with `ffmpeg atempo`, mixed into one dubbed track, and combined with the source video.

- If the local `ffmpeg` build supports the `subtitles` filter, subtitles are burned into the video.
- Otherwise, VideoCut falls back to muxing soft subtitles into the final MP4.
- The source thumbnail is copied into a standard publish asset bundle together with `title.txt`, `tags.txt`, `description.txt`, `metadata.json`, and `content_preview.html`.

## Requirements

- Python 3.11+
- `ffmpeg`
- `ffprobe`
- `yt-dlp`

Optional:

- `faster-whisper` for ASR fallback
- `CosyVoice` plus model weights for local voice cloning

## Installation

### Base setup

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

### Enable ASR fallback

```bash
uv pip install -e ".[asr]"
```

### Optional CosyVoice setup

The repository does not vendor `CosyVoice` or model weights. Keep them locally outside Git, or place a local checkout under `.vendor/CosyVoice`.

Example:

```bash
git clone https://github.com/FunAudioLLM/CosyVoice.git .vendor/CosyVoice
```

Then point `VIDEOCUT_COSYVOICE_MODEL_DIR` to your downloaded model directory.

In practice, it is often cleaner to keep a dedicated Python environment for `CosyVoice`, for example:

```bash
python3.11 -m venv .venv-cosyvoice
source .venv-cosyvoice/bin/activate
pip install -r .vendor/CosyVoice/requirements.txt
```

Then set `VIDEOCUT_COSYVOICE_PYTHON` to that interpreter path, such as `./.venv-cosyvoice/bin/python`.

Note: the first `CosyVoice` run may download extra frontend assets from `ModelScope` at runtime. Plan for network access on the first run.

## Configuration

Copy the environment template:

```bash
cp .env.example .env
```

### Translation settings

```env
VIDEOCUT_LLM_BASE_URL=https://api.openai.com/v1
VIDEOCUT_LLM_API_KEY=your_api_key
VIDEOCUT_LLM_MODEL=gpt-4o-mini
```

If the source video already has `zh-Hans`, `zh-CN`, or `zh-Hant` subtitles, VideoCut can still complete without an API key by reusing the Chinese track.

### Default TTS path: CosyVoice

```env
VIDEOCUT_TTS_PROVIDER=cosyvoice
VIDEOCUT_COSYVOICE_PYTHON=./.venv-cosyvoice/bin/python
VIDEOCUT_COSYVOICE_REPO_DIR=.vendor/CosyVoice
VIDEOCUT_COSYVOICE_MODEL_DIR=.vendor/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B
VIDEOCUT_COSYVOICE_MODE=cross_lingual
VIDEOCUT_COSYVOICE_GROUP_SIZE=1
VIDEOCUT_ORIGINAL_AUDIO_VOLUME=0.0
VIDEOCUT_DUB_AUDIO_VOLUME=1.0
```

`VIDEOCUT_COSYVOICE_GROUP_SIZE` is optional. Keep `1` for the safest segmentation, or raise it to `2` or `3` on longer videos to synthesize adjacent subtitles together and split them back into per-segment audio automatically.

### Fastest fallback path: edge-tts

```env
VIDEOCUT_TTS_PROVIDER=edge
VIDEOCUT_TTS_VOICE=zh-CN-YunxiNeural
VIDEOCUT_TTS_RATE=+5%
```

### Faster cloud path: MiniMax

```env
VIDEOCUT_TTS_PROVIDER=minimax
VIDEOCUT_MINIMAX_API_KEY=your_minimax_api_key
VIDEOCUT_MINIMAX_MODEL=speech-2.8-turbo
VIDEOCUT_MINIMAX_VOICE_ID=Chinese (Mandarin)_News_Anchor
VIDEOCUT_MINIMAX_SPEED=1.0
VIDEOCUT_MINIMAX_CONCURRENCY=4
VIDEOCUT_MINIMAX_VOICE_CLONE=0
```

Optional reference audio:

```env
VIDEOCUT_REFERENCE_AUDIO_PATH=/absolute/path/to/reference.wav
VIDEOCUT_REFERENCE_TEXT=
```

### Timing controls

These defaults prioritize natural speech over strict subtitle-window matching:

```env
VIDEOCUT_MAX_PLAYBACK_RATE=1.18
VIDEOCUT_MAX_SEGMENT_LAG=0.8
VIDEOCUT_MAX_OPENING_SILENCE=0.35
VIDEOCUT_MAX_GLOBAL_SHIFT=2.5
VIDEOCUT_MIN_SEGMENT_GAP=0.05
VIDEOCUT_TRIM_TTS_SILENCE=1
VIDEOCUT_TTS_SILENCE_THRESHOLD_DB=-35
VIDEOCUT_TTS_SILENCE_MIN_DURATION=0.05
VIDEOCUT_TTS_KEEP_SILENCE=0.02
```

## Usage

### Run the full pipeline

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID"
```

This now uses `CosyVoice` by default when the local repo and model paths are available.

### Choose a custom work directory

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --workdir runs/demo \
  --dub-volume 1.0
```

### Override the bundled CosyVoice setup

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --cosyvoice-python ./.venv-cosyvoice/bin/python \
  --cosyvoice-repo /absolute/path/to/CosyVoice \
  --cosyvoice-model /absolute/path/to/Fun-CosyVoice3-0.5B
```

### Speed up long CosyVoice renders

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --cosyvoice-group-size 3
```

This batches nearby subtitle lines into a single CosyVoice call, then splits the result back into per-subtitle WAV files using silence-aware boundaries. It is primarily a throughput option for long videos.

### Switch back to edge-tts

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --tts-provider edge \
  --voice zh-CN-YunxiNeural \
  --tts-rate +5%
```

### Try MiniMax

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --tts-provider minimax \
  --minimax-api-key "$MINIMAX_API_KEY" \
  --voice "Chinese (Mandarin)_News_Anchor" \
  --minimax-speed 1.0 \
  --minimax-concurrency 4
```

To let MiniMax clone a voice from the source video first:

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --tts-provider minimax \
  --minimax-api-key "$MINIMAX_API_KEY" \
  --minimax-voice-clone
```

### Export without burning subtitles

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" --no-burn-subtitles
```

## Output Layout

Each run creates a working directory under `runs/` or your custom `--workdir`.

- `source/`: downloaded video, subtitle files, thumbnail, source metadata, and extracted audio
- `subtitles/zh.srt`: generated Chinese subtitles
- `tts/`: per-segment synthesized audio files
- `tts/reference_prompt.wav`: auto-extracted reference audio for `CosyVoice`
- `tts/cosyvoice_inputs.json`: `CosyVoice` batch input manifest
- `audio/dubbed_track.m4a`: mixed Chinese dub track
- `final_cn.mp4`: final exported video; if ffmpeg lacks the `subtitles` filter, this falls back to a soft-subtitle MP4
- `publish/cover.jpg`: copied cover image in a regular image format when available
- `publish/title.txt`: localized Chinese title
- `publish/tags.txt`: localized Chinese tags
- `publish/description.txt`: localized Chinese description
- `publish/metadata.json`: structured source + localized metadata
- `publish/content_preview.html`: local preview page for the final video and metadata
- `manifest.json`: full run manifest for inspection or re-rendering

## Utility Scripts

### Re-render an existing manifest

Use this when you already have translated segments and only want to change the TTS backend, voice, or timing constraints:

```bash
python scripts/render_from_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --output-dir /absolute/path/to/rerender-cosy
```

This follows the configured default TTS provider, which is `CosyVoice` unless you override it.

You can also raise the CosyVoice batching size during a re-render:

```bash
python scripts/render_from_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --output-dir /absolute/path/to/rerender-cosy \
  --cosyvoice-group-size 3
```

To re-render the same manifest with `edge-tts`, switch the provider explicitly:

```bash
python scripts/render_from_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --output-dir /absolute/path/to/rerender-edge \
  --tts-provider edge \
  --voice zh-CN-YunxiNeural
```

### Rewrite segments that were forced too fast

This helper scans a manifest for segments whose playback rate exceeded a threshold, asks a local `ollama` model to shorten them, and writes a rewritten manifest:

```bash
python scripts/rewrite_dub_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --threshold 1.12 \
  --target-rate 1.05
```

## Current Limitations

- Single-video workflow only; no playlist processing
- No full lip-sync alignment
- Subtitle quality depends on source captions or ASR quality
- Translation quality and terminology consistency depend on the configured model
- `CosyVoice` local inference can be much slower than `edge-tts`, especially on macOS

## Practical Notes

- `CosyVoice` is best treated as a final-pass renderer, not the fastest way to validate a pipeline. For timing checks, many teams first run `edge-tts`, shorten lines that are too long, and only then switch to `CosyVoice`.
- English names, brand names, channel names, and outro promotion lines can expand a lot in `CosyVoice`. Converting them into shorter Chinese phrasing often improves sync more than raising `VIDEOCUT_MAX_PLAYBACK_RATE`.
- By default, `CosyVoice` still renders one subtitle at a time. For longer videos, `VIDEOCUT_COSYVOICE_GROUP_SIZE=2` or `3` usually reduces total wall-clock time without changing the final subtitle timing plan.
- For long videos, prefer splitting work into shorter chunks, validating subtitles and timing first, and then re-rendering from `manifest.json`.

## Compliance

Before downloading, translating, dubbing, or redistributing any source video, verify that you have the legal right to use that content and that your workflow complies with YouTube policies and copyright requirements.

# VideoCut

[简体中文](README.zh-CN.md)

VideoCut is a local pipeline that turns an English YouTube video into a Chinese-dubbed video with Chinese subtitles.

It is designed for practical end-to-end processing:

1. Download a single YouTube video and subtitle tracks with `yt-dlp`
2. Reuse English subtitles when available, or fall back to ASR
3. Translate subtitle segments into spoken Simplified Chinese
4. Generate per-segment Chinese dubbing with `edge-tts`, `MiniMax`, `CosyVoice`, or an external adapter command
5. Reschedule the dubbed speech onto a more natural timeline
6. Mix the new dub with optional original audio and export the final video with subtitles
7. Export a publish bundle with cover, translated title, tags, description, and a local preview page

## What It Can Do

- Download one YouTube video at a time
- Prefer existing English subtitle tracks for better alignment and lower cost
- Fall back to `faster-whisper` transcription if no English subtitle track exists
- Reuse YouTube Chinese subtitle tracks directly when no translation endpoint is configured
- Translate subtitles through an OpenAI-compatible chat-completions API
- Generate Chinese dubbing with:
  - `edge-tts` for fast cloud TTS
  - `MiniMax` for faster cloud TTS with higher-quality Chinese voices and optional voice cloning
  - `CosyVoice` for local cross-lingual or zero-shot voice cloning
  - `command` for plugging in a local adapter script around tools such as Fish Speech, RVC, or so-vits-svc
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

If `VIDEOCUT_LLM_BASE_URL` and `VIDEOCUT_LLM_MODEL` are configured, subtitle batches are sent to an OpenAI-compatible `/chat/completions` endpoint. Remote endpoints usually still need `VIDEOCUT_LLM_API_KEY`, but local endpoints such as `http://127.0.0.1:8000/v1` can be used without a key. The translator asks for strict JSON output and automatically retries with smaller batches when a request fails.

The same translator is also used to localize the source title, tags, and description into Simplified Chinese while preserving proper nouns.

If no translation endpoint is configured and a Chinese subtitle track exists, VideoCut skips subtitle translation and reuses that track. The publish metadata then stays in the original language.

### 4. TTS synthesis

Each subtitle segment is synthesized into its own audio file.

- `edge-tts` is the simplest option and works well for fast validation.
- `MiniMax` is a fast cloud option that can reuse a system voice ID or clone a voice from the source audio.
- `CosyVoice` can synthesize Chinese with a reference speaker sample:
  - `cross_lingual`: reference audio only
  - `zero_shot`: reference audio plus reference transcript
- `command` lets you call an arbitrary local adapter once with a manifest file. The adapter receives the Chinese lines, target output paths, and optional extracted prompt audio so you can wrap any local voice-clone stack you already use.

If no reference audio is provided for `CosyVoice`, VideoCut extracts a prompt clip from the source video automatically.
If `VIDEOCUT_MINIMAX_VOICE_CLONE=1`, VideoCut also extracts a short prompt clip automatically and caches the cloned voice id under `tts/minimax_voice.json`.

### 5. Natural timing scheduler

The dub is not forced to match the original subtitle windows exactly. Instead, VideoCut:

- reduces excessive opening silence with a bounded global shift
- computes a base playback rate needed to fit the whole dub into the video
- schedules each segment with a minimum inter-segment gap
- allows a limited lag relative to the next subtitle anchor
- optionally fits each segment closer to the original subtitle window with `VIDEOCUT_TIMING_MODE=fit`
- keeps playback-rate changes inside `VIDEOCUT_MIN_PLAYBACK_RATE` to `VIDEOCUT_MAX_PLAYBACK_RATE`

This is intentionally a natural-speech-first strategy rather than lip-sync.

If the dub still cannot fit within the configured playback-rate limit, the pipeline fails fast and tells you to shorten the translation, use a faster voice, or relax the constraints.

### 6. Mixdown and export

All synthesized segments are delayed onto the planned timeline, time-stretched with `ffmpeg atempo`, mixed into one dubbed track, and combined with the source video.

- If the local `ffmpeg` build supports the `subtitles` filter, subtitles are burned into the video.
- Otherwise, VideoCut tries a Pillow + `ffmpeg overlay` hard-subtitle fallback, and only then falls back to muxing soft subtitles into the final MP4.
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

If you want the whole workflow to be driven by a single config file, also copy the TOML template:

```bash
cp videocut.example.toml videocut.toml
```

After that, the default entry point is simply:

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID"
```

The CLI auto-loads `videocut.toml` from the current directory when it exists. Command-line flags override the TOML values.

### Translation settings

```env
VIDEOCUT_LLM_BASE_URL=http://127.0.0.1:8000/v1
VIDEOCUT_LLM_API_KEY=
VIDEOCUT_LLM_MODEL=Qwen3.5-9B-MLX-4bit
```

If the source video already has `zh-Hans`, `zh-CN`, or `zh-Hant` subtitles, VideoCut can still complete without any LLM by reusing the Chinese track.

For a hosted provider, keep the same base URL pattern and fill in `VIDEOCUT_LLM_API_KEY`.

### Local parallel translation guidance

If you want the local model server to translate subtitles in parallel, prefer chat models that support `/v1/chat/completions`, for example:

```env
VIDEOCUT_LLM_BASE_URL=http://127.0.0.1:8888/v1
VIDEOCUT_LLM_API_KEY=your_local_api_key
VIDEOCUT_LLM_MODEL=Qwen3.5-9B-MLX-4bit
VIDEOCUT_TRANSLATION_BATCH_SIZE=25
VIDEOCUT_TRANSLATION_CONCURRENCY=4
```

Start with `VIDEOCUT_TRANSLATION_CONCURRENCY=2` or `4`, then raise it only after checking local memory pressure and server stability.

Do not treat `translategemma-*` as the default high-throughput local model. The current code detects model names that start with `translategemma` and routes them through the per-line `/v1/completions` path. That still supports concurrency, but it sends many more requests and is usually less stable on longer videos than the batched JSON chat-model path.

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

### Pluggable external voice-clone path

```env
VIDEOCUT_TTS_PROVIDER=command
VIDEOCUT_TTS_COMMAND=python /absolute/path/to/your_tts_adapter.py
VIDEOCUT_TTS_COMMAND_AUDIO_FORMAT=wav
VIDEOCUT_REFERENCE_AUDIO_PATH=
VIDEOCUT_REFERENCE_TEXT=
```

VideoCut will write `tts/tts_command_inputs.json` and call the adapter with `--input-json /absolute/path/to/tts_command_inputs.json`.
The manifest contains the Chinese text, source English text, target output path per segment, optional prompt audio path, and optional prompt text.

### Timing controls

These defaults prioritize natural speech over strict subtitle-window matching:

```env
VIDEOCUT_TIMING_MODE=natural
VIDEOCUT_MIN_PLAYBACK_RATE=0.6
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

Optional hard-subtitle fallback font path:

```env
VIDEOCUT_SUBTITLE_FONT_PATH=/System/Library/Fonts/PingFang.ttc
```

## Usage

### Run the full pipeline

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID"
```

This now uses `CosyVoice` by default when the local repo and model paths are available.
YouTube downloads now prefer `1080p`, then `720p`, and only then fall back to lower resolutions.

### Run the requested YouTube Shorts voice-clone workflow

```bash
videocut run "https://www.youtube.com/shorts/4br1vok5ohk" \
  --workdir runs/4br1vok5ohk-cosy \
  --llm-base-url http://127.0.0.1:8000/v1 \
  --llm-model Qwen3.5-9B-MLX-4bit \
  --tts-provider cosyvoice \
  --cosyvoice-mode cross_lingual \
  --timing-mode fit \
  --min-playback-rate 0.6 \
  --max-playback-rate 1.5
```

This command assumes your local OpenAI-compatible model server is already running and your local `CosyVoice` repo plus weights are configured.

### Subtitle-only mode with no dubbing and no voice cloning

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --mode subtitle_only \
  --config videocut.toml
```

Recommended `videocut.toml` block:

```toml
[pipeline]
mode = "subtitle_only"
translation_backend = "llm"
export_platform_materials = true
output_name = "final_subtitled.mp4"

[translation]
llm_base_url = "http://127.0.0.1:8888/v1"
llm_api_key = "your_local_api_key"
llm_model = "Qwen3.5-9B-MLX-4bit"
batch_size = 25
concurrency = 4
```

If the local model endpoint is temporarily unstable but you still want the subtitle-only deliverable and platform kits, switch the config to:

```toml
[pipeline]
translation_backend = "google"
```

### AI agent call pattern

If the user says any of the following:

- subtitle translation only
- no dubbing
- no voice cloning
- generate Douyin / Bilibili / Xiaohongshu materials without exporting multiple video versions

Prefer this entry point:

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --mode subtitle_only \
  --config videocut.toml
```

And follow these rules:

1. If the user explicitly wants local parallel model processing, set `VIDEOCUT_LLM_MODEL` to a chat model such as `Qwen3.5-9B-MLX-4bit` and set `VIDEOCUT_TRANSLATION_CONCURRENCY=2` or `4`.
2. Subtitle-only requests should still use `videocut run`; switch the workflow only through `--mode subtitle_only` and the config file.
3. If the local LLM endpoint is unavailable or unstable, keep the same CLI and switch the config file to `translation_backend = "google"`.
4. The default output directory is `runs/<video_id>-subtitle-only-<date>/`.

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

### Plug in Fish Speech / RVC / so-vits-svc through one adapter command

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --llm-base-url http://127.0.0.1:8000/v1 \
  --llm-model Qwen3.5-9B-MLX-4bit \
  --tts-provider command \
  --tts-command "python /absolute/path/to/your_tts_adapter.py" \
  --tts-command-audio-format wav \
  --timing-mode fit \
  --min-playback-rate 0.6 \
  --max-playback-rate 1.5
```

### Export without burning subtitles

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" --no-burn-subtitles
```

## Output Layout

Each run creates a working directory under `runs/` or your custom `--workdir`.

- `source/`: downloaded video, subtitle files, thumbnail, source metadata, and extracted audio
- `subtitles/zh.srt`: generated Chinese subtitles
- `final_subtitled.mp4`: final subtitle-only export with original audio retained
- `tts/`: per-segment synthesized audio files
- `tts/reference_prompt.wav`: auto-extracted reference audio for `CosyVoice`
- `tts/cosyvoice_inputs.json`: `CosyVoice` batch input manifest
- `tts/tts_command_inputs.json`: external command-provider input manifest when `--tts-provider command` is used
- `audio/dubbed_track.m4a`: mixed Chinese dub track
- `final_cn.mp4`: final exported video; if ffmpeg lacks the `subtitles` filter, VideoCut next tries the Pillow overlay burn-in path before falling back to a soft-subtitle MP4
- `subtitles/burn_overlays/*.png`: generated overlay images when the Pillow hard-subtitle fallback is used
- `publish/cover.jpg`: copied cover image in a regular image format when available
- `publish/title.txt`: localized Chinese title
- `publish/tags.txt`: localized Chinese tags
- `publish/description.txt`: localized Chinese description
- `publish/metadata.json`: structured source + localized metadata
- `publish/content_preview.html`: local preview page for the final video and metadata
- `platforms/<platform>/cover.jpg`: platform-sized cover image
- `platforms/<platform>/cover_source.jpg`: original cover source backup
- `platforms/<platform>/requirements.md`: public upload requirements plus fit assessment
- `platforms/<platform>/title.txt` / `description.txt` / `hashtags.txt`: platform packaging materials
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
- High-resolution YouTube formats may depend on `yt-dlp` challenge solving. The downloader now enables `--remote-components ejs:github` by default to improve access to 720p/1080p formats.

## Practical Notes

- `CosyVoice` is best treated as a final-pass renderer, not the fastest way to validate a pipeline. For timing checks, many teams first run `edge-tts`, shorten lines that are too long, and only then switch to `CosyVoice`.
- English names, brand names, channel names, and outro promotion lines can expand a lot in `CosyVoice`. Converting them into shorter Chinese phrasing often improves sync more than raising `VIDEOCUT_MAX_PLAYBACK_RATE`.
- By default, `CosyVoice` still renders one subtitle at a time. For longer videos, `VIDEOCUT_COSYVOICE_GROUP_SIZE=2` or `3` usually reduces total wall-clock time without changing the final subtitle timing plan.
- For long videos, prefer splitting work into shorter chunks, validating subtitles and timing first, and then re-rendering from `manifest.json`.

## Compliance

Before downloading, translating, dubbing, or redistributing any source video, verify that you have the legal right to use that content and that your workflow complies with YouTube policies and copyright requirements.

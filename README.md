# VideoCut

VideoCut is a unified YouTube English-subtitle -> Chinese dubbing pipeline with one primary path:
local LLM translation + CosyVoice synthesis + boundary-aligned render.

## Highlights
- Fixed 10-step production flow (download, parse, translate, synthesize, measure, align, SRT, mix, render, publish assets)
- Character budget from `L/V` (`V = 1/4.5`, tolerance `±20%`)
- CosyVoice as the only TTS engine
- Per-segment stretch/compress to align synthesized speech to original subtitle start/end
- `ffmpeg-full` preferred automatically

## Quick Start
1. Install dependencies:
   - `uv sync`
   - `yt-dlp`
   - `ffmpeg-full`
2. Copy config:
```bash
cp videocut.example.toml videocut.toml
```
3. Run:
```bash
python -m videocut.cli run "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Detailed Guide
- Chinese usage manual: [USAGE.zh-CN.md](/Users/semgilo/Documents/git/VideoCut/USAGE.zh-CN.md)

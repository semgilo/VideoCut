# VideoCut

[English](README.md)

VideoCut 是一条本地视频处理流水线，用来把英文 YouTube 视频处理成带中文字幕和中文配音的新视频。

它面向实际可落地的端到端流程：

1. 用 `yt-dlp` 下载单个 YouTube 视频和字幕
2. 优先复用英文字幕，没有则回退到 ASR
3. 把字幕翻译成更适合配音的简体中文
4. 用 `edge-tts`、`MiniMax`、`CosyVoice` 或外部适配命令为每条字幕生成中文配音
5. 按“自然语速优先”的规则重新排程整条中文配音
6. 混音并导出带字幕的最终视频
7. 额外导出封面、中文标题/标签/简介和本地内容预览页

## 能做什么

- 一次处理一个 YouTube 视频
- 优先使用现成英文字幕，减少转录误差和成本
- 没有英文字幕时，回退到 `faster-whisper` 做英文转录
- 没有可用翻译端点时，直接复用 YouTube 已有中文字幕继续完成流程
- 通过 OpenAI 兼容接口完成字幕翻译
- 支持多种中文配音后端：
  - `edge-tts`：接入简单，适合快速跑通
  - `MiniMax`：云端速度更快，中文音色更稳，也可选做 voice clone
  - `CosyVoice`：适合本地跨语种 voice cloning
  - `command`：适合把 Fish Speech、RVC、so-vits-svc 等本地方案通过适配脚本接入
- 导出：
  - 中文 `SRT` 字幕
  - 混合后的中文配音轨
  - 带烧录字幕或软字幕的最终 `MP4`
  - 可直接复用的封面和中文发布素材
- 保存 `manifest.json`，后续可以在不重新下载视频的前提下重新配音和导出

## 工作原理

### 1. 下载素材

`yt-dlp` 会下载视频本体、原始元数据、缩略图，以及英文字幕或自动字幕。如果没有配置翻译接口，VideoCut 还会额外尝试拉取中文字幕轨，这样在没有 LLM 的情况下也能继续生成中文配音视频。

### 2. 字幕清洗与切分

VideoCut 会解析 VTT，清理 HTML 标签和内联时间戳，合并渐进式重复字幕，必要时把过短的相邻片段拼接起来，得到更稳定的字幕段列表。

### 3. 翻译

如果配置了 `VIDEOCUT_LLM_BASE_URL` 和 `VIDEOCUT_LLM_MODEL`，系统就会按批次把字幕发到 OpenAI 兼容的 `/chat/completions` 接口，请求返回严格 JSON。远程服务通常仍需要 `VIDEOCUT_LLM_API_KEY`；但像 `http://127.0.0.1:8000/v1` 这种本地服务可以不带 key。若某批失败，会自动拆成更小批次重试。

同一个翻译器也会把原视频的标题、标签和简介翻成简体中文，并尽量保留专有名词不变。

如果没有可用翻译端点，但视频本身带有中文字幕轨，则直接复用中文字幕，不再调用字幕翻译接口；标题、标签和简介也会保留原文。

### 4. 中文配音

每条字幕会单独生成一个音频片段。

- `edge-tts`：适合快速验证流程，部署成本低
- `MiniMax`：适合追求更快云端合成和更自然的中文系统音色，也支持可选的自动克隆音色
- `CosyVoice`：适合做参考音色驱动的中文配音
  - `cross_lingual`：只需要参考音频
  - `zero_shot`：需要参考音频和参考文本
- `command`：会把当前任务写成一个 manifest，再调用你自己的本地适配脚本；适配脚本可以自行封装任意 voice clone/TTS/VC 组合

如果没有显式提供参考音频，VideoCut 会自动从原视频前段抽一小段人声作为 `CosyVoice` 的提示音频。
如果开启 `VIDEOCUT_MINIMAX_VOICE_CLONE=1`，VideoCut 也会自动抽取一段短提示音，并把克隆出来的 voice id 缓存在 `tts/minimax_voice.json`。

### 5. 自然排程

VideoCut 不会把中文配音死板地压回原始英文字幕框，而是采用“自然语速优先”的排程策略：

- 限制开头静音时间
- 允许一小段全局前移，减少“前面空一大截”的问题
- 先计算整条中文最少需要的基础播放倍率
- 为相邻句子保留最小间隔，避免句子挤在一起
- 允许每句相对下一个锚点有小幅滞后
- 可选开启 `VIDEOCUT_TIMING_MODE=fit`，让每句更贴近原字幕时间窗
- 全部变速都会限制在 `VIDEOCUT_MIN_PLAYBACK_RATE` 到 `VIDEOCUT_MAX_PLAYBACK_RATE` 之间

如果即使在当前速度上限下仍塞不进整条视频，流水线会直接报错，而不是生成明显失真的结果。

### 6. 混音与导出

所有配音片段会先按照排程时间轴做延迟和变速，再通过 `ffmpeg` 混成一条中文配音轨，最后与原视频合成：

- 如果本地 `ffmpeg` 支持 `subtitles` filter，就直接烧录中文字幕
- 如果不支持，就先尝试走 Pillow + `ffmpeg overlay` 的硬字幕回退，再不行才退回软字幕封装
- 原视频封面会被整理进 `publish/` 目录，同时输出 `title.txt`、`tags.txt`、`description.txt`、`metadata.json` 和 `content_preview.html`

## 环境要求

- Python 3.11+
- `ffmpeg`
- `ffprobe`
- `yt-dlp`

可选依赖：

- `faster-whisper`，用于无英文字幕时的 ASR 回退
- `CosyVoice` 和模型权重，用于本地 voice cloning

## 安装

### 基础安装

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

### 启用 ASR 回退

```bash
uv pip install -e ".[asr]"
```

### 可选：配置 CosyVoice

本仓库不会把 `CosyVoice` 源码和模型权重一起纳入 Git。建议把它们保留在本地目录，或者放在 `.vendor/CosyVoice` 这种被忽略的目录下。

示例：

```bash
git clone https://github.com/FunAudioLLM/CosyVoice.git .vendor/CosyVoice
```

然后把 `VIDEOCUT_COSYVOICE_MODEL_DIR` 指向你已经下载好的模型目录。

实际使用里，建议给 `CosyVoice` 单独准备一个 Python 环境，例如：

```bash
python3.11 -m venv .venv-cosyvoice
source .venv-cosyvoice/bin/activate
pip install -r .vendor/CosyVoice/requirements.txt
```

然后把 `VIDEOCUT_COSYVOICE_PYTHON` 指到这个解释器路径，比如 `./.venv-cosyvoice/bin/python`。

注意：`CosyVoice` 第一次运行时，可能还会从 `ModelScope` 下载额外的前端资源，所以首跑通常需要联网。

## 配置

先复制环境变量模板：

```bash
cp .env.example .env
```

### 翻译接口

```env
VIDEOCUT_LLM_BASE_URL=http://127.0.0.1:8000/v1
VIDEOCUT_LLM_API_KEY=
VIDEOCUT_LLM_MODEL=Qwen3.5-9B-MLX-4bit
```

如果源视频本身已经带有 `zh-Hans`、`zh-CN` 或 `zh-Hant` 字幕，即使完全不配置 LLM，也可以直接复用中文字幕跑完整条流程。

如果你用的是远程服务，保持同样的 OpenAI 兼容格式，再补上 `VIDEOCUT_LLM_API_KEY` 即可。

### 默认 TTS 路径：CosyVoice

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

`VIDEOCUT_COSYVOICE_GROUP_SIZE` 是可选的。默认 `1` 最稳；长视频可以尝试调到 `2` 或 `3`，让相邻几条字幕合成到同一次 CosyVoice 推理里，再自动按静音边界切回逐句音频。

### 最快的备用路径：edge-tts

```env
VIDEOCUT_TTS_PROVIDER=edge
VIDEOCUT_TTS_VOICE=zh-CN-YunxiNeural
VIDEOCUT_TTS_RATE=+5%
```

### 更快的云端路径：MiniMax

```env
VIDEOCUT_TTS_PROVIDER=minimax
VIDEOCUT_MINIMAX_API_KEY=your_minimax_api_key
VIDEOCUT_MINIMAX_MODEL=speech-2.8-turbo
VIDEOCUT_MINIMAX_VOICE_ID=Chinese (Mandarin)_News_Anchor
VIDEOCUT_MINIMAX_SPEED=1.0
VIDEOCUT_MINIMAX_CONCURRENCY=4
VIDEOCUT_MINIMAX_VOICE_CLONE=0
```

可选参考音频：

```env
VIDEOCUT_REFERENCE_AUDIO_PATH=/absolute/path/to/reference.wav
VIDEOCUT_REFERENCE_TEXT=
```

### 可插拔外部音色克隆路径

```env
VIDEOCUT_TTS_PROVIDER=command
VIDEOCUT_TTS_COMMAND=python /absolute/path/to/your_tts_adapter.py
VIDEOCUT_TTS_COMMAND_AUDIO_FORMAT=wav
VIDEOCUT_REFERENCE_AUDIO_PATH=
VIDEOCUT_REFERENCE_TEXT=
```

VideoCut 会写出 `tts/tts_command_inputs.json`，然后以 `--input-json /absolute/path/to/tts_command_inputs.json` 的形式调用你的适配脚本。
manifest 里会包含逐句中文、原文、目标输出路径，以及可选的参考音频路径和参考文本。

### 排程参数

下面这组参数的目标是让中文更自然，而不是机械贴回英文字幕窗口：

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

可选的硬字幕回退字体路径：

```env
VIDEOCUT_SUBTITLE_FONT_PATH=/System/Library/Fonts/PingFang.ttc
```

## 使用方式

### 跑完整条流水线

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID"
```

默认会优先使用 `CosyVoice`，只要本地仓库和模型路径可用即可。
YouTube 视频下载现在默认优先拿 `1080p`，没有再拿 `720p`，再没有才继续往下回退。

### 直接跑这次给定的 YouTube Shorts 配音任务

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

这个命令默认你已经把本地 OpenAI 兼容模型服务启动好了，并且 `CosyVoice` 仓库和模型权重路径都已配置完成。

### 指定工作目录

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --workdir runs/demo \
  --dub-volume 1.0
```

### 覆盖默认 CosyVoice 配置

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --cosyvoice-python ./.venv-cosyvoice/bin/python \
  --cosyvoice-repo /absolute/path/to/CosyVoice \
  --cosyvoice-model /absolute/path/to/Fun-CosyVoice3-0.5B
```

### 加速长视频 CosyVoice 渲染

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --cosyvoice-group-size 3
```

这个参数会把相邻几条字幕合成到一次 CosyVoice 调用里，再按静音边界切回逐句 WAV。它主要用于提升长视频的吞吐速度。

### 临时切回 edge-tts

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --tts-provider edge \
  --voice zh-CN-YunxiNeural \
  --tts-rate +5%
```

### 试跑 MiniMax

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --tts-provider minimax \
  --minimax-api-key "$MINIMAX_API_KEY" \
  --voice "Chinese (Mandarin)_News_Anchor" \
  --minimax-speed 1.0 \
  --minimax-concurrency 4
```

如果想先从原视频里克隆一个音色再逐句合成：

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --tts-provider minimax \
  --minimax-api-key "$MINIMAX_API_KEY" \
  --minimax-voice-clone
```

### 通过一个适配脚本接入 Fish Speech / RVC / so-vits-svc

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

### 不烧录字幕，只保留字幕文件

```bash
videocut run "https://www.youtube.com/watch?v=VIDEO_ID" --no-burn-subtitles
```

## 输出结构

每次运行都会在 `runs/` 或你指定的 `--workdir` 下生成一个工作目录。

- `source/`：下载的视频、字幕、缩略图、原始元数据和抽取的音频
- `subtitles/zh.srt`：生成的中文字幕
- `tts/`：逐句配音片段
- `tts/reference_prompt.wav`：自动抽取的 `CosyVoice` 参考音频
- `tts/cosyvoice_inputs.json`：`CosyVoice` 批量推理输入清单
- `tts/tts_command_inputs.json`：使用 `command` 提供方时生成的外部适配输入清单
- `audio/dubbed_track.m4a`：混合后的中文配音轨
- `final_cn.mp4`：最终导出视频；如果 ffmpeg 当前环境没有 `subtitles` 滤镜，VideoCut 会先尝试 Pillow 覆盖式硬字幕，再不行才退回软字幕 MP4
- `subtitles/burn_overlays/*.png`：走 Pillow 硬字幕回退时生成的透明字幕叠加图
- `publish/cover.jpg`：整理后的封面图（如果源站提供）
- `publish/title.txt`：中文版标题
- `publish/tags.txt`：中文版标签
- `publish/description.txt`：中文版简介
- `publish/metadata.json`：结构化保存的原始/中文元数据
- `publish/content_preview.html`：本地内容预览页
- `manifest.json`：完整任务清单，可用于复查或二次渲染

## 辅助脚本

### 基于 manifest 重新渲染

如果你已经拿到了翻译后的片段，只想换 TTS、换音色或换排程参数，可以直接重渲染：

```bash
python scripts/render_from_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --output-dir /absolute/path/to/rerender-cosy
```

这个脚本默认会跟随当前配置里的 TTS 提供方；现在默认就是 `CosyVoice`。

如果只是想在重渲染时提高 CosyVoice 吞吐，也可以直接传：

```bash
python scripts/render_from_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --output-dir /absolute/path/to/rerender-cosy \
  --cosyvoice-group-size 3
```

如果你想基于同一个 manifest 临时切回 `edge-tts`，再显式指定 provider：

```bash
python scripts/render_from_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --output-dir /absolute/path/to/rerender-edge \
  --tts-provider edge \
  --voice zh-CN-YunxiNeural
```

### 重写过长字幕

这个脚本会扫描 `manifest.json` 里播放倍率过高的句子，调用本地 `ollama` 模型把中文改写得更短，再输出一个新的 manifest：

```bash
python scripts/rewrite_dub_manifest.py \
  --manifest /absolute/path/to/manifest.json \
  --threshold 1.12 \
  --target-rate 1.05
```

## 当前限制

- 目前只处理单视频，不处理播放列表
- 不做逐帧口型同步
- 字幕质量受原字幕或 ASR 质量影响
- 翻译质量、术语一致性取决于你配置的模型
- `CosyVoice` 在 macOS 上通常比 `edge-tts` 慢很多，长视频需要预留时间
- YouTube 的高分辨率格式有时依赖 `yt-dlp` 的挑战求解。下载器现在默认启用 `--remote-components ejs:github`，以提高 720p/1080p 格式的可见性和可下载性。

## 实战建议

- `CosyVoice` 更适合作为最终成片阶段的渲染器，不适合拿来做最快速的流程验证。更实用的做法通常是先用 `edge-tts` 检查时轴，再缩短过长句子，最后切回 `CosyVoice`。
- 英文人名、品牌名、频道名和片尾宣传口播，常常会在 `CosyVoice` 里被念得很长。把它们改写成更短的中文表达，通常比一味提高 `VIDEOCUT_MAX_PLAYBACK_RATE` 更有效。
- 默认情况下，`CosyVoice` 仍然是逐句合成。长视频建议把 `VIDEOCUT_COSYVOICE_GROUP_SIZE` 调到 `2` 或 `3`，通常能在不改变最终时轴规划的前提下明显缩短总耗时。
- 对长视频，建议先拆成较短片段处理，先验证字幕和时轴，再基于 `manifest.json` 做最终重渲。

## 合规提醒

在下载、翻译、配音、再发布任何源视频之前，请先确认你对源内容拥有合法使用权限，并遵守 YouTube 平台规则以及相关版权要求。

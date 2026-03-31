# VideoCut

VideoCut 是一个统一的 YouTube 英文字幕 -> 中文配音流水线，默认只走一条主路径：本地 LLM 翻译 + CosyVoice 合成 + 起止点对齐渲染。

## 核心特性
- 固定 10 步统一流程（下载、解析、翻译、合成、测长、调速、字幕、混音、渲染、发布资产）
- 翻译阶段按 `L/V` 计算中文字符预算（`V=1/4.5`，容差 `±20%`）
- CosyVoice 作为唯一 TTS
- 每句通过拉伸/压缩实现与原字幕起止点对齐（不裁剪）
- 内置 `ffmpeg-full` 优先解析与调用

## 快速开始
1. 安装依赖：
   - `uv sync`
   - `yt-dlp`
   - `ffmpeg-full`
2. 配置：
   - `cp videocut.example.toml videocut.toml`
3. 运行：
```bash
python -m videocut.cli run "https://www.youtube.com/watch?v=VIDEO_ID"
```

## 使用手册
完整文档见：
- [USAGE.zh-CN.md](/Users/semgilo/Documents/git/VideoCut/USAGE.zh-CN.md)

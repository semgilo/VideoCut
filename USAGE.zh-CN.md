# VideoCut 统一流程使用手册

## 1. 统一流程（固定 10 步）
1. 下载视频/字幕（YouTube）
2. 解析英文字幕
3. 本地 LLM 翻译（`batch=10`），按 `L/V` 预先给出中文字符区间（`V=1/4.5`，容差 `±20%`）
4. CosyVoice 合成（唯一 TTS）
5. 测量每段音频时长
6. 调速规划：每句音频与原字幕起止点对齐（通过压缩/拉伸，不裁剪）
7. 生成双语 SRT
8. 合成配音轨（`ffmpeg-full`）
9. 渲染最终视频（`ffmpeg-full`）
10. 元数据翻译 + 导出发布资产

## 2. 环境要求
- Python 3.11+
- `yt-dlp`
- `ffmpeg-full`（Homebrew: `brew install ffmpeg-full`）
- 本地 OpenAI 兼容翻译接口（如 `http://127.0.0.1:8888/v1`）
- CosyVoice 本地仓库与模型

## 3. 安装
```bash
uv sync
```

## 4. 配置
复制示例：
```bash
cp videocut.example.toml videocut.toml
```

关键配置（`videocut.toml`）：
- `[translation]`
  - `batch_size = 10`
  - `target_cps = 4.5`
  - `char_tolerance = 0.2`
- `[cosyvoice]`
  - `repo_dir`、`model_dir`、`python`
- `[audio]`
  - `original_volume`、`dub_volume`

字符预算公式：
- 句子时长：`L`（秒）
- 平均每字时长：`V = 1/4.5`（秒/字）
- 目标字数：`L / V = L * 4.5`
- 合法区间：`[target * (1 - 0.2), target * (1 + 0.2)]`

## 5. 运行
```bash
python -m videocut.cli run "https://www.youtube.com/watch?v=VIDEO_ID"
```

可选覆盖：
```bash
python -m videocut.cli run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --workdir runs/my_run \
  --translation-concurrency 4 \
  --cosyvoice-concurrency 2 \
  --output-name final_cn.mp4
```

## 6. 输出目录
`runs/<timestamp>/`（或你指定的 `--workdir`）下会生成：
- `source/` 原视频与字幕
- `tts/` 分段 CosyVoice 音频
- `subtitles/zh.srt` 双语字幕
- `audio/dubbed_track.m4a` 配音轨
- `final_cn.mp4` 最终视频
- `manifest.json` 全流程清单
- `platforms/` 平台发布资产（标题、描述、标签、封面等）

## 7. 性能建议
- 翻译（本地 LLM）：
  - `TranslateGemma`（completion 路径）优先调 `translation.concurrency`；`batch_size` 对吞吐影响较小
  - 兼容 JSON 批量输出的 chat 模型可同时提高 `translation.batch_size` 与 `translation.concurrency`
  - 16G 机器建议先从并发 `4` 起步，通常 `4~6` 比 `8+` 更稳
- CosyVoice：
  - 16G 内存建议 `cosyvoice.concurrency=1~2`，若出现 `MPS fallback` 或长时间卡住，固定为 `1`
  - 用 `cosyvoice.group_size=3~5` 降低单句调度开销（越大吞吐越高，但分句精细度会下降）
- 渲染：
  - 速度优先可将 `video.preset` 从 `medium` 调到 `veryfast`
  - 若允许软字幕，设 `subtitles.burn=false` 可显著减少最终编码时间

推荐的 16G「速度优先」配置示例：
```toml
[translation]
batch_size = 10
concurrency = 4

[cosyvoice]
group_size = 4
concurrency = 1

[subtitles]
burn = true

[video]
preset = "veryfast"
crf = 21
```

## 8. 注意事项
- 该统一流程要求能拿到英文字幕；若视频无英文字幕会报错。
- 该统一流程要求配置本地 LLM 翻译接口；未配置会报错。
- 第 6 步按“起止点强对齐”执行，主要依赖变速，不做裁剪。

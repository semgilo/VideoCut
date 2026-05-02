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
  - `voice_clone`（是否做声音克隆，默认 `true`）
  - `speaker`（`voice_clone=false` 时可指定内置音色 ID）
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

关闭声音克隆（改用模型内置音色）：
```bash
python -m videocut.cli run "https://www.youtube.com/watch?v=VIDEO_ID" \
  --no-voice-clone \
  --cosyvoice-speaker "<speaker_id>"
```

### 5.1 封面合成（原图 + 标题背景框）
最小示例：
```bash
python -m videocut.cli cover \
  --input ~/Downloads/cover_source.jpg \
  --output ~/Downloads/cover_final.jpg \
  --title "三步学会 AI 视频剪辑"
```

生成指定尺寸（例如 1080x1920 竖版）：
```bash
python -m videocut.cli cover \
  --input ~/Downloads/cover_source.jpg \
  --output ~/Downloads/cover_1080x1920.jpg \
  --title "三步学会 AI 视频剪辑" \
  --width 1080 \
  --height 1920
```

常用参数：
- `--position top|center|bottom`：标题框位置（默认 `top`）
- `--offset-y 40`：在默认位置基础上微调 Y 偏移
- `--box-color "#111111"` + `--box-alpha 190`：标题背景框颜色/透明度
- `--text-color "#FFFFFF"` + `--stroke-color "#000000"`：文字/描边颜色
- `--font-path /path/to/font.ttc`：指定字体文件（未指定时自动尝试系统中文字体）
- `--font-size 88`：手动设定字号（未指定时按画布高度自适应）

### 5.2 视频修复：去除 Logo / 老片划痕（`inpaint`）

#### 快速示例

去除固定位置水印（如左上角「AI生成」标记）：
```bash
videocut inpaint input.mp4 output.mp4 --region 0,5,120,55
```

自动修复老胶片竖向划痕：
```bash
videocut inpaint old_film.mp4 restored.mp4 --scratch
```

用遮罩图片指定复杂区域（白色像素 = 需修复）：
```bash
videocut inpaint input.mp4 output.mp4 --mask logo_mask.png
```

#### 坐标确认技巧

先用 `drawbox` 在单帧上画出绿框，确认位置后再批量处理：
```bash
# 从视频取一帧
ffmpeg -i input.mp4 -ss 3 -vframes 1 frame.jpg

# 叠加绿框预览（x,y,w,h 单位为像素）
ffmpeg -i frame.jpg -vf "drawbox=x=0:y=5:w=120:h=55:color=green:t=2" preview.jpg
```

#### 算法选择

| `--method` | 原理 | 适合场景 | 速度 |
|-----------|------|---------|------|
| `telea`（默认） | Fast Marching Method，从遮罩边界向内扩散 | 细划痕、小字幕/水印 | 最快 |
| `ns` | Navier-Stokes 各向异性扩散，沿等亮度线传播 | 中型区域、角落 Logo | 中 |
| `lama` | 深度卷积网络（需 `pip install simple-lama-inpainting`） | 大面积遮挡、复杂背景 | 慢（GPU 可加速） |

经验规则：
- 纯色/渐变背景（白墙、天空）→ `ns --radius 10` 效果最干净
- 有纹理的背景（草地、人群）→ `lama` 质量最好
- 划痕修复 → `telea --scratch --scratch-sensitivity 1.5`

#### 完整参数

```
videocut inpaint INPUT OUTPUT [选项]

必选（三选一或混用）：
  --region X,Y,W,H        静态矩形区域，可重复使用多次
  --mask MASK_IMAGE        灰度遮罩图片（白色=修复，黑色=保留）
  --scratch               自动检测并修复竖向胶片划痕

算法：
  --method telea|ns|lama  算法选择（默认 telea）
  --radius N              TELEA/NS 邻域半径，单位像素（默认 5）

精细调节：
  --dilate PIXELS         遮罩向外扩展像素数，消除边缘残影（默认 2）
  --scratch-sensitivity   划痕检测灵敏度（默认 1.0，越大越敏感）
```

#### 多 Logo 场景

同一视频有多个水印时，重复 `--region` 即可：
```bash
videocut inpaint input.mp4 output.mp4 \
  --region 0,5,120,55 \
  --region 600,1220,120,55
```

## 6. 输出目录
`runs/<timestamp>/`（或你指定的 `--workdir`）下会生成：

### 核心产物 ⭐
- `final_compressed.mp4` — 最终发布视频（manifest.json 中 `final_video` 指向此文件）
- `publish/` — 统一发布素材（title.txt, description.txt, tags.txt, cover.jpg, metadata.json）
- `manifest.json` — 唯一数据源，所有下游代码从此读取路径

### 中间产物（cleanup 后可删除）
- `source/` 原视频与字幕
- `tts/` 分段 CosyVoice 音频
- `subtitles/zh.srt` 双语字幕
- `audio/dubbed_track.m4a` 配音轨
- `platforms/` 旧版平台目录（已由 publish/ 取代）

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
- 当使用 `--no-voice-clone` 或 `cosyvoice.voice_clone=false` 时，需要 CosyVoice 模型提供内置 speaker（`spk2info.pt`）。若模型没有内置 speaker，会报错并提示切回声音克隆模式。

## 9. 平台材料规则（标题/标签/封面）
- 标题：
  - 抖音：基于原始标题生成，追加 `｜中文字幕`，限制 36 字。
  - B 站：基于原始标题生成，统一 `【中文字幕】` 前缀，限制 60 字。
  - 小红书：基于原始标题生成，追加 `｜中文字幕`，限制 22 字。
  - 微信视频号（tecent）：基于原始标题生成，追加 `｜中文字幕`，限制 30 字。
- 标签：
  - 抖音最多 5 个、B 站最多 6 个、小红书最多 10 个、微信视频号最多 8 个。
  - 优先使用元数据标签与标题关键词，超限自动截断。
- 封面：
  - 各平台都会输出 `cover.jpg`（平台尺寸）和 `cover_source.*`（源封面备份）。
  - `cover_text.txt` 基于标题自动生成，小红书固定两行（每行尽量不超过 10 字）。

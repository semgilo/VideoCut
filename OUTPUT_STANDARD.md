# VideoCut 输出标准规范

## 目录结构

```
runs/<video_id>-<timestamp>/
├── source/                                          # 原始下载文件（cleanup 后可删除）
│   ├── <video>.mp4                                 # 原始视频
│   └── info.json                                   # YouTube 元数据
├── subtitles/
│   └── zh.srt                                      # 双语字幕（中文+英文）
├── audio/                                           # 音频处理产物（cleanup 后可删除）
│   └── dubbed_track.m4a                            # 配音轨 / original_audio.m4a（subtitle_only）
├── final_compressed.mp4                             # 最终发布视频 ⭐
├── publish/                                         # 统一发布素材 ⭐
│   ├── title.txt                                   # 标题
│   ├── description.txt                             # 描述
│   ├── tags.txt                                    # 逗号分隔标签
│   ├── cover.jpg                                   # 封面图
│   ├── metadata.json                               # 完整元数据
│   └── content_preview.html                        # 预览页
├── manifest.json                                   # 唯一数据源 ⭐
└── delivery_summary.md                             # 交付摘要
```

## 核心原则

1. **manifest.json 是唯一数据源** — 所有下游代码从此读取路径，不得硬编码或猜测文件名
2. **最终视频始终是 `final_compressed.mp4`** — 经过压缩适配发布平台，manifest.final_video 指向此文件
3. **publish/ 是唯一的发布素材目录** — 所有平台共享同一套标题/描述/标签/封面

## manifest.json 格式

```json
{
  "source_video": "runs/xxx/source/video.mp4",
  "generated_srt": "runs/xxx/subtitles/zh.srt",
  "final_video": "runs/xxx/final_compressed.mp4",
  "publish_assets": {
    "cover_image": "runs/xxx/publish/cover.jpg",
    "title_text": "runs/xxx/publish/title.txt",
    "description_text": "runs/xxx/publish/description.txt",
    "tags_text": "runs/xxx/publish/tags.txt",
    "metadata_json": "runs/xxx/publish/metadata.json",
    "preview_html": "runs/xxx/publish/content_preview.html"
  },
  "segments": [...],
  "source_metadata": {...},
  "localized_metadata": {...}
}
```

### 必填字段（下游依赖）

| JSON路径 | 文件 | 说明 |
|----------|------|------|
| `final_video` | `final_compressed.mp4` | 最终发布视频 |
| `publish_assets.title_text` | `publish/title.txt` | 标题 |
| `publish_assets.description_text` | `publish/description.txt` | 描述 |
| `publish_assets.tags_text` | `publish/tags.txt` | 标签 |
| `publish_assets.cover_image` | `publish/cover.jpg` | 封面图 |

## 发布素材（publish/）

统一目录，所有平台共用：

| 文件 | 内容 | 说明 |
|------|------|------|
| `title.txt` | 中文标题 | UTF-8, 尾部带换行 |
| `description.txt` | 中文描述 | UTF-8 |
| `tags.txt` | 逗号分隔的中文标签 | UTF-8, 无换行 |
| `cover.jpg` | 封面图 | 原始封面复制 |
| `metadata.json` | 完整元数据 | source + localized + merged |
| `content_preview.html` | 预览页 | 可选 |

## 视频文件

- **最终文件**: `final_compressed.mp4`
- **来源**: 由渲染产物（`final_subtitled.mp4` / `final_cn.mp4`）压缩得到
- **压缩条件**: config `compress_to_max_mb > 0` 时启用
- **manifest 指向**: `manifest.final_video`

## 遗留目录（不再作为数据源）

以下目录可能存在，但下游不应依赖：

| 目录 | 说明 |
|------|------|
| `platforms/` | 旧版平台素材（douyin/bilibili/xiaohongshu/tecent），cleanup 后可删除 |
| `source/` | 原始下载，cleanup 后可删除 |
| `audio/` | 音频处理产物，cleanup 后可删除 |

## 质量检查

- [ ] `final_compressed.mp4` 存在且可播放
- [ ] `manifest.json` 存在，`final_video` 路径正确
- [ ] `publish/` 包含 title.txt, description.txt, tags.txt, cover.jpg
- [ ] 封面图片清晰
- [ ] 标签数量符合平台限制

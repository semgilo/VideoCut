# VideoCut 输出标准规范

## 最终目录结构

cleanup 完成后，run 目录只保留三样东西：

```
runs/<video_id>-<timestamp>/
├── final_compressed.mp4     # 最终发布视频 ⭐
├── publish/                 # 发布素材 ⭐
│   ├── title.txt
│   ├── description.txt
│   ├── tags.txt
│   ├── cover.jpg
│   ├── metadata.json
│   └── content_preview.html
└── manifest.json            # 唯一数据源 ⭐
```

其余所有目录和文件（`source/`、`audio/`、`subtitles/`、`platforms/`、`final_subtitled.mp4`、`delivery_summary.md`）在 cleanup 阶段全部删除。

## 核心原则

1. **manifest.json 是唯一数据源** — 所有下游代码从此读取路径，不得硬编码或猜测文件名
2. **最终视频始终是 `final_compressed.mp4`** — manifest.final_video 指向此文件
3. **publish/ 是唯一的发布素材目录** — 所有平台共享同一套标题/描述/标签/封面

## manifest.json 格式

cleanup 后只保留四个字段，其余（stale 路径、segments）全部移除：

```json
{
  "final_video": "runs/xxx/final_compressed.mp4",
  "publish_assets": {
    "cover_image": "runs/xxx/publish/cover.jpg",
    "title_text": "runs/xxx/publish/title.txt",
    "description_text": "runs/xxx/publish/description.txt",
    "tags_text": "runs/xxx/publish/tags.txt",
    "metadata_json": "runs/xxx/publish/metadata.json",
    "preview_html": "runs/xxx/publish/content_preview.html"
  },
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
- **来源**: 由渲染产物压缩得到（config `compress_to_max_mb > 0` 时启用）
- **manifest 指向**: `manifest.final_video`

## 质量检查

- [ ] `final_compressed.mp4` 存在且可播放
- [ ] `manifest.json` 存在，`final_video` 路径正确
- [ ] `publish/` 包含 title.txt, description.txt, tags.txt, cover.jpg
- [ ] run 目录下无其他目录或多余文件

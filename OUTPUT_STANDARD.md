# VideoCut 输出标准规范

## 目录结构

```
/Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/
├── source/                                          # 原始下载文件
│   ├── <video>.mp4                                 # 原始视频
│   ├── thumbnail.jpg                               # 封面缩略图
│   └── info.json                                   # YouTube 元数据
├── subtitles/
│   └── zh.srt                                      # 双语字幕（中文+英文）
│       # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/subtitles/zh.srt
├── audio/
│   └── original_audio.m4a                          # 原声音轨（subtitle_only模式）
│       # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/audio/original_audio.m4a
├── final_subtitled.mp4                             # 最终双语字幕视频 ⭐
│   # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/final_subtitled.mp4
├── platforms/                                       # 平台发布材料 ⭐
│   ├── douyin/                                     # 抖音
│   │   # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/platforms/douyin/
│   ├── bilibili/                                   # B站
│   │   # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/platforms/bilibili/
│   ├── xiaohongshu/                                # 小红书
│   │   # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/platforms/xiaohongshu/
│   └── tecent/                                     # 微信视频号
│       # 示例: /Users/semgilo/Documents/git/VideoCut/runs/xxx/platforms/tecent/
├── publish/                                         # 通用发布材料
├── manifest.json                                   # 完整任务清单
└── delivery_summary.md                             # 交付摘要
```

## 核心输出文件

### 1. 视频文件
- **文件**: `final_subtitled.mp4`
- **要求**: 1080p或原始分辨率，H.264编码，双语字幕烧录
- **音频**: 保留原声（subtitle_only模式）或配音（dub模式）

### 2. 字幕文件
- **文件**: `subtitles/zh.srt`
- **格式**: 双语字幕，每段两行：
  ```
  中文翻译
  English original
  ```
- **编码**: UTF-8

## 平台材料标准（platforms/）

每个平台目录必须包含以下文件：

### 基础文件（完整路径示例）

| 文件 | 内容 | 完整路径示例 |
|------|------|-------------|
| `title.txt` | 平台适配标题 | `/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325/platforms/xiaohongshu/title.txt` |
| `description.txt` | 平台适配描述/笔记 | `/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325/platforms/xiaohongshu/description.txt` |
| `hashtags.txt` | 标签列表 | `/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325/platforms/xiaohongshu/hashtags.txt` |
| `cover_text.txt` | 封面文字（2行） | `/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325/platforms/xiaohongshu/cover_text.txt` |
| `cover.jpg` | 平台适配封面 | `/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325/platforms/xiaohongshu/cover.jpg` |
| `cover_source.jpg` | 原始封面 | `/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325/platforms/xiaohongshu/cover_source.jpg` |

### 元数据文件
| 文件 | 用途 |
|------|------|
| `asset_refs.json` | 资源引用清单，供下游步骤使用 |
| `requirements.md` | 平台上传要求说明 |
| `upload_checklist.md` | 上传前检查清单 |
| `compression_ffmpeg.sh` | 如需压缩，自动生成的ffmpeg命令 |

### 平台特定要求

#### 小红书 (xiaohongshu/)
- **标题**: 基于原始标题生成，统一追加 `｜中文字幕`，50字以内（不编造新主题）
- **描述**:
  - 说明“仅做字幕翻译，保留原声”
  - 内容摘要
  - 原视频链接
  - 标签建议（最多10个）
- **封面**: 3:4比例，1242x1660
- **封面文字**: 两行，每行不超过10字

#### B站 (bilibili/)
- **标题**: 【中文字幕】前缀，60字以内
- **描述**: 
  - 说明字幕来源
  - 视频标题
  - 内容摘要
  - 原视频链接
  - 标签建议（最多6个）
- **封面**: 16:9比例，1280x720

#### 抖音 (douyin/)
- **标题**: 基于原始标题生成，统一追加 `｜中文字幕`，36字以内
- **描述**: 
  - 简短说明
  - 内容摘要
  - 原视频链接
  - 标签建议（最多5个）
- **封面**: 9:16比例，1080x1920

#### 微信视频号 (tecent/)
- **标题**: 基于原始标题生成，统一追加 `｜中文字幕`，30字以内
- **描述**:
  - 说明“仅做字幕翻译，保留原声”
  - 内容摘要
  - 原视频链接
  - 标签建议（最多8个）
- **封面**: 1080x1260

## 通用发布材料（publish/）

| 文件 | 内容 |
|------|------|
| `title.txt` | 中文标题 |
| `description.txt` | 中文描述 |
| `tags.txt` | 中文标签 |
| `cover.jpg` | 封面图 |
| `content_preview.html` | 预览页面（可选）

## 元数据文件

### manifest.json
```json
{
  "source_video": "...",
  "subtitle_source": "...",
  "thumbnail_source": "...",
  "generated_srt": "...",
  "final_video": "...",
  "segments": [...],
  "source_metadata": {...},
  "localized_metadata": {...},
  "publish_assets": {...}
}
```

### delivery_summary.md
- 最终视频路径
- 字幕文件路径
- 平台材料根目录
- 视频信息（分辨率、时长、大小）

## 质量检查清单

发布前确认：
- [ ] `final_subtitled.mp4` 存在且可播放
- [ ] 字幕显示正常（中文在上，英文在下）
- [ ] `platforms/` 目录存在且包含4个平台
- [ ] 每个平台目录包含完整8个文件
- [ ] 各平台 `title.txt` 与视频主题一致（无明显“跑题标题”）
- [ ] 各平台 `hashtags.txt` 数量符合限制（抖音5/B站6/小红书10/视频号8）
- [ ] 封面图片清晰，文字可读

## 下游消费接口

下游系统（如 Flow Deck、发布 Agent）消费输出时，文件完整路径：

### 核心文件路径
```
视频文件:   /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/final_subtitled.mp4
字幕文件:   /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/subtitles/zh.srt
原始视频:   /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/source/<video>.mp4
元数据:     /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/manifest.json
```

### 平台材料路径
```
抖音:       /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/platforms/douyin/
├── title.txt
├── description.txt
├── hashtags.txt
├── cover_text.txt
├── cover.jpg
├── cover_source.jpg
├── asset_refs.json
├── requirements.md
└── upload_checklist.md

B站:        /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/platforms/bilibili/
└── (同上8个文件)

小红书:     /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/platforms/xiaohongshu/
└── (同上8个文件)

视频号:     /Users/semgilo/Documents/git/VideoCut/runs/<video_id>-<timestamp>/platforms/tecent/
└── (同上8个文件)
```

### 读取示例（以小红书为例）
```javascript
const BASE_DIR = '/Users/semgilo/Documents/git/VideoCut/runs/UlC7pTdH_y4-20250325';
const XHS_DIR = `${BASE_DIR}/platforms/xiaohongshu`;

const title = fs.readFileSync(`${XHS_DIR}/title.txt`, 'utf-8');
const description = fs.readFileSync(`${XHS_DIR}/description.txt`, 'utf-8');
const hashtags = fs.readFileSync(`${XHS_DIR}/hashtags.txt`, 'utf-8');
const coverPath = `${XHS_DIR}/cover.jpg`;
```

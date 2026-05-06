# 技术栈说明（模板生成 · update 时会更新）

⚠️ **不要手动改这个文件**——`/update-project` 会覆盖。

---

## 这个项目的"做事方式"

### 包管理

跟随 `uv.lock`。**不要混用** pip/poetry/pipenv。只用 `uv`。

### 文件组织

```
videocut/       # 主包，每个模块对应一个流水线阶段
  cli.py        # 命令入口，argparse subcommands
  config.py     # TOML 配置加载
  pipeline.py   # 主流水线编排
  asr.py        # 语音识别
  translate.py  # 翻译
  tts.py        # 文字转语音
  subtitles.py  # 字幕生成
  timing.py     # 时间轴处理
  media.py      # 媒体操作（ffmpeg）
  publish.py    # 发布/压缩
  cover.py      # 封面生成
  inpaint.py    # 视频修复
  shell.py      # shell 工具、step_guard
scripts/        # 一次性脚本，不在主包里
runs/           # 运行产物（gitignore）
videocut.toml   # 本地配置（gitignore）
```

### 类型/Lint

- ruff：format + lint（`.ruff_cache/` 存在）
- mypy：类型检查（`.mypy_cache/` 存在）
- 改 Python 文件后建议 `uv run ruff check` 验证

### 测试

无测试框架，不强制。有临时验证需求用独立脚本放 `scripts/`。

---

## Claude 在这个项目里应该特别注意

- `videocut.toml` 包含本地路径和 API key，已在 `.gitignore`——**不要读写这个文件的 secret 字段**
- 流水线各阶段通过 `runs/<run_id>/` 目录传递中间产物，不要硬编码路径
- `shell.py:step_guard` 是幂等保护机制——跳过已完成步骤，改流水线时注意兼容

---

**如果这些信息不对**：在 `<repo>/.claude/CLAUDE.md` 自己的段落里 override。

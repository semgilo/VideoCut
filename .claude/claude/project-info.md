# 项目信息（模板生成 · update 时会更新）

⚠️ **不要手动改这个文件**——`/update-project` 会覆盖。
项目化内容写在 `<repo>/.claude/CLAUDE.md` 自己的段落里。

---

## 项目基本信息

- **名称**: VideoCut
- **主要语言**: Python
- **包管理**: uv
- **是 git repo**: yes

## 常用命令

- 安装依赖: `uv sync`
- 运行 CLI: `uv run videocut --help`

## 测试框架

- 单元测试: 无（不强制 TDD）
- E2E: 无

## 数据/存储

- 无数据库
- 运行产物存放在 `runs/<run_id>/`（gitignore）

## 主要框架

CLI 工具。入口：`videocut/cli.py`，通过 `pyproject.toml` 的 `[project.scripts]` 注册为 `videocut` 命令。

---

**模板版本**: 0.1.0

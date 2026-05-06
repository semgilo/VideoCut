---
paths:
  - "**/*.py"
---

# Python 规则

## 约定

- 只用 `uv`，不用 pip/poetry/pipenv
- 包管理命令：`uv add`、`uv run`、`uv sync`
- Python 最低版本：3.11（`requires-python = ">=3.11"`）

## Lint / Format

- `uv run ruff format <file>` 格式化
- `uv run ruff check <file>` lint
- `uv run mypy <file>` 类型检查
- 改完文件建议跑 ruff check 确认没引入新问题

## 风格

- 类型注解：函数签名必须标注，局部变量可省
- import 顺序：stdlib → 第三方 → 本地（ruff 会管）
- 不要用 `from __future__ import annotations` 以外的 future import

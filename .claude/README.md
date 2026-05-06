# VideoCut / .claude/

这个目录是 Claude Code 的项目级配置，由 `/init-project` 创建。

## 目录结构

```
.claude/
├── CLAUDE.md              ← 你的项目宪章（update 不动）
├── claude/                ← 模板生成区（update 全覆盖，不要手改）
│   ├── project-info.md
│   └── tech-stack.md
├── settings.json          ← 项目级 settings（你拥有，update 不动）
├── rules/
│   ├── python.md          ← paths: **/*.py（update 不动，你的）
│   ├── cli.md             ← CLI 约定（update 不动，你的）
│   └── (你自己加的 .md)   ← update 不动
├── specs/                 ← spec-driven 的 spec 文件（gitignore）
├── README.md              ← 这个文件（update 全覆盖）
└── .template-version      ← 模板版本号
```

## 怎么用

### 加项目特定规则

直接在 `<repo>/.claude/rules/` 下新建 `.md` 文件。例如：

```markdown
---
paths:
  - "videocut/translate.py"
---
# 翻译模块规则
- 保护词表在 translation_protected_terms.txt
```

### Override 工作流

在 `<repo>/.claude/CLAUDE.md` 的"工作流 override"段写。

### 项目级 secret / 个人偏好

`.claude/CLAUDE.local.md` 是你的私有偏好，被 `.gitignore` 排除，不会 commit。

### 用 spec-driven

这个项目默认走 spec-driven。spec 文件放在 `.claude/specs/<task>.md`，不会被 commit。

## 维护

```bash
/audit-memory       # 看当前配置体积
/update-project     # 模板有更新时同步进来
```

## 模板更新策略

| 文件 | update 时 |
|---|---|
| `CLAUDE.md` | **不动**（你的） |
| `claude/*.md` | **全覆盖** |
| `settings.json` | **不动**（你的） |
| `rules/python.md` | **不动**（你的） |
| `rules/cli.md` | **不动**（你的） |
| `specs/` | **不动** |
| `README.md` | **全覆盖** |

# VideoCut 项目宪章

`<repo>/.claude/CLAUDE.md` —— 这个项目特定的规则，叠加在个人级 `~/.claude/CLAUDE.md` 之后。

**这个文件完全是你的**。`/update-project` 不会动它。
模板生成的内容在下面 `@./claude/` 子目录里通过 import 引入——那部分会被 update 覆盖。

---

<!-- 模板生成的项目信息（update 时会更新，不要直接改这里） -->
@./claude/project-info.md

<!-- 模板生成的技术栈说明（update 时会更新，不要直接改这里） -->
@./claude/tech-stack.md

---

<!-- 下面是你自己写的项目特定规则、override，随便改 -->

## 工作流 override

### 不强制 TDD

这个项目没有测试框架，不强制 TDD。`new-feature` 流程走默认"实验/原型"分支。

### spec-driven 默认

这个项目所有 `new-feature` / `refactor` 任务默认走 spec-driven，不需要先问。
除非我说"这次不走 spec"。

## 项目特有约定

<!-- 这里写项目特有的事，比如：
- 命名约定
- 文件组织
- 不要碰的目录
- 部署流程的特殊步骤
-->

## 已知陷阱

<!-- 这里记录"曾经踩过的坑"，比如：
- X 文件改了要重启 dev server
- Y 模块的测试很慢，本地不要频繁跑
-->

# CLI 开发约定

## 命令结构

- 所有命令通过 argparse subcommands 注册在 `videocut/cli.py`
- 新命令：加 `_add_<cmd>_parser()` 函数，在 `main()` 里注册

## 行为约定

- 默认 dry-run，`--force` 才真正执行破坏性操作
- 成功退出 code 0，用户错误 1，内部错误 2
- 进度输出到 stderr，最终产物路径输出到 stdout（方便 pipe）
- 长任务用 `shell.step_guard` 包裹以支持幂等重跑

## 帮助文本

- `help=` 一句话说清楚做什么，不要废话
- 参数默认值写进 `help=`（`default=7, help="... (default: 7)"`）

#!/usr/bin/env python3
"""
RunningHub API CLI — 调用 RunningHub 云端 ComfyUI 工作流

用法示例:
  # 提交工作流并等待结果
  runninghub run 2029632742534680578 --watch

  # 带节点参数
  runninghub run 2029632742534680578 --node nodeId=6,fieldName=text,fieldValue="hello world"

  # 传入 JSON 节点参数文件
  runninghub run 2029632742534680578 --nodes-file params.json --watch

  # 查询任务状态
  runninghub query 2013508786110730241

  # 上传本地文件
  runninghub upload /path/to/image.png

配置 API Key（任选其一）:
  export RUNNINGHUB_API_KEY=your_key_here
  runninghub --api-key your_key_here run ...
  在 ~/.runninghub 文件中写入: RUNNINGHUB_API_KEY=your_key_here
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

BASE_URL = "https://www.runninghub.cn/openapi/v2"
CONFIG_FILE = Path.home() / ".runninghub"


# ── 配置加载 ────────────────────────────────────────────────────────────────

def load_api_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    # 环境变量
    if key := os.environ.get("RUNNINGHUB_API_KEY"):
        return key
    # ~/.runninghub 文件
    if CONFIG_FILE.exists():
        cfg = dotenv_values(CONFIG_FILE)
        if key := cfg.get("RUNNINGHUB_API_KEY"):
            return key
    print("错误: 未找到 API Key。\n"
          "请设置环境变量 RUNNINGHUB_API_KEY，或使用 --api-key 参数，\n"
          f"或在 {CONFIG_FILE} 中写入 RUNNINGHUB_API_KEY=your_key", file=sys.stderr)
    sys.exit(1)


def make_headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


# ── 节点参数解析 ────────────────────────────────────────────────────────────

def parse_node_param(s: str) -> dict:
    """
    解析单个节点参数，支持两种格式:
      1. nodeId=6,fieldName=text,fieldValue=hello
      2. {"nodeId":"6","fieldName":"text","fieldValue":"hello"}
    """
    s = s.strip()
    if s.startswith("{"):
        return json.loads(s)
    parts = {}
    for kv in s.split(","):
        if "=" not in kv:
            raise ValueError(f"无效的节点参数: {kv!r}，期望 key=value 格式")
        k, _, v = kv.partition("=")
        parts[k.strip()] = v.strip()
    return parts


# ── API 调用 ────────────────────────────────────────────────────────────────

def run_workflow(api_key: str, workflow_id: str, args) -> dict:
    url = f"{BASE_URL}/run/workflow/{workflow_id}"
    headers = make_headers(api_key)

    node_info_list = []

    # --nodes-file 优先
    if args.nodes_file:
        with open(args.nodes_file) as f:
            node_info_list = json.load(f)
    elif args.node:
        for n in args.node:
            node_info_list.append(parse_node_param(n))

    payload: dict = {
        "nodeInfoList": node_info_list,
    }
    if args.add_metadata is not None:
        payload["addMetadata"] = args.add_metadata
    if args.instance_type:
        payload["instanceType"] = args.instance_type
    if args.personal_queue is not None:
        payload["usePersonalQueue"] = str(args.personal_queue).lower()
    if args.webhook_url:
        payload["webhookUrl"] = args.webhook_url
    if args.retain_seconds is not None:
        payload["retainSeconds"] = args.retain_seconds

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def query_task(api_key: str, task_id: str) -> dict:
    url = f"{BASE_URL}/query"
    headers = make_headers(api_key)
    resp = requests.post(url, headers=headers, json={"taskId": task_id}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def upload_file(api_key: str, file_path: str) -> dict:
    url = f"{BASE_URL}/media/upload/binary"
    headers = {"Authorization": f"Bearer {api_key}"}
    with open(file_path, "rb") as f:
        resp = requests.post(url, headers=headers, files={"file": f}, timeout=120)
    resp.raise_for_status()
    return resp.json()


# ── 轮询等待 ────────────────────────────────────────────────────────────────

def watch_task(api_key: str, task_id: str, interval: int = 5, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    print(f"等待任务 {task_id} 完成 (超时 {timeout}s)...")
    while time.time() < deadline:
        result = query_task(api_key, task_id)
        status = result.get("status", "")
        print(f"  状态: {status}", end="\r", flush=True)
        if status in ("SUCCESS", "FAILED"):
            print()
            return result
        time.sleep(interval)
    print()
    print(f"超时: 任务 {task_id} 未在 {timeout}s 内完成", file=sys.stderr)
    return query_task(api_key, task_id)


# ── 子命令处理 ──────────────────────────────────────────────────────────────

def cmd_run(args):
    api_key = load_api_key(args.api_key)
    print(f"提交工作流 {args.workflow_id} ...")
    result = run_workflow(api_key, args.workflow_id, args)

    task_id = result.get("taskId")
    status = result.get("status")
    print(f"任务已提交: taskId={task_id}  status={status}")

    if result.get("errorMessage"):
        print(f"错误: {result['errorMessage']}", file=sys.stderr)
        sys.exit(1)

    if args.watch and task_id:
        result = watch_task(api_key, task_id, interval=args.poll_interval, timeout=args.timeout)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 打印下载链接
    for r in result.get("results") or []:
        if r.get("url"):
            print(f"结果: [{r.get('outputType','file')}] {r['url']}")
        if r.get("text"):
            print(f"文本输出: {r['text']}")


def cmd_query(args):
    api_key = load_api_key(args.api_key)
    if args.watch:
        result = watch_task(api_key, args.task_id, interval=args.poll_interval, timeout=args.timeout)
    else:
        result = query_task(api_key, args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    for r in result.get("results") or []:
        if r.get("url"):
            print(f"结果: [{r.get('outputType','file')}] {r['url']}")
        if r.get("text"):
            print(f"文本输出: {r['text']}")


def cmd_upload(args):
    api_key = load_api_key(args.api_key)
    print(f"上传文件: {args.file} ...")
    result = upload_file(api_key, args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("data", {}).get("download_url"):
        print(f"文件 URL: {result['data']['download_url']}")


def cmd_config(args):
    """写入 API Key 到 ~/.runninghub"""
    CONFIG_FILE.write_text(f"RUNNINGHUB_API_KEY={args.api_key}\n")
    print(f"API Key 已保存到 {CONFIG_FILE}")


# ── CLI 构建 ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runninghub",
        description="RunningHub ComfyUI 工作流 API CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--api-key", metavar="KEY",
                        help="API Key（也可通过 RUNNINGHUB_API_KEY 环境变量或 ~/.runninghub 设置）")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ──
    p_run = sub.add_parser("run", help="提交工作流任务")
    p_run.add_argument("workflow_id", help="工作流 ID")
    p_run.add_argument("--node", action="append", metavar="nodeId=X,fieldName=Y,fieldValue=Z",
                       help="节点参数（可多次使用）")
    p_run.add_argument("--nodes-file", metavar="FILE",
                       help="节点参数 JSON 文件路径（nodeInfoList 数组）")
    p_run.add_argument("--instance-type", choices=["default", "plus"], default="default",
                       help="实例类型: default(24G) / plus(48G)，默认 default")
    p_run.add_argument("--personal-queue", action="store_true", default=None,
                       help="使用个人独占队列")
    p_run.add_argument("--add-metadata", action="store_true", default=None,
                       help="在输出图片中嵌入工作流元数据")
    p_run.add_argument("--webhook-url", metavar="URL",
                       help="任务完成后的 Webhook 回调地址")
    p_run.add_argument("--retain-seconds", type=int, metavar="SEC",
                       help="实例保留时长 10~180s（企业共享 Key 有效）")
    p_run.add_argument("--watch", action="store_true",
                       help="提交后轮询直到任务完成")
    p_run.add_argument("--poll-interval", type=int, default=5, metavar="SEC",
                       help="轮询间隔秒数，默认 5")
    p_run.add_argument("--timeout", type=int, default=600, metavar="SEC",
                       help="最大等待秒数，默认 600")
    p_run.set_defaults(func=cmd_run)

    # ── query ──
    p_query = sub.add_parser("query", help="查询任务状态/结果")
    p_query.add_argument("task_id", help="任务 ID")
    p_query.add_argument("--watch", action="store_true",
                         help="轮询直到任务完成")
    p_query.add_argument("--poll-interval", type=int, default=5, metavar="SEC",
                         help="轮询间隔秒数，默认 5")
    p_query.add_argument("--timeout", type=int, default=600, metavar="SEC",
                         help="最大等待秒数，默认 600")
    p_query.set_defaults(func=cmd_query)

    # ── upload ──
    p_upload = sub.add_parser("upload", help="上传本地文件，获取 URL")
    p_upload.add_argument("file", help="本地文件路径")
    p_upload.set_defaults(func=cmd_upload)

    # ── config ──
    p_cfg = sub.add_parser("config", help="保存 API Key 到 ~/.runninghub")
    p_cfg.add_argument("api_key", help="你的 RunningHub API Key")
    p_cfg.set_defaults(func=cmd_config)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        sys.exit(130)
    except requests.HTTPError as e:
        print(f"HTTP 错误: {e.response.status_code} {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"网络错误: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"文件不存在: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"JSON 解析错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

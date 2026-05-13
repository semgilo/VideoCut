from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from videocut.config import PipelineConfig, load_pipeline_config


def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def _check(name: str, ok: bool, detail: str) -> tuple[bool, str]:
    icon = _green("✓") if ok else _red("✗")
    status = _green("OK") if ok else _red("FAIL")
    return ok, f"  {icon} [{status}] {_bold(name)}: {detail}"


def _run_ffmpeg_version() -> str | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0]
            return first_line.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _run_ffprobe_version() -> str | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0]
            return first_line.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _check_disk_space(path: Path, label: str) -> tuple[bool, str]:
    try:
        usage = shutil.disk_usage(path.resolve())
        free_gb = usage.free / (1024**3)
        detail = f"{free_gb:.1f} GB free"
        if free_gb < 1:
            return _check(f"Disk space ({label})", False, f"{detail} (< 1 GB)")
        if free_gb < 5:
            return _check(f"Disk space ({label})", True, _yellow(detail))
        return _check(f"Disk space ({label})", True, detail)
    except OSError as e:
        return _check(f"Disk space ({label})", False, f"Cannot check: {e}")


def _import_check(module_name: str, package_name: str | None = None) -> tuple[bool, str]:
    try:
        importlib.import_module(module_name)
        return _check(f"Package {package_name or module_name}", True, "importable")
    except ImportError as e:
        return _check(f"Package {package_name or module_name}", False, f"ImportError: {e}")
    except Exception as e:
        return _check(f"Package {package_name or module_name}", False, f"Error: {e}")


def _check_env_var(key: str, purpose: str) -> tuple[bool, str]:
    val = os.getenv(key, "")
    if val and val.strip():
        return _check(f"Env {key}", True, f"set ({purpose})")
    return _check(f"Env {key}", False, f"not set ({purpose})")


def _check_llm_connectivity(config: PipelineConfig) -> tuple[bool, str]:
    base_url = config.llm_base_url
    if not base_url:
        return _check("LLM connectivity", True, "skipped (no base URL configured)")
    try:
        import urllib.request
        import urllib.error

        models_url = base_url.rstrip("/") + "/models"
        req = urllib.request.Request(models_url, method="GET")
        api_key = config.llm_api_key.strip()
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _check("LLM connectivity", True, f"reachable at {base_url}")
    except urllib.error.HTTPError as e:
        return _check("LLM connectivity", True, f"reachable (HTTP {e.code})")
    except Exception as e:
        return _check("LLM connectivity", False, f"{type(e).__name__}: {e}")


def run_doctor(config_path: str | None) -> int:
    results: list[tuple[bool, str]] = []
    fail_count = 0

    print(_bold("\n===== System Checks =====\n"))

    # Python version
    py_ok = sys.version_info >= (3, 11)
    results.append(_check("Python version", py_ok, sys.version.strip()))

    # Config
    try:
        config = load_pipeline_config(Path(config_path) if config_path else None)
        config_file = Path(config_path).resolve() if config_path else Path("videocut.toml").resolve()
        if config_file.exists():
            results.append(_check("Config file", True, str(config_file)))
        else:
            results.append(_check("Config file", True, "not found (using defaults + env vars)"))
    except Exception as e:
        config = PipelineConfig()
        results.append(_check("Config file", False, str(e)))

    # ffmpeg
    ffmpeg_ver = _run_ffmpeg_version()
    results.append(_check("ffmpeg", ffmpeg_ver is not None, ffmpeg_ver or "not found in PATH"))

    # ffprobe
    ffprobe_ver = _run_ffprobe_version()
    results.append(_check("ffprobe", ffprobe_ver is not None, ffprobe_ver or "not found in PATH"))

    print(_bold("\n===== Runtime Checks =====\n"))

    # runs_dir
    runs_dir = config.runs_dir.expanduser().resolve()
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        results.append(_check(f"Runs dir ({runs_dir})", True, "exists and writable"))
    except OSError as e:
        results.append(_check(f"Runs dir ({runs_dir})", False, str(e)))

    # Disk space
    results.append(_check_disk_space(runs_dir, "runs_dir"))

    # Compression
    if config.compress_to_max_mb > 0:
        results.append(_check("Compression target", True, f"{config.compress_to_max_mb}MB max, 1080p"))
    else:
        results.append(_check("Compression target", True, "disabled (compress_to_max_mb = 0)"))

    print(_bold("\n===== Environment Checks =====\n"))

    # .env
    env_path = Path(".env")
    if env_path.exists():
        results.append(_check(".env file", True, "present"))
    else:
        results.append(_check(".env file", True, "not found (optional)"))

    # LLM config (from env vars, .toml, or defaults)
    llm_base_url = config.llm_base_url
    llm_api_key = config.llm_api_key.strip()
    if llm_base_url:
        results.append(_check("LLM base URL", True, llm_base_url))
    else:
        results.append(_check("LLM base URL", False, "not configured"))
    if llm_api_key:
        results.append(_check("LLM API key", True, "configured"))
    else:
        results.append(_check("LLM API key", False, "not configured"))
    # VIDEOCUT_MODE defaults to "subtitle_only", so not set is fine
    mode_val = os.getenv("VIDEOCUT_MODE", "")
    if mode_val:
        results.append(_check("Env VIDEOCUT_MODE", True, f'set to "{mode_val}"'))
    else:
        results.append(_check("Env VIDEOCUT_MODE", True, 'not set (defaults to "subtitle_only")'))

    print(_bold("\n===== Dependency Checks =====\n"))

    results.append(_import_check("PIL", "Pillow"))
    results.append(_import_check("dotenv", "python-dotenv"))
    results.append(_import_check("requests"))

    # Optional but recommended
    try:
        import faster_whisper  # noqa: F401
        results.append(_check("faster-whisper (ASR)", True, "importable"))
    except ImportError:
        results.append(_check("faster-whisper (ASR)", True, "not installed (pip install videocut[asr])"))

    print(_bold("\n===== Network Checks =====\n"))

    results.append(_check_llm_connectivity(config))

    # Summary
    print()
    for ok, line in results:
        print(line)
        if not ok:
            fail_count += 1

    print(f"\n{'=' * 40}")
    total = len(results)
    passed = total - fail_count
    if fail_count == 0:
        print(f"{_green('All checks passed')} ({passed}/{total})")
    else:
        print(f"{_red(f'{fail_count} check(s) failed')} ({passed}/{total})")
    return 1 if fail_count else 0

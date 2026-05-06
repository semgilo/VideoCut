from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path


_FFMPEG_FULL_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
]
_FFPROBE_FULL_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
    "/usr/local/opt/ffmpeg-full/bin/ffprobe",
]


def _find_binary(candidates: list[str], fallback: str) -> str:
    for path in candidates:
        if Path(path).exists():
            return path
    return fallback


_FFMPEG_BIN: str = _find_binary(_FFMPEG_FULL_CANDIDATES, "ffmpeg")
_FFPROBE_BIN: str = _find_binary(_FFPROBE_FULL_CANDIDATES, "ffprobe")


def resolve_tool_binary(name: str) -> str:
    if name == "ffmpeg":
        return _FFMPEG_BIN
    if name == "ffprobe":
        return _FFPROBE_BIN
    return name


def _normalize_command(args: list[str]) -> list[str]:
    if not args:
        return args
    return [resolve_tool_binary(args[0]), *args[1:]]


_TAIL_LINES = 80
_WATCHDOG_INTERVAL_SEC = 30.0
# If the subprocess produces no output for this many seconds, the watchdog
# kills it — likely a hang (e.g. ffmpeg stuck in final mux).  The caller
# can recover if the output file is still valid.
_STALL_TIMEOUT_SEC = 120.0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _label_for(args: list[str]) -> str:
    return Path(args[0]).name if args else "<empty>"


def _emit(line: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    print(line, file=stream, flush=True)


def run_command(
    args: list[str],
    cwd: Path | None = None,
    capture_output: bool = False,
    log_command: bool = True,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    resolved = _normalize_command(args)
    label = _label_for(resolved)
    if log_command:
        _emit(f"[{_now_iso()}] [{label}] $ {' '.join(resolved)}")
    merged_env = None if env is None else {**os.environ, **env}

    if capture_output:
        try:
            completed = subprocess.run(
                resolved,
                cwd=cwd,
                env=merged_env,
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            return completed.stdout.strip()
        except subprocess.TimeoutExpired:
            _emit(f"[{_now_iso()}] [{label}] TIMEOUT after {timeout}s", err=True)
            raise
        except subprocess.CalledProcessError as err:
            _emit(f"[{_now_iso()}] [{label}] FAILED exit={err.returncode}", err=True)
            stderr_text = err.stderr or ""
            for tail_line in stderr_text.splitlines()[-_TAIL_LINES:]:
                _emit(f"[{label}!] {tail_line}", err=True)
            raise

    proc = subprocess.Popen(
        resolved,
        cwd=cwd,
        env=merged_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    started_at = time.time()
    last_output_at = [started_at]
    tail: deque[str] = deque(maxlen=_TAIL_LINES)
    stop_watchdog = threading.Event()
    stall_killed = threading.Event()

    def _drain() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            tail.append(line)
            last_output_at[0] = time.time()
            _emit(f"[{label}] {line}")

    def _watchdog() -> None:
        while not stop_watchdog.wait(_WATCHDOG_INTERVAL_SEC):
            if proc.poll() is not None:
                return
            elapsed = int(time.time() - started_at)
            silent_for = int(time.time() - last_output_at[0])
            last = tail[-1] if tail else ""
            _emit(
                f"[{_now_iso()}] [{label} watchdog] running {elapsed}s, "
                f"silent {silent_for}s; last_line={last[:160]!r}"
            )

            # Stall detection: if silent longer than threshold, kill the process.
            # A correctly running encode should produce periodic progress lines.
            if silent_for >= _STALL_TIMEOUT_SEC:
                _emit(
                    f"[{_now_iso()}] [{label}] STALL DETECTED — no output for {silent_for}s, killing",
                    err=True,
                )
                stall_killed.set()
                proc.kill()
                return
            else:
                consecutive_silent = 0

    drain_t = threading.Thread(target=_drain, daemon=True)
    drain_t.start()
    watchdog_t = threading.Thread(target=_watchdog, daemon=True)
    watchdog_t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _emit(f"[{_now_iso()}] [{label}] TIMEOUT after {timeout}s — killing", err=True)
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    finally:
        stop_watchdog.set()
        drain_t.join(timeout=5)

    if timed_out:
        for tail_line in tail:
            _emit(f"[{label}!] {tail_line}", err=True)
        raise subprocess.TimeoutExpired(resolved, timeout)

    # Stall killed by watchdog — don't raise; the caller should verify its
    # output file.  A hung ffmpeg may have written a complete file before
    # getting stuck in final mux.
    if stall_killed.is_set():
        _emit(
            f"[{_now_iso()}] [{label}] PROCESS KILLED BY WATCHDOG (stall), "
            f"output may still be valid — caller to verify",
            err=True,
        )
        return ""

    if proc.returncode != 0:
        _emit(f"[{_now_iso()}] [{label}] FAILED exit={proc.returncode}", err=True)
        for tail_line in tail:
            _emit(f"[{label}!] {tail_line}", err=True)
        raise subprocess.CalledProcessError(proc.returncode, resolved)
    return ""


@contextlib.contextmanager
def stage(name: str):
    started = time.time()
    _emit(f"[{_now_iso()}] [STAGE >>] {name}")
    try:
        yield
    except BaseException as exc:
        dt = time.time() - started
        _emit(
            f"[{_now_iso()}] [STAGE XX] {name} FAILED after {dt:.1f}s: "
            f"{type(exc).__name__}: {exc}",
            err=True,
        )
        traceback.print_exc()
        raise
    else:
        dt = time.time() - started
        _emit(f"[{_now_iso()}] [STAGE OK] {name} done in {dt:.1f}s")


_current_step = ["<init>"]
_current_step_started_at = [time.time()]


def step(name: str) -> None:
    """Mark the current pipeline step without changing indentation.

    Pair with ``raise_with_step_context`` (or a top-level try/except that reads
    ``current_step()``) so a crash anywhere downstream is attributed to this
    step. Cheaper than ``stage()`` at the cost of approximate timing.
    """
    prev = _current_step[0]
    prev_started = _current_step_started_at[0]
    if prev != "<init>":
        dt = time.time() - prev_started
        _emit(f"[{_now_iso()}] [STAGE OK] {prev} done in {dt:.1f}s")
    _current_step[0] = name
    _current_step_started_at[0] = time.time()
    _emit(f"[{_now_iso()}] [STAGE >>] {name}")


def current_step() -> str:
    return _current_step[0]


@contextlib.contextmanager
def step_guard():
    """Wrap a sequence of ``step()`` calls so any exception is attributed.

    Prints ``[STAGE XX] <last step> FAILED ...`` with traceback on exit, then
    re-raises. Use once at the top of a pipeline function whose body uses
    ``step()`` markers.
    """
    try:
        yield
    except BaseException as exc:
        dt = time.time() - _current_step_started_at[0]
        _emit(
            f"[{_now_iso()}] [STAGE XX] {_current_step[0]} FAILED after {dt:.1f}s: "
            f"{type(exc).__name__}: {exc}",
            err=True,
        )
        traceback.print_exc()
        raise
    else:
        if _current_step[0] != "<init>":
            dt = time.time() - _current_step_started_at[0]
            _emit(f"[{_now_iso()}] [STAGE OK] {_current_step[0]} done in {dt:.1f}s")

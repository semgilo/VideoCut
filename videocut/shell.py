from __future__ import annotations

import subprocess
from pathlib import Path


def run_command(
    args: list[str],
    cwd: Path | None = None,
    capture_output: bool = False,
    log_command: bool = True,
) -> str:
    if log_command:
        print("$", " ".join(args))
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture_output,
    )
    if capture_output:
        return completed.stdout.strip()
    return ""

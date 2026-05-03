from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"


def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"


def _format_size(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    if bytes_ < 1024**2:
        return f"{bytes_ / 1024:.1f} KB"
    if bytes_ < 1024**3:
        return f"{bytes_ / 1024**2:.1f} MB"
    return f"{bytes_ / 1024**3:.1f} GB"


def _get_dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def _get_creation_time(path: Path) -> float:
    """Get directory creation time using stat birthtime."""
    stat = path.stat()
    return stat.st_birthtime


def _git_modified_time(path: Path) -> float | None:
    """Try to get the last modified time of a run dir from git status."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _format_time(timestamp: float) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def run_clean_runs(
    runs_dir: Path,
    *,
    keep_days: int | None = None,
    all_: bool = False,
    force: bool = False,
) -> int:
    runs_root = runs_dir.expanduser().resolve()

    if not runs_root.exists():
        print(f"Runs directory does not exist: {runs_root}")
        return 0

    run_dirs: list[Path] = sorted(
        [d for d in runs_root.iterdir() if d.is_dir()],
        key=_get_creation_time,
    )

    if not run_dirs:
        print(f"No run directories found in {runs_root}")
        return 0

    now = time.time()
    cutoff = now - (keep_days * 86400) if keep_days else None

    candidates: list[tuple[Path, float, int]] = []
    skipped = 0
    for d in run_dirs:
        ctime = _get_creation_time(d)
        size = _get_dir_size(d)

        if all_:
            candidates.append((d, ctime, size))
        elif cutoff and ctime < cutoff:
            candidates.append((d, ctime, size))
        else:
            skipped += 1

    if not candidates:
        print(f"No run directories to clean (skipped {skipped})")
        return 0

    total_size = sum(s for _, _, s in candidates)

    print(f"{_bold(f'Found {len(candidates)} run(s) to clean')} "
          f"({_format_size(total_size)}), keeping {skipped}\n")

    # Show what would be deleted
    header = f"{'Run dir':<70} {'Created':<20} {'Size':>10}"
    print(header)
    print("-" * len(header))
    for d, ctime, size in candidates:
        name = d.name[:68]
        print(f"{name:<70} {_format_time(ctime):<20} {_format_size(size):>10}")

    if not force:
        print(f"\n{_yellow('Dry-run mode. Use --force to actually delete.')}")
        return 0

    # Confirm
    print()
    confirm = input(f"{_yellow('Delete these {0} run(s)? (y/N): ')}".format(len(candidates)))
    if confirm.strip().lower() != "y":
        print("Aborted.")
        return 1

    # Delete
    deleted_count = 0
    deleted_size = 0
    for d, _, size in candidates:
        try:
            shutil.rmtree(d)
            deleted_count += 1
            deleted_size += size
            print(f"  {_red('Deleted')} {d.name}")
        except OSError as e:
            print(f"  {_red('Error')} deleting {d.name}: {e}")

    print(f"\n{_green(f'Cleaned {deleted_count} run(s), freed {_format_size(deleted_size)}')}")
    return 0

#!/usr/bin/env python3
"""Development-only polling supervisor for Pulse V2."""

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path


IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".pulse_v2"}
IGNORED_SUFFIXES = {".pyc", ".db"}


def watched_files(repo_root: Path) -> list[Path]:
    candidates = []
    for directory in ("daemon_v2", "tests_v2", "scripts"):
        root = repo_root / directory
        if not root.exists():
            continue
        patterns = ("*.py",) if directory != "scripts" else ("*.py", "*.sh")
        for pattern in patterns:
            candidates.extend(root.rglob(pattern))
    candidates.extend(repo_root / name for name in ("Makefile", "README.md"))
    return sorted(
        path
        for path in candidates
        if path.is_file()
        and not any(part in IGNORED_PARTS for part in path.relative_to(repo_root).parts)
        and path.suffix not in IGNORED_SUFFIXES
    )


def snapshot(repo_root: Path) -> dict[Path, tuple[int, int]]:
    state = {}
    for path in watched_files(repo_root):
        try:
            stat = path.stat()
        except OSError:
            continue
        state[path] = (stat.st_mtime_ns, stat.st_size)
    return state


def start_pulse(repo_root: Path) -> subprocess.Popen:
    print("Pulse dev-reload: démarrage de Pulse V2", flush=True)
    return subprocess.Popen(
        [str(repo_root / "scripts" / "dev.sh")],
        cwd=repo_root,
        start_new_session=True,
    )


def stop_pulse(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run(repo_root: Path, poll_interval: float, debounce: float) -> int:
    current_snapshot = snapshot(repo_root)
    process = start_pulse(repo_root)
    running = True

    def request_stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    pending_reload_at = None
    try:
        while running:
            time.sleep(poll_interval)
            if process.poll() is not None:
                print("Pulse dev-reload: un processus Pulse s’est arrêté.", flush=True)
                return process.returncode or 1

            next_snapshot = snapshot(repo_root)
            if next_snapshot != current_snapshot:
                current_snapshot = next_snapshot
                pending_reload_at = time.monotonic() + debounce

            if pending_reload_at is not None and time.monotonic() >= pending_reload_at:
                print("Pulse dev-reload: changement détecté, rechargement…", flush=True)
                stop_pulse(process)
                process = start_pulse(repo_root)
                pending_reload_at = None
    finally:
        stop_pulse(process)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Reload Pulse V2 during development")
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--debounce", type=float, default=0.75)
    args = parser.parse_args()
    if args.poll <= 0 or args.debounce < 0:
        parser.error("--poll must be positive and --debounce must not be negative")

    repo_root = Path(__file__).resolve().parent.parent
    raise SystemExit(run(repo_root, args.poll, args.debounce))


if __name__ == "__main__":
    main()

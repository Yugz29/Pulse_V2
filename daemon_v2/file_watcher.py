"""Small polling file watcher for one explicitly selected workspace."""

import argparse
import json
import os
import time
from pathlib import Path
from typing import TypeAlias
from urllib.error import URLError
from urllib.request import Request, urlopen


ACTIVITIES_URL = "http://127.0.0.1:5000/activities"
IGNORED_DIRECTORY_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache"}
IGNORED_FILE_NAMES = {".DS_Store"}
IGNORED_FILE_SUFFIXES = {".pyc", ".db"}

FileSignature: TypeAlias = tuple[int, int]
Snapshot: TypeAlias = dict[Path, FileSignature]


def should_ignore(path: Path, workspace: Path) -> bool:
    try:
        relative_path = path.relative_to(workspace)
    except ValueError:
        return True
    return (
        any(part in IGNORED_DIRECTORY_NAMES for part in relative_path.parts[:-1])
        or path.name in IGNORED_FILE_NAMES
        or path.suffix in IGNORED_FILE_SUFFIXES
    )


def take_snapshot(workspace: Path) -> Snapshot:
    snapshot: Snapshot = {}
    for root, directory_names, file_names in os.walk(workspace):
        directory_names[:] = [
            name for name in directory_names if name not in IGNORED_DIRECTORY_NAMES
        ]
        root_path = Path(root)
        for file_name in file_names:
            path = root_path / file_name
            if should_ignore(path, workspace):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def compare_snapshots(previous: Snapshot, current: Snapshot) -> list[tuple[str, Path]]:
    previous_paths = set(previous)
    current_paths = set(current)
    events = [
        ("created", path) for path in sorted(current_paths - previous_paths)
    ]
    events.extend(
        ("modified", path)
        for path in sorted(previous_paths & current_paths)
        if previous[path] != current[path]
    )
    events.extend(
        ("deleted", path) for path in sorted(previous_paths - current_paths)
    )
    return events


def post_file_event(event: str, path: Path, workspace: Path) -> bool:
    payload = json.dumps(
        {
            "type": "file_changed",
            "path": str(path),
            "event": event,
            "workspace": str(workspace),
        }
    ).encode()
    request = Request(
        ACTIVITIES_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=0.5):
            return True
    except (OSError, URLError):
        return False


def watch(workspace: Path, interval: float = 1.0) -> None:
    previous = take_snapshot(workspace)
    print(f"Watching files in {workspace}", flush=True)
    try:
        while True:
            time.sleep(interval)
            current = take_snapshot(workspace)
            for event, path in compare_snapshots(previous, current):
                post_file_event(event, path, workspace)
            previous = current
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch one workspace for file changes")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    watch(workspace, args.interval)


if __name__ == "__main__":
    main()

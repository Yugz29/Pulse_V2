"""Small, failure-tolerant Git context reader for terminal events."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


GIT_TIMEOUT_SECONDS = 0.75


@dataclass(frozen=True)
class GitContext:
    repository: str
    git_root: str
    branch: str
    head: str
    dirty: bool
    staged: int
    unstaged: int
    untracked: int

    def as_details(self) -> dict[str, str | bool | int]:
        return asdict(self)


def read_git_context(path: Path) -> GitContext | None:
    """Read repository identity and status, returning None on every failure."""
    try:
        candidate = Path(path).expanduser()
        start = candidate if candidate.is_dir() else candidate.parent
        identity = _run_git(
            [
                "git",
                "rev-parse",
                "--show-toplevel",
                "--short",
                "HEAD",
            ],
            cwd=start,
        )
        if identity is None:
            return None
        identity_lines = identity.splitlines()
        if len(identity_lines) != 2:
            return None

        root = Path(identity_lines[0]).expanduser()
        head = identity_lines[1].strip()
        if not root.is_absolute() or not head:
            return None

        status = _run_git(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--branch",
                "--untracked-files=all",
            ],
            cwd=root,
        )
        if status is None:
            return None
        branch = _parse_branch(status, head)
        if branch is None:
            return None
        staged, unstaged, untracked = _parse_status_counts(status)
        return GitContext(
            repository=root.name,
            git_root=str(root),
            branch=branch,
            head=head,
            dirty=bool(staged or unstaged or untracked),
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
        )
    except Exception:
        return None


def _run_git(args: list[str], *, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _parse_status_counts(output: str) -> tuple[int, int, int]:
    staged = 0
    unstaged = 0
    untracked = 0
    for line in output.splitlines():
        if line.startswith("## "):
            continue
        if len(line) < 2:
            continue
        status = line[:2]
        if status == "??":
            untracked += 1
            continue
        if status[0] not in {" ", "?"}:
            staged += 1
        if status[1] not in {" ", "?"}:
            unstaged += 1
    return staged, unstaged, untracked


def _parse_branch(output: str, head: str) -> str | None:
    first_line = output.splitlines()[0] if output else ""
    if not first_line.startswith("## "):
        return None
    description = first_line[3:]
    if description.startswith("HEAD "):
        return f"detached:{head}"
    branch = description.split("...", 1)[0].split(" ", 1)[0].strip()
    return branch or None

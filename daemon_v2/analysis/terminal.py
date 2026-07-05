"""Pure classification helpers for observed terminal commands."""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


TERMINAL_LABEL_ORDER = ("test", "git", "pulse", "erreur")


@dataclass(frozen=True)
class ObservedGitCommand:
    is_git: bool
    action: str | None = None
    commit_message: str | None = None


def parse_git_command(command: str) -> ObservedGitCommand:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts or parts[0] != "git":
        return ObservedGitCommand(is_git=False)

    action = parts[1] if len(parts) > 1 else "other"
    if action not in {"commit", "push", "pull", "status"}:
        action = "other"

    commit_message = None
    if (
        action == "commit"
        and "-m" in parts
        and parts.index("-m") + 1 < len(parts)
    ):
        commit_message = parts[parts.index("-m") + 1]
    return ObservedGitCommand(
        is_git=True,
        action=action,
        commit_message=commit_message,
    )


def is_test_command(line: str) -> bool:
    normalized = " ".join(line.split())
    parts = normalized.split()
    python_pytest = (
        len(parts) >= 3
        and Path(parts[0]).name in {"python", "python3"}
        and parts[1:3] == ["-m", "pytest"]
    )
    return python_pytest or any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in ("make test", "pytest", "npm test", "swift test")
    )


def is_pulse_inspection_command(line: str) -> bool:
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if not parts or Path(parts[0]).name != "curl":
        return False

    match = re.search(r"https?://[^\s|\"']+", line)
    if not match:
        return False
    parsed = urlsplit(match.group(0))
    try:
        is_local_pulse = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 5000
        )
    except ValueError:
        return False
    if not is_local_pulse:
        return False

    path = parsed.path or "/"
    return (
        path in {"/", "/days", "/trace/today", "/trace/today.md", "/trace/days"}
        or bool(re.fullmatch(r"/trace/\d{4}-\d{2}-\d{2}(?:\.md)?", path))
        or bool(re.fullmatch(r"/day/\d{4}-\d{2}-\d{2}", path))
    )


def is_pasted_prompt_command(command: str) -> bool:
    lines = [line.strip() for line in command.splitlines() if line.strip()]
    if not lines:
        return False

    try:
        first_parts = shlex.split(lines[0])
    except ValueError:
        first_parts = lines[0].split()
    first_executable = Path(first_parts[0]).name if first_parts else ""
    if first_executable.startswith("python") or first_executable in {
        "curl",
        "git",
        "make",
        "npm",
        "pytest",
    }:
        return False

    markers = (
        "contexte",
        "objectif",
        "à faire",
        "contraintes",
        "validation attendue",
        "problème",
    )
    marker_count = sum(
        bool(re.search(rf"(?i)\b{re.escape(marker)}\s*:", command))
        for marker in markers
    )
    document_title = bool(
        re.match(r"^[\w.-]+\s+[—-]\s+\S+", lines[0])
    )
    return (
        marker_count >= 2
        and (len(lines) >= 3 or len(command) >= 160)
    ) or (
        document_title
        and marker_count >= 1
        and len(command) >= 80
    )


def useful_command_lines(command: Any) -> list[str]:
    if not isinstance(command, str):
        return []
    if is_pasted_prompt_command(command):
        return []
    return [
        line.strip()
        for line in command.splitlines()
        if line.strip() and not is_pulse_inspection_command(line.strip())
    ]


def terminal_labels(activity: dict[str, Any]) -> list[str]:
    details = activity.get("details", {})
    command = details.get("command")
    command_lines = [
        " ".join(line.split()) for line in useful_command_lines(command)
    ]
    if not command_lines:
        return []
    labels: set[str] = set()
    for line in command_lines:
        if is_test_command(line):
            labels.add("test")
        if parse_git_command(line).action in {"commit", "push", "pull", "status"}:
            labels.add("git")
        if any(
            line == prefix or line.startswith(f"{prefix} ")
            for prefix in (
                "./scripts/dev.sh",
                "python -m daemon_v2.main",
                "python -m daemon_v2.file_watcher",
                "python -m daemon_v2.app_watcher",
            )
        ):
            labels.add("pulse")
    exit_code = details.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        labels.add("erreur")
    return [label for label in TERMINAL_LABEL_ORDER if label in labels]

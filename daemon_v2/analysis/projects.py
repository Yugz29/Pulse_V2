"""Pure helpers for the existing project and workspace rules."""

from pathlib import Path
from typing import Any

from .terminal import useful_command_lines


def activity_workspace(activity: dict[str, Any]) -> str | None:
    details = activity.get("details", {})
    if details.get("workspace"):
        return details["workspace"]
    if activity["type"] == "terminal_finished" and details.get("cwd"):
        return details["cwd"]
    return None


def _generic_workspace_containers(home: Path) -> set[Path]:
    # Local heuristic only; this can become configurable if more layouts emerge.
    return {home / "Projets"}


def is_weak_workspace(workspace: str) -> bool:
    path = Path(workspace).expanduser()
    home = Path.home()
    return path == home or path in _generic_workspace_containers(home)


def last_observed_workspace(trace: dict[str, Any]) -> str | None:
    workspace = None
    for session in trace["sessions"]:
        for activity in session["activities"]:
            if activity["type"] == "app_activated":
                continue
            details = activity.get("details", {})
            useful_activity = (
                activity["type"] != "terminal_finished"
                or bool(useful_command_lines(details.get("command")))
            )
            if not useful_activity:
                continue
            candidate = activity_workspace(activity)
            if candidate and not is_weak_workspace(candidate):
                workspace = candidate
    return workspace


def most_frequent_explicit_workspace(
    trace: dict[str, Any],
) -> str | None:
    counts: dict[str, int] = {}
    for session in trace["sessions"]:
        for activity in session["activities"]:
            workspace = activity.get("details", {}).get("workspace")
            if workspace and not is_weak_workspace(workspace):
                counts[workspace] = counts.get(workspace, 0) + 1
    return max(counts, key=counts.get) if counts else None

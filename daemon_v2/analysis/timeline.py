"""Pure helpers for preparing timeline data for renderers."""

from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any


IGNORED_APP_NAMES_FOR_RENDERING = {"CleanMyMac Menu", "Finder", "loginwindow"}


def _display_time(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%H:%M")


def _display_file_path(path: str, workspace: str | None) -> str:
    display_path = Path(path)
    if workspace:
        try:
            display_path = display_path.relative_to(Path(workspace))
        except ValueError:
            pass
    return str(display_path)


def _activity_workspace(activity: dict[str, Any]) -> str | None:
    details = activity.get("details", {})
    if details.get("workspace"):
        return details["workspace"]
    if activity["type"] == "terminal_finished" and details.get("cwd"):
        return details["cwd"]
    return None


def _generic_workspace_containers(home: Path) -> set[Path]:
    # Local heuristic only; this can become configurable if more layouts emerge.
    return {home / "Projets"}


def _is_weak_workspace(workspace: str) -> bool:
    path = Path(workspace).expanduser()
    home = Path.home()
    return path == home or path in _generic_workspace_containers(home)


def _app_activation_counts(session: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for activity in session["activities"]:
        if activity["type"] == "app_activated":
            app = activity.get("details", {}).get("app")
            if app and app not in IGNORED_APP_NAMES_FOR_RENDERING:
                counts[app] = counts.get(app, 0) + 1
    return counts


def _ranked_apps(counts: dict[str, int], limit: int = 5) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: -item[1])[:limit]


def _displayed_sessions(trace: dict[str, Any]) -> list[dict[str, Any]]:
    displayed = []
    for session in trace["sessions"]:
        if any(
            activity["type"] in {"terminal_finished", "file_changed"}
            or (
                activity["type"] == "app_activated"
                and activity.get("details", {}).get("app")
                not in IGNORED_APP_NAMES_FOR_RENDERING
            )
            for activity in session["activities"]
        ):
            displayed.append(session)
    return displayed


def _file_change_groups(
    session: dict[str, Any],
) -> dict[int, list[tuple[str, str, str | None, int]]]:
    activities_by_minute: dict[
        tuple[datetime, str | None],
        list[dict[str, Any]],
    ] = {}
    for activity in session["activities"]:
        if activity["type"] != "file_changed":
            continue
        details = activity.get("details", {})
        path = details.get("path")
        event = details.get("event", details.get("change"))
        if path and event:
            minute = datetime.fromisoformat(activity["occurred_at"]).replace(
                second=0, microsecond=0
            )
            activities_by_minute.setdefault(
                (minute, details.get("workspace")), []
            ).append(activity)

    groups = {}
    for activities in activities_by_minute.values():
        counts: OrderedDict[str, int] = OrderedDict()
        first_activities = {}
        for activity in activities:
            path = activity["details"]["path"]
            counts[path] = counts.get(path, 0) + 1
            first_activities.setdefault(path, activity)
        group = [
            (
                path,
                first_activities[path]["details"].get(
                    "event", first_activities[path]["details"].get("change")
                ),
                first_activities[path]["details"].get("workspace"),
                count,
            )
            for path, count in counts.items()
        ]
        first_activity = next(iter(first_activities.values()))
        groups[id(first_activity)] = group
    return groups


def _session_project_sequence(
    session: dict[str, Any],
    project_workspaces: set[str],
) -> list[str]:
    file_change_groups = _file_change_groups(session)
    sequence = []
    current_workspace = None
    for activity in session["activities"]:
        details = activity.get("details", {})
        duplicate_file = (
            activity["type"] == "file_changed"
            and bool(
                details.get("event", details.get("change"))
                and details.get("path")
            )
            and id(activity) not in file_change_groups
        )
        workspace = _activity_workspace(activity)
        if (
            not duplicate_file
            and workspace in project_workspaces
            and workspace != current_workspace
        ):
            current_workspace = workspace
            sequence.append(workspace)
    return sequence

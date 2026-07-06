"""Pure helpers for preparing timeline data for renderers."""

from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .projects import activity_project_root, activity_workspace, is_weak_workspace


IGNORED_APP_NAMES_FOR_RENDERING = {"CleanMyMac Menu", "Finder", "loginwindow"}
WORK_SESSION_GAP = timedelta(minutes=30)
WEAK_CONTEXT_WINDOW = timedelta(minutes=15)

# Temporary aliases preserve the renderer-facing timeline API.
_activity_workspace = activity_workspace
_is_weak_workspace = is_weak_workspace


def _display_time(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%H:%M")


def _session_observed_bounds(session: dict[str, Any]) -> tuple[str, str]:
    strong_activities = [
        activity
        for activity in session["activities"]
        if _is_strong_work_activity(activity)
    ]
    if strong_activities:
        return (
            strong_activities[0]["occurred_at"],
            strong_activities[-1]["occurred_at"],
        )

    file_change_groups = _file_change_groups(session)
    app_activation_counts = _app_activation_counts(session)
    rendered_app_activations = False
    rendered_activities = []
    for activity in session["activities"]:
        details = activity.get("details", {})
        if activity["type"] == "app_activated":
            if details.get("app") not in app_activation_counts:
                continue
            if rendered_app_activations:
                continue
            rendered_app_activations = True
        elif (
            activity["type"] == "file_changed"
            and details.get("event", details.get("change"))
            and details.get("path")
            and id(activity) not in file_change_groups
        ):
            continue
        rendered_activities.append(activity)

    if not rendered_activities:
        return session["started_at"], session["ended_at"]
    return (
        rendered_activities[0]["occurred_at"],
        rendered_activities[-1]["occurred_at"],
    )


def _session_duration(session: dict[str, Any]) -> str:
    started_at, ended_at = _session_observed_bounds(session)
    duration = (
        datetime.fromisoformat(ended_at)
        - datetime.fromisoformat(started_at)
    )
    minutes = max(0, int(duration.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h{remaining_minutes:02d}"


def _session_duration_seconds(session: dict[str, Any]) -> float:
    started_at, ended_at = _session_observed_bounds(session)
    return max(
        0,
        (
            datetime.fromisoformat(ended_at)
            - datetime.fromisoformat(started_at)
        ).total_seconds(),
    )


def _display_file_path(path: str, workspace: str | None) -> str:
    display_path = Path(path)
    if workspace:
        try:
            display_path = display_path.relative_to(Path(workspace))
        except ValueError:
            pass
    return str(display_path)


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


def _is_strong_work_activity(activity: dict[str, Any]) -> bool:
    return activity["type"] in {"terminal_finished", "file_changed"}


def _session_from_activities(
    source_session: dict[str, Any],
    activities: list[dict[str, Any]],
    suffix: str,
) -> dict[str, Any]:
    return {
        "id": f"{source_session['id']}-{suffix}",
        "started_at": activities[0]["occurred_at"],
        "ended_at": activities[-1]["occurred_at"],
        "activity_count": len(activities),
        "activities": activities,
    }


def _split_rendered_sessions(
    trace: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    work_sessions = []
    passive_sessions = []
    for source_session in trace["sessions"]:
        activities = sorted(
            source_session["activities"],
            key=lambda activity: (
                activity["occurred_at"],
                activity.get("id", 0),
            ),
        )
        strong_activities = [
            activity
            for activity in activities
            if _is_strong_work_activity(activity)
        ]

        strong_groups: list[list[dict[str, Any]]] = []
        for activity in strong_activities:
            if not strong_groups:
                strong_groups.append([activity])
                continue
            previous_at = datetime.fromisoformat(
                strong_groups[-1][-1]["occurred_at"]
            )
            current_at = datetime.fromisoformat(activity["occurred_at"])
            if current_at - previous_at <= WORK_SESSION_GAP:
                strong_groups[-1].append(activity)
            else:
                strong_groups.append([activity])

        assigned_ids: set[int] = set()
        for index, strong_group in enumerate(strong_groups, start=1):
            started_at = datetime.fromisoformat(
                strong_group[0]["occurred_at"]
            ) - WEAK_CONTEXT_WINDOW
            ended_at = datetime.fromisoformat(
                strong_group[-1]["occurred_at"]
            ) + WEAK_CONTEXT_WINDOW
            grouped_activities = [
                activity
                for activity in activities
                if started_at
                <= datetime.fromisoformat(activity["occurred_at"])
                <= ended_at
            ]
            assigned_ids.update(id(activity) for activity in grouped_activities)
            work_sessions.append(
                _session_from_activities(
                    source_session,
                    grouped_activities,
                    f"work-{index}",
                )
            )

        passive_activities = [
            activity
            for activity in activities
            if id(activity) not in assigned_ids
            and not (
                activity["type"] == "app_activated"
                and activity.get("details", {}).get("app")
                in IGNORED_APP_NAMES_FOR_RENDERING
            )
        ]
        passive_groups: list[list[dict[str, Any]]] = []
        for activity in passive_activities:
            if not passive_groups:
                passive_groups.append([activity])
                continue
            previous_at = datetime.fromisoformat(
                passive_groups[-1][-1]["occurred_at"]
            )
            current_at = datetime.fromisoformat(activity["occurred_at"])
            if current_at - previous_at <= WORK_SESSION_GAP:
                passive_groups[-1].append(activity)
            else:
                passive_groups.append([activity])
        passive_sessions.extend(
            _session_from_activities(
                source_session,
                group,
                f"passive-{index}",
            )
            for index, group in enumerate(passive_groups, start=1)
        )
    return work_sessions, passive_sessions


def _passive_sessions(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return _split_rendered_sessions(trace)[1]


def _displayed_sessions(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Return work sessions segmented only by strong activity."""
    return _split_rendered_sessions(trace)[0]


def _session_has_recent_strong_activity(
    session: dict[str, Any],
    now: datetime,
) -> bool:
    strong_times = [
        datetime.fromisoformat(activity["occurred_at"])
        for activity in session["activities"]
        if _is_strong_work_activity(activity)
    ]
    return bool(strong_times) and now - strong_times[-1] <= WORK_SESSION_GAP


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
        workspace = activity_project_root(activity)
        if (
            not duplicate_file
            and workspace in project_workspaces
            and workspace != current_workspace
        ):
            current_workspace = workspace
            sequence.append(workspace)
    return sequence

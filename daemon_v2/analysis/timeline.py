"""Pure helpers for preparing timeline data for renderers."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .projects import activity_project_root, activity_workspace, is_weak_workspace


IGNORED_APP_NAMES_FOR_RENDERING = {"CleanMyMac Menu", "Finder", "loginwindow"}
WORK_SESSION_GAP = timedelta(minutes=30)
WEAK_CONTEXT_WINDOW = timedelta(minutes=15)
DEFAULT_INTERRUPTION_THRESHOLD = timedelta(minutes=5)
WORKSPACE_PROMOTION_WINDOW = timedelta(minutes=5)

# Temporary aliases preserve the renderer-facing timeline API.
_activity_workspace = activity_workspace
_is_weak_workspace = is_weak_workspace


def _trace_timezone(trace: dict[str, Any]) -> tzinfo:
    name = str(trace["timezone"])
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "UTC":
            return timezone.utc
        try:
            return datetime.fromisoformat(
                f"2000-01-01T00:00:00{name}"
            ).tzinfo or timezone.utc
        except ValueError as exc:
            raise ValueError(f"invalid trace timezone: {name}") from exc


def _display_time(value: str, zone: tzinfo) -> str:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("timeline timestamps must include a timezone")
    return instant.astimezone(zone).strftime("%H:%M")


def _session_observed_bounds(session: dict[str, Any]) -> tuple[str, str]:
    if "end_reason" in session:
        return session["started_at"], session["ended_at"]
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


def _display_file_path(path: str, workspace: Any) -> str:
    display_path = Path(path)
    if isinstance(workspace, dict):
        workspace = workspace.get("workspace_root")
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


@dataclass(frozen=True)
class WorkspaceIdentity:
    root: str | None
    project_name: str | None
    method: str | None
    confidence: str | None


def _is_generic_workspace_path(root: str) -> bool:
    path = Path(root).expanduser()
    generic_names = {
        "build",
        "dist",
        "home",
        "project",
        "projects",
        "test",
        "tmp",
        "unqualified",
        "users",
    }
    generic_paths = {
        Path("/"),
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/tmp"),
        Path.home(),
        Path.home() / "Projets",
    }
    return (
        path in generic_paths
        or is_weak_workspace(str(path))
        or path.name.casefold() in generic_names
    )


def _persisted_workspace(
    activity: dict[str, Any],
) -> WorkspaceIdentity:
    """Resolve a project only from persisted event details."""
    details = activity.get("details", {})
    workspace = details.get("workspace")
    if isinstance(workspace, dict):
        root = workspace.get("workspace_root")
        name = workspace.get("project_name")
        method = workspace.get("resolution_method")
        confidence = workspace.get("resolution_confidence")
        if isinstance(root, str) and root.strip():
            normalized_method = (
                method if isinstance(method, str) and method else "workspace"
            )
            normalized_confidence = (
                confidence
                if confidence in {"low", "medium", "high"}
                else (
                    "high"
                    if normalized_method == "git"
                    else "medium"
                    if normalized_method == "marker"
                    else "low"
                    if normalized_method == "cwd"
                    else "medium"
                )
            )
            if (
                normalized_confidence == "low"
                and normalized_method == "cwd"
                and _is_generic_workspace_path(root)
            ):
                return WorkspaceIdentity(
                    None,
                    None,
                    normalized_method,
                    normalized_confidence,
                )
            return WorkspaceIdentity(
                root,
                name if isinstance(name, str) and name.strip() else Path(root).name,
                normalized_method,
                normalized_confidence,
            )
    elif isinstance(workspace, str) and workspace.strip():
        return WorkspaceIdentity(
            workspace,
            Path(workspace).name,
            "workspace",
            "medium",
        )

    git = details.get("git")
    if isinstance(git, dict):
        root = git.get("git_root")
        name = git.get("repository")
        if isinstance(root, str) and root.strip():
            return WorkspaceIdentity(
                root,
                name if isinstance(name, str) and name.strip() else Path(root).name,
                "git",
                "high",
            )
    git_root = details.get("git_root")
    if isinstance(git_root, str) and git_root.strip():
        root = git_root.strip()
        return WorkspaceIdentity(
            root,
            Path(root).name or None,
            "git",
            "high",
        )

    cwd = details.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        root = cwd.strip()
        if _is_generic_workspace_path(root):
            return WorkspaceIdentity(None, None, "cwd", "low")
        return WorkspaceIdentity(root, Path(root).name, "cwd", "low")
    return WorkspaceIdentity(None, None, None, None)


def configured_interruption_threshold() -> timedelta:
    raw = os.environ.get("PULSE_SESSION_INTERRUPTION_MINUTES")
    if raw is None:
        return DEFAULT_INTERRUPTION_THRESHOLD
    try:
        minutes = float(raw)
    except ValueError:
        return DEFAULT_INTERRUPTION_THRESHOLD
    if minutes < 0:
        return DEFAULT_INTERRUPTION_THRESHOLD
    return timedelta(minutes=minutes)


def _session_metadata(
    activities: list[dict[str, Any]],
    *,
    started_at: datetime,
    ended_at: datetime,
    workspace_root: str | None,
    project_name: str | None,
    end_reason: str,
    zone: tzinfo,
    interruptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    files: set[str] = set()
    commands_executed = 0
    applications: list[str] = []
    for activity in activities:
        details = activity.get("details", {})
        if activity["type"] == "file_changed":
            path = details.get("path")
            if isinstance(path, str) and path:
                files.add(path)
        elif activity["type"] == "terminal_finished":
            commands_executed += 1
        elif activity["type"] == "app_activated":
            app = details.get("app")
            if (
                isinstance(app, str)
                and app
                and app not in IGNORED_APP_NAMES_FOR_RENDERING
                and app not in applications
            ):
                applications.append(app)

    calendar_duration = max(0, int((ended_at - started_at).total_seconds()))
    interruption_rows = interruptions or []
    interrupted_seconds = 0
    for interruption in interruption_rows:
        interruption_start = datetime.fromisoformat(interruption["started_at"])
        interruption_end = datetime.fromisoformat(interruption["ended_at"])
        overlap_start = max(started_at, interruption_start)
        overlap_end = min(ended_at, interruption_end)
        if overlap_end > overlap_start:
            interrupted_seconds += int(
                (overlap_end - overlap_start).total_seconds()
            )

    localized_interruptions = [
        {
            **interruption,
            "started_at": datetime.fromisoformat(
                interruption["started_at"]
            ).astimezone(zone).isoformat(),
            "ended_at": datetime.fromisoformat(
                interruption["ended_at"]
            ).astimezone(zone).isoformat(),
        }
        for interruption in interruption_rows
    ]

    return {
        "started_at": started_at.astimezone(zone).isoformat(),
        "ended_at": ended_at.astimezone(zone).isoformat(),
        "duration_seconds": calendar_duration,
        "active_duration_seconds": max(
            0,
            calendar_duration - interrupted_seconds,
        ),
        "project_name": project_name,
        "workspace_root": workspace_root,
        "event_count": len(activities),
        "activity_count": len(activities),
        "files_changed": len(files),
        "commands_executed": commands_executed,
        "applications": applications,
        "interruptions": localized_interruptions,
        "end_reason": end_reason,
        "activities": activities,
    }


def _session_from_activities(
    activities: list[dict[str, Any]],
    session_id: str,
    zone: tzinfo,
) -> dict[str, Any]:
    return {
        "id": session_id,
        "started_at": datetime.fromisoformat(
            activities[0]["occurred_at"]
        ).astimezone(zone).isoformat(),
        "ended_at": datetime.fromisoformat(
            activities[-1]["occurred_at"]
        ).astimezone(zone).isoformat(),
        "activity_count": len(activities),
        "activities": activities,
    }


def reconstruct_session_views(
    trace: dict[str, Any],
    *,
    now: datetime | None = None,
    interruption_threshold: timedelta | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    interruption_threshold = (
        interruption_threshold
        if interruption_threshold is not None
        else configured_interruption_threshold()
    )
    trace_zone = _trace_timezone(trace)
    activities = sorted(
        (
            activity
            for source_session in trace["sessions"]
            for activity in source_session["activities"]
        ),
        key=lambda activity: (
            datetime.fromisoformat(activity["occurred_at"]),
            activity.get("id", 0),
        ),
    )
    work_sessions: list[dict[str, Any]] = []
    assigned_ids: set[int] = set()
    current: dict[str, Any] | None = None

    def close_current(ended_at: datetime, reason: str) -> None:
        nonlocal current
        assert current is not None
        session_activities = sorted(
            (
                activity
                for activity in current["activities"]
                if current["started_at"]
                <= datetime.fromisoformat(activity["occurred_at"])
                <= ended_at
            ),
            key=lambda activity: (
                datetime.fromisoformat(activity["occurred_at"]),
                activity.get("id", 0),
            ),
        )
        work_sessions.append(
            {
                "id": f"work-{len(work_sessions) + 1}",
                **_session_metadata(
                    session_activities,
                    started_at=current["started_at"],
                    ended_at=ended_at,
                    workspace_root=current["workspace_root"],
                    project_name=current["project_name"],
                    end_reason=reason,
                    zone=trace_zone,
                    interruptions=current["interruptions"],
                ),
            }
        )
        assigned_ids.update(id(activity) for activity in session_activities)
        current = None

    def start_session(activity: dict[str, Any], occurred_at: datetime) -> None:
        nonlocal current
        workspace = _persisted_workspace(activity)
        current = {
            "started_at": occurred_at,
            "last_work_at": occurred_at,
            "workspace_root": workspace.root,
            "project_name": workspace.project_name,
            "workspace_method": workspace.method,
            "workspace_confidence": workspace.confidence,
            "workspace_observed_at": occurred_at,
            "activities": [activity],
            "pending_passive": [],
            "interruptions": [],
            "pending_interruption": None,
        }

    def workspace_transition(
        incoming: WorkspaceIdentity,
        occurred_at: datetime,
    ) -> str:
        """Return same, promote, or split for the active session."""
        assert current is not None
        current_root = current["workspace_root"]
        current_confidence = current["workspace_confidence"]
        if incoming.root is None:
            return "same"
        if current_root is None:
            return "promote"
        if incoming.root == current_root:
            return (
                "promote"
                if current_confidence == "low"
                and incoming.confidence in {"medium", "high"}
                else "same"
            )
        if current_confidence == "high" and incoming.confidence == "low":
            return "same"
        if (
            current_confidence == "low"
            and incoming.confidence in {"medium", "high"}
            and occurred_at - current["workspace_observed_at"]
            <= WORKSPACE_PROMOTION_WINDOW
        ):
            current_path = Path(current_root).expanduser()
            incoming_path = Path(incoming.root).expanduser()
            if (
                current_path in incoming_path.parents
                or incoming_path in current_path.parents
            ):
                return "promote"
        return "split"

    def promote_workspace(
        incoming: WorkspaceIdentity,
        occurred_at: datetime,
    ) -> None:
        assert current is not None
        current["workspace_root"] = incoming.root
        current["project_name"] = incoming.project_name
        current["workspace_method"] = incoming.method
        current["workspace_confidence"] = incoming.confidence
        current["workspace_observed_at"] = occurred_at

    def confirm_pending_passive() -> None:
        assert current is not None
        current["activities"].extend(current["pending_passive"])
        current["pending_passive"] = []

    def completed_interruption(
        pending: dict[str, Any],
        ended_at: datetime,
    ) -> dict[str, Any]:
        return {
            "type": pending["type"],
            "started_at": pending["started_at"].isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": max(
                0,
                int((ended_at - pending["started_at"]).total_seconds()),
            ),
        }

    for activity in activities:
        occurred_at = datetime.fromisoformat(activity["occurred_at"])
        activity_type = activity["type"]
        is_work = _is_strong_work_activity(activity)

        if (
            current is not None
            and current["pending_interruption"] is None
            and occurred_at - current["last_work_at"] > WORK_SESSION_GAP
        ):
            close_current(current["last_work_at"], "inactivity")

        if activity_type in {"screen_locked", "system_sleep"}:
            if (
                current is not None
                and current["pending_interruption"] is None
            ):
                current["activities"].append(activity)
                current["pending_interruption"] = {
                    "type": activity_type,
                    "started_at": occurred_at,
                    "ended_at": None,
                }
            continue

        if activity_type in {"screen_unlocked", "system_wake"}:
            if (
                current is not None
                and current["pending_interruption"] is not None
            ):
                pending = current["pending_interruption"]
                pending["ended_at"] = occurred_at
                current["activities"].append(activity)
                interruption = completed_interruption(pending, occurred_at)
                if (
                    timedelta(seconds=interruption["duration_seconds"])
                    > interruption_threshold
                ):
                    current["interruptions"].append(interruption)
                    close_current(occurred_at, pending["type"])
            continue

        if is_work:
            workspace = _persisted_workspace(activity)
            if current is None:
                start_session(activity, occurred_at)
                continue
            transition = workspace_transition(workspace, occurred_at)
            pending = current["pending_interruption"]
            if pending is not None:
                interruption_end = pending["ended_at"] or occurred_at
                interruption = completed_interruption(
                    pending,
                    interruption_end,
                )
                workspace_changed = (
                    transition == "split"
                )
                interruption_is_long = (
                    timedelta(seconds=interruption["duration_seconds"])
                    > interruption_threshold
                )
                current["interruptions"].append(interruption)
                if workspace_changed or interruption_is_long:
                    close_current(
                        interruption_end
                        if pending["ended_at"] is not None
                        else pending["started_at"],
                        "workspace_changed"
                        if workspace_changed
                        else pending["type"],
                    )
                    start_session(activity, occurred_at)
                    continue
                current["pending_interruption"] = None
                active_idle = (
                    occurred_at
                    - current["last_work_at"]
                    - timedelta(seconds=interruption["duration_seconds"])
                )
                if active_idle > WORK_SESSION_GAP:
                    close_current(current["last_work_at"], "inactivity")
                    start_session(activity, occurred_at)
                    continue
            if transition == "split":
                close_current(occurred_at, "workspace_changed")
                start_session(activity, occurred_at)
                continue
            confirm_pending_passive()
            if transition == "promote":
                promote_workspace(workspace, occurred_at)
            current["activities"].append(activity)
            current["last_work_at"] = occurred_at
            continue

        if activity_type == "app_activated":
            if (
                current is not None
                and (
                    current["pending_interruption"] is not None
                    or occurred_at - current["last_work_at"]
                    <= WEAK_CONTEXT_WINDOW
                )
            ):
                current["pending_passive"].append(activity)

    if current is not None:
        pending = current["pending_interruption"]
        if pending is not None:
            interruption_end = pending["ended_at"] or pending["started_at"]
            current["interruptions"].append(
                completed_interruption(pending, interruption_end)
            )
            close_current(interruption_end, pending["type"])
            current = None
    if current is not None:
        current_day = (now or datetime.now().astimezone()).date().isoformat()
        if trace["date"] != current_day:
            reason = "day_boundary"
        elif now is not None and now - current["last_work_at"] <= WORK_SESSION_GAP:
            reason = "open"
        elif now is None:
            reason = "open"
        else:
            reason = "inactivity"
        close_current(current["last_work_at"], reason)

    passive_activities = [
        activity
        for activity in activities
        if id(activity) not in assigned_ids
        and activity["type"]
        not in {
            "screen_locked",
            "screen_unlocked",
            "system_sleep",
            "system_wake",
        }
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
    passive_sessions = [
        _session_from_activities(group, f"passive-{index}", trace_zone)
        for index, group in enumerate(passive_groups, start=1)
    ]
    return work_sessions, passive_sessions


def _passive_sessions(trace: dict[str, Any]) -> list[dict[str, Any]]:
    if "passive_sessions" in trace:
        return trace["passive_sessions"]
    return reconstruct_session_views(trace)[1]


def _displayed_sessions(trace: dict[str, Any]) -> list[dict[str, Any]]:
    if "work_sessions" in trace:
        return trace["work_sessions"]
    return reconstruct_session_views(trace)[0]


def _session_has_recent_strong_activity(
    session: dict[str, Any],
    now: datetime,
) -> bool:
    if "end_reason" in session:
        return session["end_reason"] == "open"
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
            workspace = _activity_workspace(activity)
            activities_by_minute.setdefault(
                (minute, workspace), []
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

"""Build a readable day view from durable activity rows."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any
from urllib.parse import urlsplit

from .analysis.timeline import (
    IGNORED_APP_NAMES_FOR_RENDERING,
    _activity_workspace,
    _display_file_path,
    _display_time,
    _displayed_sessions,
    _is_weak_workspace,
)
from .trace_store import TraceStore


TERMINAL_LABEL_ORDER = ("test", "git", "pulse", "erreur")
SummaryFact = str | tuple[str, list[str]]


def _file_summary_fact(
    categories: list[tuple[str, list[str]]],
) -> SummaryFact | None:
    categories = [(label, paths) for label, paths in categories if paths]
    if not categories:
        return None
    if len(categories) == 1 and len(categories[0][1]) <= 3:
        label, paths = categories[0]
        return f"Fichiers {label.lower()} : {', '.join(paths)}"

    details = []
    for label, paths in categories:
        displayed = paths[:3]
        remaining = len(paths) - len(displayed)
        values = displayed + ([f"+{remaining} autres"] if remaining else [])
        details.append(f"{label} : {', '.join(values)}")
    return ("Fichiers :", details)


def build_session_summary(
    session: dict[str, Any],
    project_workspaces: set[str],
    *,
    include_projects: bool = True,
) -> list[SummaryFact]:
    project_sequence: list[str] = []
    projects: list[str] = []
    created_files: list[str] = []
    modified_files: list[str] = []
    deleted_files: list[str] = []
    passed_tests: list[str] = []
    failed_tests: list[str] = []
    commit_messages: list[str] = []
    git_commit_observed = False
    git_push_observed = False
    errors: list[str] = []

    for activity in session["activities"]:
        details = activity.get("details", {})
        workspace = _activity_workspace(activity)
        workspace_is_useful = (
            activity["type"] != "terminal_finished"
            or bool(_useful_command_lines(details.get("command")))
        )
        if workspace_is_useful and workspace in project_workspaces:
            project = Path(workspace).name
            if not project_sequence or project_sequence[-1] != project:
                project_sequence.append(project)
            if project not in projects:
                projects.append(project)

        if activity["type"] == "file_changed":
            path = details.get("path")
            event = details.get("event", details.get("change"))
            if path and event:
                display_path = _display_file_path(path, details.get("workspace"))
                target = {
                    "created": created_files,
                    "modified": modified_files,
                    "deleted": deleted_files,
                }.get(event)
                if target is not None and display_path not in target:
                    target.append(display_path)

        if activity["type"] != "terminal_finished":
            continue
        command = details.get("command")
        command_lines = _useful_command_lines(command)
        labels = _terminal_labels(activity)
        exit_code = details.get("exit_code")
        if "test" in labels:
            target = passed_tests if exit_code == 0 else failed_tests
            for line in command_lines:
                if line not in target:
                    target.append(line)
        for line in command_lines:
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            if parts[:2] == ["git", "commit"]:
                git_commit_observed = True
                if "-m" in parts and parts.index("-m") + 1 < len(parts):
                    message = parts[parts.index("-m") + 1]
                    if message not in commit_messages:
                        commit_messages.append(message)
            elif parts[:2] == ["git", "push"]:
                git_push_observed = True
        if (
            isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and exit_code != 0
        ):
            for line in command_lines:
                if line not in errors:
                    errors.append(line)

    facts = []
    if include_projects and projects:
        label = "Projet" if len(projects) == 1 else "Projets"
        project_fact = f"{label} : {', '.join(projects)}"
        if len(project_sequence) > 1:
            project_fact += (
                f" ; Changement de projet : {' → '.join(project_sequence)}"
            )
        facts.append(project_fact)

    file_fact = _file_summary_fact(
        [
            ("Créés", created_files),
            ("Modifiés", modified_files),
            ("Supprimés", deleted_files),
        ]
    )
    if file_fact:
        facts.append(file_fact)

    test_parts = []
    if passed_tests:
        test_parts.append(f"Tests passés : {', '.join(passed_tests[:3])}")
    if failed_tests:
        test_parts.append(f"Tests échoués : {', '.join(failed_tests[:3])}")
    if test_parts:
        facts.append(" ; ".join(test_parts))
    if len(commit_messages) > 1:
        facts.append(("Git :", commit_messages[:3]))
    elif commit_messages:
        facts.append(f"Git : {commit_messages[0]}")
    elif git_commit_observed and git_push_observed:
        facts.append("Git : commit + push")
    elif git_commit_observed:
        facts.append("Git : commit")
    elif git_push_observed:
        facts.append("Git : push")
    if errors:
        facts.append(f"Erreurs terminal : {', '.join(errors[:3])}")
    return facts[:5]


def _session_project_summaries(
    session: dict[str, Any],
    project_workspaces: set[str],
) -> list[tuple[str, list[SummaryFact]]]:
    grouped_activities: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    active_workspace = None
    for activity in session["activities"]:
        workspace = _activity_workspace(activity)
        if workspace in project_workspaces:
            active_workspace = workspace
            grouped_activities.setdefault(workspace, [])
        if active_workspace:
            grouped_activities[active_workspace].append(activity)

    if not grouped_activities:
        return []

    summaries = []
    for workspace, activities in grouped_activities.items():
        facts = build_session_summary(
            {"activities": activities},
            {workspace},
            include_projects=False,
        )
        if facts:
            summaries.append((Path(workspace).name, facts))
    return summaries


def _is_test_command(line: str) -> bool:
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


def _is_pulse_inspection_command(line: str) -> bool:
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


def _is_pasted_prompt_command(command: str) -> bool:
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


def _useful_command_lines(command: Any) -> list[str]:
    if not isinstance(command, str):
        return []
    if _is_pasted_prompt_command(command):
        return []
    return [
        line.strip()
        for line in command.splitlines()
        if line.strip() and not _is_pulse_inspection_command(line.strip())
    ]


def _terminal_labels(activity: dict[str, Any]) -> list[str]:
    details = activity.get("details", {})
    command = details.get("command")
    command_lines = [
        " ".join(line.split()) for line in _useful_command_lines(command)
    ]
    if not command_lines:
        return []
    labels: set[str] = set()
    for line in command_lines:
        if _is_test_command(line):
            labels.add("test")
        if any(
            line == prefix or line.startswith(f"{prefix} ")
            for prefix in ("git commit", "git push", "git pull", "git status")
        ):
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


def _useful_activity_description(activity: dict[str, Any]) -> str:
    details = activity.get("details", {})
    if activity["type"] == "terminal_finished":
        command_lines = _useful_command_lines(details.get("command"))
        return command_lines[-1] if command_lines else activity["summary"]
    if activity["type"] == "file_changed":
        event = details.get("event", details.get("change", "changed"))
        path = details.get("path", "")
        return (
            f"{str(event).capitalize()} "
            f"{_display_file_path(path, details.get('workspace'))}"
        )
    return activity["summary"]


def build_current_state(trace: dict[str, Any]) -> dict[str, Any]:
    displayed_sessions = _displayed_sessions(trace)
    current_session = displayed_sessions[-1] if displayed_sessions else None
    recent_files = []
    seen_paths: set[str] = set()

    if current_session:
        for activity in reversed(current_session["activities"]):
            if activity["type"] != "file_changed":
                continue
            details = activity.get("details", {})
            path = details.get("path")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            recent_files.append(
                {
                    "event": details.get("event", details.get("change", "changed")),
                    "path": _display_file_path(path, details.get("workspace")),
                }
            )
            if len(recent_files) == 5:
                break

    workspace = None
    last_app = None
    last_command = None
    last_useful_activity = None
    for session in trace["sessions"]:
        for activity in session["activities"]:
            details = activity.get("details", {})
            if activity["type"] == "app_activated":
                app = details.get("app")
                if app and app not in IGNORED_APP_NAMES_FOR_RENDERING:
                    last_app = app
            else:
                useful_activity = (
                    activity["type"] != "terminal_finished"
                    or bool(_useful_command_lines(details.get("command")))
                )
                if useful_activity:
                    last_useful_activity = activity
                    activity_workspace = _activity_workspace(activity)
                    if (
                        activity_workspace
                        and not _is_weak_workspace(activity_workspace)
                    ):
                        workspace = activity_workspace
            if activity["type"] == "terminal_finished":
                command_lines = [
                    line.strip()
                    for line in str(details.get("command", "")).splitlines()
                    if line.strip()
                ]
                if command_lines:
                    last_command = command_lines[-1]

    return {
        "project": Path(workspace).name if workspace else "Non détecté",
        "workspace": workspace or "Non détecté",
        "app": last_app or "Non détectée",
        "command": last_command or "Non détectée",
        "recent_files": recent_files,
        "session_started_at": (
            _display_time(current_session["started_at"])
            if current_session
            else "Non détectée"
        ),
        "last_activity_type": (
            last_useful_activity["type"] if last_useful_activity else None
        ),
        "last_activity_description": (
            _useful_activity_description(last_useful_activity)
            if last_useful_activity
            else None
        ),
    }


def _git_local_summary(workspace: str) -> str | None:
    if not Path(workspace).is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", workspace, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    counts = {"modified": 0, "untracked": 0, "deleted": 0}
    for line in result.stdout.splitlines():
        if len(line) < 2 or line.startswith("!!"):
            continue
        status = line[:2]
        if status == "??":
            counts["untracked"] += 1
        elif "D" in status:
            counts["deleted"] += 1
        else:
            counts["modified"] += 1
    if not any(counts.values()):
        return "propre"

    parts = []
    labels = (
        ("modified", "fichier modifié", "fichiers modifiés"),
        ("untracked", "fichier non suivi", "fichiers non suivis"),
        ("deleted", "fichier supprimé", "fichiers supprimés"),
    )
    for key, singular, plural in labels:
        count = counts[key]
        if count:
            parts.append(f"{count} {singular if count == 1 else plural}")
    return ", ".join(parts)


def build_resume(trace: dict[str, Any]) -> list[str]:
    current = build_current_state(trace)
    git_local = (
        _git_local_summary(current["workspace"])
        if current["workspace"] != "Non détecté"
        else None
    )
    last_test = None
    last_commit = None
    last_commit_at = None
    last_push_at = None
    last_error = None
    last_error_at = None
    last_file_at = None
    last_test_succeeded = None
    last_successful_test_at = None

    for session in trace["sessions"]:
        for activity in session["activities"]:
            if activity["type"] == "file_changed":
                last_file_at = activity["occurred_at"]
            if activity["type"] != "terminal_finished":
                continue
            details = activity.get("details", {})
            command = details.get("command")
            command_lines = _useful_command_lines(command)
            if not command_lines:
                continue
            occurred_at = activity["occurred_at"]
            exit_code = details.get("exit_code")
            test_lines = [line for line in command_lines if _is_test_command(line)]
            if test_lines:
                status = "OK" if exit_code == 0 else f"Échec ({exit_code})"
                last_test = f"{test_lines[-1]} — {status}"
                last_test_succeeded = exit_code == 0
                if exit_code == 0:
                    last_successful_test_at = occurred_at
            for line in command_lines:
                try:
                    parts = shlex.split(line)
                except ValueError:
                    parts = line.split()
                if parts[:2] == ["git", "commit"]:
                    last_commit = "commit"
                    if "-m" in parts and parts.index("-m") + 1 < len(parts):
                        last_commit = parts[parts.index("-m") + 1]
                    last_commit_at = occurred_at
                elif parts[:2] == ["git", "push"]:
                    last_push_at = occurred_at
            if (
                isinstance(exit_code, int)
                and not isinstance(exit_code, bool)
                and exit_code != 0
                and command_lines
            ):
                error_command = test_lines[-1] if test_lines else command_lines[-1]
                last_error = f"{error_command} — code {exit_code}"
                last_error_at = occurred_at

    show_error = last_error and (
        not last_successful_test_at
        or (last_error_at is not None and last_error_at > last_successful_test_at)
    )
    commit_pushed = bool(
        last_commit
        and last_commit_at
        and last_push_at
        and last_push_at >= last_commit_at
    )
    state_parts = []
    files_after_successful_test = bool(
        last_file_at
        and last_successful_test_at
        and last_file_at > last_successful_test_at
    )
    files_after_push = bool(
        last_file_at and last_push_at and last_file_at > last_push_at
    )
    changes_committed_and_pushed = bool(
        commit_pushed
        and last_file_at
        and last_commit_at
        and last_commit_at >= last_file_at
    )
    if last_test_succeeded is False:
        state_parts.append("tests échoués")
    elif show_error:
        state_parts.append("erreur récente")
    elif files_after_successful_test:
        if changes_committed_and_pushed and git_local == "propre":
            state_parts.append(
                "push observé, aucun test local observé depuis les modifications"
            )
        else:
            state_parts.append("activité en cours, test non relancé")
    else:
        if last_test_succeeded:
            state_parts.append("tests OK")
        if files_after_push:
            state_parts.append("modifications non push")
        elif commit_pushed:
            state_parts.append("dernier commit poussé")

    facts = []
    if state_parts:
        facts.append(f"État : {', '.join(state_parts)}")
    if git_local:
        facts.append(f"État Git local : {git_local}")
    if current["project"] != "Non détecté":
        facts.append(f"Dernier projet observé : {current['project']}")
    if current["last_activity_type"]:
        facts.append(
            "Dernier signal utile observé : "
            f"{current['last_activity_type']} — "
            f"{current['last_activity_description']}"
        )
    if current["recent_files"]:
        facts.append(
            "Derniers fichiers observés : "
            + ", ".join(item["path"] for item in current["recent_files"][:3])
        )
    if last_test:
        facts.append(f"Dernier test local observé : {last_test}")
    if last_commit or last_push_at:
        git_value = last_commit or "push"
        if (
            last_commit
            and last_commit_at
            and last_push_at
            and last_push_at >= last_commit_at
        ):
            git_value = f"{last_commit} — push"
        facts.append(f"Dernière commande Git observée : {git_value}")
    if show_error:
        if len(facts) >= 7:
            files_index = next(
                (
                    index
                    for index, fact in enumerate(facts)
                    if fact.startswith("Derniers fichiers observés :")
                ),
                None,
            )
            if files_index is not None:
                facts.pop(files_index)
        facts.append(f"Erreur terminal récente : {last_error}")
    return facts[:7]


def build_daily_summary(trace: dict[str, Any]) -> dict[str, Any]:
    app_counts: dict[str, int] = {}
    workspace_order: list[str] = []
    workspace_counts: dict[str, int] = {}
    explicit_file_workspaces: set[str] = set()
    terminal_count = 0
    terminal_label_counts = {label: 0 for label in TERMINAL_LABEL_ORDER}
    file_paths: set[str] = set()

    for session in trace["sessions"]:
        for activity in session["activities"]:
            details = activity.get("details", {})
            workspace = _activity_workspace(activity)
            workspace_is_useful = (
                activity["type"] != "terminal_finished"
                or bool(_useful_command_lines(details.get("command")))
            )
            if workspace and workspace_is_useful:
                if workspace not in workspace_counts:
                    workspace_order.append(workspace)
                    workspace_counts[workspace] = 0
                workspace_counts[workspace] += 1
                if activity["type"] == "file_changed" and details.get("workspace"):
                    explicit_file_workspaces.add(workspace)
            if activity["type"] == "terminal_finished":
                terminal_count += 1
                for label in _terminal_labels(activity):
                    terminal_label_counts[label] += 1
            elif activity["type"] == "file_changed" and details.get("path"):
                file_paths.add(details["path"])
            elif activity["type"] == "app_activated" and details.get("app"):
                app = details["app"]
                if app not in IGNORED_APP_NAMES_FOR_RENDERING:
                    app_counts[app] = app_counts.get(app, 0) + 1

    workspaces = [
        workspace
        for workspace in workspace_order
        if not _is_weak_workspace(workspace)
        and (
            workspace in explicit_file_workspaces
            or workspace_counts[workspace] >= 2
            or (Path(workspace) / ".git").exists()
        )
    ]

    return {
        "session_count": len(_displayed_sessions(trace)),
        "activity_count": trace["activity_count"],
        "terminal_count": terminal_count,
        "test_count": terminal_label_counts["test"],
        "git_count": terminal_label_counts["git"],
        "error_count": terminal_label_counts["erreur"],
        "pulse_count": terminal_label_counts["pulse"],
        "distinct_file_count": len(file_paths),
        "apps": app_counts,
        "workspaces": workspaces,
    }


def primary_workspace(trace: dict[str, Any]) -> str | None:
    counts: dict[str, int] = {}
    for session in trace["sessions"]:
        for activity in session["activities"]:
            workspace = activity.get("details", {}).get("workspace")
            if workspace and not _is_weak_workspace(workspace):
                counts[workspace] = counts.get(workspace, 0) + 1
    return max(counts, key=counts.get) if counts else None


def render_daily_trace_markdown(trace: dict[str, Any]) -> str:
    """Render a daily trace as Markdown through the renderer package."""
    from .renderers.markdown import render_daily_trace_markdown as render

    return render(trace)


def render_daily_trace_html(
    trace: dict[str, Any],
    system_status: dict[str, Any] | None = None,
    trace_json_url: str = "/trace/today",
    trace_markdown_url: str = "/trace/today.md",
    archive_mode: bool = False,
) -> str:
    """Render a daily trace as HTML through the renderer package."""
    from .renderers.html import render_daily_trace_html as render

    return render(
        trace,
        system_status=system_status,
        trace_json_url=trace_json_url,
        trace_markdown_url=trace_markdown_url,
        archive_mode=archive_mode,
    )


def build_daily_trace(
    store: TraceStore,
    day: date | None = None,
    local_timezone: tzinfo | None = None,
) -> dict[str, Any]:
    zone = local_timezone or datetime.now().astimezone().tzinfo or timezone.utc
    selected_day = day or datetime.now(zone).date()
    start = datetime.combine(selected_day, time.min, zone)
    end = start + timedelta(days=1)
    activities = store.activities_between(start, end)

    grouped: OrderedDict[str, list] = OrderedDict()
    for stored in activities:
        grouped.setdefault(stored.session_id, []).append(stored)

    sessions = []
    for session_id, items in grouped.items():
        sessions.append(
            {
                "id": session_id,
                "started_at": items[0].activity.occurred_at.astimezone(zone).isoformat(),
                "ended_at": items[-1].activity.occurred_at.astimezone(zone).isoformat(),
                "activity_count": len(items),
                "activities": [
                    {
                        "id": item.id,
                        "type": item.activity.activity_type,
                        "occurred_at": item.activity.occurred_at.astimezone(zone).isoformat(),
                        "source": item.activity.source,
                        "summary": item.activity.summary,
                        "details": item.activity.details,
                    }
                    for item in items
                ],
            }
        )

    merged_sessions = []
    for session in sessions:
        if (
            merged_sessions
            and datetime.fromisoformat(session["started_at"])
            <= datetime.fromisoformat(merged_sessions[-1]["ended_at"])
        ):
            previous = merged_sessions[-1]
            previous["activities"] = sorted(
                previous["activities"] + session["activities"],
                key=lambda activity: (activity["occurred_at"], activity["id"]),
            )
            previous["started_at"] = previous["activities"][0]["occurred_at"]
            previous["ended_at"] = previous["activities"][-1]["occurred_at"]
            previous["activity_count"] = len(previous["activities"])
        else:
            merged_sessions.append(session)

    return {
        "date": selected_day.isoformat(),
        "timezone": str(zone),
        "activity_count": len(activities),
        "session_count": len(merged_sessions),
        "sessions": merged_sessions,
    }


def build_available_days(
    store: TraceStore,
    local_timezone: tzinfo | None = None,
) -> dict[str, list[dict[str, Any]]]:
    zone = local_timezone or datetime.now().astimezone().tzinfo or timezone.utc
    days = []
    for day in store.activity_dates(zone):
        trace = build_daily_trace(store, day, zone)
        summary = build_daily_summary(trace)
        projects = [
            Path(workspace).name for workspace in summary["workspaces"]
        ]
        activities = [
            activity
            for session in trace["sessions"]
            for activity in session["activities"]
        ]
        project_summaries = []
        for workspace in summary["workspaces"]:
            project_activities = [
                activity
                for activity in activities
                if _activity_workspace(activity) == workspace
            ]
            if project_activities:
                project_summaries.append(
                    {
                        "project": Path(workspace).name,
                        "workspace": workspace,
                        "event_count": len(project_activities),
                        "summary": _build_compact_activity_summary(
                            project_activities,
                            include_event_count=False,
                        ),
                    }
                )
        days.append(
            {
                "date": day.isoformat(),
                "event_count": trace["activity_count"],
                "session_count": summary["session_count"],
                "projects": projects,
                "summary": _build_short_day_summary(trace, projects),
                "project_summaries": project_summaries,
            }
        )
    return {"days": days}


def _build_short_day_summary(
    trace: dict[str, Any],
    projects: list[str],
) -> list[str]:
    activities = [
        activity
        for session in trace["sessions"]
        for activity in session["activities"]
    ]
    prefix = ", ".join(projects) if projects else "Activité locale"
    summary = _build_compact_activity_summary(
        activities,
        include_event_count=True,
    )
    primary = f"{prefix} — {summary[0]}"
    summary[0] = (
        primary
        if len(primary) <= 160
        else f"{primary[:159].rstrip()}…"
    )
    return summary


def _build_compact_activity_summary(
    activities: list[dict[str, Any]],
    *,
    include_event_count: bool,
) -> list[str]:
    def shorten(value: str, limit: int = 160) -> str:
        return value if len(value) <= limit else f"{value[:limit - 1].rstrip()}…"

    file_paths = {"created": set(), "modified": set(), "deleted": set()}
    folder_counts: dict[str, int] = {}
    folder_order: list[str] = []
    commit_count = 0
    saw_push = False
    saw_test = False
    failed_test = False
    saw_error = False

    for activity in activities:
        details = activity.get("details", {})
        if activity["type"] == "file_changed":
            event = details.get("event", details.get("change"))
            path = details.get("path")
            if event in file_paths and path:
                file_paths[event].add(path)
                display_path = Path(
                    _display_file_path(path, details.get("workspace"))
                )
                folder = (
                    display_path.parts[0]
                    if not display_path.is_absolute()
                    and len(display_path.parts) > 1
                    else "racine"
                )
                if folder not in folder_counts:
                    folder_counts[folder] = 0
                    folder_order.append(folder)
                folder_counts[folder] += 1
            continue

        if activity["type"] != "terminal_finished":
            continue
        command = details.get("command")
        command_lines = _useful_command_lines(command)
        if not command_lines:
            continue
        exit_code = details.get("exit_code")
        test_lines = [line for line in command_lines if _is_test_command(line)]
        if test_lines:
            saw_test = True
            failed_test = failed_test or exit_code != 0
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            saw_error = saw_error or exit_code != 0
        for line in command_lines:
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            if parts[:2] == ["git", "commit"]:
                commit_count += 1
            elif parts[:2] == ["git", "push"]:
                saw_push = True

    primary_facts = []
    if commit_count:
        primary_facts.append(
            f"{commit_count} {'commit' if commit_count == 1 else 'commits'}"
        )

    file_counts = {
        event: len(paths) for event, paths in file_paths.items() if paths
    }
    total_files = len(set().union(*file_paths.values()))
    if total_files:
        if len(file_counts) == 1:
            event, count = next(iter(file_counts.items()))
            labels = {
                "created": ("fichier créé", "fichiers créés"),
                "modified": ("fichier modifié", "fichiers modifiés"),
                "deleted": ("fichier supprimé", "fichiers supprimés"),
            }
            singular, plural = labels[event]
            primary_facts.append(f"{count} {singular if count == 1 else plural}")
        else:
            labels = {
                "created": ("créé", "créés"),
                "modified": ("modifié", "modifiés"),
                "deleted": ("supprimé", "supprimés"),
            }
            breakdown = ", ".join(
                f"{file_counts[event]} "
                f"{labels[event][0] if file_counts[event] == 1 else labels[event][1]}"
                for event in ("created", "modified", "deleted")
                if event in file_counts
            )
            primary_facts.append(
                f"{total_files} fichiers touchés ({breakdown})"
            )

        folders = sorted(
            folder_counts,
            key=lambda folder: (-folder_counts[folder], folder_order.index(folder)),
        )[:3]
        primary_facts.append(f"dossiers principaux : {', '.join(folders)}")

    if not primary_facts:
        primary_facts.append("activité enregistrée")

    secondary = []
    if saw_test:
        secondary.append("Tests échoués" if failed_test else "Tests OK")
    if commit_count and saw_push:
        secondary.append("Git : commit + push")
    elif commit_count:
        secondary.append("Git : commit")
    elif saw_push:
        secondary.append("Git : push")
    if saw_error:
        secondary.append("Erreurs observées")
    if include_event_count:
        activity_count = len(activities)
        secondary.append(
            f"{activity_count} "
            f"{'événement' if activity_count == 1 else 'événements'}"
        )

    lines = [shorten(" · ".join(primary_facts))]
    if secondary:
        lines.append(shorten(" · ".join(secondary)))
    return lines


def render_available_days_html(
    available_days: dict[str, list[dict[str, Any]]],
) -> str:
    """Render available days as HTML through the renderer package."""
    from .renderers.html import render_available_days_html as render

    return render(available_days)

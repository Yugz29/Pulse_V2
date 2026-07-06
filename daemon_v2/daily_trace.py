"""Build a readable day view from durable activity rows."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
import subprocess
from typing import Any, Literal

from .analysis.projects import (
    activity_project_root,
    activity_workspace,
    is_weak_workspace,
    last_observed_workspace,
    most_frequent_explicit_workspace,
    resolve_project_context,
)
from .analysis.terminal import (
    TERMINAL_LABEL_ORDER,
    is_pasted_prompt_command,
    is_pulse_inspection_command,
    is_test_command,
    parse_git_command,
    terminal_labels,
    useful_command_lines,
)
from .analysis.timeline import (
    IGNORED_APP_NAMES_FOR_RENDERING,
    _display_file_path,
    _display_time,
    _displayed_sessions,
)
from .trace_store import TraceStore


# Temporary private aliases preserve existing internal and renderer imports.
_is_test_command = is_test_command
_is_pulse_inspection_command = is_pulse_inspection_command
_is_pasted_prompt_command = is_pasted_prompt_command
_useful_command_lines = useful_command_lines
_terminal_labels = terminal_labels

SummaryFact = str | tuple[str, list[str]]
ResumeGroup = tuple[str, list[tuple[str, str | list[str]]]]
ResumeFact = str | ResumeGroup
ProjectQualificationMode = Literal["live", "archive"]


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
        workspace = activity_project_root(activity)
        workspace_is_useful = (
            activity["type"] != "terminal_finished"
            or bool(_useful_command_lines(details.get("command")))
        )
        if workspace_is_useful and workspace in project_workspaces:
            project = resolve_project_context(workspace).project_name
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
            git_command = parse_git_command(line)
            if git_command.action == "commit":
                git_commit_observed = True
                if git_command.commit_message is not None:
                    message = git_command.commit_message
                    if message not in commit_messages:
                        commit_messages.append(message)
            elif git_command.action == "push":
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
        workspace = activity_project_root(activity)
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
            summaries.append((resolve_project_context(workspace).project_name, facts))
    return summaries


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

    workspace = last_observed_workspace(trace)
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
            if activity["type"] == "terminal_finished":
                command_lines = [
                    line.strip()
                    for line in str(details.get("command", "")).splitlines()
                    if line.strip()
                ]
                if command_lines:
                    last_command = command_lines[-1]

    return {
        "project": (
            resolve_project_context(workspace).project_name
            if workspace
            else "Non détecté"
        ),
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


def _git_local_snapshot(
    workspace: str,
    day: str,
) -> dict[str, str | list[str]] | None:
    if not Path(workspace).is_dir():
        return None
    try:
        status_result = subprocess.run(
            ["git", "-C", workspace, "status", "--short", "--branch"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if status_result.returncode != 0:
        return None

    status_lines = status_result.stdout.splitlines()
    branch = None
    if status_lines and status_lines[0].startswith("## "):
        branch_value = status_lines.pop(0)[3:].split("...", 1)[0]
        if branch_value and branch_value != "HEAD (no branch)":
            branch = branch_value

    counts = {"modified": 0, "untracked": 0, "deleted": 0}
    for line in status_lines:
        if len(line) < 2 or line.startswith("!!"):
            continue
        status = line[:2]
        if status == "??":
            counts["untracked"] += 1
        elif "D" in status:
            counts["deleted"] += 1
        else:
            counts["modified"] += 1
    if any(counts.values()):
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
        status_summary = ", ".join(parts)
    else:
        status_summary = "propre"

    snapshot = {"status": status_summary}
    if branch:
        snapshot["branch"] = branch
    next_day = (
        date.fromisoformat(day) + timedelta(days=1)
    ).isoformat()
    try:
        log_result = subprocess.run(
            [
                "git",
                "-C",
                workspace,
                "log",
                f"--since={day}T00:00:00",
                f"--until={next_day}T00:00:00",
                "--pretty=%s",
            ],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return snapshot
    if log_result.returncode == 0:
        commits_today = []
        for message in log_result.stdout.splitlines():
            if message and message not in commits_today:
                commits_today.append(message)
        if commits_today:
            snapshot["commit"] = commits_today[0]
            snapshot["commits_today"] = commits_today
            return snapshot
    try:
        last_commit_result = subprocess.run(
            ["git", "-C", workspace, "log", "-1", "--pretty=%s"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return snapshot
    last_commit = last_commit_result.stdout.strip()
    if last_commit_result.returncode == 0 and last_commit:
        snapshot["commit"] = last_commit.splitlines()[0]
    return snapshot


def build_resume(trace: dict[str, Any]) -> list[ResumeFact]:
    current = build_current_state(trace)
    git_snapshot = (
        _git_local_snapshot(current["workspace"], trace["date"])
        if current["workspace"] != "Non détecté"
        else None
    )
    git_local = git_snapshot["status"] if git_snapshot else None
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
                git_command = parse_git_command(line)
                if git_command.action == "commit":
                    last_commit = (
                        git_command.commit_message
                        if git_command.commit_message is not None
                        else "commit"
                    )
                    last_commit_at = occurred_at
                elif git_command.action == "push":
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

    facts: list[ResumeFact] = []
    if state_parts:
        facts.append(f"État : {', '.join(state_parts)}")
    git_group = None
    if git_snapshot:
        git_rows: list[tuple[str, str | list[str]]] = [
            ("État local", str(git_snapshot["status"]))
        ]
        if git_snapshot.get("branch"):
            git_rows.append(("Branche", str(git_snapshot["branch"])))
        commits_today = git_snapshot.get("commits_today")
        if isinstance(commits_today, list) and commits_today:
            displayed_commits = commits_today[:5]
            hidden_count = len(commits_today) - len(displayed_commits)
            if hidden_count:
                displayed_commits.append(
                    f"+ {hidden_count} autres commits aujourd’hui"
                )
            git_rows.append(("Commits aujourd’hui", displayed_commits))
        elif git_snapshot.get("commit"):
            git_rows.append(
                ("Dernier commit", str(git_snapshot["commit"]))
            )
        git_group = ("Git", git_rows)
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
    if show_error:
        if len(facts) >= 9:
            files_index = next(
                (
                    index
                    for index, fact in enumerate(facts)
                    if isinstance(fact, str)
                    and fact.startswith("Derniers fichiers observés :")
                ),
                None,
            )
            if files_index is not None:
                facts.pop(files_index)
        facts.append(f"Erreur terminal récente : {last_error}")
    if git_group:
        facts.append(git_group)
    return facts[:9]


def build_daily_summary(
    trace: dict[str, Any],
    *,
    project_mode: ProjectQualificationMode = "live",
) -> dict[str, Any]:
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
            workspace = activity_project_root(activity)
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
        if not is_weak_workspace(workspace)
        and (
            workspace in explicit_file_workspaces
            or workspace_counts[workspace] >= 2
            or (
                project_mode == "live"
                and (Path(workspace) / ".git").exists()
            )
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
    return most_frequent_explicit_workspace(trace)


def render_daily_trace_markdown(
    trace: dict[str, Any],
    archive_mode: bool = False,
) -> str:
    """Render a daily trace as Markdown through the renderer package."""
    from .renderers.markdown import render_daily_trace_markdown as render

    return render(trace, archive_mode=archive_mode)


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
        summary = build_daily_summary(trace, project_mode="archive")
        projects = [
            resolve_project_context(workspace).project_name
            for workspace in summary["workspaces"]
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
                if activity_project_root(activity) == workspace
            ]
            if project_activities:
                project_summaries.append(
                    {
                        "project": resolve_project_context(workspace).project_name,
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
            git_command = parse_git_command(line)
            if git_command.action == "commit":
                commit_count += 1
            elif git_command.action == "push":
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

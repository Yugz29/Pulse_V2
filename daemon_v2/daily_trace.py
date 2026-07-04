"""Build a readable day view from durable activity rows."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from html import escape
from pathlib import Path
import shlex
import subprocess
from typing import Any

from .trace_store import TraceStore


IGNORED_APP_NAMES_FOR_RENDERING = {"CleanMyMac Menu", "Finder", "loginwindow"}
TERMINAL_LABEL_ORDER = ("test", "git", "pulse", "erreur")
SummaryFact = str | tuple[str, list[str]]


def _markdown_text(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    for character in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(character, f"\\{character}")
    return text


def _display_time(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%H:%M")


def _markdown_inline_code(value: str) -> str:
    fence = "`"
    while fence in value:
        fence += "`"
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{padding}{value}{padding}{fence}"


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


def _markdown_summary_facts(facts: list[SummaryFact]) -> list[str]:
    lines = []
    for fact in facts:
        if isinstance(fact, tuple):
            label, details = fact
            lines.append(f"- {_markdown_text(label)}")
            lines.extend(f"  - {_markdown_text(detail)}" for detail in details)
        else:
            lines.append(f"- {_markdown_text(fact)}")
    return lines


def _html_summary_facts(facts: list[SummaryFact]) -> str:
    items = []
    for fact in facts:
        if isinstance(fact, tuple):
            label, details = fact
            nested = "".join(f"<li>{escape(detail)}</li>" for detail in details)
            items.append(f"<li>{escape(label)}<ul>{nested}</ul></li>")
        else:
            items.append(f"<li>{escape(fact)}</li>")
    return f"<ul>{''.join(items)}</ul>"


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
    git_facts: list[str] = []
    errors: list[str] = []

    for activity in session["activities"]:
        details = activity.get("details", {})
        workspace = _activity_workspace(activity)
        if workspace in project_workspaces:
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
        command_lines = (
            [line.strip() for line in command.splitlines() if line.strip()]
            if isinstance(command, str)
            else []
        )
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
                fact = "commit"
                if "-m" in parts and parts.index("-m") + 1 < len(parts):
                    fact = f"commit — {parts[parts.index('-m') + 1]}"
                if fact not in git_facts:
                    git_facts.append(fact)
            elif parts[:2] == ["git", "push"] and "push" not in git_facts:
                git_facts.append("push")
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
    if git_facts:
        facts.append(f"Git : {' ; '.join(git_facts[:3])}")
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
        summaries.append((Path(workspace).name, facts))
    return summaries


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


def _terminal_labels(activity: dict[str, Any]) -> list[str]:
    details = activity.get("details", {})
    command = details.get("command")
    command_lines = (
        [" ".join(line.split()) for line in command.splitlines() if line.strip()]
        if isinstance(command, str)
        else []
    )
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
        command_lines = [
            line.strip()
            for line in str(details.get("command", "")).splitlines()
            if line.strip()
        ]
        return command_lines[-1] if command_lines else activity["summary"]
    if activity["type"] == "file_changed":
        event = details.get("event", details.get("change", "changed"))
        path = details.get("path", "")
        return (
            f"{str(event).capitalize()} "
            f"{_display_file_path(path, details.get('workspace'))}"
        )
    return activity["summary"]


def _activity_workspace(activity: dict[str, Any]) -> str | None:
    details = activity.get("details", {})
    if details.get("workspace"):
        return details["workspace"]
    if activity["type"] == "terminal_finished" and details.get("cwd"):
        return details["cwd"]
    return None


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
                last_useful_activity = activity
                activity_workspace = _activity_workspace(activity)
                if activity_workspace:
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
            command_lines = (
                [line.strip() for line in command.splitlines() if line.strip()]
                if isinstance(command, str)
                else []
            )
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
    if last_test_succeeded is False:
        state_parts.append("tests échoués")
    elif show_error:
        state_parts.append("erreur récente")
    elif files_after_successful_test:
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
        facts.append(f"Git local : {git_local}")
    if current["project"] != "Non détecté":
        facts.append(f"Projet courant : {current['project']}")
    if current["last_activity_type"]:
        facts.append(
            "Dernière activité utile : "
            f"{current['last_activity_type']} — "
            f"{current['last_activity_description']}"
        )
    if current["recent_files"]:
        facts.append(
            "Derniers fichiers : "
            + ", ".join(item["path"] for item in current["recent_files"][:3])
        )
    if last_test:
        facts.append(f"Dernier test : {last_test}")
    if last_commit or last_push_at:
        git_value = last_commit or "push"
        if (
            last_commit
            and last_commit_at
            and last_push_at
            and last_push_at >= last_commit_at
        ):
            git_value = f"{last_commit} — push"
        facts.append(f"Dernier Git : {git_value}")
    if show_error:
        if len(facts) >= 7:
            files_index = next(
                (
                    index
                    for index, fact in enumerate(facts)
                    if fact.startswith("Derniers fichiers :")
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
            if workspace:
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
        if workspace in explicit_file_workspaces
        or workspace_counts[workspace] >= 2
        or (Path(workspace) / ".git").exists()
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
            if workspace:
                counts[workspace] = counts.get(workspace, 0) + 1
    return max(counts, key=counts.get) if counts else None


def render_daily_trace_markdown(trace: dict[str, Any]) -> str:
    summary = build_daily_summary(trace)
    current = build_current_state(trace)
    resume = build_resume(trace)
    displayed_sessions = _displayed_sessions(trace)
    apps = [_markdown_text(app) for app, _count in _ranked_apps(summary["apps"])]
    projects = [_markdown_text(Path(path).name) for path in summary["workspaces"]]
    project_workspaces = set(summary["workspaces"])
    last_activity = (
        f"{_markdown_text(current['last_activity_type'])} — "
        f"{_markdown_text(current['last_activity_description'])}"
        if current["last_activity_type"]
        else "Non détectée"
    )
    lines = [
        f"# Trace du {trace['date']}",
        "",
        "## Maintenant",
        f"- Projet probable : {_markdown_text(current['project'])}",
        f"- Workspace : {_markdown_text(current['workspace'])}",
        f"- App active : {_markdown_text(current['app'])}",
        (
            f"- Dernière commande : {_markdown_inline_code(current['command'])}"
            if current["command"] != "Non détectée"
            else "- Dernière commande : Non détectée"
        ),
        "- Fichiers récents :",
    ]
    if current["recent_files"]:
        lines.extend(
            f"  - {str(item['event']).capitalize()} "
            f"{_markdown_inline_code(item['path'])}"
            for item in current["recent_files"]
        )
    else:
        lines.append("  - Aucun")
    lines.extend(
        [
        f"- Session active depuis : {current['session_started_at']}",
        f"- Dernière activité utile : {last_activity}",
        "",
        ]
    )
    if resume:
        lines.extend(
            ["## Reprise"]
            + [f"- {_markdown_text(fact)}" for fact in resume]
            + [""]
        )
    lines.extend(
        [
        "## Aujourd’hui",
        f"- Sessions : {summary['session_count']}",
        f"- Événements : {summary['activity_count']}",
        f"- Commandes terminal : {summary['terminal_count']}",
        f"- Tests : {summary['test_count']}",
        f"- Git : {summary['git_count']}",
        f"- Erreurs : {summary['error_count']}",
        f"- Commandes Pulse : {summary['pulse_count']}",
        f"- Fichiers modifiés : {summary['distinct_file_count']}",
        f"- Projets : {', '.join(projects) if projects else 'Aucun'}",
        f"- Apps principales : {', '.join(apps) if apps else 'Aucune'}",
        "",
        ]
    )
    if not displayed_sessions:
        lines.extend(["_Aucune activité._", ""])
        return "\n".join(lines)

    for index, session in enumerate(displayed_sessions, start=1):
        started_at = _display_time(session["started_at"])
        ended_at = _display_time(session["ended_at"])
        lines.extend([f"## Session {index} — {started_at}–{ended_at}", ""])
        project_summaries = _session_project_summaries(
            session, project_workspaces
        )
        if project_summaries:
            lines.append("### Résumé de session")
            for project, facts in project_summaries:
                lines.append(f"#### {_markdown_text(project)}")
                lines.extend(_markdown_summary_facts(facts))
            lines.append("")
        else:
            session_facts = build_session_summary(session, project_workspaces)
            if session_facts:
                lines.extend(
                    ["### Résumé de session"]
                    + _markdown_summary_facts(session_facts)
                    + [""]
                )

        file_change_groups = _file_change_groups(session)
        app_activation_counts = _app_activation_counts(session)
        rendered_app_activations = False
        rendered_project = None

        for activity in session["activities"]:
            occurred_at = _display_time(activity["occurred_at"])
            activity_type = _markdown_text(activity["type"])
            details = activity.get("details", {})
            command = details.get("command")
            command_lines = (
                [line.strip() for line in command.splitlines() if line.strip()]
                if isinstance(command, str)
                else []
            )
            terminal_labels = (
                _terminal_labels(activity)
                if activity["type"] == "terminal_finished"
                else []
            )
            label_text = "".join(
                f" {_markdown_inline_code(label)}" for label in terminal_labels
            )

            event = details.get("event", details.get("change"))
            path = details.get("path")
            workspace = details.get("workspace")
            duplicate_file = (
                activity["type"] == "file_changed"
                and bool(event and path)
                and id(activity) not in file_change_groups
            )
            activity_workspace = _activity_workspace(activity)
            if (
                not duplicate_file
                and activity_workspace in project_workspaces
                and activity_workspace != rendered_project
            ):
                rendered_project = activity_workspace
                lines.append(f"### {_markdown_text(Path(activity_workspace).name)}")

            if activity["type"] == "app_activated":
                if details.get("app") not in app_activation_counts:
                    continue
                if rendered_app_activations:
                    continue
                rendered_app_activations = True
                apps = [
                    _markdown_text(app)
                    for app, _count in _ranked_apps(app_activation_counts)
                ]
                lines.append(f"- Apps actives : {', '.join(apps)}")
                continue
            elif activity["type"] == "file_changed" and event and path:
                if id(activity) not in file_change_groups:
                    continue
                group = file_change_groups[id(activity)]
                if len(group) > 1:
                    lines.append(
                        f"- {occurred_at} · **{activity_type}** — Fichiers modifiés :"
                    )
                    for item_path, item_event, item_workspace, count in group:
                        suffix = f" ×{count}" if count > 1 else ""
                        display_path = _display_file_path(item_path, item_workspace)
                        lines.append(
                            f"  - {str(item_event).capitalize()} "
                            f"{_markdown_inline_code(display_path)}{suffix}"
                        )
                    workspace = None
                else:
                    count = group[0][3]
                    count_suffix = f" ×{count}" if count > 1 else ""
                    display_path = _display_file_path(path, workspace)
                    lines.append(
                        f"- {occurred_at} · **{activity_type}** — "
                        f"{str(event).capitalize()} "
                        f"{_markdown_inline_code(display_path)}{count_suffix}"
                    )
            elif activity["type"] == "terminal_finished" and len(command_lines) > 1:
                exit_code = details.get("exit_code")
                status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
                lines.append(
                    f"- {occurred_at} · **{activity_type}**{label_text} — "
                    f"Command {status}:"
                )
                for command_line in command_lines:
                    lines.append(f"  - {_markdown_inline_code(command_line)}")
            elif activity["type"] == "terminal_finished" and command_lines:
                exit_code = details.get("exit_code")
                status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
                lines.append(
                    f"- {occurred_at} · **{activity_type}**{label_text} — "
                    f"Command {status}: {_markdown_inline_code(command_lines[0])}"
                )
            else:
                summary = _markdown_text(activity["summary"])
                lines.append(f"- {occurred_at} · **{activity_type}** — {summary}")

            cwd = details.get("cwd")
            if cwd:
                lines.append(f"  - CWD : {_markdown_text(cwd)}")
            if workspace:
                lines.append(f"  - Workspace : {_markdown_text(workspace)}")
        lines.append("")

    return "\n".join(lines)


def render_daily_trace_html(
    trace: dict[str, Any],
    system_status: dict[str, Any] | None = None,
    trace_json_url: str = "/trace/today",
    trace_markdown_url: str = "/trace/today.md",
    archive_mode: bool = False,
) -> str:
    summary = build_daily_summary(trace)
    current = build_current_state(trace) if not archive_mode else None
    resume = build_resume(trace) if not archive_mode else []
    displayed_sessions = _displayed_sessions(trace)
    apps = [escape(str(app)) for app, _count in _ranked_apps(summary["apps"])]
    projects = [
        f'<span title="{escape(path)}">{escape(Path(path).name)}</span>'
        for path in summary["workspaces"]
    ]
    project_workspaces = set(summary["workspaces"])
    last_activity = (
        f"{escape(str(current['last_activity_type']))} — "
        f"{escape(str(current['last_activity_description']))}"
        if current and current["last_activity_type"]
        else "Non détectée"
    )
    recent_files = (
        "<ul>"
        + "".join(
            f"<li>{escape(str(item['event']).capitalize())} "
            f"<code>{escape(item['path'])}</code></li>"
            for item in current["recent_files"]
        )
        + "</ul>"
        if current and current["recent_files"]
        else "Aucun"
    )
    navigation = []
    if archive_mode:
        navigation.append(
            '<a class="nav-main" href="#resume-jour">Résumé du jour</a>'
        )
    else:
        navigation.append(
            '<a class="nav-main" href="#maintenant">Maintenant</a>'
        )
        if resume:
            navigation.append('<a class="nav-main" href="#reprise">Reprise</a>')
        navigation.append(
            '<a class="nav-main" href="#aujourdhui">Aujourd’hui</a>'
        )
    if system_status and not archive_mode:
        navigation.append(
            '<a class="nav-main" href="#etat-systeme">État système</a>'
        )
    for index, session in enumerate(displayed_sessions, start=1):
        navigation.append(
            f'<a class="nav-session" href="#session-{index}">Session {index}</a>'
        )
        for project_index, workspace in enumerate(
            _session_project_sequence(session, project_workspaces), start=1
        ):
            navigation.append(
                f'<a class="nav-project" '
                f'href="#session-{index}-projet-{project_index}">'
                f"{escape(Path(workspace).name)}</a>"
            )
    end_anchor = "timeline-end" if archive_mode else "timeline-live"
    end_label = "Fin du jour" if archive_mode else "Direct"
    navigation.append(
        f'<a class="nav-main nav-live nav-bottom" '
        f'href="#{end_anchor}">{end_label}</a>'
    )
    body = [
        "<!doctype html>",
        '<html lang="fr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        (
            f"<title>Pulse — Journal du {escape(trace['date'])}</title>"
            if archive_mode
            else f"<title>Pulse — {escape(trace['date'])}</title>"
        ),
        """<style>
:root{color-scheme:dark;--bg:#11151a;--panel:#191f26;--panel-soft:#161c22;
--border:#2a333d;--text:#d7dee7;--muted:#8f9aaa;--link:#83a9d8}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:1rem}
body{font:16px/1.6 system-ui,sans-serif;margin:0;padding:2.5rem 2rem 4rem;
background:var(--bg);color:var(--text)}.page-shell{max-width:1180px;margin:0 auto;
display:grid;grid-template-columns:12rem minmax(0,1fr);gap:2rem;align-items:start}
.sidebar{position:sticky;top:1rem;background:var(--panel-soft);border:1px solid var(--border);
border-radius:10px;padding:.9rem}.sidebar h2{font-size:.78rem;text-transform:uppercase;
letter-spacing:.08em;color:var(--muted);margin:0 0 .55rem}.sidebar a{display:block;
padding:.22rem .4rem;border-radius:5px;color:#aebdcb;font-size:.84rem}
.sidebar a:hover{background:#202832;text-decoration:none;color:#dbe5ef}
.sidebar .nav-live{color:#8fc6a1}
.sidebar .nav-bottom{margin-top:.8rem;border-top:1px solid var(--border);padding-top:.6rem}
.nav-session{margin-top:.45rem;font-weight:650;color:#c6d2df!important}
.nav-project{padding-left:1.15rem!important;color:#829bb6!important;font-size:.78rem!important}
header{margin-bottom:2rem}h1{font-size:2rem;letter-spacing:-.025em;margin:0 0 .25rem}
h2{color:#e4eaf1;letter-spacing:-.01em}.meta,.detail{color:var(--muted)}
.current,.resume,.summary,.system,.session{background:var(--panel);border:1px solid var(--border);
border-radius:12px;padding:1.25rem 1.5rem;margin:1.25rem 0;box-shadow:0 8px 24px #0003}
.current{border-top:3px solid #5d8fc4}.resume{border-top:3px solid #8d78b5}
.summary{border-top:3px solid #669b78}
.system{border-top:3px solid #778493;background:var(--panel-soft)}
.session{margin-top:1.5rem}.session h2{font-size:1.1rem;margin:0 0 1rem;color:#cbd5e1}
.current h2,.resume h2,.summary h2,.system h2{font-size:1.2rem;margin:0 0 1rem}
.resume dl{display:grid;grid-template-columns:10rem minmax(0,1fr);gap:.45rem 1rem;margin:0}
.resume dt{font-weight:650;color:#b8a8d3}.resume dd{margin:0;min-width:0;
overflow-wrap:anywhere;color:#cbd3dd}
.current dl,.summary dl,.system dl{display:grid;grid-template-columns:12rem 1fr;
gap:.5rem 1.25rem;margin:0}.current dt,.summary dt,.system dt{font-weight:600;
color:#aeb9c6}.current dd,.summary dd,.system dd{margin:0;min-width:0;
overflow-wrap:anywhere}.timeline{list-style:none;padding:0;margin:0}.event{display:grid;
grid-template-columns:4rem 10rem 1fr;gap:1rem;padding:.85rem .25rem;
border-top:1px solid var(--border)}.event:first-child{border-top:0}.event time{
color:var(--muted);font-variant-numeric:tabular-nums}.type{font-family:ui-monospace,
SFMono-Regular,Menlo,monospace;font-size:.82rem;color:#9daaba;overflow-wrap:anywhere}
.content{min-width:0}.content code,.current code{background:#222a33;color:#dce6f1;
padding:.12rem .35rem;border:1px solid #303b47;border-radius:5px;overflow-wrap:anywhere}
.label{display:inline-block;margin-top:.18rem;border:1px solid transparent;border-radius:999px;
padding:.02rem .4rem;font-size:.72rem;font-weight:650}.label-test{background:#173528;
border-color:#285940;color:#8fd5aa}.label-git{background:#29243f;border-color:#493d70;
color:#b9a7ed}.label-pulse{background:#17333c;border-color:#285663;color:#86c9d8}
.label-erreur{background:#3a2023;border-color:#6d363d;color:#e8a0a7}
.commands{margin:.5rem 0;padding-left:1.35rem}.commands li{margin:.25rem 0}
.project-separator{padding:1rem .25rem .45rem;color:#8fb4dd;font-weight:650;
letter-spacing:.01em;border-top:1px solid var(--border)}.project-separator:first-child{
border-top:0;padding-top:.25rem}
.session-summary{margin:0 0 1rem;padding:.8rem 1rem;background:#151b21;
border:1px solid #26313b;border-radius:8px}.session-summary h3{font-size:.9rem;
color:#aebdcb;margin:0 0 .4rem}.session-summary ul{margin:0;padding-left:1.2rem;
color:#b8c2cd}.session-summary li{margin:.18rem 0}
.session-summary ul ul{margin:.25rem 0 0;padding-left:1.3rem;color:#aeb8c4}
.session-project-summary+.session-project-summary{margin-top:.7rem}
.session-project-summary h4{margin:0 0 .3rem;color:#8fb4dd;font-size:.88rem}
.detail{font-size:.88rem;margin-top:.4rem}footer{margin-top:2.5rem;color:var(--muted);
font-size:.9rem}a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
@media(max-width:850px){body{padding:1.5rem 1rem 3rem}.page-shell{display:block}
.sidebar{position:static;margin-bottom:1.25rem}.sidebar a{display:inline-block}
.nav-session{margin-top:0}.nav-project{padding-left:.4rem!important}
.current dl,.resume dl,.summary dl,
.system dl{grid-template-columns:1fr;gap:.1rem}.current dd,.summary dd,.system dd{
margin-bottom:.55rem}.resume dd{margin-bottom:.45rem}.event{
grid-template-columns:3.25rem 1fr;gap:.65rem}.content{
grid-column:2}.current,.resume,.summary,.system,.session{padding:1rem}}
</style></head><body>""",
        '<div class="page-shell">',
        '<nav class="sidebar" aria-label="Navigation de la timeline">',
        "<h2>Navigation</h2>",
        *navigation,
        "</nav>",
        "<main>",
        "<header>",
        (
            f"<h1>Journal du {escape(trace['date'])}</h1>"
            if archive_mode
            else f"<h1>Trace du {escape(trace['date'])}</h1>"
        ),
        (
            f'<div class="meta">{trace["activity_count"]} activité(s) · '
            f'{trace["session_count"]} session(s)</div>'
        ),
        "</header>",
    ]
    if not archive_mode:
        body.extend(
            [
                '<section class="current" id="maintenant"><h2>Maintenant</h2><dl>',
                f"<dt>Projet probable</dt><dd>{escape(str(current['project']))}</dd>",
                f"<dt>Workspace</dt><dd>{escape(str(current['workspace']))}</dd>",
                f"<dt>App active</dt><dd>{escape(str(current['app']))}</dd>",
                f"<dt>Dernière commande</dt><dd>{escape(str(current['command']))}</dd>",
                f"<dt>Fichiers récents</dt><dd>{recent_files}</dd>",
                f"<dt>Session active depuis</dt><dd>{current['session_started_at']}</dd>",
                f"<dt>Dernière activité utile</dt><dd>{last_activity}</dd>",
                "</dl></section>",
            ]
        )
    if resume:
        resume_rows = []
        for fact in resume:
            label, value = fact.split(" : ", 1)
            resume_rows.extend(
                [f"<dt>{escape(label)}</dt>", f"<dd>{escape(value)}</dd>"]
            )
        body.append(
            '<section class="resume" id="reprise"><h2>Reprise</h2>'
            f"<dl>{''.join(resume_rows)}</dl></section>"
        )
    body.extend(
        [
        (
            '<section class="summary" id="resume-jour">'
            "<h2>Résumé du jour</h2><dl>"
            if archive_mode
            else '<section class="summary" id="aujourdhui"><h2>Aujourd’hui</h2><dl>'
        ),
        f"<dt>Sessions</dt><dd>{summary['session_count']}</dd>",
        f"<dt>Événements</dt><dd>{summary['activity_count']}</dd>",
        f"<dt>Commandes terminal</dt><dd>{summary['terminal_count']}</dd>",
        f"<dt>Tests</dt><dd>{summary['test_count']}</dd>",
        f"<dt>Git</dt><dd>{summary['git_count']}</dd>",
        f"<dt>Erreurs</dt><dd>{summary['error_count']}</dd>",
        f"<dt>Commandes Pulse</dt><dd>{summary['pulse_count']}</dd>",
        f"<dt>Fichiers modifiés</dt><dd>{summary['distinct_file_count']}</dd>",
        f"<dt>Projets</dt><dd>{', '.join(projects) if projects else 'Aucun'}</dd>",
        f"<dt>Apps principales</dt><dd>{', '.join(apps) if apps else 'Aucune'}</dd>",
        "</dl></section>",
        ]
    )

    if system_status and not archive_mode:
        database_exists = "oui" if system_status["database_exists"] else "non"
        workspace = system_status["primary_workspace"] or "Non détecté"
        body.extend(
            [
                '<section class="system" id="etat-systeme"><h2>État système</h2><dl>',
                f"<dt>Daemon</dt><dd>{escape(system_status['daemon'])}</dd>",
                (
                    "<dt>URL locale</dt><dd>"
                    f"<a href=\"{escape(system_status['url'])}\">"
                    f"{escape(system_status['url'])}</a></dd>"
                ),
                (
                    "<dt>Base SQLite</dt><dd>"
                    f"{escape(system_status['database_path'])}</dd>"
                ),
                f"<dt>Base existante</dt><dd>{database_exists}</dd>",
                f"<dt>Événements du jour</dt><dd>{system_status['event_count']}</dd>",
                (
                    "<dt>Sessions affichées</dt><dd>"
                    f"{system_status['displayed_session_count']}</dd>"
                ),
                f"<dt>Workspace principal</dt><dd>{escape(workspace)}</dd>",
                (
                    "<dt>Watcher terminal</dt><dd>"
                    f"{escape(system_status['terminal_watcher'])}</dd>"
                ),
                "</dl></section>",
            ]
        )

    if not displayed_sessions:
        body.append("<p>Aucune activité pour cette journée.</p>")

    for index, session in enumerate(displayed_sessions, start=1):
        started_at = _display_time(session["started_at"])
        ended_at = _display_time(session["ended_at"])
        project_summaries = _session_project_summaries(
            session, project_workspaces
        )
        session_facts = (
            [] if project_summaries
            else build_session_summary(session, project_workspaces)
        )
        body.extend(
            [
                f'<section class="session" id="session-{index}">',
                f"<h2>Session {index} · {started_at}–{ended_at}</h2>",
            ]
        )
        if project_summaries or session_facts:
            if project_summaries:
                summary_html = "".join(
                    '<div class="session-project-summary">'
                    f"<h4>{escape(project)}</h4>"
                    + (_html_summary_facts(facts) if facts else "")
                    + "</div>"
                    for project, facts in project_summaries
                )
            else:
                summary_html = _html_summary_facts(session_facts)
            body.append(
                '<div class="session-summary"><h3>Résumé de session</h3>'
                f"{summary_html}</div>"
            )
        body.append('<ul class="timeline">')
        file_change_groups = _file_change_groups(session)
        app_activation_counts = _app_activation_counts(session)
        rendered_app_activations = False
        rendered_project = None
        rendered_project_index = 0

        for activity in session["activities"]:
            details = activity.get("details", {})
            path = details.get("path")
            workspace = details.get("workspace")
            event = details.get("event", details.get("change"))
            display_type = activity["type"]
            terminal_labels = (
                _terminal_labels(activity)
                if activity["type"] == "terminal_finished"
                else []
            )
            duplicate_file = (
                activity["type"] == "file_changed"
                and bool(event and path)
                and id(activity) not in file_change_groups
            )
            activity_workspace = _activity_workspace(activity)
            if (
                not duplicate_file
                and activity_workspace in project_workspaces
                and activity_workspace != rendered_project
            ):
                rendered_project = activity_workspace
                rendered_project_index += 1
                body.append(
                    f'<li class="project-separator" '
                    f'id="session-{index}-projet-{rendered_project_index}">'
                    f"{escape(Path(activity_workspace).name)}</li>"
                )
            if activity["type"] == "app_activated":
                if details.get("app") not in app_activation_counts:
                    continue
                if rendered_app_activations:
                    continue
                rendered_app_activations = True
                apps = [
                    escape(str(app))
                    for app, _count in _ranked_apps(app_activation_counts)
                ]
                content = f"Apps actives : {', '.join(apps)}"
                display_type = "applications"
            elif activity["type"] == "file_changed" and event and path:
                if id(activity) not in file_change_groups:
                    continue
                group = file_change_groups[id(activity)]
                if len(group) > 1:
                    items = []
                    for item_path, item_event, item_workspace, count in group:
                        suffix = f" ×{count}" if count > 1 else ""
                        items.append(
                            f"<li>{escape(str(item_event).capitalize())} "
                            f"<code>{escape(_display_file_path(item_path, item_workspace))}"
                            f"</code>{suffix}</li>"
                        )
                    content = (
                        "Fichiers modifiés :"
                        f'<ul class="commands">{"".join(items)}</ul>'
                    )
                    workspace = None
                else:
                    count = group[0][3]
                    suffix = f" ×{count}" if count > 1 else ""
                    content = (
                        f"{escape(str(event).capitalize())} "
                        f"<code>{escape(_display_file_path(path, workspace))}</code>"
                        f"{suffix}"
                    )
            else:
                command = details.get("command")
                command_lines = (
                    [line.strip() for line in command.splitlines() if line.strip()]
                    if isinstance(command, str)
                    else []
                )
                if activity["type"] == "terminal_finished" and len(command_lines) > 1:
                    exit_code = details.get("exit_code")
                    status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
                    items = "".join(
                        f"<li><code>{escape(line)}</code></li>"
                        for line in command_lines
                    )
                    content = f"Command {status}:<ul class=\"commands\">{items}</ul>"
                elif activity["type"] == "terminal_finished" and command_lines:
                    exit_code = details.get("exit_code")
                    status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
                    content = (
                        f"Command {status}: "
                        f"<code>{escape(command_lines[0])}</code>"
                    )
                else:
                    content = escape(str(activity["summary"]))

            detail_lines = []
            if details.get("cwd"):
                detail_lines.append(f"CWD : {escape(str(details['cwd']))}")
            if workspace:
                detail_lines.append(f"Workspace : {escape(str(workspace))}")
            detail_html = "".join(
                f'<div class="detail">{line}</div>' for line in detail_lines
            )
            type_html = escape(display_type) + "".join(
                f' <span class="label label-{escape(label)}">{escape(label)}</span>'
                for label in terminal_labels
            )
            body.append(
                '<li class="event">'
                f'<time>{_display_time(activity["occurred_at"])}</time>'
                f'<span class="type">{type_html}</span>'
                f'<div class="content">{content}{detail_html}</div></li>'
            )
        body.extend(["</ul>", "</section>"])

    body.extend(
        [
            f'<div id="{end_anchor}" aria-hidden="true"></div>',
            '<footer><a href="/days">Jours</a> · '
            f'<a href="{escape(trace_json_url)}">JSON</a> · '
            f'<a href="{escape(trace_markdown_url)}">Markdown</a></footer>',
            "</main></div></body></html>",
        ]
    )
    return "\n".join(body)


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
        days.append(
            {
                "date": day.isoformat(),
                "event_count": trace["activity_count"],
                "session_count": summary["session_count"],
                "projects": [
                    Path(workspace).name for workspace in summary["workspaces"]
                ],
            }
        )
    return {"days": days}


def render_available_days_html(
    available_days: dict[str, list[dict[str, Any]]],
) -> str:
    day_cards = []
    for item in available_days["days"]:
        day = escape(item["date"])
        event_count = item["event_count"]
        session_count = item["session_count"]
        projects = ", ".join(escape(project) for project in item["projects"])
        day_cards.append(
            '<article class="day">'
            f"<h2>{day}</h2>"
            f"<p>{event_count} événement{'s' if event_count != 1 else ''} · "
            f"{session_count} session{'s' if session_count != 1 else ''}</p>"
            f"<p>Projets : {projects or 'Aucun'}</p>"
            f'<nav><a href="/day/{day}">HTML</a> · '
            f'<a href="/trace/{day}">JSON</a> · '
            f'<a href="/trace/{day}.md">Markdown</a></nav>'
            "</article>"
        )
    content = "".join(day_cards) or "<p>Aucun jour disponible.</p>"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="fr"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Pulse — Jours disponibles</title>",
            """<style>
:root{color-scheme:dark;--bg:#11151a;--panel:#191f26;--border:#2a333d;
--text:#d7dee7;--muted:#8f9aaa;--link:#83a9d8}
*{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;max-width:760px;
margin:0 auto;padding:2.5rem 1.5rem 4rem;background:var(--bg);color:var(--text)}
header{margin-bottom:1.5rem}h1{margin:0 0 .2rem;font-size:2rem}header p,.day p{
color:var(--muted);margin:.2rem 0}.days{display:grid;gap:1rem}.day{background:var(--panel);
border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.3rem}
.day h2{margin:0 0 .35rem;font-size:1.15rem;color:#e4eaf1}.day nav{margin-top:.65rem}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
footer{margin-top:2rem;color:var(--muted)}
</style></head><body>""",
            "<header><h1>Jours récents</h1>"
            "<p>Traces disponibles du plus récent au plus ancien.</p></header>",
            f'<main class="days">{content}</main>',
            '<footer><a href="/">Aujourd’hui</a> · '
            '<a href="/trace/days">JSON</a></footer>',
            "</body></html>",
        ]
    )

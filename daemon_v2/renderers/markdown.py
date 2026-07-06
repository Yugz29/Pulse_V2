"""Markdown renderer for daily traces."""

from datetime import datetime
from pathlib import Path
from typing import Any

from ..analysis.projects import (
    activity_project_context,
    activity_project_root,
    resolve_project_context,
)
from ..analysis.timeline import (
    _app_activation_counts,
    _display_file_path,
    _display_time,
    _displayed_sessions,
    _file_change_groups,
    _passive_sessions,
    _ranked_apps,
    _session_duration,
    _session_has_recent_strong_activity,
    _session_observed_bounds,
)
from ..daily_trace import (
    SummaryFact,
    _session_project_summaries,
    _terminal_labels,
    build_current_state,
    build_daily_summary,
    build_resume,
    build_session_summary,
)


def _markdown_text(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    for character in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(character, f"\\{character}")
    return text


def _markdown_inline_code(value: str) -> str:
    fence = "`"
    while fence in value:
        fence += "`"
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{padding}{value}{padding}{fence}"


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


def render_daily_trace_markdown(
    trace: dict[str, Any],
    archive_mode: bool = False,
) -> str:
    summary = build_daily_summary(
        trace,
        project_mode="archive" if archive_mode else "live",
    )
    current = build_current_state(trace) if not archive_mode else None
    resume = build_resume(trace) if not archive_mode else []
    displayed_sessions = _displayed_sessions(trace)
    passive_sessions = _passive_sessions(trace)
    apps = [_markdown_text(app) for app, _count in _ranked_apps(summary["apps"])]
    projects = [
        _markdown_text(resolve_project_context(path).project_name)
        for path in summary["workspaces"]
    ]
    project_workspaces = set(summary["workspaces"])
    last_activity = (
        f"{_markdown_text(current['last_activity_type'])} — "
        f"{_markdown_text(current['last_activity_description'])}"
        if current and current["last_activity_type"]
        else "Non détectée"
    )
    lines = [f"# Trace du {trace['date']}", ""]
    if current:
        lines.extend(
            [
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
        )
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
        lines.extend(["## Reprise"])
        for fact in resume:
            if isinstance(fact, tuple):
                label, rows = fact
                lines.extend(["", f"### {_markdown_text(label)}"])
                for row_label, value in rows:
                    if isinstance(value, list):
                        lines.append(f"- {_markdown_text(row_label)} :")
                        lines.extend(
                            f"  - {_markdown_text(item)}" for item in value
                        )
                    else:
                        lines.append(
                            f"- {_markdown_text(row_label)} : "
                            f"{_markdown_text(value)}"
                        )
            else:
                lines.append(f"- {_markdown_text(fact)}")
        lines.append("")
    lines.extend(
        [
        "## Aujourd’hui",
        f"- Sessions de travail : {summary['session_count']}",
        f"- Activités passives : {summary['passive_activity_count']}",
        f"- Événements : {summary['activity_count']}",
        f"- Commandes terminal : {summary['terminal_count']}",
        f"- Tests : {summary['test_count']}",
        f"- Git : {summary['git_count']}",
        f"- Erreurs : {summary['error_count']}",
        f"- Fichiers modifiés : {summary['distinct_file_count']}",
        f"- Projets : {', '.join(projects) if projects else 'Aucun'}",
        f"- Apps principales : {', '.join(apps) if apps else 'Aucune'}",
        "",
        ]
    )
    if not displayed_sessions and not passive_sessions:
        lines.extend(["_Aucune activité._", ""])
        return "\n".join(lines)

    now = datetime.now().astimezone()
    current_day = now.date().isoformat()
    for index, session in enumerate(displayed_sessions, start=1):
        observed_start, observed_end = _session_observed_bounds(session)
        started_at = _display_time(observed_start)
        ended_at = _display_time(observed_end)
        duration = _session_duration(session)
        in_progress = (
            " · en cours"
            if trace["date"] == current_day
            and index == len(displayed_sessions)
            and _session_has_recent_strong_activity(session, now)
            else ""
        )
        lines.extend(
            [
                f"## Session {index} — {started_at}–{ended_at}"
                f" · {duration}{in_progress}",
                "",
            ]
        )
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
            elif not _app_activation_counts(session):
                lines.extend(
                    [
                        "### Résumé de session",
                        "_Aucun signal significatif dans cette session._",
                        "",
                    ]
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
            activity_workspace = activity_project_root(activity)
            if (
                not duplicate_file
                and activity_workspace in project_workspaces
                and activity_workspace != rendered_project
            ):
                rendered_project = activity_workspace
                project_name = resolve_project_context(activity_workspace).project_name
                lines.append(f"### {_markdown_text(project_name)}")

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
            project_context = activity_project_context(activity)
            if project_context and project_context.module:
                lines.append(
                    f"  - Module : {_markdown_text(project_context.module)}"
                )
            if cwd:
                lines.append(f"  - CWD : {_markdown_text(cwd)}")
            if workspace:
                lines.append(f"  - Workspace : {_markdown_text(workspace)}")
        lines.append("")

    if passive_sessions:
        lines.extend(
            [
                "## Activité passive",
                (
                    "Ces signaux ont été observés mais ne sont pas considérés "
                    "comme des sessions de travail."
                ),
            ]
        )
        for session in passive_sessions:
            passive_started_at, _passive_ended_at = _session_observed_bounds(
                session
            )
            passive_apps = [
                _markdown_text(app)
                for app, _count in _ranked_apps(
                    _app_activation_counts(session)
                )
            ]
            lines.append(
                f"- {_display_time(passive_started_at)} · "
                f"{', '.join(passive_apps)}"
            )
        lines.append("")

    return "\n".join(lines)

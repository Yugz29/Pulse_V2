"""Build a readable day view from durable activity rows."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from html import escape
from pathlib import Path
from typing import Any

from .trace_store import TraceStore


IGNORED_APP_NAMES_FOR_RENDERING = {"CleanMyMac Menu", "Finder", "loginwindow"}
TERMINAL_LABEL_ORDER = ("test", "git", "pulse", "erreur")


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
) -> dict[str, list[tuple[str, str, str | None, int]]]:
    counts: dict[str, int] = {}
    first_activities: dict[str, dict[str, Any]] = {}
    for activity in session["activities"]:
        if activity["type"] != "file_changed":
            continue
        details = activity.get("details", {})
        path = details.get("path")
        event = details.get("event", details.get("change"))
        if path and event:
            counts[path] = counts.get(path, 0) + 1
            first_activities.setdefault(path, activity)

    groups_by_minute: dict[datetime, list[tuple[str, str, str | None, int]]] = {}
    for path, activity in first_activities.items():
        details = activity["details"]
        minute = datetime.fromisoformat(activity["occurred_at"]).replace(
            second=0, microsecond=0
        )
        groups_by_minute.setdefault(minute, []).append(
            (
                path,
                details.get("event", details.get("change")),
                details.get("workspace"),
                counts[path],
            )
        )

    return {
        path: group
        for group in groups_by_minute.values()
        for path, _event, _workspace, _count in group
    }


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
        parts = line.split()
        python_pytest = (
            len(parts) >= 3
            and Path(parts[0]).name in {"python", "python3"}
            and parts[1:3] == ["-m", "pytest"]
        )
        if python_pytest or any(
            line == prefix or line.startswith(f"{prefix} ")
            for prefix in ("pytest", "npm test", "swift test")
        ):
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
    displayed_sessions = _displayed_sessions(trace)
    apps = [
        f"{_markdown_text(app)} ×{count}" if count > 1 else _markdown_text(app)
        for app, count in summary["apps"].items()
    ]
    projects = [_markdown_text(Path(path).name) for path in summary["workspaces"]]
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

        file_change_groups = _file_change_groups(session)
        rendered_file_paths: set[str] = set()
        app_activation_counts = _app_activation_counts(session)
        rendered_app_activations = False

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

            if activity["type"] == "app_activated":
                if details.get("app") not in app_activation_counts:
                    continue
                if rendered_app_activations:
                    continue
                rendered_app_activations = True
                apps = [
                    f"{_markdown_text(app)} ×{count}" if count > 1 else _markdown_text(app)
                    for app, count in app_activation_counts.items()
                ]
                lines.append(f"- Apps actives : {', '.join(apps)}")
                continue
            elif activity["type"] == "file_changed" and event and path:
                if path in rendered_file_paths:
                    continue
                group = file_change_groups[path]
                rendered_file_paths.update(item[0] for item in group)
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
) -> str:
    summary = build_daily_summary(trace)
    current = build_current_state(trace)
    displayed_sessions = _displayed_sessions(trace)
    apps = [
        f"{escape(str(app))} ×{count}" if count > 1 else escape(str(app))
        for app, count in summary["apps"].items()
    ]
    projects = [
        f'<span title="{escape(path)}">{escape(Path(path).name)}</span>'
        for path in summary["workspaces"]
    ]
    last_activity = (
        f"{escape(str(current['last_activity_type']))} — "
        f"{escape(str(current['last_activity_description']))}"
        if current["last_activity_type"]
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
        if current["recent_files"]
        else "Aucun"
    )
    body = [
        "<!doctype html>",
        '<html lang="fr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Pulse — {escape(trace['date'])}</title>",
        """<style>
:root{color-scheme:dark;--bg:#11151a;--panel:#191f26;--panel-soft:#161c22;
--border:#2a333d;--text:#d7dee7;--muted:#8f9aaa;--link:#83a9d8}
*{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;max-width:980px;
margin:0 auto;padding:2.5rem 2rem 4rem;background:var(--bg);color:var(--text)}
header{margin-bottom:2rem}h1{font-size:2rem;letter-spacing:-.025em;margin:0 0 .25rem}
h2{color:#e4eaf1;letter-spacing:-.01em}.meta,.detail{color:var(--muted)}
.current,.summary,.system,.session{background:var(--panel);border:1px solid var(--border);
border-radius:12px;padding:1.25rem 1.5rem;margin:1.25rem 0;box-shadow:0 8px 24px #0003}
.current{border-top:3px solid #5d8fc4}.summary{border-top:3px solid #669b78}
.system{border-top:3px solid #778493;background:var(--panel-soft)}
.session{margin-top:1.5rem}.session h2{font-size:1.1rem;margin:0 0 1rem;color:#cbd5e1}
.current h2,.summary h2,.system h2{font-size:1.2rem;margin:0 0 1rem}
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
.detail{font-size:.88rem;margin-top:.4rem}footer{margin-top:2.5rem;color:var(--muted);
font-size:.9rem}a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
@media(max-width:700px){body{padding:1.5rem 1rem 3rem}.current dl,.summary dl,
.system dl{grid-template-columns:1fr;gap:.1rem}.current dd,.summary dd,.system dd{
margin-bottom:.55rem}.event{grid-template-columns:3.25rem 1fr;gap:.65rem}.content{
grid-column:2}.current,.summary,.system,.session{padding:1rem}}
</style></head><body>""",
        "<header>",
        f"<h1>Trace du {escape(trace['date'])}</h1>",
        (
            f'<div class="meta">{trace["activity_count"]} activité(s) · '
            f'{trace["session_count"]} session(s)</div>'
        ),
        "</header>",
        '<section class="current"><h2>Maintenant</h2><dl>',
        f"<dt>Projet probable</dt><dd>{escape(str(current['project']))}</dd>",
        f"<dt>Workspace</dt><dd>{escape(str(current['workspace']))}</dd>",
        f"<dt>App active</dt><dd>{escape(str(current['app']))}</dd>",
        f"<dt>Dernière commande</dt><dd>{escape(str(current['command']))}</dd>",
        f"<dt>Fichiers récents</dt><dd>{recent_files}</dd>",
        f"<dt>Session active depuis</dt><dd>{current['session_started_at']}</dd>",
        f"<dt>Dernière activité utile</dt><dd>{last_activity}</dd>",
        "</dl></section>",
        '<section class="summary"><h2>Aujourd’hui</h2><dl>',
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

    if system_status:
        database_exists = "oui" if system_status["database_exists"] else "non"
        workspace = system_status["primary_workspace"] or "Non détecté"
        body.extend(
            [
                '<section class="system"><h2>État système</h2><dl>',
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
        body.append("<p>Aucune activité aujourd’hui.</p>")

    for index, session in enumerate(displayed_sessions, start=1):
        started_at = _display_time(session["started_at"])
        ended_at = _display_time(session["ended_at"])
        body.extend(
            [
                '<section class="session">',
                f"<h2>Session {index} · {started_at}–{ended_at}</h2>",
                '<ul class="timeline">',
            ]
        )
        file_change_groups = _file_change_groups(session)
        rendered_file_paths: set[str] = set()
        app_activation_counts = _app_activation_counts(session)
        rendered_app_activations = False

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
            if activity["type"] == "app_activated":
                if details.get("app") not in app_activation_counts:
                    continue
                if rendered_app_activations:
                    continue
                rendered_app_activations = True
                apps = [
                    f"{escape(str(app))} ×{count}" if count > 1 else escape(str(app))
                    for app, count in app_activation_counts.items()
                ]
                content = f"Apps actives : {', '.join(apps)}"
                display_type = "applications"
            elif activity["type"] == "file_changed" and event and path:
                if path in rendered_file_paths:
                    continue
                group = file_change_groups[path]
                rendered_file_paths.update(item[0] for item in group)
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
            '<footer><a href="/trace/today">JSON</a> · '
            '<a href="/trace/today.md">Markdown</a></footer>',
            "</body></html>",
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

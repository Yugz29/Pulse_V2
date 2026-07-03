"""Build a readable day view from durable activity rows."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from html import escape
from pathlib import Path
from typing import Any

from .trace_store import TraceStore


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
            if app:
                counts[app] = counts.get(app, 0) + 1
    return counts


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


def render_daily_trace_markdown(trace: dict[str, Any]) -> str:
    lines = [f"# Trace du {trace['date']}", ""]
    if not trace["sessions"]:
        lines.extend(["_Aucune activité._", ""])
        return "\n".join(lines)

    for index, session in enumerate(trace["sessions"], start=1):
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

            event = details.get("event", details.get("change"))
            path = details.get("path")
            workspace = details.get("workspace")

            if activity["type"] == "app_activated":
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
                    f"- {occurred_at} · **{activity_type}** — Command {status}:"
                )
                for command_line in command_lines:
                    lines.append(f"  - {_markdown_inline_code(command_line)}")
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


def render_daily_trace_html(trace: dict[str, Any]) -> str:
    body = [
        "<!doctype html>",
        '<html lang="fr"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Pulse — {escape(trace['date'])}</title>",
        """<style>
body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:2rem;
background:#f6f7f9;color:#20242a}header{margin-bottom:2rem}h1{margin-bottom:.25rem}
.meta,.detail{color:#626b76}.session{background:white;border:1px solid #dfe3e8;
border-radius:10px;padding:1rem 1.25rem;margin:1rem 0}.session h2{font-size:1.1rem;
margin:0 0 1rem}.timeline{list-style:none;padding:0;margin:0}.event{display:grid;
grid-template-columns:4rem 9rem 1fr;gap:.75rem;padding:.65rem 0;
border-top:1px solid #edf0f2}.event:first-child{border-top:0}.type{font-family:monospace;
font-size:.85rem;color:#46515d}.content code{background:#eef1f4;padding:.1rem .3rem;
border-radius:4px}.commands{margin:.4rem 0;padding-left:1.25rem}.detail{font-size:.9rem;
margin-top:.3rem}footer{margin-top:2rem}a{color:#315fa8}
@media(max-width:650px){.event{grid-template-columns:3.5rem 1fr}.content{grid-column:2}}
</style></head><body>""",
        "<header>",
        f"<h1>Trace du {escape(trace['date'])}</h1>",
        (
            f'<div class="meta">{trace["activity_count"]} activité(s) · '
            f'{trace["session_count"]} session(s)</div>'
        ),
        "</header>",
    ]

    if not trace["sessions"]:
        body.append("<p>Aucune activité aujourd’hui.</p>")

    for index, session in enumerate(trace["sessions"], start=1):
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
            if activity["type"] == "app_activated":
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
            body.append(
                '<li class="event">'
                f'<time>{_display_time(activity["occurred_at"])}</time>'
                f'<span class="type">{escape(display_type)}</span>'
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

    return {
        "date": selected_day.isoformat(),
        "timezone": str(zone),
        "activity_count": len(activities),
        "session_count": len(sessions),
        "sessions": sessions,
    }

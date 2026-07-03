"""Build a readable day view from durable activity rows."""

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone, tzinfo
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


def render_daily_trace_markdown(trace: dict[str, Any]) -> str:
    lines = [f"# Trace du {trace['date']}", ""]
    if not trace["sessions"]:
        lines.extend(["_Aucune activité._", ""])
        return "\n".join(lines)

    for index, session in enumerate(trace["sessions"], start=1):
        started_at = _display_time(session["started_at"])
        ended_at = _display_time(session["ended_at"])
        lines.extend([f"## Session {index} — {started_at}–{ended_at}", ""])

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

            if activity["type"] == "file_changed" and event and path:
                display_path = Path(path)
                if workspace:
                    try:
                        display_path = display_path.relative_to(Path(workspace))
                    except ValueError:
                        pass
                lines.append(
                    f"- {occurred_at} · **{activity_type}** — "
                    f"{str(event).capitalize()} {_markdown_inline_code(str(display_path))}"
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

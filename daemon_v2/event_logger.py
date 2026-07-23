"""Concise opt-in console logging for accepted development events."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .ingest import redact_command
from .models import Activity, SYSTEM_ACTIVITY_TYPES


_ENABLED_VALUES = {"1", "true", "yes", "on"}
_TYPE_WIDTH = 17
_MAX_COMMAND_LENGTH = 120


def event_log_enabled() -> bool:
    value = os.environ.get("PULSE_CORE_EVENT_LOG", "")
    return value.strip().casefold() in _ENABLED_VALUES


def validation_error_summary(field: str | None, message: str) -> str:
    if not field:
        return message
    displayed_field = (
        f"details.{field}" if field in {"app", "path"} else field
    )
    return f"{displayed_field}: {message}"


def log_ingested_event(
    *,
    activity: Activity | None,
    status: str,
    error: str | None = None,
) -> None:
    """Print one best-effort development line without affecting ingestion."""
    try:
        if not event_log_enabled():
            return
        event_type, summary = _event_label(activity, error=error)
        suffix = "" if status == "created" else f"  [{status}]"
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        summary_text = f"  {summary}" if summary else ""
        print(
            f"{timestamp}  {event_type:<{_TYPE_WIDTH}}"
            f"{summary_text}{suffix}".rstrip(),
            flush=True,
        )
    except Exception:
        # Development logging must never alter persistence or HTTP responses.
        return


def _event_label(
    activity: Activity | None,
    *,
    error: str | None,
) -> tuple[str, str]:
    if activity is None:
        return "invalid_event", _safe_text(error)

    details = activity.details if isinstance(activity.details, dict) else {}
    if activity.activity_type == "app_activated":
        return activity.activity_type, _safe_text(details.get("app"))
    if activity.activity_type == "file_changed":
        return activity.activity_type, _file_summary(details)
    if activity.activity_type == "terminal_finished":
        command = redact_command(_safe_text(details.get("command")))
        return activity.activity_type, _truncate(command)
    if activity.activity_type in SYSTEM_ACTIVITY_TYPES:
        return activity.activity_type, ""
    return _safe_text(activity.activity_type), "<unknown>"


def _file_summary(details: dict[str, Any]) -> str:
    path_value = details.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        return "<unknown>"
    path = Path(path_value).expanduser()
    workspace_root = _workspace_root(details)
    if workspace_root is not None:
        try:
            return str(path.relative_to(workspace_root))
        except (TypeError, ValueError):
            pass
    return str(path)


def _workspace_root(details: dict[str, Any]) -> Path | None:
    workspace = details.get("workspace")
    if isinstance(workspace, dict):
        candidate = workspace.get("workspace_root")
    elif isinstance(workspace, str):
        candidate = workspace
    else:
        candidate = details.get("workspace_root")
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    return Path(candidate).expanduser()


def _safe_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "<unknown>"
    return " ".join(value.split())


def _truncate(value: str) -> str:
    if len(value) <= _MAX_COMMAND_LENGTH:
        return value
    return value[: _MAX_COMMAND_LENGTH - 1].rstrip() + "…"

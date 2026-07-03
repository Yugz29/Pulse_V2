"""Validation and normalization for locally observed activity."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Activity, SUPPORTED_ACTIVITY_TYPES


_SENSITIVE_OPTION = re.compile(
    r"(?i)(--?(?:password|passwd|token|secret|api[-_]?key))(?:=|\s+)(\S+)"
)
_ENV_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|TOKEN|SECRET|API_KEY)[A-Z0-9_]*)=(\S+)"
)


class InvalidActivity(ValueError):
    """Raised when an activity payload cannot be normalized."""


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidActivity(f"{key} must be a non-empty string")
    return value.strip()


def _parse_occurred_at(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, str):
        raise InvalidActivity("occurred_at must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidActivity("occurred_at must be a valid ISO 8601 string") from exc
    if parsed.tzinfo is None:
        raise InvalidActivity("occurred_at must include a timezone")
    return parsed


def redact_command(command: str) -> str:
    redacted = _SENSITIVE_OPTION.sub(r"\1=[REDACTED]", command)
    return _ENV_SECRET.sub(r"\1=[REDACTED]", redacted)


def normalize_activity(payload: Any) -> Activity:
    if not isinstance(payload, dict):
        raise InvalidActivity("request body must be a JSON object")

    activity_type = _required_string(payload, "type")
    if activity_type not in SUPPORTED_ACTIVITY_TYPES:
        raise InvalidActivity(f"type must be one of: {', '.join(sorted(SUPPORTED_ACTIVITY_TYPES))}")

    occurred_at = _parse_occurred_at(payload.get("occurred_at"))
    details: dict[str, Any]

    if activity_type == "file_changed":
        path = _required_string(payload, "path")
        change = payload.get("change", "modified")
        if change not in {"created", "modified", "deleted"}:
            raise InvalidActivity("change must be created, modified, or deleted")
        normalized_path = str(Path(path).expanduser())
        details = {"path": normalized_path, "change": change}
        source = "filesystem"
        summary = f"{change.capitalize()} {normalized_path}"
    else:
        command = redact_command(_required_string(payload, "command"))
        exit_code = payload.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise InvalidActivity("exit_code must be an integer")
        cwd = _required_string(payload, "cwd")
        details = {"command": command, "exit_code": exit_code, "cwd": str(Path(cwd).expanduser())}
        source = "terminal"
        status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
        summary = f"Command {status}: {command}"

    return Activity(
        activity_type=activity_type,
        occurred_at=occurred_at,
        source=source,
        summary=summary,
        details=details,
    )

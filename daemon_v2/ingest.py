"""Validation and normalization for locally observed activity."""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    Activity,
    CanonicalEvent,
    IngestedEvent,
    SUPPORTED_ACTIVITY_TYPES,
    SYSTEM_ACTIVITY_TYPES,
    canonical_event_fingerprint,
)


_SENSITIVE_OPTION = re.compile(
    r"(?i)(--?(?:password|passwd|token|secret|api[-_]?key))(?:=|\s+)(\S+)"
)
_ENV_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|TOKEN|SECRET|API_KEY)[A-Z0-9_]*)=(\S+)"
)
_IGNORED_TERMINAL_COMMANDS = {
    "clear",
    "source ~/.zshrc",
}


class InvalidActivity(ValueError):
    """Raised when an activity payload cannot be normalized."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class IgnoredActivity(ValueError):
    """Raised when a valid but intentionally noisy activity should not be stored."""


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidActivity(
            f"{key} must be a non-empty string",
            field=key,
        )
    return value.strip()


def _parse_occurred_at(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, str):
        raise InvalidActivity(
            "occurred_at must be an ISO 8601 string",
            field="occurred_at",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidActivity(
            "occurred_at must be a valid ISO 8601 string",
            field="occurred_at",
        ) from exc
    if parsed.tzinfo is None:
        raise InvalidActivity(
            "occurred_at must include a timezone",
            field="occurred_at",
        )
    return parsed


def _copy_persisted_context(
    payload: dict[str, Any],
    details: dict[str, Any],
) -> None:
    """Preserve producer-resolved context without recalculating it in Core."""
    workspace = payload.get("workspace")
    if isinstance(workspace, str) and workspace.strip():
        details["workspace"] = workspace
    elif isinstance(workspace, dict):
        details["workspace"] = dict(workspace)

    git = payload.get("git")
    if isinstance(git, dict):
        details["git"] = dict(git)
    git_root = payload.get("git_root")
    if isinstance(git_root, str) and git_root.strip():
        details["git_root"] = git_root.strip()


def redact_command(command: str) -> str:
    redacted = _SENSITIVE_OPTION.sub(r"\1=[REDACTED]", command)
    return _ENV_SECRET.sub(r"\1=[REDACTED]", redacted)


def _is_internal_pulse_curl(command: str) -> bool:
    return (
        command.startswith("curl ")
        and "http://127.0.0.1:5000/activities" in command
    )


def filter_terminal_command(command: str) -> str | None:
    useful_lines = []
    ignoring_internal_curl = False
    for line in command.splitlines():
        stripped_line = line.strip()
        normalized_line = " ".join(stripped_line.split())
        if ignoring_internal_curl:
            continue
        if _is_internal_pulse_curl(normalized_line):
            # Remaining lines may be curl options or a multiline JSON body.
            ignoring_internal_curl = True
            continue
        if normalized_line and normalized_line not in _IGNORED_TERMINAL_COMMANDS:
            useful_lines.append(stripped_line)
    return "\n".join(useful_lines) or None


def normalize_activity(payload: Any) -> Activity:
    """Normalize a historical flat activity payload.

    Kept as the compatibility-facing semantic normalizer. Canonical ingestion
    uses :func:`normalize_event`, which passes only the canonical ``details``
    object into this function.
    """
    if not isinstance(payload, dict):
        raise InvalidActivity(
            "request body must be a JSON object",
            field="request",
        )

    activity_type = _required_string(payload, "type")
    if activity_type not in SUPPORTED_ACTIVITY_TYPES:
        raise InvalidActivity(
            f"type must be one of: {', '.join(sorted(SUPPORTED_ACTIVITY_TYPES))}",
            field="type",
        )

    terminal_command = None
    if activity_type == "terminal_finished":
        raw_command = payload.get("command")
        if not isinstance(raw_command, str):
            raise InvalidActivity(
                "command must be a non-empty string",
                field="details.command",
            )
        terminal_command = filter_terminal_command(raw_command)
        if terminal_command is None:
            raise IgnoredActivity("terminal command is intentionally ignored")

    occurred_at = _parse_occurred_at(payload.get("occurred_at"))
    details: dict[str, Any]

    if activity_type == "file_changed":
        path = _required_string(payload, "path")
        event = payload.get("event", payload.get("change", "modified"))
        if event not in {"created", "modified", "deleted"}:
            raise InvalidActivity(
                "event must be created, modified, or deleted",
                field="details.event",
            )
        normalized_path = str(Path(path).expanduser().absolute())
        details = {"path": normalized_path, "event": event}
        _copy_persisted_context(payload, details)
        if isinstance(details.get("workspace"), str):
            details["workspace"] = str(
                Path(details["workspace"]).expanduser().absolute()
            )
        source = "filesystem"
        summary = f"{event.capitalize()} {normalized_path}"
    elif activity_type == "terminal_finished":
        assert terminal_command is not None
        command = redact_command(terminal_command)
        exit_code = payload.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise InvalidActivity(
                "exit_code must be an integer",
                field="details.exit_code",
            )
        cwd = _required_string(payload, "cwd")
        details = {"command": command, "exit_code": exit_code, "cwd": str(Path(cwd).expanduser())}
        for key in ("started_at", "finished_at"):
            if key in payload:
                details[key] = _parse_occurred_at(payload[key]).isoformat()
        _copy_persisted_context(payload, details)
        source = "terminal"
        status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
        summary = f"Command {status}: {command}"
    elif activity_type == "app_activated":
        app = _required_string(payload, "app")
        details = {"app": app}
        bundle_id = payload.get("bundle_id")
        if isinstance(bundle_id, str) and bundle_id.strip():
            details["bundle_id"] = bundle_id.strip()
        if "title" in payload:
            details["title"] = _required_string(payload, "title")
        source = "application"
        summary = f"Activated {app}"
    else:
        assert activity_type in SYSTEM_ACTIVITY_TYPES
        details = {}
        source = "system"
        summary = activity_type

    return Activity(
        activity_type=activity_type,
        occurred_at=occurred_at,
        source=source,
        summary=summary,
        details=details,
    )


_CANONICAL_MARKERS = {"event_id", "schema_version", "producer", "details"}
_LEGACY_PRODUCER = "pulse-legacy"


def normalize_event(payload: Any) -> IngestedEvent:
    """Validate canonical input or explicitly adapt a legacy flat payload."""
    if not isinstance(payload, dict):
        raise InvalidActivity(
            "request body must be a JSON object",
            field="request",
        )
    if _CANONICAL_MARKERS.intersection(payload):
        return _normalize_canonical_event(payload)
    return adapt_legacy_payload(payload)


def _normalize_canonical_event(payload: dict[str, Any]) -> IngestedEvent:
    event_id = _canonical_required_string(payload, "event_id")

    schema_version = payload.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version <= 0
    ):
        raise InvalidActivity(
            "schema_version must be a strictly positive integer",
            field="schema_version",
        )

    event_type = _canonical_required_string(payload, "type")
    producer = payload.get("producer")
    if not isinstance(producer, dict):
        raise InvalidActivity(
            "producer must be an object",
            field="producer",
        )
    producer_name = _canonical_required_string(producer, "name", prefix="producer")
    producer_version = _canonical_optional_string(
        producer,
        "version",
        prefix="producer",
    )
    producer_instance_id = _canonical_optional_string(
        producer,
        "instance_id",
        prefix="producer",
    )

    if "occurred_at" not in payload:
        raise InvalidActivity(
            "occurred_at is required",
            field="occurred_at",
        )
    occurred_at = _parse_occurred_at(payload["occurred_at"])

    details = payload.get("details")
    if not isinstance(details, dict):
        raise InvalidActivity(
            "details must be a JSON object",
            field="details",
        )

    event = CanonicalEvent(
        event_id=event_id,
        schema_version=schema_version,
        event_type=event_type,
        producer_name=producer_name,
        producer_version=producer_version,
        producer_instance_id=producer_instance_id,
        occurred_at=occurred_at,
        details=dict(details),
    )
    activity = _activity_from_event(event)
    return IngestedEvent(
        event=event,
        activity=activity,
        fingerprint=_validated_event_fingerprint(event),
    )


def adapt_legacy_payload(payload: dict[str, Any]) -> IngestedEvent:
    """Temporary adapter for the existing flat Core producers.

    A fresh server-side event_id is generated for every request. Consequently,
    two identical legacy requests are intentionally *not* idempotent. This
    adapter is isolated so it can be removed once all producers send the
    versioned contract.
    """
    event_type = _required_string(payload, "type")
    raw_occurred_at = payload.get("timestamp", payload.get("occurred_at"))
    occurred_at = _parse_occurred_at(raw_occurred_at)
    legacy_details = {
        key: value
        for key, value in payload.items()
        if key not in {"type", "timestamp", "occurred_at"}
    }
    event = CanonicalEvent(
        event_id=str(uuid.uuid4()),
        schema_version=1,
        event_type=event_type,
        producer_name=_LEGACY_PRODUCER,
        producer_version=None,
        producer_instance_id=None,
        occurred_at=occurred_at,
        details=legacy_details,
    )
    activity = _activity_from_event(event)
    return IngestedEvent(
        event=event,
        activity=activity,
        fingerprint=_validated_event_fingerprint(event),
        legacy=True,
    )


def _activity_from_event(event: CanonicalEvent) -> Activity:
    flat_payload = {
        "type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        **event.details,
    }
    return normalize_activity(flat_payload)


def _canonical_required_string(
    payload: dict[str, Any],
    key: str,
    *,
    prefix: str | None = None,
) -> str:
    field = f"{prefix}.{key}" if prefix else key
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidActivity(
            f"{field} must be a non-empty string",
            field=field,
        )
    return value.strip()


def _canonical_optional_string(
    payload: dict[str, Any],
    key: str,
    *,
    prefix: str,
) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        field = f"{prefix}.{key}"
        raise InvalidActivity(
            f"{field} must be a string when provided",
            field=field,
        )
    return value


def _validated_event_fingerprint(event: CanonicalEvent) -> str:
    try:
        return canonical_event_fingerprint(event)
    except (TypeError, ValueError) as exc:
        raise InvalidActivity(
            "details must contain strictly valid JSON values",
            field="details",
        ) from exc

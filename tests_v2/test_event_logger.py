import json
import re
from datetime import datetime, timezone

import pytest

from daemon_v2.event_logger import log_ingested_event
from daemon_v2.main import create_app
from daemon_v2.models import Activity


def activity(activity_type, details):
    return Activity(
        activity_type=activity_type,
        occurred_at=datetime(2026, 7, 23, 17, 0, tzinfo=timezone.utc),
        source="test",
        summary="test event",
        details=details,
    )


def canonical_payload(event_id="event-log", **details):
    return {
        "event_id": event_id,
        "schema_version": 1,
        "type": "app_activated",
        "producer": {
            "name": "event-logger-tests",
            "version": "1",
            "instance_id": "tests",
        },
        "occurred_at": "2026-07-23T17:00:00Z",
        "details": {"app": "Visual Studio Code", **details},
    }


def logged_message(capsys):
    output = capsys.readouterr()
    assert output.err == ""
    return output.out


def test_logger_is_disabled_by_default(monkeypatch, capsys):
    monkeypatch.delenv("PULSE_CORE_EVENT_LOG", raising=False)

    log_ingested_event(
        activity=activity("app_activated", {"app": "Safari"}),
        status="created",
    )

    assert logged_message(capsys) == ""


def test_logger_is_disabled_with_zero(monkeypatch, capsys):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "0")

    log_ingested_event(
        activity=activity("app_activated", {"app": "Safari"}),
        status="created",
    )

    assert logged_message(capsys) == ""


@pytest.mark.parametrize("enabled", ["1", "true", "TRUE", "yes", "on"])
def test_logger_accepts_explicit_enabled_values(monkeypatch, capsys, enabled):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", enabled)

    log_ingested_event(
        activity=activity("app_activated", {"app": "Safari"}),
        status="created",
    )

    assert re.fullmatch(
        r"\d{2}:\d{2}:\d{2}  app_activated\s+Safari\n",
        logged_message(capsys),
    )


def test_file_path_is_relative_to_workspace(monkeypatch, capsys):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")

    log_ingested_event(
        activity=activity(
            "file_changed",
            {
                "path": "/project/Pulse_Core/daemon_v2/main.py",
                "workspace": {
                    "workspace_root": "/project/Pulse_Core",
                },
            },
        ),
        status="created",
    )

    assert "file_changed       daemon_v2/main.py" in logged_message(capsys)


def test_file_path_without_workspace_uses_available_path(monkeypatch, capsys):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")

    log_ingested_event(
        activity=activity("file_changed", {"path": "/tmp/main.py"}),
        status="created",
    )

    assert "file_changed       /tmp/main.py" in logged_message(capsys)


def test_terminal_command_is_redacted_and_long_command_is_truncated(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")
    secret = "very-secret-value"
    command = f"deploy --token {secret} " + ("argument " * 30)

    log_ingested_event(
        activity=activity("terminal_finished", {"command": command}),
        status="created",
    )

    output = logged_message(capsys)
    assert "terminal_finished  deploy --token=[REDACTED]" in output
    assert secret not in output
    assert output.rstrip().endswith("…")
    assert len(output.split("  ", 2)[-1].rstrip()) <= 120


def test_missing_field_uses_unknown_fallback(monkeypatch, capsys):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")

    log_ingested_event(
        activity=activity("app_activated", {}),
        status="created",
    )

    assert "app_activated      <unknown>" in logged_message(capsys)


@pytest.mark.parametrize(
    ("status", "suffix"),
    [
        ("duplicate", "[duplicate]"),
        ("conflict", "[conflict]"),
    ],
)
def test_special_status_is_displayed(monkeypatch, capsys, status, suffix):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")

    log_ingested_event(
        activity=activity("terminal_finished", {"command": "git status"}),
        status=status,
    )

    output = logged_message(capsys)
    assert "terminal_finished  git status" in output
    assert output.rstrip().endswith(suffix)


def test_validation_rejection_does_not_log_payload(monkeypatch, capsys):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")

    log_ingested_event(
        activity=None,
        status="rejected",
        error="details.app: app must be a non-empty string",
    )

    output = logged_message(capsys)
    assert "invalid_event      details.app: app must be a non-empty string" in output
    assert output.rstrip().endswith("[rejected]")
    assert "payload_json" not in output


def test_route_logging_does_not_change_http_responses(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()
    payload = canonical_payload()

    created = client.post("/activities", json=payload)
    duplicate = client.post("/activities", json=payload)
    conflicting_payload = canonical_payload(app="Safari")
    conflict = client.post("/activities", json=conflicting_payload)
    rejected = client.post(
        "/activities",
        json=canonical_payload("invalid", app=""),
    )

    assert created.status_code == 201
    assert created.get_json()["accepted"] is True
    assert created.get_json()["duplicate"] is False
    assert duplicate.status_code == 200
    assert duplicate.get_json()["accepted"] is True
    assert duplicate.get_json()["duplicate"] is True
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "event_id_conflict"
    assert rejected.status_code == 400
    assert rejected.get_json() == {
        "error": {
            "code": "invalid_event",
            "field": "app",
            "message": "app must be a non-empty string",
        }
    }

    output = logged_message(capsys)
    assert "Visual Studio Code" in output
    assert "[duplicate]" in output
    assert "[conflict]" in output
    assert "[rejected]" in output
    assert json.dumps(payload) not in output

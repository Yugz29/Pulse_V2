import pytest

from daemon_v2.ingest import (
    IgnoredActivity,
    InvalidActivity,
    normalize_activity,
    normalize_event,
)


def test_normalizes_and_redacts_terminal_activity():
    activity = normalize_activity(
        {
            "type": "terminal_finished",
            "occurred_at": "2026-07-03T10:00:00+02:00",
            "command": "deploy --token very-secret",
            "exit_code": 0,
            "cwd": "~/project",
            "started_at": "2026-07-03T09:59:58+02:00",
            "finished_at": "2026-07-03T10:00:00+02:00",
        }
    )

    assert activity.source == "terminal"
    assert activity.details["command"] == "deploy --token=[REDACTED]"
    assert activity.details["started_at"] == "2026-07-03T09:59:58+02:00"
    assert activity.details["finished_at"] == "2026-07-03T10:00:00+02:00"
    assert activity.summary == "Command succeeded: deploy --token=[REDACTED]"


def test_rejects_unknown_activity_type():
    with pytest.raises(InvalidActivity):
        normalize_activity({"type": "browser_opened"})


def test_normalizes_app_activated_activity():
    activity = normalize_activity(
        {
            "type": "app_activated",
            "app": "Visual Studio Code",
        }
    )

    assert activity.source == "application"
    assert activity.details == {"app": "Visual Studio Code"}
    assert activity.summary == "Activated Visual Studio Code"


@pytest.mark.parametrize("event", ["modified", "created", "deleted"])
def test_normalizes_file_changed_activity(event):
    activity = normalize_activity(
        {
            "type": "file_changed",
            "path": "/project/daemon_v2/daily_trace.py",
            "event": event,
            "workspace": "/project",
        }
    )

    assert activity.source == "filesystem"
    assert activity.details == {
        "path": "/project/daemon_v2/daily_trace.py",
        "event": event,
        "workspace": "/project",
    }
    assert activity.summary == f"{event.capitalize()} /project/daemon_v2/daily_trace.py"


@pytest.mark.parametrize(
    "command",
    [
        "",
        "   ",
        "source ~/.zshrc",
    ],
)
def test_ignores_noisy_terminal_commands(command):
    with pytest.raises(IgnoredActivity):
        normalize_activity(
            {
                "type": "terminal_finished",
                "command": command,
                "exit_code": 0,
                "cwd": "/project",
            }
        )


@pytest.mark.parametrize(
    "command",
    [
        "curl http://127.0.0.1:5000/trace/today",
        "curl http://127.0.0.1:5000/trace/today.md",
        "curl -s http://127.0.0.1:5000/trace/days | python -m json.tool",
    ],
)
def test_keeps_pulse_inspection_commands_in_raw_activity(command):
    activity = normalize_activity(
        {
            "type": "terminal_finished",
            "command": command,
            "exit_code": 0,
            "cwd": "/project",
        }
    )

    assert activity.details["command"] == command


def test_removes_ignored_lines_from_multiline_command():
    activity = normalize_activity(
        {
            "type": "terminal_finished",
            "command": "clear\ngit status",
            "exit_code": 0,
            "cwd": "/project",
        }
    )

    assert activity.details["command"] == "git status"


def test_keeps_useful_lines_before_multiline_internal_curl():
    activity = normalize_activity(
        {
            "type": "terminal_finished",
            "command": (
                "git status\n"
                "curl -X POST http://127.0.0.1:5000/activities \\\n"
                "  -H 'Content-Type: application/json' \\\n"
                "  -d '{\n"
                '    \"type\": \"file_changed\"\n'
                "  }'"
            ),
            "exit_code": 0,
            "cwd": "/project",
        }
    )

    assert activity.details["command"] == "git status"


def canonical_payload(**overrides):
    payload = {
        "event_id": "019c-valid",
        "schema_version": 1,
        "type": "file_changed",
        "producer": {
            "name": "pulse-test",
            "version": "1.0",
            "instance_id": "test-instance",
        },
        "occurred_at": "2026-07-23T14:32:10.123+02:00",
        "details": {
            "path": "/project/main.py",
            "event": "modified",
        },
    }
    payload.update(overrides)
    return payload


def test_normalizes_complete_canonical_event():
    ingested = normalize_event(canonical_payload(unused_top_level="ignored"))

    assert ingested.event.event_id == "019c-valid"
    assert ingested.event.schema_version == 1
    assert ingested.event.producer_name == "pulse-test"
    assert ingested.event.occurred_at.isoformat() == "2026-07-23T14:32:10.123000+02:00"
    assert ingested.activity.details == {
        "path": "/project/main.py",
        "event": "modified",
    }
    assert "unused_top_level" not in ingested.activity.details


def test_preserves_enriched_terminal_context_sent_by_producer():
    workspace = {
        "project_name": "Pulse_Core",
        "workspace_root": "/project/Pulse_Core",
        "git_root": "/project/Pulse_Core",
        "resolution_method": "git",
        "resolution_confidence": "high",
    }
    git = {
        "repository": "Pulse_Core",
        "git_root": "/project/Pulse_Core",
        "branch": "main",
        "head": "1234567",
        "dirty": False,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
    }
    ingested = normalize_event(
        canonical_payload(
            type="terminal_finished",
            details={
                "command": "git status",
                "exit_code": 0,
                "cwd": "/project/Pulse_Core",
                "workspace": workspace,
                "git": git,
            },
        )
    )

    assert ingested.activity.details["workspace"] == workspace
    assert ingested.activity.details["git"] == git


def test_preserves_object_workspace_for_file_event():
    workspace = {
        "project_name": "Pulse_Core",
        "workspace_root": "/project/Pulse_Core",
    }
    ingested = normalize_event(
        canonical_payload(
            details={
                "path": "/project/Pulse_Core/main.py",
                "event": "modified",
                "workspace": workspace,
            }
        )
    )

    assert ingested.activity.details["workspace"] == workspace


def test_preserves_historical_direct_git_root():
    ingested = normalize_event(
        canonical_payload(
            details={
                "path": "/project/Pulse_Core/main.py",
                "event": "modified",
                "git_root": "/project/Pulse_Core",
            }
        )
    )

    assert ingested.activity.details["git_root"] == "/project/Pulse_Core"


def test_preserves_application_bundle_identifier():
    ingested = normalize_event(
        canonical_payload(
            type="app_activated",
            details={
                "app": "Visual Studio Code",
                "bundle_id": "com.microsoft.VSCode",
            },
        )
    )

    assert ingested.activity.details == {
        "app": "Visual Studio Code",
        "bundle_id": "com.microsoft.VSCode",
    }


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"event_id": None}, "event_id"),
        ({"event_id": ""}, "event_id"),
        ({"schema_version": 0}, "schema_version"),
        ({"schema_version": -1}, "schema_version"),
        ({"type": ""}, "type"),
        ({"producer": None}, "producer"),
        ({"producer": {"name": ""}}, "producer.name"),
        ({"occurred_at": "2026-07-23T14:32:10"}, "occurred_at"),
        ({"occurred_at": "not-a-date"}, "occurred_at"),
        ({"details": []}, "details"),
    ],
)
def test_rejects_invalid_canonical_fields(change, field):
    with pytest.raises(InvalidActivity) as raised:
        normalize_event(canonical_payload(**change))

    assert raised.value.field == field


def test_rejects_missing_event_id_on_otherwise_canonical_payload():
    payload = canonical_payload()
    del payload["event_id"]

    with pytest.raises(InvalidActivity) as raised:
        normalize_event(payload)

    assert raised.value.field == "event_id"


def test_legacy_timestamp_becomes_occurred_at_and_gets_explicit_producer():
    ingested = normalize_event(
        {
            "type": "app_activated",
            "timestamp": "2026-07-23T12:00:00+02:00",
            "app": "Terminal",
        }
    )

    assert ingested.legacy is True
    assert ingested.event.event_id
    assert ingested.event.schema_version == 1
    assert ingested.event.producer_name == "pulse-legacy"
    assert ingested.event.occurred_at.isoformat() == "2026-07-23T12:00:00+02:00"


def test_identical_legacy_requests_receive_different_event_ids():
    payload = {"type": "app_activated", "app": "Terminal"}

    first = normalize_event(payload)
    second = normalize_event(payload)

    assert first.event.event_id != second.event.event_id


def test_rejects_nan_in_canonical_details():
    with pytest.raises(InvalidActivity) as raised:
        normalize_event(
            canonical_payload(
                details={
                    "path": "/project/main.py",
                    "event": "modified",
                    "invalid_number": float("nan"),
                }
            )
        )

    assert raised.value.field == "details"
    assert "strictly valid JSON" in str(raised.value)

import pytest

from daemon_v2.ingest import IgnoredActivity, InvalidActivity, normalize_activity


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
        "curl http://127.0.0.1:5000/trace/today",
        "curl http://127.0.0.1:5000/trace/today.md",
        "  curl   http://127.0.0.1:5000/trace/today.md  ",
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

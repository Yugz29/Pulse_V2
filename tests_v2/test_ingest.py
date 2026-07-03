import pytest

from daemon_v2.ingest import InvalidActivity, normalize_activity


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

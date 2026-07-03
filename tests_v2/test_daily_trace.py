from datetime import date, datetime, timedelta, timezone

from daemon_v2.daily_trace import build_daily_trace
from daemon_v2.models import Activity
from daemon_v2.trace_store import TraceStore


def test_builds_structured_daily_trace(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
    store.append(Activity("file_changed", first_at, "filesystem", "Modified a.py", {"path": "a.py"}))
    store.append(
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            "Command succeeded: pytest",
            {"command": "pytest", "exit_code": 0, "cwd": "/project"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)

    assert trace["activity_count"] == 2
    assert trace["session_count"] == 1
    assert [item["type"] for item in trace["sessions"][0]["activities"]] == [
        "file_changed",
        "terminal_finished",
    ]

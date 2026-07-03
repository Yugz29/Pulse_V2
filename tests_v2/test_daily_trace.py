from datetime import date, datetime, timedelta, timezone

from daemon_v2.daily_trace import build_daily_trace, render_daily_trace_markdown
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

    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n"
        "\n"
        "## Session 1 — 08:00–08:05\n"
        "\n"
        "- 08:00 · **file\\_changed** — Modified a.py\n"
        "- 08:05 · **terminal\\_finished** — Command succeeded: pytest\n"
        "  - CWD : /project\n"
    )


def test_renders_empty_daily_trace():
    trace = {
        "date": "2026-07-03",
        "timezone": "UTC",
        "activity_count": 0,
        "session_count": 0,
        "sessions": [],
    }

    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n\n_Aucune activité._\n"
    )

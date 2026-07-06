import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from daemon_v2.daily_trace import build_daily_trace, render_daily_trace_markdown
from daemon_v2.models import Activity
from daemon_v2.trace_store import TraceStore


def activity(occurred_at):
    return Activity("file_changed", occurred_at, "filesystem", "Modified /tmp/a", {"path": "/tmp/a"})


def test_append_persists_activity_and_reuses_nearby_session(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)

    first = store.append(activity(first_at))
    second = store.append(activity(first_at + timedelta(minutes=10)))
    rows = store.activities_between(first_at, first_at + timedelta(hours=1))

    assert first.session_id == second.session_id
    assert [row.id for row in rows] == [first.id, second.id]


def test_activities_are_append_only(tmp_path):
    database = tmp_path / "pulse.sqlite3"
    store = TraceStore(database)
    stored = store.append(activity(datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)))

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE activities SET summary = 'changed' WHERE id = ?",
                (stored.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM activities WHERE id = ?", (stored.id,))


def test_out_of_order_activity_reuses_session_containing_its_timestamp(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 12, 28, tzinfo=timezone.utc)

    stored = [
        store.append(activity(first_at)),
        store.append(
            Activity(
                "app_activated",
                first_at + timedelta(minutes=22),
                "application",
                "Activated Code",
                {"app": "Code"},
            )
        ),
        store.append(
            Activity(
                "app_activated",
                first_at + timedelta(minutes=47),
                "application",
                "Activated Terminal",
                {"app": "Terminal"},
            )
        ),
        store.append(
            Activity(
                "app_activated",
                first_at + timedelta(minutes=57),
                "application",
                "Activated Code",
                {"app": "Code"},
            )
        ),
        store.append(
            Activity(
                "terminal_finished",
                first_at + timedelta(minutes=42),
                "terminal",
                "Command succeeded: pytest tests_v2",
                {"command": "pytest tests_v2", "exit_code": 0, "cwd": "/project"},
            )
        ),
    ]

    assert len({item.session_id for item in stored}) == 1
    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    assert trace["session_count"] == 1
    assert "## Session 1 — 12:28–12:28 · 0 min" in markdown
    assert "## Session 2 — 13:10–13:10 · 0 min" in markdown
    assert "## Activité passive" in markdown
    assert "- 12:50 · Code" in markdown
    assert "Apps actives : Terminal, Code" in markdown
    assert "pytest tests_v2" in markdown

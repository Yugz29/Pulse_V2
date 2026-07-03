import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

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

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest

from daemon_v2.daily_trace import build_daily_trace, render_daily_trace_markdown
from daemon_v2.ingest import normalize_event
from daemon_v2.models import Activity
from daemon_v2.trace_store import EventConflictError, TraceStore


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
    assert "Apps actives : Terminal, Code" not in markdown
    assert "- 12:50 · Code, Terminal" in markdown
    assert "pytest tests_v2" in markdown


def canonical_ingested(event_id="019c-store", **details):
    return normalize_event(
        {
            "event_id": event_id,
            "schema_version": 1,
            "type": "file_changed",
            "producer": {
                "name": "pulse-test",
                "version": "1.0",
                "instance_id": "store-tests",
            },
            "occurred_at": "2026-07-23T14:32:10.123+02:00",
            "details": {
                "path": "/project/main.py",
                "event": "modified",
                **details,
            },
        }
    )


def create_historical_database(database):
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                source TEXT NOT NULL,
                summary TEXT NOT NULL,
                details_json TEXT NOT NULL
            );
            CREATE INDEX idx_activities_occurred_at
                ON activities(occurred_at);
            CREATE INDEX idx_activities_session_id
                ON activities(session_id);
            CREATE TRIGGER activities_no_update
            BEFORE UPDATE ON activities
            BEGIN
                SELECT RAISE(ABORT, 'activities are append-only');
            END;
            CREATE TRIGGER activities_no_delete
            BEFORE DELETE ON activities
            BEGIN
                SELECT RAISE(ABORT, 'activities are append-only');
            END;
            """
        )
        connection.execute(
            """
            INSERT INTO activities (
                session_id, activity_type, occurred_at, recorded_at,
                source, summary, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "historical-session",
                "file_changed",
                "2026-07-22T10:00:00+00:00",
                "2026-07-22T10:00:01+00:00",
                "filesystem",
                "Modified /project/old.py",
                '{"event":"modified","path":"/project/old.py"}',
            ),
        )


def test_migrates_historical_schema_without_loss_and_is_idempotent(tmp_path):
    database = tmp_path / "historical.sqlite3"
    create_historical_database(database)

    TraceStore(database)
    store = TraceStore(database)
    rows = store.activities_between(
        datetime(2026, 7, 22, tzinfo=timezone.utc),
        datetime(2026, 7, 23, tzinfo=timezone.utc),
    )

    assert len(rows) == 1
    assert rows[0].event_id == "legacy-migrated:1"
    assert rows[0].schema_version == 0
    assert rows[0].producer_name == "pulse-legacy-migrated"
    assert rows[0].activity.details["path"] == "/project/old.py"
    assert rows[0].recorded_at.isoformat() == "2026-07-22T10:00:01+00:00"
    historical_trace = build_daily_trace(
        store,
        date(2026, 7, 22),
        timezone.utc,
    )
    exported = historical_trace["sessions"][0]["activities"][0]
    assert exported["event_id"] == "legacy-migrated:1"
    assert exported["schema_version"] == 0
    assert exported["producer"]["name"] == "pulse-legacy-migrated"
    assert exported["recorded_at"] == "2026-07-22T10:00:01+00:00"

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(activities)")
        }
        indexes = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA index_list(activities)")
        }
        count = connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE activities SET summary = 'changed' WHERE id = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM activities WHERE id = 1")

    assert {
        "event_id",
        "schema_version",
        "type",
        "producer_name",
        "producer_version",
        "producer_instance_id",
        "occurred_at",
        "recorded_at",
        "details_json",
    } <= columns
    assert indexes["idx_activities_event_id"] == 1
    assert count == 1


def test_same_event_id_with_different_payload_raises_conflict(tmp_path):
    database = tmp_path / "pulse.sqlite3"
    store = TraceStore(database)
    original = canonical_ingested(event_id="019c-conflict")
    conflicting = canonical_ingested(
        event_id="019c-conflict",
        path="/project/other.py",
    )

    store.append_event(original)
    with pytest.raises(EventConflictError):
        store.append_event(conflicting)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT event_id, details_json
            FROM activities
            WHERE event_id = ?
            """,
            ("019c-conflict",),
        ).fetchall()

    assert len(rows) == 1
    assert "/project/main.py" in rows[0][1]
    assert "/project/other.py" not in rows[0][1]


def test_unique_new_event_and_concurrent_retry_create_one_row(tmp_path):
    database = tmp_path / "concurrent.sqlite3"
    store = TraceStore(database)
    ingested = canonical_ingested()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.append_event(ingested), range(2)))

    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE event_id = ?",
            (ingested.event.event_id,),
        ).fetchone()[0]

    assert count == 1
    assert sorted(item.duplicate for item in results) == [False, True]
    assert results[0].recorded_at == results[1].recorded_at

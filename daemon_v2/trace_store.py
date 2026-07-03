"""SQLite append-only activity store with derived session metadata."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Activity, Session, StoredActivity
from .session_tracker import select_session


SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_occurred_at ON activities(occurred_at);
CREATE INDEX IF NOT EXISTS idx_activities_session_id ON activities(session_id);
CREATE TRIGGER IF NOT EXISTS activities_no_update
BEFORE UPDATE ON activities
BEGIN
    SELECT RAISE(ABORT, 'activities are append-only');
END;
CREATE TRIGGER IF NOT EXISTS activities_no_delete
BEFORE DELETE ON activities
BEGIN
    SELECT RAISE(ABORT, 'activities are append-only');
END;
"""


class TraceStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def append(self, activity: Activity) -> StoredActivity:
        recorded_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._latest_session(connection)
            session_id = select_session(activity.occurred_at_utc, latest)
            cursor = connection.execute(
                """
                INSERT INTO activities (
                    session_id, activity_type, occurred_at, recorded_at,
                    source, summary, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    activity.activity_type,
                    activity.occurred_at_utc.isoformat(),
                    recorded_at.isoformat(),
                    activity.source,
                    activity.summary,
                    json.dumps(activity.details, sort_keys=True),
                ),
            )
            activity_id = int(cursor.lastrowid)
        return StoredActivity(activity_id, session_id, activity, recorded_at)

    def _latest_session(self, connection: sqlite3.Connection) -> Session | None:
        row = connection.execute(
            """
            SELECT session_id, MIN(occurred_at) AS started_at,
                   MAX(occurred_at) AS ended_at, COUNT(*) AS activity_count
            FROM activities
            GROUP BY session_id
            ORDER BY ended_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return Session(
            id=row["session_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]),
            activity_count=row["activity_count"],
        )

    def activities_between(self, start: datetime, end: datetime) -> list[StoredActivity]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM activities
                WHERE occurred_at >= ? AND occurred_at < ?
                ORDER BY occurred_at ASC, id ASC
                """,
                (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()),
            ).fetchall()
        return [self._row_to_stored_activity(row) for row in rows]

    @staticmethod
    def _row_to_stored_activity(row: sqlite3.Row) -> StoredActivity:
        activity = Activity(
            activity_type=row["activity_type"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            source=row["source"],
            summary=row["summary"],
            details=json.loads(row["details_json"]),
        )
        return StoredActivity(
            id=row["id"],
            session_id=row["session_id"],
            activity=activity,
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

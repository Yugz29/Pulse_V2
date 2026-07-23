"""SQLite append-only activity store with versioned, idempotent events."""

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path

from .models import (
    Activity,
    CanonicalEvent,
    IngestedEvent,
    Session,
    StoredActivity,
    canonical_event_fingerprint,
)
from .session_tracker import select_session


CREATE_ACTIVITIES_TABLE = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    type TEXT NOT NULL,
    producer_name TEXT NOT NULL,
    producer_version TEXT NULL,
    producer_instance_id TEXT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    event_fingerprint TEXT NULL,
    activity_type TEXT NOT NULL,
    source TEXT NOT NULL,
    summary TEXT NOT NULL
)
"""

INDEX_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_event_id ON activities(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_activities_occurred_at ON activities(occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_activities_session_id ON activities(session_id)",
)

TRIGGER_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS activities_no_update
    BEFORE UPDATE ON activities
    BEGIN
        SELECT RAISE(ABORT, 'activities are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS activities_no_delete
    BEFORE DELETE ON activities
    BEGIN
        SELECT RAISE(ABORT, 'activities are append-only');
    END
    """,
)


class EventConflictError(ValueError):
    """An event_id was reused with different canonical content."""

    def __init__(self, event_id: str) -> None:
        super().__init__(
            f"event_id already exists with different canonical content: {event_id}"
        )
        self.event_id = event_id


class TraceStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        """Create or transactionally migrate the single activities table.

        Historical rows receive a deterministic ``legacy-migrated:<row id>``
        event_id, schema_version 0, producer ``pulse-legacy-migrated``, and
        ``type`` copied from ``activity_type``. The historical Core schema
        already recorded both timestamps, so those values are retained. If
        only one timestamp had existed, copying it to ``recorded_at`` would
        merely have been a documented migration approximation.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(CREATE_ACTIVITIES_TABLE)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(activities)")
            }

            # Temporarily remove append-only guards only inside the migration
            # transaction; they are recreated before commit.
            connection.execute("DROP TRIGGER IF EXISTS activities_no_update")
            connection.execute("DROP TRIGGER IF EXISTS activities_no_delete")

            additions = {
                "event_id": "TEXT",
                "schema_version": "INTEGER",
                "type": "TEXT",
                "producer_name": "TEXT",
                "producer_version": "TEXT",
                "producer_instance_id": "TEXT",
                "event_fingerprint": "TEXT",
            }
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE activities ADD COLUMN {name} {sql_type}"
                    )

            connection.execute(
                """
                UPDATE activities
                SET event_id = 'legacy-migrated:' || id
                WHERE event_id IS NULL OR event_id = ''
                """
            )
            connection.execute(
                "UPDATE activities SET schema_version = 0 WHERE schema_version IS NULL"
            )
            connection.execute(
                """
                UPDATE activities
                SET type = activity_type
                WHERE type IS NULL OR type = ''
                """
            )
            connection.execute(
                """
                UPDATE activities
                SET producer_name = 'pulse-legacy-migrated'
                WHERE producer_name IS NULL OR producer_name = ''
                """
            )

            for statement in INDEX_STATEMENTS:
                connection.execute(statement)
            for statement in TRIGGER_STATEMENTS:
                connection.execute(statement)

    def append_event(self, ingested: IngestedEvent) -> StoredActivity:
        event = ingested.event
        activity = ingested.activity
        details_json = json.dumps(
            activity.details,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM activities WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["event_fingerprint"] != ingested.fingerprint:
                    raise EventConflictError(event.event_id)
                stored = self._row_to_stored_activity(existing)
                return StoredActivity(
                    id=stored.id,
                    session_id=stored.session_id,
                    activity=stored.activity,
                    event_id=stored.event_id,
                    schema_version=stored.schema_version,
                    producer_name=stored.producer_name,
                    producer_version=stored.producer_version,
                    producer_instance_id=stored.producer_instance_id,
                    recorded_at=stored.recorded_at,
                    duplicate=True,
                )

            recorded_at = datetime.now(timezone.utc)
            session_id = select_session(
                activity.occurred_at_utc,
                self._sessions(connection),
            )
            cursor = connection.execute(
                """
                INSERT INTO activities (
                    session_id, event_id, schema_version, type,
                    producer_name, producer_version, producer_instance_id,
                    occurred_at, recorded_at, details_json, event_fingerprint,
                    activity_type, source, summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event.event_id,
                    event.schema_version,
                    event.event_type,
                    event.producer_name,
                    event.producer_version,
                    event.producer_instance_id,
                    event.occurred_at.isoformat(),
                    recorded_at.isoformat(),
                    details_json,
                    ingested.fingerprint,
                    activity.activity_type,
                    activity.source,
                    activity.summary,
                ),
            )
            activity_id = int(cursor.lastrowid)

        return StoredActivity(
            id=activity_id,
            session_id=session_id,
            activity=activity,
            event_id=event.event_id,
            schema_version=event.schema_version,
            producer_name=event.producer_name,
            producer_version=event.producer_version,
            producer_instance_id=event.producer_instance_id,
            recorded_at=recorded_at,
        )

    def append(self, activity: Activity) -> StoredActivity:
        """Compatibility helper for existing internal callers and tests."""
        event = CanonicalEvent(
            event_id=str(uuid.uuid4()),
            schema_version=1,
            event_type=activity.activity_type,
            producer_name="pulse-internal-legacy",
            producer_version=None,
            producer_instance_id=None,
            occurred_at=activity.occurred_at,
            details=activity.details,
        )
        return self.append_event(
            IngestedEvent(
                event=event,
                activity=activity,
                fingerprint=canonical_event_fingerprint(event),
                legacy=True,
            )
        )

    def _sessions(self, connection: sqlite3.Connection) -> list[Session]:
        rows = connection.execute(
            """
            SELECT session_id, occurred_at
            FROM activities
            ORDER BY julianday(occurred_at) ASC, id ASC
            """
        ).fetchall()
        grouped: dict[str, list[datetime]] = {}
        for row in rows:
            grouped.setdefault(row["session_id"], []).append(
                datetime.fromisoformat(row["occurred_at"])
            )
        return [
            Session(
                id=session_id,
                started_at=min(timestamps),
                ended_at=max(timestamps),
                activity_count=len(timestamps),
            )
            for session_id, timestamps in grouped.items()
        ]

    def activities_between(self, start: datetime, end: datetime) -> list[StoredActivity]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM activities
                WHERE julianday(occurred_at) >= julianday(?)
                  AND julianday(occurred_at) < julianday(?)
                ORDER BY julianday(occurred_at) ASC, id ASC
                """,
                (
                    start.astimezone(timezone.utc).isoformat(),
                    end.astimezone(timezone.utc).isoformat(),
                ),
            ).fetchall()
        return [self._row_to_stored_activity(row) for row in rows]

    def activity_dates(self, local_timezone: tzinfo) -> list[date]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT occurred_at FROM activities"
            ).fetchall()
        return sorted(
            {
                datetime.fromisoformat(row["occurred_at"])
                .astimezone(local_timezone)
                .date()
                for row in rows
            },
            reverse=True,
        )

    @staticmethod
    def _row_to_stored_activity(row: sqlite3.Row) -> StoredActivity:
        event_type = row["type"] or row["activity_type"]
        activity = Activity(
            activity_type=event_type,
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            source=row["source"],
            summary=row["summary"],
            details=json.loads(row["details_json"]),
        )
        return StoredActivity(
            id=row["id"],
            session_id=row["session_id"],
            activity=activity,
            event_id=row["event_id"],
            schema_version=row["schema_version"],
            producer_name=row["producer_name"],
            producer_version=row["producer_version"],
            producer_instance_id=row["producer_instance_id"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

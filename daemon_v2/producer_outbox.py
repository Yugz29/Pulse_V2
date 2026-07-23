"""Durable SQLite outbox for local Pulse Core producers."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .git_context import read_git_context
from .ingest import filter_terminal_command, normalize_event, redact_command
from .models import CanonicalEvent
from .workspace_context import read_workspace_context


DEFAULT_PRODUCER_NAME = "pulse-zsh"
DEFAULT_PRODUCER_VERSION = "1.0"


@dataclass(frozen=True)
class PendingEvent:
    event_id: str
    payload_json: str
    created_at: str
    attempts: int
    last_attempt_at: str | None
    next_attempt_at: str | None
    last_error: str | None


class ProducerOutbox:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = str(database_path or default_outbox_path())
        Path(self.database_path).expanduser().parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    next_attempt_at TEXT,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_fifo
                    ON events(created_at);

                CREATE TABLE IF NOT EXISTS dead_letters (
                    event_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    http_status INTEGER,
                    response_body TEXT,
                    failed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS producer_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def producer_instance_id(self) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM producer_metadata WHERE key = 'instance_id'"
            ).fetchone()
            if row is not None and str(row["value"]).strip():
                return str(row["value"])
            instance_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO producer_metadata(key, value)
                VALUES ('instance_id', ?)
                """,
                (instance_id,),
            )
            return instance_id

    def enqueue_payload(self, payload_json: str, *, created_at: str | None = None) -> str:
        """Persist the exact canonical JSON that the worker will later send."""
        payload = _strict_json_object(payload_json)
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("payload event_id must be a non-empty string")
        timestamp = created_at or utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events(event_id, payload_json, created_at)
                VALUES (?, ?, ?)
                """,
                (event_id, payload_json, timestamp),
            )
        return event_id

    def oldest(self) -> PendingEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM events
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """
            ).fetchone()
        return _pending_from_row(row) if row is not None else None

    def delete(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM events WHERE event_id = ?",
                (event_id,),
            )

    def mark_retry(
        self,
        event_id: str,
        *,
        attempted_at: datetime,
        next_attempt_at: datetime,
        error: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE events
                SET attempts = attempts + 1,
                    last_attempt_at = ?,
                    next_attempt_at = ?,
                    last_error = ?
                WHERE event_id = ?
                """,
                (
                    attempted_at.isoformat(),
                    next_attempt_at.isoformat(),
                    error,
                    event_id,
                ),
            )

    def move_to_dead_letter(
        self,
        pending: PendingEvent,
        *,
        error: str,
        http_status: int | None,
        response_body: str | None,
        failed_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO dead_letters(
                    event_id, payload_json, error,
                    http_status, response_body, failed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pending.event_id,
                    pending.payload_json,
                    error,
                    http_status,
                    response_body,
                    failed_at.isoformat(),
                ),
            )
            connection.execute(
                "DELETE FROM events WHERE event_id = ?",
                (pending.event_id,),
            )

    def counts(self) -> tuple[int, int]:
        with self._connect() as connection:
            pending = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            dead = connection.execute(
                "SELECT COUNT(*) FROM dead_letters"
            ).fetchone()[0]
        return int(pending), int(dead)

    def inspect_dead_letters(self, *, limit: int) -> list[dict[str, Any]]:
        """Return recent dead letters without modifying or replaying them."""
        if limit <= 0:
            raise ValueError("limit must be a strictly positive integer")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, payload_json, error, http_status
                FROM dead_letters
                ORDER BY failed_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        inspected: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = _strict_json_object(row["payload_json"])
                event_type = payload.get("type")
            except (json.JSONDecodeError, TypeError, ValueError):
                event_type = None
            inspected.append(
                {
                    "event_id": row["event_id"],
                    "type": event_type,
                    "last_error": row["error"],
                    "http_status": row["http_status"],
                    "payload_json": row["payload_json"],
                }
            )
        return inspected


def default_outbox_path() -> Path:
    configured = os.environ.get("PULSE_CORE_OUTBOX_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".pulse_core" / "outbox.sqlite3"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_terminal_payload(
    outbox: ProducerOutbox,
    *,
    command: str,
    cwd: str,
    exit_code: int,
    started_at: str,
    finished_at: str,
) -> str | None:
    """Build the final canonical event, redacting before SQLite persistence."""
    filtered_command = filter_terminal_command(command)
    if filtered_command is None:
        return None
    redacted_command = redact_command(filtered_command)
    occurred_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    if occurred_at.tzinfo is None:
        raise ValueError("finished_at must include a timezone")
    details: dict[str, Any] = {
        "command": redacted_command,
        "exit_code": exit_code,
        "cwd": cwd,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    git_context = read_git_context(Path(cwd))
    if git_context is not None:
        details["git"] = git_context.as_details()
    workspace_context = read_workspace_context(
        Path(cwd),
        git_context=git_context,
    )
    if workspace_context is not None:
        details["workspace"] = workspace_context.as_details()
    event = CanonicalEvent(
        event_id=str(uuid.uuid4()),
        schema_version=1,
        event_type="terminal_finished",
        producer_name=DEFAULT_PRODUCER_NAME,
        producer_version=DEFAULT_PRODUCER_VERSION,
        producer_instance_id=outbox.producer_instance_id(),
        occurred_at=occurred_at,
        details=details,
    )
    payload: dict[str, Any] = {
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "type": event.event_type,
        "producer": {
            "name": event.producer_name,
            "version": event.producer_version,
            "instance_id": event.producer_instance_id,
        },
        "occurred_at": event.occurred_at.isoformat(),
        "details": event.details,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def enqueue_terminal_input(
    outbox: ProducerOutbox,
    raw_input: str,
) -> str | None:
    request = _strict_json_object(raw_input)
    required = ("command", "cwd", "exit_code", "started_at", "finished_at")
    missing = [key for key in required if key not in request]
    if missing:
        raise ValueError(f"missing terminal fields: {', '.join(missing)}")
    payload_json = build_terminal_payload(
        outbox,
        command=str(request["command"]),
        cwd=str(request["cwd"]),
        exit_code=int(request["exit_code"]),
        started_at=str(request["started_at"]),
        finished_at=str(request["finished_at"]),
    )
    if payload_json is None:
        return None
    return outbox.enqueue_payload(payload_json)


def enqueue_json_input(outbox: ProducerOutbox, raw_input: str) -> str:
    """Validate one canonical object, then persist its original JSON exactly."""
    payload = _strict_json_object(raw_input)
    required = {
        "event_id",
        "schema_version",
        "type",
        "producer",
        "occurred_at",
        "details",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"missing canonical fields: {', '.join(missing)}")
    normalize_event(payload)
    return outbox.enqueue_payload(raw_input)


def _strict_json_object(raw: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    parsed = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError("JSON payload must be an object")
    return parsed


def _pending_from_row(row: sqlite3.Row) -> PendingEvent:
    return PendingEvent(
        event_id=row["event_id"],
        payload_json=row["payload_json"],
        created_at=row["created_at"],
        attempts=row["attempts"],
        last_attempt_at=row["last_attempt_at"],
        next_attempt_at=row["next_attempt_at"],
        last_error=row["last_error"],
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pulse producer outbox")
    parser.add_argument("--database", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "enqueue-terminal",
        help="read terminal observation JSON from stdin and enqueue it",
    )
    subparsers.add_parser(
        "enqueue-json",
        help="validate and enqueue one canonical JSON object from stdin",
    )
    subparsers.add_parser(
        "instance-id",
        help="print the stable producer instance identifier",
    )
    inspect_parser = subparsers.add_parser(
        "inspect-dead-letter",
        help="show recent dead letters without replaying them",
    )
    inspect_parser.add_argument("--limit", type=int, default=10)
    subparsers.add_parser("status", help="show pending and dead-letter counts")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    outbox = ProducerOutbox(args.database)
    try:
        if args.command == "enqueue-terminal":
            event_id = enqueue_terminal_input(outbox, sys.stdin.read())
            if event_id:
                print(event_id)
            return
        if args.command == "enqueue-json":
            print(enqueue_json_input(outbox, sys.stdin.read()))
            return
        if args.command == "instance-id":
            print(outbox.producer_instance_id())
            return
        if args.command == "inspect-dead-letter":
            inspected = outbox.inspect_dead_letters(limit=args.limit)
            print(
                json.dumps(
                    inspected,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            )
            return
    except Exception as exc:
        print(f"Pulse outbox: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    pending, dead = outbox.counts()
    print("Outbox")
    print(f"{pending} événement{'s' if pending != 1 else ''}")
    print("Dead-letter")
    print(f"{dead} événement{'s' if dead != 1 else ''}")


if __name__ == "__main__":
    main()

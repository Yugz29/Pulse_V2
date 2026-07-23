import json
import sqlite3
from datetime import datetime, timedelta, timezone

from daemon_v2.outbox_worker import HttpResult, OutboxWorker, TemporaryDeliveryError
from daemon_v2.producer_outbox import (
    ProducerOutbox,
    build_terminal_payload,
    enqueue_terminal_input,
    main,
)


def canonical_payload(event_id: str, *, path: str = "/project/main.py") -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "schema_version": 1,
            "type": "file_changed",
            "producer": {
                "name": "pulse-test",
                "version": "1.0",
                "instance_id": "outbox-tests",
            },
            "occurred_at": "2026-07-23T14:32:10+02:00",
            "details": {
                "path": path,
                "event": "modified",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def ack(event_id: str, *, status: int = 201) -> HttpResult:
    return HttpResult(
        status=status,
        body=json.dumps({"accepted": True, "event_id": event_id}),
    )


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def read_pending(database, event_id):
    with sqlite3.connect(database) as connection:
        return connection.execute(
            """
            SELECT payload_json, attempts, last_error
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()


def read_dead_letter(database, event_id):
    with sqlite3.connect(database) as connection:
        return connection.execute(
            """
            SELECT payload_json, error, http_status, response_body
            FROM dead_letters
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()


def test_unavailable_core_keeps_event_then_sends_when_core_returns(tmp_path):
    database = tmp_path / "outbox.sqlite3"
    outbox = ProducerOutbox(database)
    payload = canonical_payload("event-recovery")
    outbox.enqueue_payload(payload)
    clock = Clock()
    available = False

    def sender(raw):
        if not available:
            raise TemporaryDeliveryError("connection refused")
        assert raw == payload
        return ack("event-recovery")

    worker = OutboxWorker(outbox, sender=sender, now=clock)
    assert worker.process_one() == "retry"
    pending = read_pending(database, "event-recovery")
    assert pending[0] == payload
    assert pending[1] == 1

    available = True
    clock.advance(1)
    assert worker.process_one() == "sent"
    assert outbox.counts() == (0, 0)


def test_success_and_duplicate_ack_delete_event(tmp_path):
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue_payload(canonical_payload("first"))
    worker = OutboxWorker(outbox, sender=lambda _: ack("first", status=201))

    assert worker.process_one() == "sent"
    assert outbox.counts() == (0, 0)

    outbox.enqueue_payload(canonical_payload("duplicate"))
    worker = OutboxWorker(outbox, sender=lambda _: ack("duplicate", status=200))
    assert worker.process_one() == "sent"
    assert outbox.counts() == (0, 0)


def test_conflict_and_bad_request_move_to_dead_letter(tmp_path):
    database = tmp_path / "outbox.sqlite3"
    outbox = ProducerOutbox(database)
    outbox.enqueue_payload(canonical_payload("conflict"))
    worker = OutboxWorker(
        outbox,
        sender=lambda _: HttpResult(409, '{"error":"conflict"}'),
    )

    assert worker.process_one() == "dead-letter"
    conflict = read_dead_letter(database, "conflict")
    assert conflict[2] == 409
    assert canonical_payload("conflict") == conflict[0]

    outbox.enqueue_payload(canonical_payload("invalid"))
    worker = OutboxWorker(
        outbox,
        sender=lambda _: HttpResult(400, '{"error":"invalid"}'),
    )
    assert worker.process_one() == "dead-letter"
    assert read_dead_letter(database, "invalid")[2] == 400
    assert outbox.counts() == (0, 2)


def test_server_error_and_timeout_retry_without_changing_payload(tmp_path):
    database = tmp_path / "outbox.sqlite3"
    outbox = ProducerOutbox(database)
    original = canonical_payload("server-error")
    outbox.enqueue_payload(original)
    clock = Clock()
    worker = OutboxWorker(
        outbox,
        sender=lambda _: HttpResult(500, "temporary"),
        now=clock,
    )

    assert worker.process_one() == "retry"
    row = read_pending(database, "server-error")
    assert row[0] == original
    assert row[1] == 1

    outbox.enqueue_payload(canonical_payload("timeout"))
    # FIFO deliberately keeps the second event behind the first retry.
    clock.advance(1)
    worker = OutboxWorker(
        outbox,
        sender=lambda _: ack("server-error"),
        now=clock,
    )
    assert worker.process_one() == "sent"

    def timeout_sender(_):
        raise TimeoutError("timed out")

    worker = OutboxWorker(outbox, sender=timeout_sender, now=clock)
    assert worker.process_one() == "retry"
    timeout_row = read_pending(database, "timeout")
    assert timeout_row[1] == 1
    assert "timed out" in timeout_row[2]


def test_protocol_errors_dead_letter_even_on_2xx(tmp_path):
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")
    cases = [
        ("invalid-json", HttpResult(201, "not-json")),
        ("not-accepted", HttpResult(201, '{"accepted":false,"event_id":"not-accepted"}')),
        ("wrong-id", HttpResult(200, '{"accepted":true,"event_id":"other"}')),
        ("no-content", HttpResult(204, "")),
    ]

    for event_id, result in cases:
        outbox.enqueue_payload(canonical_payload(event_id))
        worker = OutboxWorker(outbox, sender=lambda _, response=result: response)
        assert worker.process_one() == "dead-letter"

    assert outbox.counts() == (0, 4)


def test_multiple_events_are_delivered_fifo(tmp_path):
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")
    for index in range(3):
        outbox.enqueue_payload(
            canonical_payload(f"event-{index}"),
            created_at=f"2026-07-23T12:00:0{index}+00:00",
        )
    delivered = []

    def sender(raw):
        event_id = json.loads(raw)["event_id"]
        delivered.append(event_id)
        return ack(event_id)

    worker = OutboxWorker(outbox, sender=sender)
    assert [worker.process_one() for _ in range(3)] == ["sent", "sent", "sent"]
    assert delivered == ["event-0", "event-1", "event-2"]


def test_pending_event_survives_outbox_and_worker_restart(tmp_path):
    database = tmp_path / "outbox.sqlite3"
    first_outbox = ProducerOutbox(database)
    payload = canonical_payload("after-restart")
    first_outbox.enqueue_payload(payload)

    restarted_outbox = ProducerOutbox(database)
    restarted_worker = OutboxWorker(
        restarted_outbox,
        sender=lambda raw: ack(json.loads(raw)["event_id"]),
    )

    assert restarted_worker.process_one() == "sent"
    assert restarted_outbox.counts() == (0, 0)


def test_terminal_payload_is_redacted_before_exact_json_is_enqueued(tmp_path):
    database = tmp_path / "outbox.sqlite3"
    outbox = ProducerOutbox(database)
    raw_secret = "super-secret-value"
    raw_input = json.dumps(
        {
            "command": f"deploy --token {raw_secret}",
            "cwd": "/project",
            "exit_code": 0,
            "started_at": "2026-07-23T14:31:00+02:00",
            "finished_at": "2026-07-23T14:32:00+02:00",
        }
    )

    event_id = enqueue_terminal_input(outbox, raw_input)
    pending = outbox.oldest()

    assert event_id
    assert pending is not None
    assert raw_secret not in pending.payload_json
    assert "[REDACTED]" in pending.payload_json
    payload = json.loads(pending.payload_json)
    assert payload["producer"]["name"] == "pulse-zsh"
    assert payload["producer"]["version"] == "1.0"
    assert payload["producer"]["instance_id"]
    assert payload["event_id"] == event_id


def test_producer_instance_id_is_stable_across_restarts(tmp_path):
    database = tmp_path / "outbox.sqlite3"
    first = ProducerOutbox(database).producer_instance_id()
    second = ProducerOutbox(database).producer_instance_id()

    assert first
    assert second == first


def test_status_cli_reports_outbox_and_dead_letter_counts(
    tmp_path,
    monkeypatch,
    capsys,
):
    database = tmp_path / "outbox.sqlite3"
    outbox = ProducerOutbox(database)
    outbox.enqueue_payload(canonical_payload("pending"))
    dead = outbox.oldest()
    assert dead is not None
    outbox.move_to_dead_letter(
        dead,
        error="test",
        http_status=400,
        response_body="bad",
        failed_at=datetime.now(timezone.utc),
    )
    outbox.enqueue_payload(canonical_payload("still-pending"))

    monkeypatch.setattr(
        "sys.argv",
        ["producer_outbox", "--database", str(database), "status"],
    )
    main()

    assert capsys.readouterr().out == (
        "Outbox\n"
        "1 événement\n"
        "Dead-letter\n"
        "1 événement\n"
    )


def test_build_terminal_payload_does_not_regenerate_fields_after_enqueue(tmp_path):
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")
    payload = build_terminal_payload(
        outbox,
        command="pytest -q",
        cwd="/project",
        exit_code=0,
        started_at="2026-07-23T14:31:00+02:00",
        finished_at="2026-07-23T14:32:00+02:00",
    )
    assert payload is not None
    outbox.enqueue_payload(payload)

    sent = []
    worker = OutboxWorker(
        outbox,
        sender=lambda raw: sent.append(raw) or ack(json.loads(raw)["event_id"]),
    )
    assert worker.process_one() == "sent"
    assert sent == [payload]

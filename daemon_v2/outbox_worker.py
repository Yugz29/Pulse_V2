"""Synchronous FIFO delivery worker for the Pulse producer outbox."""

from __future__ import annotations

import argparse
import fcntl
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .producer_outbox import PendingEvent, ProducerOutbox, default_outbox_path
from .runtime_config import activities_url


MAX_BACKOFF_SECONDS = 300


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: str


class TemporaryDeliveryError(Exception):
    pass


class OutboxWorker:
    def __init__(
        self,
        outbox: ProducerOutbox,
        *,
        sender: Callable[[str], HttpResult] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.outbox = outbox
        self.sender = sender or post_payload
        self.now = now or (lambda: datetime.now(timezone.utc))

    def process_one(self) -> str:
        pending = self.outbox.oldest()
        if pending is None:
            return "empty"

        attempted_at = self.now()
        if pending.next_attempt_at:
            next_attempt = datetime.fromisoformat(pending.next_attempt_at)
            if next_attempt > attempted_at:
                return "waiting"

        try:
            result = self.sender(pending.payload_json)
        except (TemporaryDeliveryError, TimeoutError, OSError) as exc:
            self._retry(pending, attempted_at, str(exc))
            return "retry"

        if result.status in {408, 429} or 500 <= result.status <= 599:
            self._retry(
                pending,
                attempted_at,
                f"HTTP {result.status}",
            )
            return "retry"

        if result.status in {200, 201}:
            error = validate_ack(result, pending.event_id)
            if error is None:
                self.outbox.delete(pending.event_id)
                return "sent"
            self._dead_letter(pending, attempted_at, result, error)
            return "dead-letter"

        if result.status in {204, 400, 409}:
            self._dead_letter(
                pending,
                attempted_at,
                result,
                f"permanent HTTP {result.status}",
            )
            return "dead-letter"

        self._dead_letter(
            pending,
            attempted_at,
            result,
            f"unexpected HTTP {result.status}",
        )
        return "dead-letter"

    def _retry(
        self,
        pending: PendingEvent,
        attempted_at: datetime,
        error: str,
    ) -> None:
        delay = min(2 ** pending.attempts, MAX_BACKOFF_SECONDS)
        self.outbox.mark_retry(
            pending.event_id,
            attempted_at=attempted_at,
            next_attempt_at=attempted_at + timedelta(seconds=delay),
            error=error,
        )

    def _dead_letter(
        self,
        pending: PendingEvent,
        failed_at: datetime,
        result: HttpResult,
        error: str,
    ) -> None:
        self.outbox.move_to_dead_letter(
            pending,
            error=error,
            http_status=result.status,
            response_body=result.body,
            failed_at=failed_at,
        )


def validate_ack(result: HttpResult, expected_event_id: str) -> str | None:
    try:
        payload = json.loads(result.body)
    except (json.JSONDecodeError, TypeError):
        return "invalid JSON acknowledgement"
    if not isinstance(payload, dict):
        return "acknowledgement must be a JSON object"
    if payload.get("accepted") is not True:
        return "acknowledgement accepted is not true"
    if payload.get("event_id") != expected_event_id:
        return "acknowledgement event_id mismatch"
    return None


def post_payload(
    payload_json: str,
    *,
    url: str | None = None,
    timeout: float = 2.0,
) -> HttpResult:
    selected_url = url or activities_url()
    request = Request(
        selected_url,
        data=payload_json.encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return HttpResult(
                status=response.status,
                body=response.read().decode("utf-8", errors="replace"),
            )
    except HTTPError as exc:
        return HttpResult(
            status=exc.code,
            body=exc.read().decode("utf-8", errors="replace"),
        )
    except (URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise TemporaryDeliveryError(str(exc)) from exc


def run_forever(worker: OutboxWorker, *, poll_interval: float = 1.0) -> None:
    while True:
        outcome = worker.process_one()
        if outcome in {"empty", "waiting", "retry"}:
            time.sleep(poll_interval)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pulse outbox delivery worker")
    parser.add_argument("--database", type=Path, default=default_outbox_path())
    parser.add_argument("--url", default=activities_url())
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    outbox = ProducerOutbox(args.database)
    lock_path = Path(str(args.database) + ".worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        worker = OutboxWorker(
            outbox,
            sender=lambda payload: post_payload(payload, url=args.url),
        )
        if args.once:
            worker.process_one()
            return
        run_forever(worker)


if __name__ == "__main__":
    main()

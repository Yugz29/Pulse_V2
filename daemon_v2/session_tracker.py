"""Minimal inactivity-gap session logic."""

from datetime import datetime, timedelta
from typing import Iterable
from uuid import uuid4

from .models import Session


DEFAULT_SESSION_GAP = timedelta(minutes=30)


def select_session(
    occurred_at: datetime,
    sessions: Iterable[Session],
    gap: timedelta = DEFAULT_SESSION_GAP,
) -> str:
    candidates = []
    for session in sessions:
        if session.started_at <= occurred_at <= session.ended_at:
            distance = timedelta(0)
        elif occurred_at < session.started_at:
            distance = session.started_at - occurred_at
        else:
            distance = occurred_at - session.ended_at
        if distance <= gap:
            candidates.append((distance, session.started_at, session.id))
    if candidates:
        return min(candidates)[2]
    return uuid4().hex

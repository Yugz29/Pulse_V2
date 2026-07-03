"""Minimal inactivity-gap session logic."""

from datetime import datetime, timedelta
from uuid import uuid4

from .models import Session


DEFAULT_SESSION_GAP = timedelta(minutes=30)


def select_session(
    occurred_at: datetime,
    latest_session: Session | None,
    gap: timedelta = DEFAULT_SESSION_GAP,
) -> str:
    if latest_session is None:
        return uuid4().hex
    distance = occurred_at - latest_session.ended_at
    if timedelta(0) <= distance <= gap:
        return latest_session.id
    return uuid4().hex

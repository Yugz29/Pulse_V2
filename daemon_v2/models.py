"""Small domain models used by the ingestion and trace layers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SUPPORTED_ACTIVITY_TYPES = {"file_changed", "terminal_finished"}


@dataclass(frozen=True)
class Activity:
    activity_type: str
    occurred_at: datetime
    source: str
    summary: str
    details: dict[str, Any]

    def __post_init__(self) -> None:
        if self.activity_type not in SUPPORTED_ACTIVITY_TYPES:
            raise ValueError(f"unsupported activity type: {self.activity_type}")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        if not self.source:
            raise ValueError("source must not be empty")
        if not self.summary:
            raise ValueError("summary must not be empty")

    @property
    def occurred_at_utc(self) -> datetime:
        return self.occurred_at.astimezone(timezone.utc)


@dataclass(frozen=True)
class StoredActivity:
    id: int
    session_id: str
    activity: Activity
    recorded_at: datetime


@dataclass(frozen=True)
class Session:
    id: str
    started_at: datetime
    ended_at: datetime
    activity_count: int

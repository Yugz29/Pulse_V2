import re
import sqlite3
from datetime import timedelta

import pytest

from daemon_v2.event_logger import log_ingested_event
from daemon_v2.ingest import normalize_event
from daemon_v2.main import create_app
from daemon_v2.trace_store import TraceStore


SYSTEM_EVENT_TYPES = (
    "system_sleep",
    "system_wake",
    "screen_locked",
    "screen_unlocked",
)


def canonical_system_payload(event_type, *, event_id=None):
    return {
        "event_id": event_id or f"system-{event_type}",
        "schema_version": 1,
        "type": event_type,
        "producer": {
            "name": "pulse-macos-application-observer",
            "version": "1",
            "instance_id": "system-tests",
        },
        "occurred_at": "2026-07-23T20:13:42.000Z",
        "details": {},
    }


@pytest.mark.parametrize("event_type", SYSTEM_EVENT_TYPES)
def test_normalizes_system_event_with_empty_details(event_type):
    ingested = normalize_event(canonical_system_payload(event_type))

    assert ingested.event.event_type == event_type
    assert ingested.event.details == {}
    assert ingested.activity.activity_type == event_type
    assert ingested.activity.source == "system"
    assert ingested.activity.summary == event_type
    assert ingested.activity.details == {}


@pytest.mark.parametrize("event_type", SYSTEM_EVENT_TYPES)
def test_system_event_is_stored_through_canonical_ingestion(tmp_path, event_type):
    store = TraceStore(tmp_path / "trace.sqlite3")
    ingested = normalize_event(canonical_system_payload(event_type))

    stored = store.append_event(ingested)
    activities = store.activities_between(
        ingested.event.occurred_at,
        ingested.event.occurred_at + timedelta(seconds=1),
    )

    assert stored.type == event_type
    assert stored.details == {}
    assert len(activities) == 1
    assert activities[0].event_id == ingested.event.event_id
    assert activities[0].type == event_type
    assert activities[0].details == {}


def test_routes_accept_all_system_events_with_http_201(tmp_path):
    database = tmp_path / "trace.sqlite3"
    app = create_app(database)
    client = app.test_client()

    responses = [
        client.post("/activities", json=canonical_system_payload(event_type))
        for event_type in SYSTEM_EVENT_TYPES
    ]

    assert [response.status_code for response in responses] == [201] * 4
    assert all(response.get_json()["accepted"] is True for response in responses)
    assert client.get("/status").status_code == 200
    assert client.get("/trace/today").status_code == 200
    with sqlite3.connect(database) as connection:
        stored_types = {
            row[0]
            for row in connection.execute(
                "SELECT type FROM activities"
            ).fetchall()
        }
    assert stored_types == set(SYSTEM_EVENT_TYPES)


@pytest.mark.parametrize("event_type", SYSTEM_EVENT_TYPES)
def test_event_logger_prints_system_transition_without_details(
    monkeypatch,
    capsys,
    event_type,
):
    monkeypatch.setenv("PULSE_CORE_EVENT_LOG", "1")
    activity = normalize_event(canonical_system_payload(event_type)).activity

    log_ingested_event(activity=activity, status="created")

    output = capsys.readouterr()
    assert output.err == ""
    assert re.fullmatch(
        rf"\d{{2}}:\d{{2}}:\d{{2}}  {event_type}\n",
        output.out,
    )

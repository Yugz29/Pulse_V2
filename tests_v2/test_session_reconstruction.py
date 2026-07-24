from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from daemon_v2.analysis.timeline import reconstruct_session_views
from daemon_v2.analysis.timeline import configured_interruption_threshold
from daemon_v2.daily_trace import (
    build_daily_trace,
    render_daily_trace_html,
    render_daily_trace_markdown,
)
from daemon_v2.models import Activity
from daemon_v2.trace_store import TraceStore


BASE = datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc)
PULSE = "/workspace/Pulse_Core"
DEVNOTE = "/workspace/DevNote"


def event(
    event_type: str,
    minutes: int,
    details: dict | None = None,
    event_id: int = 1,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "occurred_at": (BASE + timedelta(minutes=minutes)).isoformat(),
        "details": details or {},
    }


def workspace(root: str) -> dict:
    return {
        "workspace": {
            "project_name": root.rsplit("/", 1)[-1],
            "workspace_root": root,
            "git_root": root,
            "resolution_method": "git",
            "resolution_confidence": "high",
        }
    }


def low_workspace(root: str) -> dict:
    return {
        "workspace": {
            "project_name": Path(root).name,
            "workspace_root": root,
            "git_root": None,
            "resolution_method": "cwd",
            "resolution_confidence": "low",
        }
    }


def reconstruct(
    *activities: dict,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    trace = {
        "date": BASE.date().isoformat(),
        "timezone": "UTC",
        "sessions": [{"activities": list(activities)}],
    }
    return reconstruct_session_views(
        trace,
        now=now or BASE + timedelta(minutes=10),
    )


@pytest.mark.parametrize("event_type", ["terminal_finished", "file_changed"])
def test_real_work_starts_a_session(event_type):
    details = workspace(PULSE)
    details["command" if event_type == "terminal_finished" else "path"] = (
        "pytest -q" if event_type == "terminal_finished" else f"{PULSE}/main.py"
    )

    sessions, _passive = reconstruct(event(event_type, 0, details))

    assert len(sessions) == 1
    assert sessions[0]["project_name"] == "Pulse_Core"
    assert sessions[0]["end_reason"] == "open"


def test_unlock_or_wake_alone_does_not_start_a_session():
    sessions, passive = reconstruct(
        event("screen_unlocked", 0),
        event("system_wake", 1, event_id=2),
    )

    assert sessions == []
    assert passive == []


@pytest.mark.parametrize("end_type", ["screen_locked", "system_sleep"])
def test_explicit_interruption_closes_session_immediately(end_type):
    sessions, _passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "make"}),
        event("file_changed", 10, {**workspace(PULSE), "path": f"{PULSE}/a.py"}, 2),
        event(end_type, 20, event_id=3),
    )

    assert len(sessions) == 1
    assert sessions[0]["ended_at"] == (BASE + timedelta(minutes=20)).isoformat()
    assert sessions[0]["duration_seconds"] == 20 * 60
    assert sessions[0]["end_reason"] == end_type


@pytest.mark.parametrize(
    ("stop_type", "resume_type"),
    [("screen_locked", "screen_unlocked"), ("system_sleep", "system_wake")],
)
def test_resume_transition_waits_for_new_work(stop_type, resume_type):
    sessions, _passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "make"}),
        event(stop_type, 20, event_id=2),
        event(resume_type, 50, event_id=3),
        event(
            "terminal_finished",
            55,
            {**workspace(PULSE), "command": "pytest"},
            4,
        ),
    )

    assert len(sessions) == 2
    assert sessions[0]["ended_at"] == (BASE + timedelta(minutes=50)).isoformat()
    assert sessions[0]["active_duration_seconds"] == 20 * 60
    assert sessions[0]["interruptions"][0]["duration_seconds"] == 30 * 60
    assert sessions[1]["started_at"] == (BASE + timedelta(minutes=55)).isoformat()


def test_short_interruption_followed_by_same_workspace_keeps_session():
    sessions, _passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "make"}),
        event("screen_locked", 10, event_id=2),
        event("screen_unlocked", 11, event_id=3),
        event(
            "terminal_finished",
            12,
            {**workspace(PULSE), "command": "git status"},
            4,
        ),
    )

    assert len(sessions) == 1
    assert sessions[0]["duration_seconds"] == 12 * 60
    assert sessions[0]["active_duration_seconds"] == 11 * 60
    assert sessions[0]["interruptions"] == [
        {
            "type": "screen_locked",
            "started_at": (BASE + timedelta(minutes=10)).isoformat(),
            "ended_at": (BASE + timedelta(minutes=11)).isoformat(),
            "duration_seconds": 60,
        }
    ]


def test_workspace_change_after_short_interruption_splits_session():
    sessions, _passive = reconstruct(
        event("file_changed", 0, {**workspace(PULSE), "path": f"{PULSE}/a.py"}),
        event("screen_locked", 10, event_id=2),
        event("screen_unlocked", 11, event_id=3),
        event(
            "file_changed",
            12,
            {**workspace(DEVNOTE), "path": f"{DEVNOTE}/app.js"},
            4,
        ),
    )

    assert len(sessions) == 2
    assert sessions[0]["end_reason"] == "workspace_changed"
    assert sessions[1]["project_name"] == "DevNote"


def test_application_context_never_starts_work_session():
    sessions, passive = reconstruct(
        event(
            "app_activated",
            0,
            {**workspace(PULSE), "app": "Visual Studio Code"},
        )
    )

    assert sessions == []
    assert len(passive) == 1


def test_interruption_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("PULSE_SESSION_INTERRUPTION_MINUTES", "2.5")

    assert configured_interruption_threshold() == timedelta(minutes=2.5)


def test_direct_historical_git_root_resolves_workspace():
    sessions, _passive = reconstruct(
        event(
            "terminal_finished",
            0,
            {"command": "git status", "git_root": PULSE},
        )
    )

    assert sessions[0]["workspace_root"] == PULSE
    assert sessions[0]["project_name"] == "Pulse_Core"


def test_events_are_ordered_by_instant_not_iso_string():
    first = event(
        "file_changed",
        0,
        {**workspace(PULSE), "path": f"{PULSE}/main.py"},
        event_id=1,
    )
    first["occurred_at"] = "2026-07-23T20:59:00+02:00"
    terminal = event(
        "terminal_finished",
        0,
        {**workspace(PULSE), "command": "git status"},
        event_id=4,
    )
    terminal["occurred_at"] = "2026-07-23T21:11:55+02:00"
    locked = event("screen_locked", 0, event_id=2)
    locked["occurred_at"] = "2026-07-23T19:10:32+00:00"
    unlocked = event("screen_unlocked", 0, event_id=3)
    unlocked["occurred_at"] = "2026-07-23T19:10:51+00:00"

    sessions, _passive = reconstruct(terminal, unlocked, locked, first)

    assert len(sessions) == 1
    assert sessions[0]["started_at"] == "2026-07-23T18:59:00+00:00"
    assert sessions[0]["interruptions"][0]["duration_seconds"] == 19


def test_inactivity_separates_sessions_without_inventing_work_time():
    sessions, _passive = reconstruct(
        event("file_changed", 0, {**workspace(PULSE), "path": f"{PULSE}/a.py"}),
        event(
            "terminal_finished",
            5,
            {**workspace(PULSE), "command": "pytest"},
            2,
        ),
        event(
            "file_changed",
            60,
            {**workspace(PULSE), "path": f"{PULSE}/b.py"},
            3,
        ),
    )

    assert len(sessions) == 2
    assert sessions[0]["ended_at"] == (BASE + timedelta(minutes=5)).isoformat()
    assert sessions[0]["duration_seconds"] == 5 * 60
    assert sessions[0]["end_reason"] == "inactivity"


def test_workspace_change_splits_projects_at_the_new_event():
    sessions, _passive = reconstruct(
        event("file_changed", 0, {**workspace(PULSE), "path": f"{PULSE}/a.py"}),
        event(
            "file_changed",
            15,
            {**workspace(DEVNOTE), "path": f"{DEVNOTE}/app.js"},
            2,
        ),
    )

    assert [item["project_name"] for item in sessions] == [
        "Pulse_Core",
        "DevNote",
    ]
    assert sessions[0]["end_reason"] == "workspace_changed"
    assert sessions[0]["ended_at"] == (BASE + timedelta(minutes=15)).isoformat()


def test_low_confidence_parent_is_promoted_by_nearby_high_context():
    home = str(Path.home())
    pulse = str(Path.home() / "Projets" / "Pulse" / "Pulse_Core")
    sessions, _passive = reconstruct(
        event(
            "terminal_finished",
            0,
            {**low_workspace(home), "command": "cd Projets/Pulse/Pulse_Core"},
        ),
        event(
            "terminal_finished",
            1,
            {**workspace(pulse), "command": "git status"},
            2,
        ),
    )

    assert len(sessions) == 1
    assert sessions[0]["project_name"] == "Pulse_Core"
    assert sessions[0]["workspace_root"] == pulse
    assert sessions[0]["commands_executed"] == 2


def test_isolated_low_confidence_specific_path_remains_identifiable():
    root = "/work/client-alpha"
    sessions, _passive = reconstruct(
        event(
            "terminal_finished",
            0,
            {**low_workspace(root), "command": "make test"},
        )
    )

    assert sessions[0]["project_name"] == "client-alpha"
    assert sessions[0]["workspace_root"] == root


@pytest.mark.parametrize(
    "root",
    [
        str(Path.home()),
        str(Path.home() / "Projets"),
        "/tmp",
        "/",
    ],
)
def test_generic_low_confidence_cwd_is_not_a_project(root):
    sessions, _passive = reconstruct(
        event(
            "terminal_finished",
            0,
            {**low_workspace(root), "command": "pwd"},
        )
    )

    assert sessions[0]["project_name"] is None
    assert sessions[0]["workspace_root"] is None


def test_aggregates_unique_files_apps_and_total_events():
    sessions, _passive = reconstruct(
        event("file_changed", 0, {**workspace(PULSE), "path": f"{PULSE}/a.py"}),
        event("app_activated", 1, {"app": "Terminal"}, 2),
        event("app_activated", 2, {"app": "loginwindow"}, 3),
        event("file_changed", 3, {**workspace(PULSE), "path": f"{PULSE}/a.py"}, 4),
        event("app_activated", 4, {"app": "Visual Studio Code"}, 5),
        event(
            "terminal_finished",
            5,
            {**workspace(PULSE), "command": "pytest"},
            6,
        ),
    )

    session = sessions[0]
    assert session["event_count"] == 6
    assert session["files_changed"] == 1
    assert session["commands_executed"] == 1
    assert session["applications"] == ["Terminal", "Visual Studio Code"]


def test_passive_application_between_strong_events_is_confirmed():
    middle = event("app_activated", 1, {"app": "Safari"}, 2)
    sessions, _passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "make"}),
        middle,
        event(
            "terminal_finished",
            2,
            {**workspace(PULSE), "command": "pytest"},
            3,
        ),
    )

    assert middle in sessions[0]["activities"]
    assert sessions[0]["applications"] == ["Safari"]


def test_trailing_passive_application_is_not_beyond_session_end():
    trailing = event("app_activated", 1, {"app": "Safari"}, 2)
    sessions, passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "pytest"}),
        trailing,
    )

    session = sessions[0]
    assert trailing not in session["activities"]
    assert session["ended_at"] == BASE.isoformat()
    assert session["duration_seconds"] == 0
    assert session["active_duration_seconds"] == 0
    assert all(
        datetime.fromisoformat(session["started_at"])
        <= datetime.fromisoformat(activity["occurred_at"])
        <= datetime.fromisoformat(session["ended_at"])
        for activity in session["activities"]
    )
    assert trailing in passive[0]["activities"]


def test_interruption_durations_and_activity_bounds_remain_coherent():
    trailing = event("app_activated", 13, {"app": "Safari"}, 5)
    sessions, _passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "make"}),
        event("screen_locked", 10, event_id=2),
        event("screen_unlocked", 11, event_id=3),
        event(
            "terminal_finished",
            12,
            {**workspace(PULSE), "command": "git status"},
            4,
        ),
        trailing,
    )

    session = sessions[0]
    assert session["duration_seconds"] == 12 * 60
    assert session["active_duration_seconds"] == 11 * 60
    assert session["interruptions"][0]["duration_seconds"] == 60
    assert trailing not in session["activities"]
    assert all(
        datetime.fromisoformat(session["started_at"])
        <= datetime.fromisoformat(activity["occurred_at"])
        <= datetime.fromisoformat(session["ended_at"])
        for activity in session["activities"]
    )


def test_recent_session_is_open_with_fixed_now():
    sessions, _passive = reconstruct(
        event("terminal_finished", 0, {**workspace(PULSE), "command": "pytest"}),
        now=BASE + timedelta(minutes=10),
    )

    assert sessions[0]["end_reason"] == "open"


def test_legacy_cwd_remains_usable_without_workspace():
    sessions, _passive = reconstruct(
        event(
            "terminal_finished",
            0,
            {"command": "pytest", "cwd": PULSE},
        )
    )

    assert sessions[0]["workspace_root"] == PULSE
    assert sessions[0]["project_name"] == "Pulse_Core"


def test_json_and_markdown_exports_include_session_metadata(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    store.append(
        Activity(
            "file_changed",
            BASE,
            "filesystem",
            f"Modified {PULSE}/main.py",
            {**workspace(PULSE), "path": f"{PULSE}/main.py"},
        )
    )
    store.append(
        Activity(
            "screen_locked",
            BASE + timedelta(minutes=20),
            "system",
            "Screen locked",
            {},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 23), timezone.utc)
    session = trace["work_sessions"][0]
    markdown = render_daily_trace_markdown(trace, archive_mode=True)

    assert session["project_name"] == "Pulse_Core"
    assert session["duration_seconds"] == 1200
    assert session["end_reason"] == "screen_locked"
    assert "- Projet : Pulse\\_Core" in markdown
    assert "- Durée calendaire : 20 min" in markdown
    assert "- Durée active : 20 min" in markdown
    assert "- Fin : écran verrouillé" in markdown


def test_mixed_offsets_use_journal_timezone_in_json_markdown_and_html(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    activities = [
        Activity(
            "terminal_finished",
            datetime.fromisoformat("2026-07-24T11:00:36+02:00"),
            "terminal",
            "Command succeeded: git status",
            {**workspace(PULSE), "command": "git status", "exit_code": 0},
        ),
        Activity(
            "file_changed",
            datetime.fromisoformat("2026-07-24T09:00:55+00:00"),
            "filesystem",
            f"Modified {PULSE}/main.py",
            {**workspace(PULSE), "path": f"{PULSE}/main.py", "event": "modified"},
        ),
        Activity(
            "screen_locked",
            datetime.fromisoformat("2026-07-24T09:01:10+00:00"),
            "system",
            "screen_locked",
            {},
        ),
        Activity(
            "screen_unlocked",
            datetime.fromisoformat("2026-07-24T09:02:15+00:00"),
            "system",
            "screen_unlocked",
            {},
        ),
        Activity(
            "terminal_finished",
            datetime.fromisoformat("2026-07-24T11:03:44+02:00"),
            "terminal",
            "Command succeeded: pytest -q",
            {**workspace(PULSE), "command": "pytest -q", "exit_code": 0},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(
        store,
        date(2026, 7, 24),
        ZoneInfo("Europe/Paris"),
    )
    session = trace["work_sessions"][0]
    markdown = render_daily_trace_markdown(trace, archive_mode=True)
    html = render_daily_trace_html(trace, archive_mode=True)

    assert [
        activity["occurred_at"]
        for activity in session["activities"]
    ] == [
        "2026-07-24T11:00:36+02:00",
        "2026-07-24T09:00:55+00:00",
        "2026-07-24T09:01:10+00:00",
        "2026-07-24T09:02:15+00:00",
        "2026-07-24T11:03:44+02:00",
    ]
    assert session["started_at"] == "2026-07-24T11:00:36+02:00"
    assert session["ended_at"] == "2026-07-24T11:03:44+02:00"
    assert session["interruptions"][0]["started_at"] == (
        "2026-07-24T11:01:10+02:00"
    )
    assert session["interruptions"][0]["ended_at"] == (
        "2026-07-24T11:02:15+02:00"
    )

    assert "## Session 1 — 11:00–11:03" in markdown
    for expected in (
        "- 11:00 · **terminal\\_finished**",
        "- 11:00 · **file\\_changed**",
        "- 11:01 · **screen\\_locked**",
        "- 11:02 · **screen\\_unlocked**",
        "- 11:03 · **terminal\\_finished**",
    ):
        assert expected in markdown

    assert "<h2>Session 1 · 11:00–11:03" in html
    assert html.count("<time>11:00</time>") == 2
    assert "<time>11:01</time>" in html
    assert "<time>11:02</time>" in html
    assert "<time>11:03</time>" in html

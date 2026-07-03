from datetime import date, datetime, timedelta, timezone

from daemon_v2.daily_trace import build_daily_trace, render_daily_trace_markdown
from daemon_v2.models import Activity
from daemon_v2.trace_store import TraceStore


def test_builds_structured_daily_trace(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
    store.append(Activity("file_changed", first_at, "filesystem", "Modified a.py", {"path": "a.py"}))
    store.append(
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            "Command succeeded: pytest",
            {"command": "pytest", "exit_code": 0, "cwd": "/project"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)

    assert trace["activity_count"] == 2
    assert trace["session_count"] == 1
    assert [item["type"] for item in trace["sessions"][0]["activities"]] == [
        "file_changed",
        "terminal_finished",
    ]

    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n"
        "\n"
        "## Session 1 — 08:00–08:05\n"
        "\n"
        "- 08:00 · **file\\_changed** — Modified a.py\n"
        "- 08:05 · **terminal\\_finished** — Command succeeded: pytest\n"
        "  - CWD : /project\n"
    )


def test_renders_empty_daily_trace():
    trace = {
        "date": "2026-07-03",
        "timezone": "UTC",
        "activity_count": 0,
        "session_count": 0,
        "sessions": [],
    }

    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n\n_Aucune activité._\n"
    )


def test_renders_multiline_terminal_command_as_nested_list(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    occurred_at = datetime(2026, 7, 3, 21, 6, tzinfo=timezone.utc)
    command = (
        "git add .\n"
        'git commit -m "filter multiline terminal noise"\n'
        "git push"
    )
    store.append(
        Activity(
            "terminal_finished",
            occurred_at,
            "terminal",
            f"Command succeeded: {command}",
            {"command": command, "exit_code": 0, "cwd": "/project/Pulse_V2"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)

    assert trace["sessions"][0]["activities"][0]["details"]["command"] == command
    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n"
        "\n"
        "## Session 1 — 21:06–21:06\n"
        "\n"
        "- 21:06 · **terminal\\_finished** — Command succeeded:\n"
        "  - `git add .`\n"
        '  - `git commit -m "filter multiline terminal noise"`\n'
        "  - `git push`\n"
        "  - CWD : /project/Pulse\\_V2\n"
    )


def test_renders_file_path_relative_to_workspace(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    occurred_at = datetime(2026, 7, 3, 21, 20, tzinfo=timezone.utc)
    absolute_path = "/Users/yugz/Projets/Pulse_V2/daemon_v2/daily_trace.py"
    workspace = "/Users/yugz/Projets/Pulse_V2"
    store.append(
        Activity(
            "file_changed",
            occurred_at,
            "filesystem",
            f"Modified {absolute_path}",
            {
                "path": absolute_path,
                "event": "modified",
                "workspace": workspace,
            },
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)

    assert trace["sessions"][0]["activities"][0]["details"]["path"] == absolute_path
    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n"
        "\n"
        "## Session 1 — 21:20–21:20\n"
        "\n"
        "- 21:20 · **file\\_changed** — Modified `daemon_v2/daily_trace.py`\n"
        "  - Workspace : /Users/yugz/Projets/Pulse\\_V2\n"
    )


def test_does_not_coalesce_same_file_across_sessions(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    path = "/project/a.py"
    details = {"path": path, "event": "modified", "workspace": "/project"}
    first_at = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
    store.append(
        Activity("file_changed", first_at, "filesystem", f"Modified {path}", details)
    )
    store.append(
        Activity(
            "file_changed",
            first_at + timedelta(hours=1),
            "filesystem",
            f"Modified {path}",
            details,
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)

    assert trace["session_count"] == 2
    assert markdown.count("Modified `a.py`") == 2
    assert "×2" not in markdown


def test_groups_distinct_file_changes_from_the_same_minute(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 23, 17, 5, tzinfo=timezone.utc)
    workspace = "/project"
    changes = [
        ("created", "/project/a.py", first_at),
        ("modified", "/project/b.py", first_at + timedelta(seconds=20)),
        ("created", "/project/a.py", first_at + timedelta(minutes=1)),
    ]
    for event, path, occurred_at in changes:
        store.append(
            Activity(
                "file_changed",
                occurred_at,
                "filesystem",
                f"{event.capitalize()} {path}",
                {"path": path, "event": event, "workspace": workspace},
            )
        )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)

    assert trace["activity_count"] == 3
    assert render_daily_trace_markdown(trace) == (
        "# Trace du 2026-07-03\n"
        "\n"
        "## Session 1 — 23:17–23:18\n"
        "\n"
        "- 23:17 · **file\\_changed** — Fichiers modifiés :\n"
        "  - Created `a.py` ×2\n"
        "  - Modified `b.py`\n"
    )


def test_does_not_coalesce_app_activations_across_sessions(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
    details = {"app": "ChatGPT"}
    store.append(
        Activity(
            "app_activated",
            first_at,
            "application",
            "Activated ChatGPT",
            details,
        )
    )
    store.append(
        Activity(
            "app_activated",
            first_at + timedelta(hours=1),
            "application",
            "Activated ChatGPT",
            details,
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)

    assert trace["session_count"] == 2
    assert markdown.count("Apps actives : ChatGPT") == 2
    assert "ChatGPT ×2" not in markdown

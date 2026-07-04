from datetime import date, datetime, timedelta, timezone

from daemon_v2.daily_trace import (
    build_daily_trace,
    render_daily_trace_html,
    render_daily_trace_markdown,
)
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

    assert (
        ("\n".join(render_daily_trace_markdown(trace).splitlines()[-6:]) + "\n").lstrip()
        == (
        "## Session 1 — 08:00–08:05\n"
        "\n"
        "- 08:00 · **file\\_changed** — Modified a.py\n"
        "- 08:05 · **terminal\\_finished** `test` — Command succeeded: `pytest`\n"
        "  - CWD : /project\n"
        )
    )


def test_renders_empty_daily_trace():
    trace = {
        "date": "2026-07-03",
        "timezone": "UTC",
        "activity_count": 0,
        "session_count": 0,
        "sessions": [],
    }

    markdown = render_daily_trace_markdown(trace)
    assert "## Résumé" in markdown
    assert "- Projet principal : Non détecté" in markdown
    assert "- Sessions : 0" in markdown
    assert "- Événements : 0" in markdown
    assert "- Dernière activité utile : Non détectée" in markdown
    assert markdown.endswith("_Aucune activité._\n")


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
    assert (
        ("\n".join(render_daily_trace_markdown(trace).splitlines()[-8:]) + "\n").lstrip()
        == (
        "## Session 1 — 21:06–21:06\n"
        "\n"
        "- 21:06 · **terminal\\_finished** `git` — Command succeeded:\n"
        "  - `git add .`\n"
        '  - `git commit -m "filter multiline terminal noise"`\n'
        "  - `git push`\n"
        "  - CWD : /project/Pulse\\_V2\n"
        )
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
    assert (
        ("\n".join(render_daily_trace_markdown(trace).splitlines()[-5:]) + "\n").lstrip()
        == (
        "## Session 1 — 21:20–21:20\n"
        "\n"
        "- 21:20 · **file\\_changed** — Modified `daemon_v2/daily_trace.py`\n"
        "  - Workspace : /Users/yugz/Projets/Pulse\\_V2\n"
        )
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
    assert (
        ("\n".join(render_daily_trace_markdown(trace).splitlines()[-6:]) + "\n").lstrip()
        == (
        "## Session 1 — 23:17–23:18\n"
        "\n"
        "- 23:17 · **file\\_changed** — Fichiers modifiés :\n"
        "  - Created `a.py` ×2\n"
        "  - Modified `b.py`\n"
        )
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
    assert markdown.count("- Apps actives : ChatGPT\n") == 2


def test_renders_deterministic_daily_summary_in_markdown_and_html(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    activities = [
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            "Modified /project/Pulse/a.py",
            {
                "path": "/project/Pulse/a.py",
                "event": "modified",
                "workspace": "/project/Pulse",
            },
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=1),
            "filesystem",
            "Created /project/Pulse/b.py",
            {
                "path": "/project/Pulse/b.py",
                "event": "created",
                "workspace": "/project/Pulse",
            },
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=2),
            "filesystem",
            "Modified /other/c.py",
            {
                "path": "/other/c.py",
                "event": "modified",
                "workspace": "/other",
            },
        ),
        Activity(
            "app_activated",
            first_at + timedelta(minutes=3),
            "application",
            "Activated ChatGPT",
            {"app": "ChatGPT"},
        ),
        Activity(
            "app_activated",
            first_at + timedelta(minutes=4),
            "application",
            "Activated ChatGPT",
            {"app": "ChatGPT"},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            "Command succeeded: git push",
            {"command": "git push", "exit_code": 0, "cwd": "/project/Pulse"},
        ),
        Activity(
            "app_activated",
            first_at + timedelta(minutes=6),
            "application",
            "Activated Terminal",
            {"app": "Terminal"},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    markdown_lines = [
        "Projet principal : Pulse",
        "Workspace : /project/Pulse",
        "Sessions : 1",
        "Événements : 7",
        "Commandes terminal : 1",
        "Fichiers modifiés : 3",
        "Apps principales : ChatGPT ×2, Terminal",
        "Dernière activité utile : terminal\\_finished — git push",
    ]
    for line in markdown_lines:
        assert line in markdown
    html_rows = [
        "<dt>Projet principal</dt><dd>Pulse</dd>",
        "<dt>Workspace</dt><dd>/project/Pulse</dd>",
        "<dt>Sessions</dt><dd>1</dd>",
        "<dt>Événements</dt><dd>7</dd>",
        "<dt>Commandes terminal</dt><dd>1</dd>",
        "<dt>Fichiers modifiés</dt><dd>3</dd>",
        "<dt>Apps principales</dt><dd>ChatGPT ×2, Terminal</dd>",
        "<dt>Dernière activité utile</dt><dd>terminal_finished — git push</dd>",
    ]
    for row in html_rows:
        assert row in html
    assert "## Session 1" in markdown
    assert "Session 1" in html


def test_classifies_terminal_commands_in_summary_markdown_and_html(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    commands = [
        ("pytest tests_v2", 0),
        ("git status", 0),
        ("python -m daemon_v2.main", 1),
        ("echo useful", 0),
    ]
    for index, (command, exit_code) in enumerate(commands):
        status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
        store.append(
            Activity(
                "terminal_finished",
                first_at + timedelta(minutes=index),
                "terminal",
                f"Command {status}: {command}",
                {"command": command, "exit_code": exit_code, "cwd": "/project"},
            )
        )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    for line in (
        "Commandes terminal : 4",
        "Tests : 1",
        "Git : 1",
        "Erreurs : 1",
        "Commandes Pulse : 1",
    ):
        assert line in markdown
    assert "**terminal\\_finished** `test` — Command succeeded: `pytest tests_v2`" in markdown
    assert "**terminal\\_finished** `git` — Command succeeded: `git status`" in markdown
    assert (
        "**terminal\\_finished** `pulse` `erreur` — "
        "Command failed (1): `python -m daemon_v2.main`"
    ) in markdown

    for label in ("test", "git", "pulse", "erreur"):
        assert f'<span class="label">{label}</span>' in html
    assert "<dt>Tests</dt><dd>1</dd>" in html
    assert "<dt>Git</dt><dd>1</dd>" in html
    assert "<dt>Erreurs</dt><dd>1</dd>" in html
    assert "<dt>Commandes Pulse</dt><dd>1</dd>" in html


def test_hides_ignored_app_only_sessions_in_markdown_and_html(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
    activities = [
        Activity(
            "app_activated",
            first_at,
            "application",
            "Activated loginwindow",
            {"app": "loginwindow"},
        ),
        Activity(
            "app_activated",
            first_at + timedelta(hours=1),
            "application",
            "Activated Finder",
            {"app": "Finder"},
        ),
        Activity(
            "app_activated",
            first_at + timedelta(hours=1, minutes=1),
            "application",
            "Activated ChatGPT",
            {"app": "ChatGPT"},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(hours=1, minutes=2),
            "terminal",
            "Command succeeded: git status",
            {"command": "git status", "exit_code": 0, "cwd": "/project"},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert trace["session_count"] == 2
    assert trace["activity_count"] == 4
    assert [activity["details"]["app"] for activity in trace["sessions"][0]["activities"]] == [
        "loginwindow"
    ]
    assert "- Sessions : 1" in markdown
    assert markdown.count("## Session ") == 1
    assert "Apps principales : ChatGPT" in markdown
    assert "Apps actives : ChatGPT" in markdown
    assert "Finder" not in markdown
    assert "loginwindow" not in markdown
    assert "<dt>Sessions</dt><dd>1</dd>" in html
    assert html.count('<section class="session">') == 1
    assert "Apps actives : ChatGPT" in html
    assert "Finder" not in html
    assert "loginwindow" not in html

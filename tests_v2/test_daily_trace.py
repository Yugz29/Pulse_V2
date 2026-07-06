from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from daemon_v2.daily_trace import (
    _is_pasted_prompt_command,
    _is_pulse_inspection_command,
    build_available_days,
    build_current_state,
    build_daily_summary,
    build_daily_trace,
    render_daily_trace_html,
    render_daily_trace_markdown,
)
from daemon_v2.models import Activity
from daemon_v2.trace_store import TraceStore


def test_classifies_only_local_pulse_inspection_commands():
    commands = [
        "curl -s http://127.0.0.1:5000/",
        "curl http://127.0.0.1:5000/trace/today",
        "curl http://127.0.0.1:5000/trace/today.md | head",
        "curl http://127.0.0.1:5000/trace/days | python -m json.tool",
        "curl http://127.0.0.1:5000/trace/2026-07-05",
        "curl http://127.0.0.1:5000/trace/2026-07-05.md | rg Session",
        "curl http://127.0.0.1:5000/days",
        "curl http://127.0.0.1:5000/day/2026-07-05 | sed -n 1,20p",
    ]

    assert all(_is_pulse_inspection_command(command) for command in commands)
    assert not _is_pulse_inspection_command(
        "curl -X POST http://127.0.0.1:5000/activities"
    )
    assert not _is_pulse_inspection_command(
        "curl https://example.com/trace/today"
    )


def test_classifies_structured_pasted_prompts_conservatively():
    prompt = (
        "Pulse_V2 — micro-jalon de documentation\n"
        "Contexte :\n"
        "Les routes HTML existent et doivent rester stables.\n"
        "Objectif :\n"
        "Vérifier le rendu sans ajouter de nouvelle dépendance.\n"
        "À faire :\n"
        "Conserver les événements dans la timeline brute.\n"
        "Validation attendue :\n"
        "Les tests existants passent."
    )

    assert _is_pasted_prompt_command(prompt)
    for command in (
        "make test",
        "python script.py",
        "git commit -m contexte",
        "curl http://example.com",
        f"make test\n{prompt}",
    ):
        assert not _is_pasted_prompt_command(command)


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

    markdown = render_daily_trace_markdown(trace)
    assert "## Session 1 — 08:00–08:05" in markdown
    assert "### Résumé de session\n- Tests passés : pytest" in markdown
    assert "- 08:00 · **file\\_changed** — Modified a.py" in markdown
    assert (
        "- 08:05 · **terminal\\_finished** `test` — Command succeeded: `pytest`\n"
        "  - CWD : /project"
    ) in markdown


def test_renders_observed_session_durations(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    zone = timezone.utc
    session_times = [
        datetime(2026, 7, 3, 16, 47, tzinfo=zone),
        datetime(2026, 7, 3, 16, 59, tzinfo=zone),
        datetime(2026, 7, 3, 18, 4, tzinfo=zone),
        datetime(2026, 7, 3, 18, 34, tzinfo=zone),
        datetime(2026, 7, 3, 19, 4, tzinfo=zone),
        datetime(2026, 7, 3, 19, 6, tzinfo=zone),
    ]
    for index, occurred_at in enumerate(session_times):
        store.append(
            Activity(
                "terminal_finished",
                occurred_at,
                "terminal",
                f"Command succeeded: echo {index}",
                {
                    "command": f"echo {index}",
                    "exit_code": 0,
                    "cwd": "/project/Pulse",
                },
            )
        )

    trace = build_daily_trace(store, date(2026, 7, 3), zone)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert "## Session 1 — 16:47–16:59 · 12 min" in markdown
    assert "## Session 2 — 18:04–19:06 · 1h02" in markdown
    assert "<h2>Session 1 · 16:47–16:59 · 12 min</h2>" in html
    assert "<h2>Session 2 · 18:04–19:06 · 1h02</h2>" in html


def test_moves_short_app_only_sessions_to_passive_activity(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    hidden_app_times = [time(1, 32), time(2, 13), time(11, 17)]
    passive_groups = [
        (time(1, 42), ["Safari", "ChatGPT"]),
        (time(2, 23), ["Safari"]),
        (
            time(11, 27),
            ["ChatGPT", "Pages", "Google Chrome", "Safari", "Code"],
        ),
    ]
    for hidden_time, (start_time, apps) in zip(
        hidden_app_times, passive_groups, strict=True
    ):
        store.append(
            Activity(
                "app_activated",
                datetime.combine(
                    date(2026, 7, 3), hidden_time, timezone.utc
                ),
                "application",
                "Activated Finder",
                {"app": "Finder"},
            )
        )
        started_at = datetime.combine(
            date(2026, 7, 3), start_time, timezone.utc
        )
        for offset, app in enumerate(apps):
            store.append(
                Activity(
                    "app_activated",
                    started_at + timedelta(seconds=offset),
                    "application",
                    f"Activated {app}",
                    {"app": app},
                )
            )
    store.append(
        Activity(
            "terminal_finished",
            datetime(2026, 7, 3, 12, 30, tzinfo=timezone.utc),
            "terminal",
            "Command succeeded: make test",
            {"command": "make test", "exit_code": 0, "cwd": "/project/Pulse"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace, archive_mode=True)
    html = render_daily_trace_html(trace, archive_mode=True)

    assert "- Sessions de travail : 1" in markdown
    assert "- Activités passives : 3" in markdown
    assert markdown.count("## Session ") == 1
    assert "## Session 1 — 12:30–12:30 · 0 min" in markdown
    assert "- 01:42 · Safari, ChatGPT" in markdown
    assert "- 02:23 · Safari" in markdown
    assert "- 11:27 · ChatGPT, Pages, Google Chrome, Safari, Code" in markdown
    assert markdown.index("## Activité passive") > markdown.index("## Session 1")
    assert "<dt>Sessions de travail</dt><dd>1</dd>" in html
    assert "<dt>Activités passives</dt><dd>3</dd>" in html
    assert html.count('<section class="session" id="session-') == 1


def test_day_with_only_passive_activity_has_no_work_session(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    store.append(
        Activity(
            "app_activated",
            datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc),
            "application",
            "Activated Safari",
            {"app": "Safari"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace, archive_mode=True)
    html = render_daily_trace_html(trace, archive_mode=True)

    assert "- Sessions de travail : 0" in markdown
    assert "- Activités passives : 1" in markdown
    assert "## Session " not in markdown
    assert "- 09:00 · Safari" in markdown
    assert '<section class="session" id="session-' not in html
    assert "<h2>Activité passive</h2>" in html


def test_short_terminal_and_file_sessions_remain_work_sessions(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    store.append(
        Activity(
            "terminal_finished",
            datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc),
            "terminal",
            "Command succeeded: pwd",
            {"command": "pwd", "exit_code": 0, "cwd": "/project/Pulse"},
        )
    )
    store.append(
        Activity(
            "file_changed",
            datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc),
            "filesystem",
            "Modified app.py",
            {
                "path": "/project/Pulse/app.py",
                "event": "modified",
                "workspace": "/project/Pulse",
            },
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    summary = build_daily_summary(trace, project_mode="archive")
    markdown = render_daily_trace_markdown(trace, archive_mode=True)

    assert summary["session_count"] == 2
    assert summary["passive_activity_count"] == 0
    assert markdown.count("## Session ") == 2
    assert "## Activité passive" not in markdown


def test_marks_current_day_last_session_in_progress(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    now = datetime.now().astimezone()
    zone = now.tzinfo
    first_at = now - timedelta(minutes=17)
    for occurred_at in (first_at, now):
        store.append(
            Activity(
                "terminal_finished",
                occurred_at,
                "terminal",
                "Command succeeded: echo active",
                {
                    "command": "echo active",
                    "exit_code": 0,
                    "cwd": "/project/Pulse",
                },
            )
        )

    trace = build_daily_trace(store, now.date(), zone)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    archive_html = render_daily_trace_html(trace, archive_mode=True)

    expected_range = f"{first_at:%H:%M}–{now:%H:%M}"
    assert f"## Session 1 — {expected_range} · 17 min · en cours" in markdown
    assert (
        f"<h2>Session 1 · {expected_range} · 17 min · en cours</h2>"
        in html
    )
    assert "· en cours</h2>" not in archive_html


def test_weak_activity_does_not_extend_or_keep_work_session_open(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    strong_at = now - timedelta(hours=3)
    store.append(
        Activity(
            "terminal_finished",
            strong_at,
            "terminal",
            "Command succeeded: make test",
            {"command": "make test", "exit_code": 0, "cwd": "/project/Pulse"},
        )
    )
    for offset in range(20, 181, 20):
        store.append(
            Activity(
                "app_activated",
                strong_at + timedelta(minutes=offset),
                "application",
                "Activated Safari",
                {"app": "Safari"},
            )
        )

    trace = build_daily_trace(store, now.date(), now.tzinfo)
    summary = build_daily_summary(trace)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    strong_time = strong_at.strftime("%H:%M")
    assert summary["session_count"] == 1
    assert summary["passive_activity_count"] == 1
    assert f"## Session 1 — {strong_time}–{strong_time} · 0 min" in markdown
    assert "· en cours" not in markdown
    assert "· en cours</h2>" not in html
    assert "## Activité passive" in markdown


def test_strong_activity_every_ten_minutes_keeps_one_session(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)
    for offset in (0, 10, 20, 30, 40):
        store.append(
            Activity(
                "terminal_finished",
                first_at + timedelta(minutes=offset),
                "terminal",
                "Command succeeded: pwd",
                {"command": "pwd", "exit_code": 0, "cwd": "/project/Pulse"},
            )
        )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    summary = build_daily_summary(trace, project_mode="archive")
    markdown = render_daily_trace_markdown(trace, archive_mode=True)

    assert summary["session_count"] == 1
    assert summary["passive_activity_count"] == 0
    assert "## Session 1 — 18:00–18:40 · 40 min" in markdown
    assert "## Session 2" not in markdown


def test_weak_activity_does_not_join_strong_activity_across_long_pause(
    tmp_path,
):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)
    activities = [
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            "Modified app.py",
            {
                "path": "/project/Pulse/app.py",
                "event": "modified",
                "workspace": "/project/Pulse",
            },
        ),
        *[
            Activity(
                "app_activated",
                first_at + timedelta(minutes=offset),
                "application",
                "Activated Safari",
                {"app": "Safari"},
            )
            for offset in (10, 20, 30, 40, 50)
        ],
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=60),
            "terminal",
            "Command succeeded: pytest",
            {"command": "pytest", "exit_code": 0, "cwd": "/project/Pulse"},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    summary = build_daily_summary(trace, project_mode="archive")
    markdown = render_daily_trace_markdown(trace, archive_mode=True)

    assert summary["session_count"] == 2
    assert summary["passive_activity_count"] == 1
    assert "## Session 1 — 18:00–18:00 · 0 min" in markdown
    assert "## Session 2 — 19:00–19:00 · 0 min" in markdown
    assert "## Activité passive" in markdown


def test_renders_empty_daily_trace():
    trace = {
        "date": "2026-07-03",
        "timezone": "UTC",
        "activity_count": 0,
        "session_count": 0,
        "sessions": [],
    }

    markdown = render_daily_trace_markdown(trace)
    assert "## Maintenant" in markdown
    assert "## Aujourd’hui" in markdown
    assert "- Projet probable : Non détecté" in markdown
    assert "- Sessions de travail : 0" in markdown
    assert "- Activités passives : 0" in markdown
    assert "- Événements : 0" in markdown
    assert "- Dernière activité utile : Non détectée" in markdown
    assert markdown.endswith("_Aucune activité._\n")


def test_renders_multiline_terminal_command_as_nested_list(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    occurred_at = datetime(2026, 7, 3, 21, 6, tzinfo=timezone.utc)
    command = (
        "git add .\n"
        'git commit -m "filter multiline terminal noise"\n'
        "git push\n"
        'git commit -m "remove Pulse command count"'
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
    markdown = render_daily_trace_markdown(trace)
    assert "## Session 1 — 21:06–21:06" in markdown
    assert (
        "### Résumé de session\n"
        "- Git :\n"
        "  - filter multiline terminal noise\n"
        "  - remove Pulse command count"
    ) in markdown
    assert (
        "- 21:06 · **terminal\\_finished** `git` — Command succeeded:\n"
        "  - `git add .`\n"
        '  - `git commit -m "filter multiline terminal noise"`\n'
        "  - `git push`\n"
        '  - `git commit -m "remove Pulse command count"`\n'
        "  - CWD : /project/Pulse\\_V2"
    ) in markdown
    html = render_daily_trace_html(trace)
    assert (
        "<li>Git :<ul><li>filter multiline terminal noise</li>"
        "<li>remove Pulse command count</li></ul></li>"
    ) in html
    assert "<code>git push</code>" in html


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
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    assert "## Session 1 — 21:20–21:20" in markdown
    assert (
        "### Résumé de session\n"
        "#### Pulse\\_V2\n"
        "- Fichiers modifiés : daemon\\_v2/daily\\_trace.py"
    ) in markdown
    assert "- Projet : Pulse\\_V2" not in markdown
    assert (
        '<div class="session-project-summary"><h4>Pulse_V2</h4>'
        "<ul><li>Fichiers modifiés : daemon_v2/daily_trace.py</li></ul>"
    ) in html
    assert (
        "### Pulse\\_V2\n"
        "- 21:20 · **file\\_changed** — Modified `daemon_v2/daily_trace.py`\n"
        "  - Workspace : /Users/yugz/Projets/Pulse\\_V2"
    ) in markdown


def test_session_summary_nests_and_limits_long_file_lists(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    workspace = "/project/Pulse"
    first_at = datetime(2026, 7, 3, 21, 30, tzinfo=timezone.utc)
    for index in range(5):
        for offset, event in enumerate(("created", "modified")):
            path = f"{workspace}/{event[0]}{index + 1}.py"
            store.append(
                Activity(
                    "file_changed",
                    first_at + timedelta(seconds=index * 2 + offset),
                    "filesystem",
                    f"{event.capitalize()} {path}",
                    {"path": path, "event": event, "workspace": workspace},
                )
            )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert (
        "#### Pulse\n"
        "- Fichiers :\n"
        "  - Créés : c1.py, c2.py, c3.py, +2 autres\n"
        "  - Modifiés : m1.py, m2.py, m3.py, +2 autres"
    ) in markdown
    assert (
        "<li>Fichiers :<ul>"
        "<li>Créés : c1.py, c2.py, c3.py, +2 autres</li>"
        "<li>Modifiés : m1.py, m2.py, m3.py, +2 autres</li>"
        "</ul></li>"
    ) in html
    assert "- 21:30 · **file\\_changed** — Fichiers modifiés :" in markdown
    assert "Created `c5.py`" in markdown
    assert "Modified `m5.py`" in markdown


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
    timeline = markdown.split("## Session 1", 1)[1]

    assert trace["session_count"] == 2
    assert timeline.count("Modified `a.py`") == 2
    assert "×2" not in markdown


def test_groups_distinct_file_changes_from_the_same_minute(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 23, 17, 5, tzinfo=timezone.utc)
    workspace = "/project"
    changes = [
        ("created", "/project/a.py", first_at),
        ("modified", "/project/b.py", first_at + timedelta(seconds=20)),
        ("created", "/project/a.py", first_at + timedelta(seconds=40)),
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
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    assert "## Session 1 — 23:17–23:17" in markdown
    assert "### Résumé de session" in markdown
    assert (
        "### project\n"
        "- 23:17 · **file\\_changed** — Fichiers modifiés :\n"
        "  - Created `a.py` ×2\n"
        "  - Modified `b.py`"
    ) in markdown
    assert (
        'Fichiers modifiés :<ul class="commands">'
        "<li>Created <code>a.py</code> ×2</li>"
        "<li>Modified <code>b.py</code></li></ul>"
    ) in html


def test_keeps_later_file_change_visible_before_terminal_activity(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    workspace = "/Users/yugz/Projets/Pulse_V2"
    path = f"{workspace}/daemon_v2/daily_trace.py"
    first_at = datetime(2026, 7, 3, 16, 10, tzinfo=timezone.utc)
    details = {"path": path, "event": "modified", "workspace": workspace}
    store.append(
        Activity("file_changed", first_at, "filesystem", f"Modified {path}", details)
    )
    store.append(
        Activity(
            "file_changed",
            first_at + timedelta(minutes=6),
            "filesystem",
            f"Modified {path}",
            details,
        )
    )
    store.append(
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=7),
            "terminal",
            "Command succeeded: touch daemon_v2/daily_trace.py",
            {
                "command": "touch daemon_v2/daily_trace.py",
                "exit_code": 0,
                "cwd": workspace,
            },
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert (
        "- 16:16 · **file\\_changed** — "
        "Modified `daemon_v2/daily_trace.py`"
    ) in markdown
    assert markdown.index("- 16:16 · **file\\_changed**") < markdown.index(
        "- 16:17 · **terminal\\_finished**"
    )
    assert (
        "<time>16:16</time><span class=\"type\">file_changed</span>"
        '<div class="content">Modified <code>daemon_v2/daily_trace.py</code>'
    ) in html
    assert html.index("<time>16:16</time>") < html.index("<time>16:17</time>")


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
    html = render_daily_trace_html(trace)

    assert trace["session_count"] == 2
    assert "## Session " not in markdown
    assert "## Activité passive" in markdown
    assert "- 08:00 · ChatGPT" in markdown
    assert "- 09:00 · ChatGPT" in markdown
    assert "<h2>Activité passive</h2>" in html
    assert "Résumé de session" not in markdown
    assert "Résumé de session" not in html


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
        "## Maintenant",
        "Projet probable : Pulse",
        "Workspace : /project/Pulse",
        "App active : Terminal",
        "Dernière commande : `git push`",
        "Session active depuis : 10:00",
        "Dernière activité utile : terminal\\_finished — git push",
        "## Aujourd’hui",
        "Sessions de travail : 1",
        "Activités passives : 0",
        "Événements : 7",
        "Commandes terminal : 1",
        "Fichiers modifiés : 3",
        "Apps principales : ChatGPT, Terminal",
    ]
    for line in markdown_lines:
        assert line in markdown
    html_rows = [
        "<h2>Maintenant</h2>",
        "<dt>Projet probable</dt><dd>Pulse</dd>",
        "<dt>Workspace</dt><dd>/project/Pulse</dd>",
        "<dt>App active</dt><dd>Terminal</dd>",
        "<dt>Dernière commande</dt><dd>git push</dd>",
        "<dt>Session active depuis</dt><dd>10:00</dd>",
        "<dt>Dernière activité utile</dt><dd>terminal_finished — git push</dd>",
        "<h2>Aujourd’hui</h2>",
        "<dt>Sessions de travail</dt><dd>1</dd>",
        "<dt>Activités passives</dt><dd>0</dd>",
        "<dt>Événements</dt><dd>7</dd>",
        "<dt>Commandes terminal</dt><dd>1</dd>",
        "<dt>Fichiers modifiés</dt><dd>3</dd>",
        "<dt>Apps principales</dt><dd>ChatGPT, Terminal</dd>",
    ]
    for row in html_rows:
        assert row in html
    assert "## Session 1" in markdown
    assert "Session 1" in html


def test_renders_deterministic_resume_before_today(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    workspace = "/project/Pulse_V2"
    activities = [
        Activity(
            "terminal_finished",
            first_at,
            "terminal",
            "Command succeeded: .venv/bin/python -m pytest tests_v2",
            {
                "command": ".venv/bin/python -m pytest tests_v2",
                "exit_code": 0,
                "cwd": workspace,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=1),
            "terminal",
            'Command succeeded: git commit -m "add resume block"',
            {
                "command": 'git commit -m "add resume block"',
                "exit_code": 0,
                "cwd": workspace,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=2),
            "terminal",
            "Command succeeded: git push",
            {"command": "git push", "exit_code": 0, "cwd": workspace},
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=3),
            "filesystem",
            f"Modified {workspace}/daemon_v2/daily_trace.py",
            {
                "path": f"{workspace}/daemon_v2/daily_trace.py",
                "event": "modified",
                "workspace": workspace,
            },
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert markdown.index("## Maintenant") < markdown.index("## Reprise")
    assert markdown.index("## Reprise") < markdown.index("## Aujourd’hui")
    assert (
        "## Reprise\n- État : activité en cours, test non relancé"
        in markdown
    )
    assert "- Dernier projet observé : Pulse\\_V2" in markdown
    assert (
        "- Dernier signal utile observé : file\\_changed — "
        "Modified daemon\\_v2/daily\\_trace.py"
    ) in markdown
    assert (
        "- Derniers fichiers observés : daemon\\_v2/daily\\_trace.py"
        in markdown
    )
    assert (
        "- Dernier test local observé : "
        ".venv/bin/python -m pytest tests\\_v2 — OK"
        in markdown
    )
    assert "Dernière commande Git observée" not in markdown
    assert '<a class="nav-main" href="#reprise">Reprise</a>' in html
    assert '<section class="resume" id="reprise"><h2>Reprise</h2>' in html
    resume_html = html.split('<section class="resume"', 1)[1].split(
        "</section>", 1
    )[0]
    assert resume_html.count("<dt>") == 5
    assert (
        "<dt>État</dt><dd>activité en cours, test non relancé</dd>"
        in resume_html
    )
    assert (
        "<dt>Dernier projet observé</dt><dd>Pulse_V2</dd>"
        in resume_html
    )
    assert html.index('id="maintenant"') < html.index('id="reprise"')
    assert html.index('id="reprise"') < html.index('id="aujourdhui"')


def test_resume_reports_pushed_changes_with_stale_local_test(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    activities = [
        Activity(
            "terminal_finished",
            first_at,
            "terminal",
            "Command succeeded: make test",
            {
                "command": "make test",
                "exit_code": 0,
                "cwd": str(workspace),
            },
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=1),
            "filesystem",
            f"Modified {workspace}/app.py",
            {
                "path": f"{workspace}/app.py",
                "event": "modified",
                "workspace": str(workspace),
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=2),
            "terminal",
            'Command succeeded: git commit -m "pushed changes"',
            {
                "command": 'git commit -m "pushed changes"',
                "exit_code": 0,
                "cwd": str(workspace),
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=3),
            "terminal",
            "Command succeeded: git push",
            {
                "command": "git push",
                "exit_code": 0,
                "cwd": str(workspace),
            },
        ),
    ]
    for activity in activities:
        store.append(activity)

    def fake_run(command, **_kwargs):
        stdout = (
            "## main\n"
            if "status" in command
            else "pushed changes\n"
        )
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr("daemon_v2.daily_trace.subprocess.run", fake_run)
    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert (
        "- État : push observé, aucun test local observé depuis "
        "les modifications"
        in markdown
    )
    assert (
        "### Git\n"
        "- État local : propre\n"
        "- Branche : main\n"
        "- Commits aujourd’hui :\n"
        "  - pushed changes"
    ) in markdown
    assert "- Dernier test local observé : make test — OK" in markdown
    assert "Dernière commande Git observée" not in markdown
    assert (
        "<dt>État</dt><dd>"
        "push observé, aucun test local observé depuis les modifications</dd>"
    ) in html
    assert (
        '<div class="resume-git"><h3>Git</h3><dl>'
        "<dt>État local</dt><dd>propre</dd>"
        "<dt>Branche</dt><dd>main</dd>"
        "<dt>Commits aujourd’hui</dt><dd><ul>"
        "<li>pushed changes</li></ul></dd></dl></div>"
    ) in html
    assert (
        "<dt>Dernier test local observé</dt><dd>make test — OK</dd>"
        in html
    )
    assert "<dt>Dernière commande Git observée</dt>" not in html


def test_pulse_inspection_commands_stay_raw_but_not_useful(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    workspace = "/project/Pulse"
    inspections = [
        (
            "curl -s http://127.0.0.1:5000/trace/today.md "
            "| .venv/bin/python -m pytest"
        ),
        (
            "curl -s http://127.0.0.1:5000/trace/days "
            "| python -m json.tool"
        ),
        (
            "curl -s http://127.0.0.1:5000/day/2026-07-03 "
            "| rg Session"
        ),
    ]
    store.append(
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            f"Modified {workspace}/src/app.py",
            {
                "path": f"{workspace}/src/app.py",
                "event": "modified",
                "workspace": workspace,
            },
        )
    )
    for index, command in enumerate(inspections, start=1):
        store.append(
            Activity(
                "terminal_finished",
                first_at + timedelta(minutes=index),
                "terminal",
                f"Command failed (1): {command}",
                {
                    "command": command,
                    "exit_code": 1,
                    "cwd": workspace,
                },
            )
        )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    available_day = build_available_days(
        store, timezone.utc
    )["days"][0]

    for command in inspections:
        assert command in markdown
    assert (
        "- Dernier signal utile observé : file\\_changed — "
        "Modified src/app.py"
    ) in markdown
    assert "Dernier test local observé" not in markdown
    assert "Tests passés :" not in markdown
    assert "Tests échoués :" not in markdown
    assert "Erreurs terminal :" not in markdown
    assert "Erreur terminal récente" not in markdown
    assert available_day["summary"] == [
        "Pulse — 1 fichier modifié · dossiers principaux : src",
        "4 événements",
    ]
    assert available_day["project_summaries"][0]["summary"] == [
        "1 fichier modifié · dossiers principaux : src"
    ]


def test_inspection_only_sessions_hide_empty_project_summaries(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    workspace = "/project/Pulse"
    with_apps = "curl -s http://127.0.0.1:5000/trace/today.md"
    without_apps = "curl -s http://127.0.0.1:5000/trace/days"
    activities = [
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            f"Modified {workspace}/src/app.py",
            {
                "path": f"{workspace}/src/app.py",
                "event": "modified",
                "workspace": workspace,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(hours=1),
            "terminal",
            f"Command succeeded: {with_apps}",
            {"command": with_apps, "exit_code": 0, "cwd": workspace},
        ),
        Activity(
            "app_activated",
            first_at + timedelta(hours=1, minutes=1),
            "application",
            "Activated Code",
            {"app": "Code"},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(hours=2),
            "terminal",
            f"Command succeeded: {without_apps}",
            {"command": without_apps, "exit_code": 0, "cwd": workspace},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert trace["session_count"] == 3
    assert markdown.count("#### Pulse") == 1
    assert html.count("<h4>Pulse</h4>") == 1
    assert "- Apps actives : Code" in markdown
    assert "Apps actives : Code" in html
    assert markdown.count(
        "_Aucun signal significatif dans cette session._"
    ) == 1
    assert html.count(
        "<p>Aucun signal significatif dans cette session.</p>"
    ) == 1
    assert with_apps in markdown
    assert without_apps in markdown


def test_pasted_prompt_stays_raw_without_replacing_real_terminal_error(
    tmp_path
):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    workspace = "/project/Pulse"
    prompt = (
        "Pulse_V2 — micro-jalon de routes HTML\n"
        "Contexte :\n"
        "Les routes HTML existent depuis le prototype.\n"
        "Objectif :\n"
        "Conserver une documentation factuelle du comportement.\n"
        "À faire :\n"
        "Vérifier les sorties sans modifier SQLite.\n"
        "Validation attendue :\n"
        "Les tests passent."
    )
    activities = [
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            f"Modified {workspace}/src/app.py",
            {
                "path": f"{workspace}/src/app.py",
                "event": "modified",
                "workspace": workspace,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=1),
            "terminal",
            "Command failed (2): make test",
            {
                "command": "make test",
                "exit_code": 2,
                "cwd": workspace,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=2),
            "terminal",
            f"Command failed (127): {prompt}",
            {
                "command": prompt,
                "exit_code": 127,
                "cwd": workspace,
            },
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    available_day = build_available_days(
        store, timezone.utc
    )["days"][0]

    assert "Pulse_V2 — micro-jalon de routes HTML" in markdown
    assert "Contexte :" in markdown
    assert "Erreurs terminal : make test" in markdown
    assert "Erreurs terminal : Pulse" not in markdown
    assert (
        "- Erreur terminal récente : make test — code 2"
        in markdown
    )
    assert (
        "- Dernier signal utile observé : terminal\\_finished — make test"
        in markdown
    )
    assert available_day["summary"][1] == (
        "Tests échoués · Erreurs observées · 3 événements"
    )
    assert available_day["project_summaries"][0]["summary"][1] == (
        "Tests échoués · Erreurs observées"
    )
    assert all(
        "Pulse_V2 — micro-jalon" not in line
        for line in available_day["summary"]
    )


def test_resume_reports_latest_terminal_error(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    occurred_at = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    store.append(
        Activity(
            "terminal_finished",
            occurred_at,
            "terminal",
            "Command failed (1): pytest tests_v2",
            {"command": "pytest tests_v2", "exit_code": 1, "cwd": "/project"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert (
        "- Dernier test local observé : pytest tests\\_v2 — Échec (1)"
        in markdown
    )
    assert "- État : tests échoués" in markdown
    assert "- Erreur terminal récente : pytest tests\\_v2 — code 1" in markdown
    assert (
        "<dt>Erreur terminal récente</dt><dd>pytest tests_v2 — code 1</dd>"
        in html
    )


def test_resume_extracts_test_line_and_hides_older_error(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    store.append(
        Activity(
            "terminal_finished",
            first_at,
            "terminal",
            "Command failed (2): make test",
            {"command": "make test", "exit_code": 2, "cwd": "/project"},
        )
    )
    store.append(
        Activity(
            "file_changed",
            first_at + timedelta(minutes=4),
            "filesystem",
            "Modified /project/a.py",
            {
                "path": "/project/a.py",
                "event": "modified",
                "workspace": "/project",
            },
        )
    )
    command = (
        ".venv/bin/python -m pytest tests_v2\n"
        'git commit -m "validated changes"\n'
        "git push"
    )
    store.append(
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            f"Command succeeded: {command}",
            {"command": command, "exit_code": 0, "cwd": "/project"},
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert (
        "- Dernier test local observé : "
        ".venv/bin/python -m pytest tests\\_v2 — OK"
        in markdown
    )
    assert "- État : tests OK, dernier commit poussé" in markdown
    assert (
        'Dernier test local observé : git commit -m "validated changes"'
        not in markdown
    )
    assert "Erreur terminal récente" not in markdown
    assert (
        "<dt>Dernier test local observé</dt>"
        "<dd>.venv/bin/python -m pytest tests_v2 — OK</dd>"
    ) in html
    assert (
        "<dt>État</dt><dd>tests OK, dernier commit poussé</dd>"
        in html
    )
    assert "Erreur terminal récente" not in html


def test_resume_reports_changes_after_push_when_tests_are_current(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 17, 40, tzinfo=timezone.utc)
    workspace = "/project/Pulse_V2"
    git_command = 'git commit -m "checkpoint"\ngit push'
    activities = [
        Activity(
            "terminal_finished",
            first_at,
            "terminal",
            f"Command succeeded: {git_command}",
            {"command": git_command, "exit_code": 0, "cwd": workspace},
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=3),
            "filesystem",
            f"Modified {workspace}/daemon_v2/daily_trace.py",
            {
                "path": f"{workspace}/daemon_v2/daily_trace.py",
                "event": "modified",
                "workspace": workspace,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=7),
            "terminal",
            "Command succeeded: make test",
            {"command": "make test", "exit_code": 0, "cwd": workspace},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    expected = "État : tests OK, modifications non push"
    assert f"- {expected}" in markdown
    assert "État : tests OK, dernier commit poussé" not in markdown
    assert "<dt>État</dt><dd>tests OK, modifications non push</dd>" in html
    assert "<dt>État</dt><dd>tests OK, dernier commit poussé</dd>" not in html


def test_resume_reports_local_git_status_without_storing_it(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = TraceStore(tmp_path / "pulse.sqlite3")
    store.append(
        Activity(
            "terminal_finished",
            datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc),
            "terminal",
            "Command succeeded: make test",
            {"command": "make test", "exit_code": 0, "cwd": str(workspace)},
        )
    )
    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    status_result = {"stdout": "## main\n", "returncode": 0}
    log_result = {
        "stdout": (
            "passive local commit\n"
            "earlier local commit\n"
        ),
        "returncode": 0,
    }
    last_log_result = {"stdout": "older commit\n", "returncode": 0}
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "status" in command:
            result = status_result
        elif "-1" in command:
            result = last_log_result
        else:
            result = log_result
        return SimpleNamespace(**result)

    monkeypatch.setattr("daemon_v2.daily_trace.subprocess.run", fake_run)

    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    assert (
        "### Git\n"
        "- État local : propre\n"
        "- Branche : main\n"
        "- Commits aujourd’hui :\n"
        "  - passive local commit\n"
        "  - earlier local commit"
    ) in markdown
    assert "autres commits aujourd’hui" not in markdown
    assert "- Dernier commit :" not in markdown
    assert "Dernière commande Git observée" not in markdown
    assert "<dt>État local</dt><dd>propre</dd>" in html
    assert "<dt>Branche</dt><dd>main</dd>" in html
    assert "<dt>Dernier commit</dt>" not in html
    assert (
        "<dt>Commits aujourd’hui</dt><dd><ul>"
        "<li>passive local commit</li><li>earlier local commit</li>"
        "</ul></dd>"
    ) in html
    assert "autres commits aujourd’hui" not in html
    assert "<dt>Dernière commande Git observée</dt>" not in html
    assert trace["activity_count"] == 1

    status_result["stdout"] = (
        "## feature/passive-git\n"
        " M changed.py\n?? new.py\nD  deleted.py\n"
    )
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    expected = (
        "État local : 1 fichier modifié, 1 fichier non suivi, "
        "1 fichier supprimé"
    )
    assert f"- {expected}" in markdown
    assert (
        "<dt>État local</dt><dd>1 fichier modifié, 1 fichier non suivi, "
        "1 fichier supprimé</dd>"
    ) in html
    assert "- Branche : feature/passive-git" in markdown

    log_result["stdout"] = "\n".join(
        [
            "commit 7",
            "commit 6",
            "commit 5",
            "commit 4",
            "commit 3",
            "commit 2",
            "commit 1",
            "commit 7",
        ]
    )
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    assert markdown.count("  - commit ") == 5
    assert "  - + 2 autres commits aujourd’hui" in markdown
    assert "<li>+ 2 autres commits aujourd’hui</li>" in html
    assert "- Dernier commit :" not in markdown

    log_result["stdout"] = ""
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    assert "- Dernier commit : older commit" in markdown
    assert "- Commits aujourd’hui :" not in markdown
    assert "<dt>Dernier commit</dt><dd>older commit</dd>" in html
    assert "<dt>Commits aujourd’hui</dt>" not in html

    status_result["returncode"] = 128
    assert "### Git" not in render_daily_trace_markdown(trace)
    assert '<div class="resume-git">' not in render_daily_trace_html(trace)
    assert calls
    command, kwargs = calls[0]
    assert command == [
        "git",
        "-C",
        str(workspace),
        "status",
        "--short",
        "--branch",
    ]
    assert kwargs["timeout"] == 1
    assert any(
        command == [
            "git",
            "-C",
            str(workspace),
            "log",
            "--since=2026-07-03T00:00:00",
            "--until=2026-07-04T00:00:00",
            "--pretty=%s",
        ]
        and kwargs["timeout"] == 1
        for command, kwargs in calls
    )
    assert any(
        command == [
            "git",
            "-C",
            str(workspace),
            "log",
            "-1",
            "--pretty=%s",
        ]
        and kwargs["timeout"] == 1
        for command, kwargs in calls
    )


def test_classifies_terminal_commands_in_summary_markdown_and_html(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    commands = [
        (".venv/bin/python -m pytest tests_v2", 0),
        ("python -m pytest tests_v2", 0),
        ("python3 -m pytest tests_v2", 0),
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
        "Commandes terminal : 6",
        "Tests : 3",
        "Git : 1",
        "Erreurs : 1",
    ):
        assert line in markdown
    assert "Commandes Pulse :" not in markdown
    assert (
        "**terminal\\_finished** `test` — Command succeeded: "
        "`.venv/bin/python -m pytest tests_v2`"
    ) in markdown
    assert (
        "**terminal\\_finished** `test` — Command succeeded: "
        "`python3 -m pytest tests_v2`"
    ) in markdown
    assert "**terminal\\_finished** `git` — Command succeeded: `git status`" in markdown
    assert (
        "**terminal\\_finished** `pulse` `erreur` — "
        "Command failed (1): `python -m daemon_v2.main`"
    ) in markdown

    for label in ("test", "git", "pulse", "erreur"):
        assert f'<span class="label label-{label}">{label}</span>' in html
    assert "<dt>Tests</dt><dd>3</dd>" in html
    assert "<dt>Git</dt><dd>1</dd>" in html
    assert "<dt>Erreurs</dt><dd>1</dd>" in html
    assert "<dt>Commandes Pulse</dt>" not in html


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
    assert "- Sessions de travail : 1" in markdown
    assert "- Activités passives : 0" in markdown
    assert markdown.count("## Session ") == 1
    assert "Apps principales : ChatGPT" in markdown
    assert "Apps actives : ChatGPT" in markdown
    assert "Finder" not in markdown
    assert "loginwindow" not in markdown
    assert "<dt>Sessions de travail</dt><dd>1</dd>" in html
    assert "<dt>Activités passives</dt><dd>0</dd>" in html
    assert html.count('<section class="session" id="session-') == 1
    assert "Apps actives : ChatGPT" in html
    assert "Finder" not in html
    assert "loginwindow" not in html


def test_current_state_limits_recent_files_to_five(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)
    for index in range(6):
        path = f"/project/file_{index}.py"
        store.append(
            Activity(
                "file_changed",
                first_at + timedelta(seconds=index),
                "filesystem",
                f"Modified {path}",
                {
                    "path": path,
                    "event": "modified",
                    "workspace": "/project",
                },
            )
        )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    current = build_current_state(trace)

    assert [item["path"] for item in current["recent_files"]] == [
        "file_5.py",
        "file_4.py",
        "file_3.py",
        "file_2.py",
        "file_1.py",
    ]


def test_current_workspace_uses_latest_useful_event_and_today_lists_all_projects(
    tmp_path,
):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    workspaces = [
        "/Users/yugz/Projets/Pulse_V2",
        "/Users/yugz/Projets/TEST",
        "/Users/yugz/Projets/TEST/Pulse_Sandbox",
    ]
    store.append(
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            f"Modified {workspaces[0]}/README.md",
            {
                "path": f"{workspaces[0]}/README.md",
                "event": "modified",
                "workspace": workspaces[0],
            },
        )
    )
    store.append(
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=10),
            "terminal",
            "Command succeeded: mkdir Pulse_Sandbox",
            {
                "command": "mkdir Pulse_Sandbox",
                "exit_code": 0,
                "cwd": workspaces[1],
            },
        )
    )
    store.append(
        Activity(
            "file_changed",
            first_at + timedelta(hours=1),
            "filesystem",
            f"Created {workspaces[2]}/README.md",
            {
                "path": f"{workspaces[2]}/README.md",
                "event": "created",
                "workspace": workspaces[2],
            },
        )
    )

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    escaped_current_workspace = workspaces[2].replace("_", "\\_")

    assert "- Projet probable : Pulse\\_Sandbox" in markdown
    assert f"- Workspace : {escaped_current_workspace}" in markdown
    assert "- Projets : Pulse\\_V2, Pulse\\_Sandbox" in markdown
    projects_line = next(
        line for line in markdown.splitlines() if line.startswith("- Projets :")
    )
    assert "TEST" not in projects_line
    assert f"  - CWD : {workspaces[1]}" in markdown
    assert "<dt>Projet probable</dt><dd>Pulse_Sandbox</dd>" in html
    assert f"<dt>Workspace</dt><dd>{workspaces[2]}</dd>" in html
    assert f'title="{workspaces[0]}">Pulse_V2</span>' in html
    assert f'title="{workspaces[2]}">Pulse_Sandbox</span>' in html
    assert f'title="{workspaces[1]}">TEST</span>' not in html
    assert markdown.count("## Session ") == 2


def test_timeline_marks_project_changes_but_keeps_weak_cwd_as_detail(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    pulse_v2 = "/Users/yugz/Projets/Pulse_V2"
    weak_parent = "/Users/yugz/Projets/TEST"
    sandbox = "/Users/yugz/Projets/TEST/Pulse_Sandbox"
    activities = [
        Activity(
            "terminal_finished",
            first_at,
            "terminal",
            "Command succeeded: git status",
            {"command": "git status", "exit_code": 0, "cwd": pulse_v2},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            "Command succeeded: mkdir Pulse_Sandbox",
            {
                "command": "mkdir Pulse_Sandbox",
                "exit_code": 0,
                "cwd": weak_parent,
            },
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=10),
            "filesystem",
            f"Created {sandbox}/src/calc.py",
            {
                "path": f"{sandbox}/src/calc.py",
                "event": "created",
                "workspace": sandbox,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=15),
            "terminal",
            "Command succeeded: make test",
            {"command": "make test", "exit_code": 0, "cwd": pulse_v2},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)
    timeline = markdown.split("## Session 1", 1)[1]
    timeline_lines = timeline.splitlines()

    assert trace["session_count"] == 1
    assert timeline_lines.count("### Pulse\\_V2") == 2
    assert timeline_lines.count("### Pulse\\_Sandbox") == 1
    assert "### TEST" not in timeline_lines
    assert f"  - CWD : {weak_parent}" in timeline
    assert html.count('class="project-separator"') == 3
    assert (
        '<a class="nav-project" href="#session-1-projet-1">Pulse_V2</a>'
        in html
    )
    assert (
        '<a class="nav-project" href="#session-1-projet-2">'
        "Pulse_Sandbox</a>"
    ) in html
    assert (
        '<a class="nav-project" href="#session-1-projet-3">Pulse_V2</a>'
        in html
    )
    assert (
        'class="project-separator" id="session-1-projet-1">Pulse_V2</li>'
        in html
    )
    assert (
        'class="project-separator" '
        'id="session-1-projet-2">Pulse_Sandbox</li>'
    ) in html
    assert (
        'class="project-separator" id="session-1-projet-3">Pulse_V2</li>'
        in html
    )
    assert 'class="nav-project"' in html
    assert ">TEST</a>" not in html
    assert ">TEST</li>" not in html
    assert f"CWD : {weak_parent}" in html


def test_home_and_projects_parent_are_never_promoted_to_projects(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    home = str(Path.home())
    projects_parent = str(Path.home() / "Projets")
    pulse_v2 = str(Path.home() / "Projets" / "Pulse_V2")
    activities = [
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            f"Modified {pulse_v2}/README.md",
            {
                "path": f"{pulse_v2}/README.md",
                "event": "modified",
                "workspace": pulse_v2,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            "Command succeeded: pwd",
            {"command": "pwd", "exit_code": 0, "cwd": home},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=10),
            "terminal",
            "Command succeeded: cd Projets/Pulse_V2",
            {
                "command": "cd Projets/Pulse_V2",
                "exit_code": 0,
                "cwd": home,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=15),
            "terminal",
            "Command succeeded: ls",
            {"command": "ls", "exit_code": 0, "cwd": projects_parent},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=20),
            "terminal",
            "Command succeeded: echo home",
            {"command": "echo home", "exit_code": 0, "cwd": "~"},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert "- Projet probable : Pulse\\_V2" in markdown
    assert "- Projets : Pulse\\_V2" in markdown
    assert f"  - CWD : {home}" in markdown
    assert f"  - CWD : {projects_parent}" in markdown
    assert "  - CWD : ~" in markdown
    assert "### yugz" not in markdown
    assert "#### yugz" not in markdown
    assert "### Projets" not in markdown
    assert "#### Projets" not in markdown
    assert ">yugz</a>" not in html
    assert ">yugz</li>" not in html
    assert ">Projets</a>" not in html
    assert ">Projets</li>" not in html
    assert f"CWD : {home}" in html
    assert f"CWD : {projects_parent}" in html
    assert "CWD : ~" in html
    assert ">Pulse_V2</a>" in html
    assert ">Pulse_V2</li>" in html


def test_session_summary_reports_projects_files_tests_and_git(tmp_path):
    store = TraceStore(tmp_path / "pulse.sqlite3")
    first_at = datetime(2026, 7, 3, 11, 0, tzinfo=timezone.utc)
    pulse_v2 = "/project/Pulse_V2"
    sandbox = "/project/Pulse_Sandbox"
    activities = [
        Activity(
            "file_changed",
            first_at,
            "filesystem",
            f"Created {pulse_v2}/scripts/dev_reload.py",
            {
                "path": f"{pulse_v2}/scripts/dev_reload.py",
                "event": "created",
                "workspace": pulse_v2,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=1),
            "terminal",
            "Command succeeded: pytest tests_v2",
            {"command": "pytest tests_v2", "exit_code": 0, "cwd": pulse_v2},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=2),
            "terminal",
            'Command succeeded: git commit -m "show project changes"',
            {
                "command": 'git commit -m "show project changes"',
                "exit_code": 0,
                "cwd": pulse_v2,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=3),
            "terminal",
            "Command succeeded: git push",
            {"command": "git push", "exit_code": 0, "cwd": pulse_v2},
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=3, seconds=30),
            "terminal",
            "Command succeeded: python -m pytest tests_v2/test_daily_trace.py",
            {
                "command": "python -m pytest tests_v2/test_daily_trace.py",
                "exit_code": 0,
                "cwd": "/project/TEST",
            },
        ),
        Activity(
            "file_changed",
            first_at + timedelta(minutes=4),
            "filesystem",
            f"Created {sandbox}/src/calc.py",
            {
                "path": f"{sandbox}/src/calc.py",
                "event": "created",
                "workspace": sandbox,
            },
        ),
        Activity(
            "terminal_finished",
            first_at + timedelta(minutes=5),
            "terminal",
            "Command succeeded: git status",
            {"command": "git status", "exit_code": 0, "cwd": pulse_v2},
        ),
    ]
    for activity in activities:
        store.append(activity)

    trace = build_daily_trace(store, date(2026, 7, 3), timezone.utc)
    markdown = render_daily_trace_markdown(trace)
    html = render_daily_trace_html(trace)

    assert "### Résumé de session" in markdown
    assert (
        "#### Pulse\\_V2\n"
        "- Fichiers créés : scripts/dev\\_reload.py\n"
        "- Tests passés : pytest tests\\_v2, "
        "python -m pytest tests\\_v2/test\\_daily\\_trace.py\n"
        "- Git : show project changes"
    ) in markdown
    assert "#### Pulse\\_Sandbox\n- Fichiers créés : src/calc.py" in markdown
    assert "#### TEST" not in markdown
    assert '<div class="session-summary"><h3>Résumé de session</h3>' in html
    assert '<div class="session-project-summary"><h4>Pulse_V2</h4>' in html
    assert '<div class="session-project-summary"><h4>Pulse_Sandbox</h4>' in html
    assert '<div class="session-project-summary"><h4>TEST</h4>' not in html
    assert (
        "Tests passés : pytest tests_v2, "
        "python -m pytest tests_v2/test_daily_trace.py"
    ) in html
    assert "Git : show project changes" in html

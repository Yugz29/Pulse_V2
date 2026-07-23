import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from daemon_v2.main import create_app
from daemon_v2.models import Activity


def test_markdown_archive_omits_live_resume_and_does_not_read_git(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()
    client.post(
        "/activities",
        json={
            "type": "terminal_finished",
            "occurred_at": "2026-07-03T12:00:00+00:00",
            "command": "make test",
            "exit_code": 0,
            "cwd": str(workspace),
        },
    )
    git_calls = []

    def fake_run(command, **kwargs):
        git_calls.append(command)
        if "status" in command:
            return SimpleNamespace(stdout="## main\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr("daemon_v2.daily_trace.subprocess.run", fake_run)

    archive = client.get("/trace/2026-07-03.md").get_data(as_text=True)

    assert "## Maintenant" not in archive
    assert "## Reprise" not in archive
    assert "## Aujourd’hui" in archive
    assert "## Session 1" in archive
    assert "make test" in archive
    assert git_calls == []


def test_markdown_today_keeps_live_resume(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()
    client.post(
        "/activities",
        json={
            "type": "terminal_finished",
            "command": "make test",
            "exit_code": 0,
            "cwd": str(workspace),
        },
    )
    git_calls = []

    def fake_run(command, **kwargs):
        git_calls.append(command)
        if "status" in command:
            return SimpleNamespace(stdout="## main\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr("daemon_v2.daily_trace.subprocess.run", fake_run)

    live = client.get("/trace/today.md").get_data(as_text=True)

    assert "## Maintenant" in live
    assert "## Reprise" in live
    assert "### Git" in live
    assert git_calls


def test_home_route_renders_today_activity_as_html(tmp_path):
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()
    client.post(
        "/activities",
        json={
            "type": "terminal_finished",
            "command": "pytest <tests_v2>",
            "exit_code": 0,
            "cwd": "/project",
        },
    )

    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "--bg:#11151a" in html
    assert "color-scheme:dark" in html
    assert "<h1>Trace du " in html
    assert (
        '<nav class="sidebar" aria-label="Navigation de la timeline">'
        in html
    )
    assert '<a class="nav-main" href="#maintenant">Maintenant</a>' in html
    assert '<a class="nav-main" href="#reprise">Reprise</a>' in html
    assert (
        '<a class="nav-main nav-live nav-bottom" href="#timeline-live">Direct</a>'
        in html
    )
    assert '<a class="nav-main" href="#aujourdhui">Aujourd’hui</a>' in html
    assert '<a class="nav-main" href="#etat-systeme">État système</a>' in html
    assert '<a class="nav-session" href="#session-1">Session 1</a>' in html
    assert '<section class="current" id="maintenant">' in html
    assert '<section class="resume" id="reprise">' in html
    assert '<section class="summary" id="aujourdhui">' in html
    assert '<section class="system" id="etat-systeme">' in html
    assert '<section class="session" id="session-1">' in html
    assert '<div id="timeline-live" aria-hidden="true"></div>' in html
    assert html.index('<section class="session" id="session-1">') < html.index(
        '<div id="timeline-live"'
    )
    assert "<script" not in html
    assert "<h2>État système</h2>" in html
    assert "<dt>Daemon</dt><dd>running</dd>" in html
    assert f"<dt>Base SQLite</dt><dd>{tmp_path / 'trace.db'}</dd>" in html
    assert "<dt>Base existante</dt><dd>oui</dd>" in html
    assert "<dt>Événements du jour</dt><dd>1</dd>" in html
    assert "<dt>Sessions affichées</dt><dd>1</dd>" in html
    assert "<dt>Watcher terminal</dt><dd>external;" in html
    assert "Session 1" in html
    assert "Command succeeded: <code>pytest &lt;tests_v2&gt;</code>" in html
    assert '<span class="label label-test">test</span>' in html
    assert 'href="/days">Jours</a>' in html
    assert 'href="/trace/today.md"' in html


def test_app_activated_is_readable_in_markdown_and_html(tmp_path):
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()

    apps = [
        "CleanMyMac Menu",
        "Code",
        "ChatGPT",
        "Terminal",
        "Safari",
        "ChatGPT",
        "Terminal",
        "Safari",
        "ChatGPT",
        "Codex",
        "Notes",
    ]
    for app_name in apps:
        response = client.post(
            "/activities",
            json={"type": "app_activated", "app": app_name},
        )
        assert response.status_code == 201

    trace = client.get("/trace/today").get_json()
    assert trace["activity_count"] == 11
    assert [
        activity["details"]["app"]
        for activity in trace["sessions"][0]["activities"]
    ] == apps

    expected = "ChatGPT, Terminal, Safari, Code, Codex"
    expected_summary = "Apps principales : ChatGPT, Terminal, Safari, Code, Codex"
    markdown = client.get("/trace/today.md").get_data(as_text=True)
    html = client.get("/").get_data(as_text=True)
    assert expected in markdown
    assert expected in html
    assert "## Activité passive" in markdown
    assert "<h2>Activité passive</h2>" in html
    assert expected_summary in markdown
    assert (
        "<dt>Apps principales</dt>"
        "<dd>ChatGPT, Terminal, Safari, Code, Codex</dd>"
    ) in html
    app_lines = [line for line in markdown.splitlines() if "Apps " in line]
    assert all("Notes" not in line for line in app_lines)
    assert all("×" not in line for line in app_lines)
    assert "×" not in html
    assert "CleanMyMac Menu" not in markdown
    assert "CleanMyMac Menu" not in html
    assert "- Dernière activité utile : Non détectée" in markdown
    assert (
        "<dt>Dernière activité utile</dt><dd>Non détectée</dd>"
        in html
    )


def test_today_markdown_route_returns_readable_markdown(tmp_path):
    app = create_app(tmp_path / "trace.db")

    response = app.test_client().get("/trace/today.md")

    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    assert response.get_data(as_text=True).startswith("# Trace du ")


def test_trace_days_lists_available_days_newest_first(tmp_path):
    app = create_app(tmp_path / "trace.db")
    store = app.config["TRACE_STORE"]
    newest_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    activities = [
        Activity(
            "file_changed",
            newest_at - timedelta(days=1),
            "filesystem",
            "Modified /project/Legacy/a.py",
            {
                "path": "/project/Legacy/a.py",
                "event": "modified",
                "workspace": "/project/Legacy",
            },
        ),
        Activity(
            "file_changed",
            newest_at,
            "filesystem",
            "Modified /project/Pulse_V2/a.py",
            {
                "path": "/project/Pulse_V2/a.py",
                "event": "modified",
                "workspace": "/project/Pulse_V2",
            },
        ),
        Activity(
            "file_changed",
            newest_at + timedelta(minutes=5),
            "filesystem",
            "Created /project/Pulse_Sandbox/b.py",
            {
                "path": "/project/Pulse_Sandbox/b.py",
                "event": "created",
                "workspace": "/project/Pulse_Sandbox",
            },
        ),
        Activity(
            "terminal_finished",
            newest_at + timedelta(minutes=10),
            "terminal",
            "Command succeeded: pytest tests_v2",
            {
                "command": "pytest tests_v2",
                "exit_code": 0,
                "cwd": "/project/Pulse_V2",
            },
        ),
        Activity(
            "terminal_finished",
            newest_at + timedelta(minutes=15),
            "terminal",
            "Command succeeded: git commit -m compact-summary",
            {
                "command": "git commit -m compact-summary",
                "exit_code": 0,
                "cwd": "/project/Pulse_V2",
            },
        ),
        Activity(
            "terminal_finished",
            newest_at + timedelta(minutes=20),
            "terminal",
            "Command succeeded: git push",
            {
                "command": "git push",
                "exit_code": 0,
                "cwd": "/project/Pulse_V2",
            },
        ),
        Activity(
            "terminal_finished",
            newest_at + timedelta(minutes=25),
            "terminal",
            "Command succeeded: curl -s http://localhost/status | rg daemon",
            {
                "command": "curl -s http://localhost/status | rg daemon",
                "exit_code": 0,
                "cwd": "/project/Pulse_V2",
            },
        ),
        Activity(
            "terminal_finished",
            newest_at + timedelta(minutes=28),
            "terminal",
            "Command failed (1): false",
            {
                "command": "false",
                "exit_code": 1,
                "cwd": "/unqualified",
            },
        ),
    ]
    for activity in activities:
        store.append(activity)

    response = app.test_client().get("/trace/days")

    assert response.status_code == 200
    assert response.get_json() == {
        "days": [
            {
                "date": "2026-07-04",
                "event_count": 7,
                "session_count": 1,
                "projects": ["Pulse_V2", "Pulse_Sandbox"],
                "summary": [
                    "Pulse_V2, Pulse_Sandbox — 1 commit · 2 fichiers touchés "
                    "(1 créé, 1 modifié) · dossiers principaux : racine",
                    "Tests OK · Git : commit + push · Erreurs observées · "
                    "7 événements",
                ],
                "project_summaries": [
                    {
                        "project": "Pulse_V2",
                        "workspace": "/project/Pulse_V2",
                        "event_count": 5,
                        "summary": [
                            "1 commit · 1 fichier modifié · "
                            "dossiers principaux : racine",
                            "Tests OK · Git : commit + push",
                        ],
                    },
                    {
                        "project": "Pulse_Sandbox",
                        "workspace": "/project/Pulse_Sandbox",
                        "event_count": 1,
                        "summary": [
                            "1 fichier créé · dossiers principaux : racine"
                        ],
                    },
                ],
            },
            {
                "date": "2026-07-03",
                "event_count": 1,
                "session_count": 1,
                "projects": ["Legacy"],
                "summary": [
                    "Legacy — 1 fichier modifié · dossiers principaux : racine",
                    "1 événement",
                ],
                "project_summaries": [
                    {
                        "project": "Legacy",
                        "workspace": "/project/Legacy",
                        "event_count": 1,
                        "summary": [
                            "1 fichier modifié · dossiers principaux : racine"
                        ],
                    }
                ],
            },
        ]
    }

    html_response = app.test_client().get("/days")
    html = html_response.get_data(as_text=True)
    assert html_response.status_code == 200
    assert html_response.mimetype == "text/html"
    assert "<h1>Jours récents</h1>" in html
    assert html.index("<h2>2026-07-04</h2>") < html.index(
        "<h2>2026-07-03</h2>"
    )
    assert "7 événements · 1 session" in html
    assert '<p class="day-project-count">2 Projets</p>' in html
    assert '<p class="day-project-count">1 Projet</p>' in html
    assert "Projets :" not in html
    assert "curl -s http://localhost/status" not in html
    assert "pytest tests_v2" not in html
    assert '<div class="day-project-summaries">' in html
    assert (
        '<section class="day-project-summary"><h3>Pulse_V2</h3>'
        '<p class="day-project-summary-primary">1 commit · '
        "1 fichier modifié · dossiers principaux : racine</p>"
    ) in html
    assert (
        '<p class="day-project-summary-secondary">'
        "Tests OK · Git : commit + push</p>"
    ) in html
    assert (
        '<section class="day-project-summary"><h3>Pulse_Sandbox</h3>'
        '<p class="day-project-summary-primary">1 fichier créé · '
        "dossiers principaux : racine</p>"
    ) in html
    assert (
        '<section class="day-project-summary"><h3>Legacy</h3>'
        '<p class="day-project-summary-primary">1 fichier modifié · '
        "dossiers principaux : racine</p>"
    ) in html
    assert 'href="/day/2026-07-04">HTML</a>' in html
    assert 'href="/trace/2026-07-04">JSON</a>' in html
    assert 'href="/trace/2026-07-04.md">Markdown</a>' in html
    assert "<script" not in html


def test_trace_days_uses_neutral_summary_for_app_only_day(tmp_path):
    app = create_app(tmp_path / "trace.db")
    app.config["TRACE_STORE"].append(
        Activity(
            "app_activated",
            datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
            "application",
            "Activated Code",
            {"app": "Code"},
        )
    )

    response = app.test_client().get("/trace/days")
    html = app.test_client().get("/days").get_data(as_text=True)

    assert response.get_json()["days"][0]["summary"] == [
        "Activité locale — activité enregistrée",
        "1 événement",
    ]
    assert response.get_json()["days"][0]["project_summaries"] == []
    assert '<p class="day-project-count">0 Projets</p>' in html
    assert (
        '<p class="day-summary-primary">'
        "Activité locale — activité enregistrée</p>"
        in html
    )
    assert (
        '<p class="day-summary-secondary">1 événement</p>'
        in html
    )


def test_dated_trace_routes_filter_day_and_handle_empty_or_invalid_dates(tmp_path):
    app = create_app(tmp_path / "trace.db")
    store = app.config["TRACE_STORE"]
    activities = [
        Activity(
            "file_changed",
            datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
            "filesystem",
            "Modified /project/day3.py",
            {
                "path": "/project/day3.py",
                "event": "modified",
                "workspace": "/project",
            },
        ),
        Activity(
            "file_changed",
            datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc),
            "filesystem",
            "Modified /project/day4.py",
            {
                "path": "/project/day4.py",
                "event": "modified",
                "workspace": "/project",
            },
        ),
    ]
    for activity in activities:
        store.append(activity)
    client = app.test_client()

    response = client.get("/trace/2026-07-04")
    trace = response.get_json()
    assert response.status_code == 200
    assert trace["date"] == "2026-07-04"
    assert trace["activity_count"] == 1
    assert trace["sessions"][0]["activities"][0]["details"]["path"].endswith(
        "day4.py"
    )

    markdown_response = client.get("/trace/2026-07-04.md")
    markdown = markdown_response.get_data(as_text=True)
    assert markdown_response.status_code == 200
    assert markdown_response.mimetype == "text/markdown"
    assert markdown.startswith("# Trace du 2026-07-04")
    assert "day4.py" in markdown
    assert "day3.py" not in markdown

    html_response = client.get("/day/2026-07-04")
    html = html_response.get_data(as_text=True)
    assert html_response.status_code == 200
    assert html_response.mimetype == "text/html"
    assert "<h1>Journal du 2026-07-04</h1>" in html
    assert "day4.py" in html
    assert "day3.py" not in html
    assert '<nav class="sidebar"' in html
    assert 'href="#resume-jour">Résumé du jour</a>' in html
    assert '<section class="summary" id="resume-jour">' in html
    assert "<h2>Résumé du jour</h2>" in html
    assert 'href="#timeline-end">Fin du jour</a>' in html
    assert '<div id="timeline-end" aria-hidden="true"></div>' in html
    assert 'id="maintenant"' not in html
    assert 'id="reprise"' not in html
    assert 'id="etat-systeme"' not in html
    assert ">Maintenant</a>" not in html
    assert ">Reprise</a>" not in html
    assert ">État système</a>" not in html
    assert ">Direct</a>" not in html
    assert '<section class="session" id="session-1">' in html
    assert 'href="/days">Jours</a>' in html
    assert 'href="/trace/2026-07-04">JSON</a>' in html
    assert 'href="/trace/2026-07-04.md">Markdown</a>' in html

    empty_trace = client.get("/trace/2026-07-02").get_json()
    assert empty_trace["date"] == "2026-07-02"
    assert empty_trace["activity_count"] == 0
    assert empty_trace["sessions"] == []
    assert "_Aucune activité._" in client.get(
        "/trace/2026-07-02.md"
    ).get_data(as_text=True)
    empty_html = client.get("/day/2026-07-02")
    assert empty_html.status_code == 200
    assert "Aucune activité pour cette journée." in empty_html.get_data(
        as_text=True
    )

    for path in (
        "/trace/not-a-date",
        "/trace/2026-02-30.md",
        "/day/not-a-date",
    ):
        invalid_response = client.get(path)
        assert invalid_response.status_code == 400
        assert invalid_response.get_json() == {
            "error": "invalid date; expected YYYY-MM-DD"
        }


def test_status_reports_local_paths_and_today_activity(tmp_path):
    database_path = tmp_path / "trace.db"
    app = create_app(database_path)
    client = app.test_client()
    client.post(
        "/activities",
        json={
            "type": "file_changed",
            "path": "/project/a.py",
            "event": "modified",
            "workspace": "/project",
        },
    )

    response = client.get("/status")
    status = response.get_json()

    assert response.status_code == 200
    assert status["daemon"] == "running"
    assert status["url"] == "http://127.0.0.1:8765/"
    assert status["database_path"] == str(database_path)
    assert status["database_exists"] is True
    assert status["event_count"] == 1
    assert status["displayed_session_count"] == 1
    assert status["last_event"]["type"] == "file_changed"
    assert status["primary_workspace"] == "/project"
    assert status["terminal_watcher"].startswith("external")


def test_pulse_inspection_command_is_stored_as_raw_timeline_event(tmp_path):
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()

    response = client.post(
        "/activities",
        json={
            "type": "terminal_finished",
            "command": "curl http://127.0.0.1:5000/trace/today.md",
            "exit_code": 0,
            "cwd": "/project",
        },
    )

    assert response.status_code == 201
    assert client.get("/trace/today").get_json()["activity_count"] == 1
    markdown = client.get("/trace/today.md").get_data(as_text=True)
    assert "curl http://127.0.0.1:5000/trace/today.md" in markdown
    assert "Dernier signal utile observé" not in markdown


def test_clear_returns_204_and_is_not_stored(tmp_path):
    database_path = tmp_path / "trace.db"
    app = create_app(database_path)

    response = app.test_client().post(
        "/activities",
        json={
            "type": "terminal_finished",
            "command": "clear",
            "exit_code": 0,
            "cwd": "/project",
        },
    )

    assert response.status_code == 204
    with sqlite3.connect(database_path) as connection:
        activity_count = connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0]
    assert activity_count == 0


def test_multiline_clear_is_removed_but_trace_curl_is_stored(tmp_path):
    database_path = tmp_path / "trace.db"
    app = create_app(database_path)

    response = app.test_client().post(
        "/activities",
        json={
            "type": "terminal_finished",
            "command": "clear\ncurl http://127.0.0.1:5000/trace/today.md",
            "exit_code": 0,
            "cwd": "/project",
        },
    )

    assert response.status_code == 201
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT details_json FROM activities"
        ).fetchall()
    assert len(rows) == 1
    assert "clear" not in rows[0][0]
    assert "curl http://127.0.0.1:5000/trace/today.md" in rows[0][0]


def test_multiline_activities_curl_returns_204_and_is_not_stored(tmp_path):
    database_path = tmp_path / "trace.db"
    app = create_app(database_path)
    command = (
        "curl -X POST http://127.0.0.1:5000/activities \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\n"
        '    "type": "file_changed",\n'
        '    "event": "modified"\n'
        "  }'"
    )

    response = app.test_client().post(
        "/activities",
        json={
            "type": "terminal_finished",
            "command": command,
            "exit_code": 0,
            "cwd": "/project",
        },
    )

    assert response.status_code == 204
    with sqlite3.connect(database_path) as connection:
        activity_count = connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0]
    assert activity_count == 0


def test_file_changed_route_renders_relative_path_and_keeps_absolute_json(tmp_path):
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()
    workspace = "/Users/yugz/Projets/Pulse_V2"
    absolute_path = f"{workspace}/daemon_v2/daily_trace.py"

    response = client.post(
        "/activities",
        json={
            "type": "file_changed",
            "path": absolute_path,
            "event": "modified",
            "workspace": workspace,
        },
    )

    assert response.status_code == 201
    trace = client.get("/trace/today").get_json()
    assert trace["sessions"][0]["activities"][0]["details"]["path"] == absolute_path
    markdown = client.get("/trace/today.md").get_data(as_text=True)
    assert "Modified `daemon_v2/daily_trace.py`" in markdown
    assert f"Modified `{absolute_path}`" not in markdown


def test_repeated_file_changes_are_raw_in_json_and_coalesced_in_markdown(tmp_path):
    app = create_app(tmp_path / "trace.db")
    client = app.test_client()
    workspace = "/project"

    for _ in range(3):
        response = client.post(
            "/activities",
            json={
                "type": "file_changed",
                "path": f"{workspace}/a.py",
                "event": "modified",
                "workspace": workspace,
            },
        )
        assert response.status_code == 201
    client.post(
        "/activities",
        json={
            "type": "file_changed",
            "path": f"{workspace}/b.py",
            "event": "modified",
            "workspace": workspace,
        },
    )

    trace = client.get("/trace/today").get_json()
    assert trace["activity_count"] == 4
    assert len(trace["sessions"][0]["activities"]) == 4

    markdown = client.get("/trace/today.md").get_data(as_text=True)
    timeline = markdown.split("## Session 1", 1)[1]
    assert "Fichiers modifiés :" in markdown
    assert timeline.count("Modified `a.py` ×3") == 1
    assert timeline.count("Modified `b.py`") == 1
    html = client.get("/").get_data(as_text=True)
    assert "Fichiers modifiés :" in html
    assert "Modified <code>a.py</code> ×3" in html
    assert "Modified <code>b.py</code>" in html


def canonical_request(event_id="019c-route", **overrides):
    payload = {
        "event_id": event_id,
        "schema_version": 1,
        "type": "file_changed",
        "producer": {
            "name": "pulse-test",
            "version": "1.0",
            "instance_id": "route-tests",
        },
        "occurred_at": "2026-07-23T14:32:10.123+02:00",
        "details": {
            "path": "/project/main.py",
            "event": "modified",
        },
    }
    payload.update(overrides)
    return payload


def test_canonical_retry_is_idempotent_and_preserves_first_recorded_at(tmp_path):
    database = tmp_path / "trace.db"
    client = create_app(database).test_client()
    payload = canonical_request()

    first = client.post("/activities", json=payload)
    retry = client.post("/activities", json=payload)

    assert first.status_code == 201
    assert retry.status_code == 200
    assert first.json["accepted"] is True
    assert first.json["duplicate"] is False
    assert retry.json["duplicate"] is True
    assert retry.json["recorded_at"] == first.json["recorded_at"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0] == 1


def test_same_event_id_with_different_details_or_type_conflicts(tmp_path):
    database = tmp_path / "trace.db"
    client = create_app(database).test_client()
    payload = canonical_request()
    assert client.post("/activities", json=payload).status_code == 201

    changed_details = canonical_request()
    changed_details["details"] = {
        "path": "/project/other.py",
        "event": "modified",
    }
    details_response = client.post("/activities", json=changed_details)

    changed_type = canonical_request(
        type="app_activated",
        details={"app": "Terminal"},
    )
    type_response = client.post("/activities", json=changed_type)

    assert details_response.status_code == 409
    assert details_response.json["error"]["code"] == "event_id_conflict"
    assert type_response.status_code == 409
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0] == 1


def test_different_event_ids_with_same_payload_create_two_rows(tmp_path):
    database = tmp_path / "trace.db"
    client = create_app(database).test_client()

    first = client.post("/activities", json=canonical_request("event-a"))
    second = client.post("/activities", json=canonical_request("event-b"))

    assert first.status_code == second.status_code == 201
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0] == 2


def test_json_export_contains_versioned_event_metadata(tmp_path):
    client = create_app(tmp_path / "trace.db").test_client()
    response = client.post("/activities", json=canonical_request())
    assert response.status_code == 201

    trace = client.get("/trace/2026-07-23").json
    event = trace["sessions"][0]["activities"][0]

    assert event["event_id"] == "019c-route"
    assert event["schema_version"] == 1
    assert event["occurred_at"].endswith("+02:00")
    assert event["recorded_at"].endswith("+00:00")
    assert event["producer"] == {
        "name": "pulse-test",
        "version": "1.0",
        "instance_id": "route-tests",
    }
    assert event["details"]["path"] == "/project/main.py"
    assert "Modified" in client.get("/trace/2026-07-23.md").get_data(as_text=True)


def test_legacy_route_response_and_export_are_explicit(tmp_path):
    database = tmp_path / "trace.db"
    client = create_app(database).test_client()
    payload = {
        "type": "app_activated",
        "timestamp": "2026-07-23T12:00:00+02:00",
        "app": "Terminal",
    }

    first = client.post("/activities", json=payload)
    second = client.post("/activities", json=payload)
    trace = client.get("/trace/2026-07-23").json
    events = trace["sessions"][0]["activities"]

    assert first.status_code == second.status_code == 201
    assert first.json["event_id"] != second.json["event_id"]
    assert len(events) == 2
    assert all(event["producer"]["name"] == "pulse-legacy" for event in events)
    assert all(event["occurred_at"].endswith("+02:00") for event in events)

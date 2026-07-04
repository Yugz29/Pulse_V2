import sqlite3

from daemon_v2.main import create_app


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

    expected = "Apps actives : ChatGPT, Terminal, Safari, Code, Codex"
    expected_summary = "Apps principales : ChatGPT, Terminal, Safari, Code, Codex"
    markdown = client.get("/trace/today.md").get_data(as_text=True)
    html = client.get("/").get_data(as_text=True)
    assert expected in markdown
    assert expected in html
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
    assert status["url"] == "http://127.0.0.1:5000/"
    assert status["database_path"] == str(database_path)
    assert status["database_exists"] is True
    assert status["event_count"] == 1
    assert status["displayed_session_count"] == 1
    assert status["last_event"]["type"] == "file_changed"
    assert status["primary_workspace"] == "/project"
    assert status["terminal_watcher"].startswith("external")


def test_ignored_terminal_command_is_not_stored(tmp_path):
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

    assert response.status_code == 204
    assert client.get("/trace/today").get_json()["activity_count"] == 0


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


def test_multiline_clear_and_trace_curl_returns_204_and_is_not_stored(tmp_path):
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

    assert response.status_code == 204
    with sqlite3.connect(database_path) as connection:
        activity_count = connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0]
    assert activity_count == 0


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

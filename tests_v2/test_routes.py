import sqlite3

from daemon_v2.main import create_app


def test_today_markdown_route_returns_readable_markdown(tmp_path):
    app = create_app(tmp_path / "trace.db")

    response = app.test_client().get("/trace/today.md")

    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    assert response.get_data(as_text=True).startswith("# Trace du ")


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
    assert markdown.count("Modified `a.py` ×3") == 1
    assert markdown.count("Modified `b.py`") == 1

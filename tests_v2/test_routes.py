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

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from daemon_v2.analysis.projects import (
    resolve_project_context,
    last_observed_workspace,
    most_frequent_explicit_workspace,
)
from daemon_v2.daily_trace import (
    _session_project_summaries,
    build_available_days,
    build_current_state,
    build_daily_summary,
    build_daily_trace,
    build_resume,
    primary_workspace,
    render_daily_trace_html,
    render_daily_trace_markdown,
)
from daemon_v2.models import Activity
from daemon_v2.trace_store import TraceStore


DAY = date(2026, 7, 3)
START = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)


def append_terminal(
    store: TraceStore,
    command: str,
    cwd: str,
    *,
    minute: int,
    exit_code: int = 0,
) -> None:
    status = "succeeded" if exit_code == 0 else f"failed ({exit_code})"
    store.append(
        Activity(
            "terminal_finished",
            START + timedelta(minutes=minute),
            "terminal",
            f"Command {status}: {command}",
            {"command": command, "exit_code": exit_code, "cwd": cwd},
        )
    )


def append_file(
    store: TraceStore,
    workspace: str,
    *,
    minute: int,
    name: str = "app.py",
) -> None:
    path = str(Path(workspace) / name)
    store.append(
        Activity(
            "file_changed",
            START + timedelta(minutes=minute),
            "filesystem",
            f"Modified {path}",
            {"path": path, "event": "modified", "workspace": workspace},
        )
    )


def trace_for(store: TraceStore) -> dict:
    return build_daily_trace(store, DAY, timezone.utc)


def test_resolves_known_project_roots_and_modules():
    cases = [
        (
            "/Users/yugz/Projets/DevNote/DevNote/backend",
            "/Users/yugz/Projets/DevNote",
            "DevNote",
            "backend",
        ),
        (
            "/Users/yugz/Projets/DevNote/DevNote/frontend",
            "/Users/yugz/Projets/DevNote",
            "DevNote",
            "frontend",
        ),
        (
            "/Users/yugz/Projets/Pulse_V2",
            "/Users/yugz/Projets/Pulse_V2",
            "Pulse_V2",
            None,
        ),
        (
            "/Users/yugz/Projets/Pulse_V2/daemon",
            "/Users/yugz/Projets/Pulse_V2",
            "Pulse_V2",
            "daemon",
        ),
    ]

    for cwd, project_root, project_name, module in cases:
        context = resolve_project_context(cwd)
        assert context.project_root == project_root
        assert context.project_name == project_name
        assert context.cwd == cwd
        assert context.module == module


def test_unknown_isolated_directory_keeps_existing_identity(tmp_path):
    workspace = tmp_path / "unknown"
    context = resolve_project_context(str(workspace))

    assert context.project_root == str(workspace)
    assert context.project_name == "unknown"
    assert context.module is None


def test_nested_modules_are_grouped_under_logical_project(tmp_path):
    store = TraceStore(tmp_path / "trace.db")
    backend = "/Users/yugz/Projets/DevNote/DevNote/backend"
    frontend = "/Users/yugz/Projets/DevNote/DevNote/frontend"
    append_terminal(store, "python -m pytest", backend, minute=0)
    append_terminal(store, "npm run build", frontend, minute=1)

    trace = trace_for(store)
    summary = build_daily_summary(trace, project_mode="archive")
    markdown = render_daily_trace_markdown(trace, archive_mode=True)

    assert summary["workspaces"] == ["/Users/yugz/Projets/DevNote"]
    assert "- Projets : DevNote" in markdown
    assert "### DevNote" in markdown
    assert "### backend" not in markdown
    assert "### frontend" not in markdown
    assert "Module : backend" in markdown
    assert "Module : frontend" in markdown


def test_weak_workspaces_remain_visible_but_are_not_projects(tmp_path):
    store = TraceStore(tmp_path / "trace.db")
    home = str(Path.home())
    projects = str(Path.home() / "Projets")
    append_terminal(store, "pwd", home, minute=0)
    append_terminal(store, "ls", projects, minute=1)

    trace = trace_for(store)
    current = build_current_state(trace)
    summary = build_daily_summary(trace)
    markdown = render_daily_trace_markdown(trace)

    assert current["project"] == "Non détecté"
    assert summary["workspaces"] == []
    assert primary_workspace(trace) is None
    assert f"  - CWD : {home}" in markdown
    assert f"  - CWD : {projects}" in markdown


def test_one_terminal_workspace_is_live_but_not_a_qualified_day_project(tmp_path):
    store = TraceStore(tmp_path / "trace.db")
    workspace = "/work/single-observation"
    append_terminal(store, "echo useful", workspace, minute=0)

    trace = trace_for(store)
    current = build_current_state(trace)
    resume = build_resume(trace)
    summary = build_daily_summary(trace)

    assert current["workspace"] == workspace
    assert current["project"] == "single-observation"
    assert last_observed_workspace(trace) == workspace
    assert "Dernier projet observé : single-observation" in resume
    assert summary["workspaces"] == []
    # /status uses only explicit `details.workspace`, not terminal CWD values.
    assert primary_workspace(trace) is None
    assert most_frequent_explicit_workspace(trace) is None


def test_day_project_qualification_uses_files_or_two_useful_signals(tmp_path):
    store = TraceStore(tmp_path / "trace.db")
    file_workspace = "/work/from-file"
    repeated_workspace = "/work/from-terminal"
    noise_workspace = "/work/noise"
    append_file(store, file_workspace, minute=0)
    append_terminal(store, "echo first", repeated_workspace, minute=1)
    append_terminal(store, "echo second", repeated_workspace, minute=2)
    append_terminal(
        store,
        "curl http://127.0.0.1:5000/trace/today",
        noise_workspace,
        minute=3,
    )
    append_terminal(
        store,
        (
            "Pulse_V2 — consigne collée\n"
            "Contexte : vérifier la trace.\n"
            "Objectif : ne rien modifier.\n"
            "À faire : lire le résultat."
        ),
        noise_workspace,
        minute=4,
    )

    summary = build_daily_summary(trace_for(store))

    assert summary["workspaces"] == [file_workspace, repeated_workspace]


def test_session_propagation_differs_from_exact_days_attribution(tmp_path):
    store = TraceStore(tmp_path / "trace.db")
    project = "/work/project-a"
    other = "/work/unqualified"
    inspection = "curl http://127.0.0.1:5000/trace/today"
    append_terminal(store, inspection, other, minute=0)
    append_file(store, project, minute=1)
    append_terminal(store, "make test", other, minute=2)

    trace = trace_for(store)
    summary = build_daily_summary(trace)
    session_summaries = _session_project_summaries(
        trace["sessions"][0], set(summary["workspaces"])
    )
    available_day = build_available_days(store, timezone.utc)["days"][0]

    assert summary["workspaces"] == [project]
    assert session_summaries == [
        (
            "project-a",
            [
                "Fichiers modifiés : app.py",
                "Tests passés : make test",
            ],
        )
    ]
    # The earlier inspection precedes the first qualified project and is not propagated.
    assert all(
        inspection not in fact
        for _project, facts in session_summaries
        for fact in facts
        if isinstance(fact, str)
    )
    # /days selects exact workspace matches, so the propagated test is absent.
    assert available_day["project_summaries"] == [
        {
            "project": "project-a",
            "workspace": project,
            "event_count": 1,
            "summary": [
                "1 fichier modifié · dossiers principaux : racine"
            ],
        }
    ]
    assert "Tests OK" in available_day["summary"][1]


def test_current_git_directory_only_changes_live_project_qualification(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = TraceStore(tmp_path / "trace.db")
    append_terminal(store, "echo one observation", str(workspace), minute=0)
    archive_trace = trace_for(store)

    archive_summary_before = build_daily_summary(
        archive_trace, project_mode="archive"
    )
    archive_markdown_before = render_daily_trace_markdown(
        archive_trace, archive_mode=True
    )
    archive_html_before = render_daily_trace_html(
        archive_trace, archive_mode=True
    )
    days_before = build_available_days(store, timezone.utc)

    (workspace / ".git").mkdir()

    live_summary = build_daily_summary(archive_trace, project_mode="live")
    live_markdown = render_daily_trace_markdown(archive_trace)
    archive_summary_after = build_daily_summary(
        archive_trace, project_mode="archive"
    )
    archive_markdown_after = render_daily_trace_markdown(
        archive_trace, archive_mode=True
    )
    archive_html_after = render_daily_trace_html(
        archive_trace, archive_mode=True
    )
    days_after = build_available_days(store, timezone.utc)

    assert live_summary["workspaces"] == [str(workspace)]
    assert "- Projets : repo" in live_markdown
    assert archive_summary_before["workspaces"] == []
    assert archive_summary_after == archive_summary_before
    assert archive_markdown_after == archive_markdown_before
    assert archive_html_after == archive_html_before
    assert days_after == days_before

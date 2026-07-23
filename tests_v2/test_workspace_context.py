import json
import subprocess
from pathlib import Path

import pytest

from daemon_v2.git_context import GitContext
from daemon_v2.producer_outbox import ProducerOutbox, build_terminal_payload
from daemon_v2.workspace_context import read_workspace_context


def git(*args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def create_repository(path: Path) -> Path:
    path.mkdir()
    git("init", "-b", "main", cwd=path)
    git("config", "user.email", "pulse-tests@example.invalid", cwd=path)
    git("config", "user.name", "Pulse Tests", cwd=path)
    (path / "tracked.txt").write_text("initial\n")
    git("add", "tracked.txt", cwd=path)
    git("commit", "-m", "initial", cwd=path)
    return path


def test_resolves_git_repository_with_high_confidence(tmp_path):
    repository = create_repository(tmp_path / "git-project")

    context = read_workspace_context(repository)

    assert context is not None
    assert context.project_name == "git-project"
    assert context.workspace_root == str(repository)
    assert context.git_root == str(repository)
    assert context.resolution_method == "git"
    assert context.resolution_confidence == "high"


def test_resolves_git_repository_from_subdirectory(tmp_path):
    repository = create_repository(tmp_path / "nested-project")
    nested = repository / "src" / "feature"
    nested.mkdir(parents=True)

    context = read_workspace_context(nested)

    assert context is not None
    assert context.project_name == "nested-project"
    assert context.workspace_root == str(repository)
    assert context.git_root == str(repository)
    assert context.resolution_method == "git"


@pytest.mark.parametrize(
    "marker",
    [
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "setup.py",
        "requirements.txt",
        ".venv",
    ],
)
def test_resolves_nearest_known_marker(tmp_path, marker):
    project = tmp_path / f"project-{marker.replace('.', '-')}"
    nested = project / "src" / "nested"
    nested.mkdir(parents=True)
    marker_path = project / marker
    if marker == ".venv":
        marker_path.mkdir()
    else:
        marker_path.write_text("")

    context = read_workspace_context(nested, git_context=None)

    assert context is not None
    assert context.project_name == project.name
    assert context.workspace_root == str(project)
    assert context.git_root is None
    assert context.resolution_method == "marker"
    assert context.resolution_confidence == "medium"


def test_nearest_marker_wins_with_single_parent_walk(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "packages" / "inner"
    nested = inner / "src"
    nested.mkdir(parents=True)
    (outer / "pyproject.toml").write_text("")
    (inner / "package.json").write_text("{}")

    context = read_workspace_context(nested, git_context=None)

    assert context is not None
    assert context.workspace_root == str(inner)
    assert context.project_name == "inner"


def test_falls_back_to_cwd_without_marker(tmp_path):
    workspace = tmp_path / "unmarked"
    workspace.mkdir()

    context = read_workspace_context(workspace, git_context=None)

    assert context is not None
    assert context.project_name == "unmarked"
    assert context.workspace_root == str(workspace)
    assert context.git_root is None
    assert context.resolution_method == "cwd"
    assert context.resolution_confidence == "low"


def test_explicit_git_result_is_reused_without_second_lookup(tmp_path, monkeypatch):
    root = tmp_path / "known-git"
    root.mkdir()
    git_context = GitContext(
        repository="known-git",
        git_root=str(root),
        branch="main",
        head="abc1234",
        dirty=False,
        staged=0,
        unstaged=0,
        untracked=0,
    )

    monkeypatch.setattr(
        "daemon_v2.workspace_context.read_git_context",
        lambda _: pytest.fail("Git must not be resolved twice"),
    )
    context = read_workspace_context(root, git_context=git_context)

    assert context is not None
    assert context.git_root == git_context.git_root


def test_git_and_workspace_roots_are_identical_in_durable_json(tmp_path):
    repository = create_repository(tmp_path / "durable-project")
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")

    payload_json = build_terminal_payload(
        outbox,
        command="git status",
        cwd=str(repository),
        exit_code=0,
        started_at="2026-07-23T14:31:00+02:00",
        finished_at="2026-07-23T14:32:00+02:00",
    )
    assert payload_json is not None
    outbox.enqueue_payload(payload_json)

    pending = outbox.oldest()
    assert pending is not None
    assert pending.payload_json == payload_json
    details = json.loads(pending.payload_json)["details"]
    assert details["workspace"] == {
        "project_name": "durable-project",
        "workspace_root": str(repository),
        "git_root": str(repository),
        "resolution_method": "git",
        "resolution_confidence": "high",
    }
    assert details["workspace"]["git_root"] == details["git"]["git_root"]


def test_non_terminal_payload_is_not_changed_by_outbox(tmp_path):
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")
    payload = json.dumps(
        {
            "event_id": "file-event",
            "schema_version": 1,
            "type": "file_changed",
            "producer": {
                "name": "pulse-test",
                "version": "1.0",
                "instance_id": "tests",
            },
            "occurred_at": "2026-07-23T14:32:00+02:00",
            "details": {"path": "/project/main.py", "event": "modified"},
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    outbox.enqueue_payload(payload)

    pending = outbox.oldest()
    assert pending is not None
    assert pending.payload_json == payload
    assert "workspace" not in json.loads(pending.payload_json)["details"]

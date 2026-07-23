import json
import subprocess
from pathlib import Path

from daemon_v2.git_context import read_git_context
from daemon_v2.producer_outbox import ProducerOutbox, build_terminal_payload


def git(*args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def create_repository(path: Path, *, branch: str = "main") -> Path:
    path.mkdir()
    git("init", "-b", branch, cwd=path)
    git("config", "user.email", "pulse-tests@example.invalid", cwd=path)
    git("config", "user.name", "Pulse Tests", cwd=path)
    (path / "tracked.txt").write_text("initial\n")
    git("add", "tracked.txt", cwd=path)
    git("commit", "-m", "initial", cwd=path)
    return path


def test_reads_normal_repository_branch_head_and_clean_status(tmp_path):
    repository = create_repository(tmp_path / "normal-repo")

    context = read_git_context(repository)

    assert context is not None
    assert context.repository == "normal-repo"
    assert context.git_root == str(repository)
    assert context.branch == "main"
    assert context.head == git("rev-parse", "--short", "HEAD", cwd=repository)
    assert context.dirty is False
    assert (context.staged, context.unstaged, context.untracked) == (0, 0, 0)
    assert (repository / ".git").is_dir()


def test_reads_repository_from_subdirectory(tmp_path):
    repository = create_repository(tmp_path / "parent-repo")
    nested = repository / "src" / "nested"
    nested.mkdir(parents=True)

    context = read_git_context(nested)

    assert context is not None
    assert context.git_root == str(repository)
    assert context.repository == "parent-repo"


def test_absent_repository_returns_none(tmp_path):
    assert read_git_context(tmp_path) is None


def test_reads_worktree_and_dot_git_file(tmp_path):
    repository = create_repository(tmp_path / "main-repo")
    worktree = tmp_path / "linked-worktree"
    git("worktree", "add", "-b", "worktree-branch", str(worktree), cwd=repository)

    context = read_git_context(worktree)

    assert (worktree / ".git").is_file()
    assert context is not None
    assert context.git_root == str(worktree)
    assert context.repository == "linked-worktree"
    assert context.branch == "worktree-branch"
    assert context.head == git("rev-parse", "--short", "HEAD", cwd=worktree)


def test_git_command_error_is_suppressed(tmp_path, monkeypatch):
    repository = create_repository(tmp_path / "broken-repo")

    monkeypatch.setattr(
        "daemon_v2.git_context.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=128,
            stdout="",
            stderr="failure",
        ),
    )

    assert read_git_context(repository) is None


def test_detects_dirty_and_staged_unstaged_untracked_counts(tmp_path):
    repository = create_repository(tmp_path / "dirty-repo")
    (repository / "staged.txt").write_text("staged\n")
    git("add", "staged.txt", cwd=repository)
    (repository / "tracked.txt").write_text("changed\n")
    (repository / "untracked.txt").write_text("new\n")

    context = read_git_context(repository)

    assert context is not None
    assert context.dirty is True
    assert context.staged == 1
    assert context.unstaged == 1
    assert context.untracked == 1


def test_terminal_payload_is_enriched_before_outbox_persistence(tmp_path):
    repository = create_repository(tmp_path / "outbox-repo")
    database = tmp_path / "outbox.sqlite3"
    outbox = ProducerOutbox(database)

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
    payload = json.loads(pending.payload_json)
    assert payload["details"]["git"] == {
        "repository": "outbox-repo",
        "git_root": str(repository),
        "branch": "main",
        "head": git("rev-parse", "--short", "HEAD", cwd=repository),
        "dirty": False,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
    }


def test_terminal_payload_outside_git_has_no_git_details(tmp_path):
    outbox = ProducerOutbox(tmp_path / "outbox.sqlite3")

    payload_json = build_terminal_payload(
        outbox,
        command="pwd",
        cwd=str(tmp_path),
        exit_code=0,
        started_at="2026-07-23T14:31:00+02:00",
        finished_at="2026-07-23T14:32:00+02:00",
    )

    assert payload_json is not None
    assert "git" not in json.loads(payload_json)["details"]

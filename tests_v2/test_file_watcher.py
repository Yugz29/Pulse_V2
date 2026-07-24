from daemon_v2.file_watcher import compare_snapshots, should_ignore, take_snapshot


def test_snapshot_ignores_technical_paths(tmp_path):
    workspace = tmp_path
    tracked = workspace / "daemon_v2" / "main.py"
    tracked.parent.mkdir()
    tracked.write_text("tracked")

    ignored_paths = [
        workspace / ".git" / "index",
        workspace / ".venv" / "state",
        workspace / ".build" / "debug.yaml",
        workspace / ".swiftpm" / "configuration",
        workspace / "__pycache__" / "main.pyc",
        workspace / ".pytest_cache" / "state",
        workspace / "node_modules" / "package" / "index.js",
        workspace / "dist" / "bundle.js",
        workspace / "build" / "generated.o",
        workspace / "trace.db",
        workspace / ".DS_Store",
    ]
    for path in ignored_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ignored")

    snapshot = take_snapshot(workspace)

    assert set(snapshot) == {tracked}
    assert all(should_ignore(path, workspace) for path in ignored_paths)


def test_macos_swift_build_artifacts_never_enter_snapshot(tmp_path):
    workspace = tmp_path
    artifact = (
        workspace
        / "macos_observer"
        / ".build"
        / "arm64-apple-macosx"
        / "debug"
        / "observer.o"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("artifact")

    assert should_ignore(artifact, workspace)
    assert artifact not in take_snapshot(workspace)


def test_compare_snapshots_reports_created_modified_and_deleted(tmp_path):
    created = tmp_path / "created.py"
    modified = tmp_path / "modified.py"
    deleted = tmp_path / "deleted.py"
    previous = {
        modified: (1, 10),
        deleted: (1, 10),
    }
    current = {
        created: (2, 20),
        modified: (2, 10),
    }

    assert compare_snapshots(previous, current) == [
        ("created", created),
        ("modified", modified),
        ("deleted", deleted),
    ]

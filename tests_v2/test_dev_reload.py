import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "dev_reload.py"
SPEC = importlib.util.spec_from_file_location("pulse_dev_reload", SCRIPT)
dev_reload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_reload)


def test_snapshot_tracks_sources_and_ignores_generated_files(tmp_path):
    tracked = [
        tmp_path / "daemon_v2" / "main.py",
        tmp_path / "tests_v2" / "test_main.py",
        tmp_path / "scripts" / "dev.sh",
        tmp_path / "scripts" / "helper.py",
        tmp_path / "Makefile",
        tmp_path / "README.md",
    ]
    ignored = [
        tmp_path / ".venv" / "lib.py",
        tmp_path / "daemon_v2" / "__pycache__" / "main.pyc",
        tmp_path / "daemon_v2" / "trace.db",
        tmp_path / ".pytest_cache" / "state.py",
    ]
    for path in tracked + ignored:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("initial")

    first = dev_reload.snapshot(tmp_path)
    assert set(first) == set(tracked)

    ignored[0].write_text("changed")
    assert dev_reload.snapshot(tmp_path) == first

    tracked[0].write_text("changed source")
    assert dev_reload.snapshot(tmp_path) != first

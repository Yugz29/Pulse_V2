from pathlib import Path

from daemon_v2.main import select_database_path


def test_default_database_path_is_isolated_from_pulse_v1(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("PULSE_V2_DB_PATH", raising=False)
    monkeypatch.setenv("PULSE_DB_PATH", str(tmp_path / ".pulse" / "session.db"))

    assert select_database_path() == tmp_path / ".pulse_v2" / "trace.db"


def test_database_path_can_be_overridden_for_v2(monkeypatch, tmp_path):
    configured_path = tmp_path / "custom" / "trace.db"
    monkeypatch.setenv("PULSE_V2_DB_PATH", str(configured_path))

    assert select_database_path() == configured_path

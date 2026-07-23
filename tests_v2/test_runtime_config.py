import pytest

from daemon_v2 import dev_environment
from daemon_v2.main import create_app
from daemon_v2.outbox_worker import _build_parser as build_worker_parser
from daemon_v2.runtime_config import (
    DEFAULT_CORE_PORT,
    activities_url,
    core_base_url,
    core_port,
    parse_port,
    status_url,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_CORE_PORT),
        ("", DEFAULT_CORE_PORT),
        ("8765", 8765),
        (9123, 9123),
    ],
)
def test_parse_port(value, expected):
    assert parse_port(value) == expected


@pytest.mark.parametrize("value", ["invalid", "0", "65536", -1, True])
def test_parse_port_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="between 1 and 65535"):
        parse_port(value)


def test_runtime_urls_are_built_from_one_port(monkeypatch):
    monkeypatch.setenv("PULSE_CORE_HOST", "127.0.0.1")
    monkeypatch.setenv("PULSE_CORE_PORT", "9123")

    assert core_port() == 9123
    assert core_base_url() == "http://127.0.0.1:9123"
    assert activities_url() == "http://127.0.0.1:9123/activities"
    assert status_url() == "http://127.0.0.1:9123/status"


def test_core_and_worker_use_the_same_configured_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_CORE_HOST", "127.0.0.1")
    monkeypatch.setenv("PULSE_CORE_PORT", "9876")

    app = create_app(tmp_path / "trace.db")
    worker_args = build_worker_parser().parse_args([])

    assert app.config["CORE_BASE_URL"] == "http://127.0.0.1:9876"
    assert worker_args.url == f"{app.config['CORE_BASE_URL']}/activities"


class FakeResponse:
    def __init__(self, payload, *, status=200):
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


def test_healthcheck_requires_a_matching_pulse_status(monkeypatch):
    expected = b'{"daemon":"running","url":"http://127.0.0.1:8765/"}'
    monkeypatch.setattr(
        dev_environment,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(expected),
    )

    assert dev_environment.pulse_core_is_ready(
        "http://127.0.0.1:8765/status"
    )

    wrong_service = b'{"daemon":"running","url":"http://other-service/"}'
    monkeypatch.setattr(
        dev_environment,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(wrong_service),
    )
    assert not dev_environment.pulse_core_is_ready(
        "http://127.0.0.1:8765/status"
    )

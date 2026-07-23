"""Shared local endpoint configuration for Pulse Core development."""

from __future__ import annotations

import os


DEFAULT_CORE_HOST = "127.0.0.1"
DEFAULT_CORE_PORT = 8765


def parse_port(value: str | int | None) -> int:
    if value is None or value == "":
        return DEFAULT_CORE_PORT
    if isinstance(value, bool):
        raise ValueError("PULSE_CORE_PORT must be an integer between 1 and 65535")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "PULSE_CORE_PORT must be an integer between 1 and 65535"
        ) from exc
    if not 1 <= port <= 65535:
        raise ValueError("PULSE_CORE_PORT must be an integer between 1 and 65535")
    return port


def core_host() -> str:
    value = os.environ.get("PULSE_CORE_HOST", DEFAULT_CORE_HOST).strip()
    if not value:
        raise ValueError("PULSE_CORE_HOST must not be empty")
    return value


def core_port() -> int:
    return parse_port(os.environ.get("PULSE_CORE_PORT"))


def core_base_url(*, host: str | None = None, port: int | None = None) -> str:
    selected_host = host if host is not None else core_host()
    selected_port = parse_port(port if port is not None else core_port())
    return f"http://{selected_host}:{selected_port}"


def activities_url(*, host: str | None = None, port: int | None = None) -> str:
    return f"{core_base_url(host=host, port=port)}/activities"


def status_url(*, host: str | None = None, port: int | None = None) -> str:
    return f"{core_base_url(host=host, port=port)}/status"

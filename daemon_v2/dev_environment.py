"""Small testable preflight and healthcheck helpers for scripts/dev.sh."""

from __future__ import annotations

import argparse
import json
import socket
import time
from urllib.error import URLError
from urllib.request import urlopen

from .runtime_config import core_base_url, parse_port, status_url


def port_is_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((host, port))
    except OSError:
        return False
    return True


def pulse_core_is_ready(url: str, *, timeout: float = 0.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read())
    except (OSError, URLError, json.JSONDecodeError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("daemon") == "running"
        and payload.get("url") == url.removesuffix("status")
    )


def wait_for_pulse_core(url: str, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pulse_core_is_ready(url):
            return True
        time.sleep(0.1)
    return pulse_core_is_ready(url)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pulse development checks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_port = subparsers.add_parser("check-port")
    check_port.add_argument("--host", required=True)
    check_port.add_argument("--port", required=True, type=parse_port)

    wait_ready = subparsers.add_parser("wait-ready")
    wait_ready.add_argument("--host", required=True)
    wait_ready.add_argument("--port", required=True, type=parse_port)
    wait_ready.add_argument("--timeout", type=float, default=10.0)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "check-port":
        if not port_is_available(args.host, args.port):
            raise SystemExit(
                f"[dev] Port unavailable: {args.host}:{args.port}"
            )
        return

    if args.timeout <= 0:
        raise SystemExit("[dev] Healthcheck timeout must be greater than zero")
    url = status_url(host=args.host, port=args.port)
    if not wait_for_pulse_core(url, timeout=args.timeout):
        raise SystemExit(
            f"[dev] Pulse Core did not become ready at "
            f"{core_base_url(host=args.host, port=args.port)}"
        )


if __name__ == "__main__":
    main()

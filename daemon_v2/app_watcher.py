"""Poll the frontmost macOS application and report activation changes."""

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


ACTIVITIES_URL = "http://127.0.0.1:5000/activities"
LSAPPINFO = "/usr/bin/lsappinfo"
_APP_NAME = re.compile(
    r'(?:LSDisplayName|displayname|name)"?\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)


def frontmost_application(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    try:
        front = runner(
            [LSAPPINFO, "front"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    application_specifier = front.stdout.strip()
    if front.returncode != 0 or not application_specifier or "NULL" in application_specifier:
        return None

    try:
        info = runner(
            [LSAPPINFO, "info", "-only", "name", application_specifier],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if info.returncode != 0:
        return None
    match = _APP_NAME.search(info.stdout)
    return match.group(1).strip() if match else None


def post_app_activated(app: str) -> bool:
    payload = json.dumps({"type": "app_activated", "app": app}).encode()
    request = Request(
        ACTIVITIES_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=0.5):
            return True
    except (OSError, URLError):
        return False


def watch(interval: float = 1.0) -> None:
    previous_app = frontmost_application()
    print("Watching frontmost macOS application", flush=True)
    try:
        while True:
            time.sleep(interval)
            current_app = frontmost_application()
            if current_app and current_app != previous_app:
                post_app_activated(current_app)
            previous_app = current_app or previous_app
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch the frontmost macOS application")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("app watcher is supported only on macOS")
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    watch(args.interval)


if __name__ == "__main__":
    main()

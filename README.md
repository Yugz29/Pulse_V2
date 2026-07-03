# Pulse V2

Pulse V0 observes local activity, stores an append-only trace, groups nearby
events into sessions, and reconstructs a readable view of the current day.

The first vertical slice supports `file_changed` and `terminal_finished`
activities through a local Flask API bound to `127.0.0.1`.

## Setup

```bash
cd /Users/yugz/Projets/Pulse_V2
python3 -m venv .venv
.venv/bin/pip install Flask pytest
```

## Tests

```bash
.venv/bin/python -m pytest tests_v2
```

## Run

```bash
.venv/bin/python -m daemon_v2.main
```

The V2 SQLite database is created at `~/.pulse_v2/trace.db`. It is not migrated
from or shared with Pulse V1 databases under `~/.pulse`. Override the V2 path
with `PULSE_V2_DB_PATH=/path/to/trace.db`.

## Send an activity

```bash
curl -X POST http://127.0.0.1:5000/activities \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "terminal_finished",
    "occurred_at": "2026-07-03T19:30:00+02:00",
    "command": "pytest tests_v2",
    "exit_code": 0,
    "cwd": "/Users/yugz/Projets/Pulse"
  }'
```

## Read today's trace

```bash
curl http://127.0.0.1:5000/trace/today
```

## Terminal watcher

Source the watcher manually from an interactive Zsh session:

```bash
source /Users/yugz/Projets/Pulse_V2/scripts/pulse_terminal_watcher.zsh
```

To load it in future Zsh sessions, add this line yourself to `~/.zshrc`:

```zsh
source /Users/yugz/Projets/Pulse_V2/scripts/pulse_terminal_watcher.zsh
```

The watcher records the command, working directory, start and finish times, and
exit code. Delivery runs in the background and fails silently when the daemon
is unavailable.

## V0 limits

- Input is accepted through the local HTTP API and the optional Zsh watcher.
- Sessions use a fixed 30-minute inactivity gap.
- Commands receive basic secret redaction, not shell-aware parsing.
- SQLite is local and single-node; there is no retention or migration system yet.
- The daemon has no authentication because it only binds to `127.0.0.1`.

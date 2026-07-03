# Pulse

Pulse V0 observes local activity, stores an append-only trace, groups nearby
events into sessions, and reconstructs a readable view of the current day.

The first vertical slice supports `file_changed` and `terminal_finished`
activities through a local Flask API bound to `127.0.0.1`.

## Setup

```bash
cd /Users/yugz/Projets/Pulse
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

The SQLite database is created at `data/pulse.sqlite3`. Override it with
`PULSE_DB_PATH=/path/to/pulse.sqlite3`.

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

## V0 limits

- Input is accepted only through the local HTTP API; no OS observer is included.
- Sessions use a fixed 30-minute inactivity gap.
- Commands receive basic secret redaction, not shell-aware parsing.
- SQLite is local and single-node; there is no retention or migration system yet.
- The daemon has no authentication because it only binds to `127.0.0.1`.

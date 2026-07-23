#!/usr/bin/env bash

set -u

workspace="$(pwd -P)"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/.." && pwd -P)"
python="$repo_root/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  echo "Pulse V2: missing Python environment at $python" >&2
  exit 1
fi

daemon_pid=""
file_watcher_pid=""
app_watcher_pid=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  [[ -n "$app_watcher_pid" ]] && kill "$app_watcher_pid" 2>/dev/null || true
  [[ -n "$file_watcher_pid" ]] && kill "$file_watcher_pid" 2>/dev/null || true
  [[ -n "$daemon_pid" ]] && kill "$daemon_pid" 2>/dev/null || true
  [[ -n "$app_watcher_pid" ]] && wait "$app_watcher_pid" 2>/dev/null || true
  [[ -n "$file_watcher_pid" ]] && wait "$file_watcher_pid" 2>/dev/null || true
  [[ -n "$daemon_pid" ]] && wait "$daemon_pid" 2>/dev/null || true
  exit "$status"
}

trap cleanup EXIT INT TERM

cd "$repo_root"
export PULSE_CORE_EVENT_LOG="${PULSE_CORE_EVENT_LOG:-1}"
"$python" -m daemon_v2.main &
daemon_pid=$!
"$python" -m daemon_v2.file_watcher "$workspace" &
file_watcher_pid=$!
"$python" -m daemon_v2.app_watcher &
app_watcher_pid=$!

echo "Pulse V2: http://127.0.0.1:5000/"
echo "Watching: $workspace"
echo "Press Ctrl+C to stop."

while kill -0 "$daemon_pid" 2>/dev/null \
  && kill -0 "$file_watcher_pid" 2>/dev/null \
  && kill -0 "$app_watcher_pid" 2>/dev/null; do
  sleep 1
done

echo "Pulse V2: a process stopped unexpectedly." >&2
exit 1

#!/usr/bin/env bash

set -uo pipefail

workspace="$(pwd -P)"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/.." && pwd -P)"
python="$repo_root/.venv/bin/python"
swift_package="$repo_root/macos_observer/Package.swift"

export PULSE_CORE_HOST="${PULSE_CORE_HOST:-127.0.0.1}"
export PULSE_CORE_PORT="${PULSE_CORE_PORT:-8765}"
export PULSE_CORE_EVENT_LOG="${PULSE_CORE_EVENT_LOG:-1}"
export PULSE_CORE_REPO_ROOT="$repo_root"

base_url="http://${PULSE_CORE_HOST}:${PULSE_CORE_PORT}"
activities_url="${base_url}/activities"

core_pid=""
worker_pid=""
file_watcher_pid=""
app_observer_pid=""
app_observer_executable=""

fail() {
  echo "[dev] $*" >&2
  exit 1
}

preflight() {
  [[ -x "$python" ]] ||
    fail "Missing Python environment: $python"
  command -v swift >/dev/null 2>&1 ||
    fail "Swift is required but was not found in PATH"
  [[ -f "$swift_package" ]] ||
    fail "Missing Swift package: $swift_package"
  [[ -f "$repo_root/scripts/pulse_terminal_watcher.zsh" ]] ||
    fail "Missing terminal watcher script"
  [[ -f "$repo_root/daemon_v2/outbox_worker.py" ]] ||
    fail "Missing outbox worker module"
  [[ -f "$repo_root/daemon_v2/file_watcher.py" ]] ||
    fail "Missing file watcher module"
  echo "[dev] Building macOS application observer"
  swift build \
    --package-path "$repo_root/macos_observer" \
    --product PulseApplicationObserver ||
    fail "Unable to build macOS application observer"
  local swift_bin_path
  swift_bin_path="$(swift build \
    --package-path "$repo_root/macos_observer" \
    --show-bin-path)" ||
    fail "Unable to locate macOS application observer"
  app_observer_executable="$swift_bin_path/PulseApplicationObserver"
  [[ -x "$app_observer_executable" ]] ||
    fail "Missing application observer executable: $app_observer_executable"
  "$python" -m daemon_v2.dev_environment check-port \
    --host "$PULSE_CORE_HOST" \
    --port "$PULSE_CORE_PORT" ||
    fail "Choose another port with PULSE_CORE_PORT=<port>"
}

stop_process() {
  local label="$1"
  local pid="$2"
  [[ -n "$pid" ]] || return
  echo "[dev] Stopping $label"
  kill "$pid" 2>/dev/null || true
  local attempt=0
  while kill -0 "$pid" 2>/dev/null && (( attempt < 30 )); do
    sleep 0.1
    ((attempt += 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "[dev] Force stopping $label" >&2
    kill -KILL "$pid" 2>/dev/null || true
  fi
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  stop_process "macOS application observer" "$app_observer_pid"
  stop_process "file watcher" "$file_watcher_pid"
  stop_process "outbox worker" "$worker_pid"
  stop_process "Pulse Core" "$core_pid"
  exit "$status"
}

component_is_running() {
  local label="$1"
  local pid="$2"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[dev] $label stopped unexpectedly" >&2
    return 1
  fi
}

trap cleanup EXIT
trap 'exit 0' INT TERM

cd "$repo_root" || fail "Cannot enter repository: $repo_root"
preflight

echo "[dev] Starting Pulse Core"
"$python" -m daemon_v2.main &
core_pid=$!

if ! "$python" -m daemon_v2.dev_environment wait-ready \
  --host "$PULSE_CORE_HOST" \
  --port "$PULSE_CORE_PORT" \
  --timeout 10; then
  fail "Pulse Core healthcheck failed"
fi
component_is_running "Pulse Core" "$core_pid" ||
  fail "Pulse Core failed during startup"
echo "[dev] Pulse Core ready on $base_url"

echo "[dev] Starting outbox worker"
"$python" -m daemon_v2.outbox_worker --url "$activities_url" &
worker_pid=$!
echo "[dev] Outbox worker started"

echo "[dev] Starting file watcher"
"$python" -m daemon_v2.file_watcher "$workspace" &
file_watcher_pid=$!
echo "[dev] File watcher started for $workspace"

echo "[dev] Starting macOS application observer"
"$app_observer_executable" &
app_observer_pid=$!
echo "[dev] macOS application observer started"

echo "[dev] Press Ctrl+C to stop all components"

while true; do
  component_is_running "Pulse Core" "$core_pid" || exit 1
  component_is_running "outbox worker" "$worker_pid" || exit 1
  component_is_running "file watcher" "$file_watcher_pid" || exit 1
  component_is_running "macOS application observer" "$app_observer_pid" || exit 1
  sleep 1
done

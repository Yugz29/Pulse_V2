# Durable Zsh hooks for recording completed commands in the Pulse outbox.

autoload -Uz add-zsh-hook

typeset -g _PULSE_TERMINAL_WATCHER_FILE="${${(%):-%N}:A}"
typeset -g _PULSE_TERMINAL_REPO_ROOT="${_PULSE_TERMINAL_WATCHER_FILE:h:h}"
typeset -g _PULSE_TERMINAL_PYTHON="$_PULSE_TERMINAL_REPO_ROOT/.venv/bin/python"
if [[ ! -x "$_PULSE_TERMINAL_PYTHON" ]]; then
  _PULSE_TERMINAL_PYTHON="$(command -v python3)"
fi

_pulse_terminal_iso_now() {
  local timestamp
  timestamp="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  print -r -- "${timestamp[1,-3]}:${timestamp[-2,-1]}"
}

_pulse_terminal_json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  print -r -- "$value"
}

_pulse_terminal_preexec() {
  _PULSE_TERMINAL_COMMAND="$1"
  _PULSE_TERMINAL_CWD="$PWD"
  _PULSE_TERMINAL_STARTED_AT="$(_pulse_terminal_iso_now)"
  _PULSE_TERMINAL_ACTIVE=1
}

_pulse_terminal_precmd() {
  local exit_code=$?
  [[ -n "${_PULSE_TERMINAL_ACTIVE:-}" ]] || return

  local finished_at command cwd payload
  finished_at="$(_pulse_terminal_iso_now)"
  command="$(_pulse_terminal_json_escape "$_PULSE_TERMINAL_COMMAND")"
  cwd="$(_pulse_terminal_json_escape "$_PULSE_TERMINAL_CWD")"
  payload="{\"type\":\"terminal_finished\",\"occurred_at\":\"$finished_at\",\"started_at\":\"$_PULSE_TERMINAL_STARTED_AT\",\"finished_at\":\"$finished_at\",\"command\":\"$command\",\"exit_code\":$exit_code,\"cwd\":\"$cwd\"}"

  unset _PULSE_TERMINAL_ACTIVE
  print -rn -- "$payload" |
    PYTHONPATH="$_PULSE_TERMINAL_REPO_ROOT" "$_PULSE_TERMINAL_PYTHON" \
      -m daemon_v2.producer_outbox enqueue-terminal >/dev/null 2>&1
  _pulse_terminal_start_worker
}

_pulse_terminal_start_worker() {
  (
    cd "$_PULSE_TERMINAL_REPO_ROOT" || return
    PYTHONPATH="$_PULSE_TERMINAL_REPO_ROOT" nohup "$_PULSE_TERMINAL_PYTHON" \
      -m daemon_v2.outbox_worker >/dev/null 2>&1
  ) &!
}

# Removing first makes sourcing this file repeatedly idempotent.
add-zsh-hook -d preexec _pulse_terminal_preexec 2>/dev/null
add-zsh-hook -d precmd _pulse_terminal_precmd 2>/dev/null
add-zsh-hook preexec _pulse_terminal_preexec
add-zsh-hook precmd _pulse_terminal_precmd

_pulse_terminal_start_worker

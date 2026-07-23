#!/usr/bin/env bash

set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "$script_dir/.." && pwd -P)"
python="$repo_root/.venv/bin/python"
configured_path="${PULSE_V2_DB_PATH:-$HOME/.pulse_v2/trace.db}"

if [[ ! -x "$python" ]]; then
  python="$(command -v python3 || true)"
fi
if [[ -z "$python" ]]; then
  echo "Pulse V2 reset: Python 3 introuvable pour valider le chemin." >&2
  exit 1
fi

cd "$repo_root"
status_url="$("$python" -c \
  'from daemon_v2.runtime_config import status_url; print(status_url())')"
db_path="$("$python" -c '
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
' "$configured_path")"

case "$db_path" in
  "$HOME/.pulse"|"$HOME/.pulse/"*)
    echo "Pulse V2 reset refusé: le chemin Pulse V1 ne doit jamais être modifié." >&2
    echo "Chemin refusé: $db_path" >&2
    exit 1
    ;;
esac

echo "Base Pulse V2 ciblée: $db_path"

if curl --silent --fail --max-time 1 "$status_url" >/dev/null 2>&1; then
  echo "Pulse V2 reset refusé: le daemon répond sur ${status_url%/status}/." >&2
  echo "Arrêtez Pulse avant de supprimer la base." >&2
  exit 1
fi

printf "Supprimer cette base de développement ? [y/N] "
if ! read -r answer; then
  echo
  echo "Reset annulé."
  exit 1
fi

case "$answer" in
  y|Y|yes|YES|oui|OUI)
    ;;
  *)
    echo "Reset annulé."
    exit 0
    ;;
esac

rm -f -- "$db_path"
mkdir -p -- "$(dirname "$db_path")"
echo "Trace de développement réinitialisée: $db_path"

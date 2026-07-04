# Pulse V2

Pulse V2 observe l’activité locale de développement, conserve une trace locale en append-only, regroupe les événements en sessions et reconstruit une vue lisible de la journée en cours.

La version actuelle prend en charge trois signaux d’activité :

- `terminal_finished` depuis le watcher Zsh du terminal ;
- `file_changed` depuis le watcher de fichiers du workspace ;
- `app_activated` depuis le watcher macOS de l’application active.

Pulse V2 expose une page HTML locale, une trace JSON et une trace Markdown via une API Flask liée à `127.0.0.1`.

## Installation

```bash
cd /Users/yugz/Projets/Pulse_V2
python3 -m venv .venv
.venv/bin/pip install Flask pytest
```

## Tests

```bash
.venv/bin/python -m pytest tests_v2
```

## Lancement

Lancer ensemble le daemon, le watcher de fichiers et le watcher d’application macOS :

```bash
./scripts/dev.sh
```

Le watcher de fichiers observe le dossier depuis lequel `scripts/dev.sh` est lancé. La page locale est disponible sur `http://127.0.0.1:5000/`. Appuyer sur `Ctrl-C` pour arrêter tous les processus.

## Commandes utiles

```bash
make dev
make dev-reload
make test
make status
make reset
make help
```

`make dev-reload` est réservé au développement. Il surveille les sources du
dépôt par polling et redémarre Pulse après un court debounce, sans utiliser les
événements `file_changed` ni écrire directement dans SQLite.

- `make dev` : lance Pulse localement ;
- `make test` : lance les tests ;
- `make status` : affiche l’état local ;
- `make reset` : réinitialise la trace de développement ;
- `make help` : affiche les commandes disponibles.

Pour lancer uniquement le daemon :

```bash
.venv/bin/python -m daemon_v2.main
```

La base SQLite V2 est créée dans `~/.pulse_v2/trace.db`. Elle n’est ni migrée depuis Pulse V1, ni partagée avec les anciennes bases situées dans `~/.pulse`. Le chemin peut être surchargé avec `PULSE_V2_DB_PATH=/chemin/vers/trace.db`.

Ouvrir la page locale de l’activité du jour :

```text
http://127.0.0.1:5000/
```

La page locale affiche les blocs `Maintenant`, `Aujourd’hui`, `État système` et la timeline détaillée. Elle utilise un thème sombre, regroupe les changements de fichiers par vague de modification et résume les applications actives par session.

Vérifier l’état local sans démarrer de processus :

```bash
./scripts/status.sh
```

Le même état est disponible en JSON sur `http://127.0.0.1:5000/status`.

Réinitialiser explicitement la trace de développement, après avoir arrêté
Pulse :

```bash
./scripts/reset-dev.sh
```

Le script cible `~/.pulse_v2/trace.db`, respecte `PULSE_V2_DB_PATH`, demande
confirmation et refuse tout chemin situé sous `~/.pulse`.

## Envoyer une activité

```bash
curl -X POST http://127.0.0.1:5000/activities \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "terminal_finished",
    "occurred_at": "2026-07-03T19:30:00+02:00",
    "command": "pytest tests_v2",
    "exit_code": 0,
    "cwd": "/Users/yugz/Projets/Pulse_V2"
  }'
```

Exemple d’activité fichier :

```bash
curl -X POST http://127.0.0.1:5000/activities \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "file_changed",
    "path": "/Users/yugz/Projets/Pulse_V2/daemon_v2/daily_trace.py",
    "event": "modified",
    "workspace": "/Users/yugz/Projets/Pulse_V2"
  }'
```

## Lire la trace du jour

```bash
curl http://127.0.0.1:5000/trace/today
```

Pour une trace Markdown lisible, regroupée par session :

```bash
curl http://127.0.0.1:5000/trace/today.md
```

## Watcher terminal

Sourcer manuellement le watcher depuis une session Zsh interactive :

```bash
source /Users/yugz/Projets/Pulse_V2/scripts/pulse_terminal_watcher.zsh
```

Pour le charger dans les futures sessions Zsh, ajouter soi-même cette ligne dans `~/.zshrc` :

```zsh
source /Users/yugz/Projets/Pulse_V2/scripts/pulse_terminal_watcher.zsh
```

Le watcher enregistre la commande, le dossier courant, les heures de début et de fin, ainsi que le code de sortie. L’envoi se fait en arrière-plan et échoue silencieusement si le daemon n’est pas disponible. Ce watcher n’est pas lancé par `scripts/dev.sh` ; il doit être sourcé depuis Zsh.

## Watcher de fichiers

Lancer manuellement le watcher par polling avec un workspace explicite :

```bash
.venv/bin/python -m daemon_v2.file_watcher /Users/yugz/Projets/Pulse_V2
```

Il envoie les fichiers créés, modifiés et supprimés au daemon local Pulse. Les chemins techniques comme `.git`, `.venv`, les caches, `*.pyc`, `*.db` et `.DS_Store` sont ignorés. Le watcher continue de tourner silencieusement si le daemon est indisponible. L’arrêter avec `Ctrl-C`.

## Watcher d’application

Sur macOS, lancer manuellement le watcher de l’application active avec :

```bash
.venv/bin/python -m daemon_v2.app_watcher
```

Il utilise la commande locale macOS `lsappinfo`, enregistre uniquement les changements d’application et ne demande ni titre de fenêtre ni accès Accessibility. `scripts/dev.sh` lance ce watcher avec le daemon et le watcher de fichiers.

## Limites actuelles

- Les entrées sont acceptées via l’API HTTP locale et les watchers optionnels terminal, fichiers et application.
- Les sessions utilisent une coupure fixe après 30 minutes d’inactivité.
- Les commandes reçoivent une redaction basique des secrets, sans parsing shell avancé.
- SQLite est local et mono-machine ; il n’y a pas encore de système de rétention ou de migration.
- Le daemon n’a pas d’authentification, car il écoute uniquement sur `127.0.0.1`.

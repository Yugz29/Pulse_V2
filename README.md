# Pulse Core

Pulse V2 observe l’activité locale de développement, conserve une trace locale en append-only, regroupe les événements en sessions et reconstruit une vue lisible de la journée en cours.

## Contrat d’événement et compatibilité temporaire

`POST /activities` accepte un contrat canonique versionné contenant
`event_id`, `schema_version`, `type`, `producer`, `occurred_at` et `details`.
`occurred_at` vient du producteur et conserve son fuseau ; `recorded_at` est
créé en UTC par Core lors de la première insertion durable.

Les producteurs historiques qui envoient encore un payload plat passent par
un adaptateur explicite `pulse-legacy`. Core leur attribue un nouvel
`event_id` à chaque requête. Ce chemin garantit leur compatibilité, mais **ne
fournit aucune idempotence entre deux requêtes legacy identiques**. Il est
destiné à être supprimé après migration des producteurs.

La version actuelle prend en charge trois signaux d’activité :

- `terminal_finished` depuis le watcher Zsh du terminal ;
- `file_changed` depuis le watcher de fichiers du workspace ;
- `app_activated` depuis le watcher macOS de l’application active.

Pulse complète ces signaux avec une lecture Git passive au rendu pour enrichir
la reprise du projet courant, sans écrire ces informations dans SQLite.

Pulse V2 expose une page HTML locale, une trace JSON et une trace Markdown via une API Flask liée à `127.0.0.1`.

## État actuel

Le projet fonctionne comme un prototype produit local :

- le daemon Python reçoit et normalise les activités ;
- SQLite conserve les événements en append-only dans
  `~/.pulse_v2/trace.db` ;
- les événements sont regroupés en sessions de travail ;
- une vue HTML vivante, des archives HTML et des représentations JSON et
  Markdown sont produites depuis la même trace quotidienne ;
- les watchers terminal, fichiers et application alimentent le daemon en
  best-effort.


L’interface HTML est conservée volontairement comme interface produit vivante.
Elle sert à stabiliser les blocs, les résumés et la navigation avant
d’envisager une interface macOS native.

## Palier 1 — Journal passif et reprise factuelle

Le premier palier de Pulse V2 est stabilisé : le projet fournit un journal local
passif capable de reconstruire une journée de travail à partir de signaux
observés localement.

Ce palier couvre :

- l’observation des commandes terminal, fichiers modifiés et applications
  actives ;
- le stockage local append-only dans SQLite ;
- la reconstruction de la journée en cours avec `Maintenant`, `Reprise`,
  `Aujourd’hui` et la timeline brute ;
- les archives multi-jours via `/days` et `/day/YYYY-MM-DD` ;
- les résumés compacts par projet dans l’index des journées ;
- la distinction entre timeline brute et signaux utiles.

Pulse conserve les événements observés dans la timeline, mais filtre certains
bruits dans les résumés : commandes d’inspection de Pulse, prompts collés
accidentellement dans le terminal et workspaces génériques comme le dossier
personnel utilisateur.

À ce stade, Pulse reste factuel. Il ne produit pas encore de synthèse
intelligente et ne cherche pas à deviner l’intention du travail. Le bloc
`Reprise` expose uniquement des signaux observés ou déduits prudemment à partir
de l’activité locale, comme le dernier test local observé, les derniers fichiers
observés et un contexte Git local lu passivement au rendu.

- Les prochaines limites connues de ce palier sont :
  - les commandes Git restent observées via le terminal, mais le contexte Git
    affiché dans `Reprise` vient de l’état réel du dépôt lu passivement ;
  - les commits faits via VS Code ou un autre client Git sont visibles dans le
    contexte Git local, mais ne créent pas encore d’événements Git dédiés ;
- le projet courant repose encore sur des heuristiques de workspace ;
- Pulse ne produit pas encore de synthèse assistée par IA ;
- l’interface HTML reste un prototype produit vivant, pas l’interface macOS
  finale.

## Installation

```bash
cd /Users/yugz/Projets/Pulse/Pulse_Core
python3 -m venv .venv
.venv/bin/pip install Flask pytest
```

## Tests

```bash
.venv/bin/python -m pytest tests_v2
```

## Développement local

Le port local dédié est `8765`. Il peut être remplacé pour toute la pile avec
une seule variable, par exemple `PULSE_CORE_PORT=9876 make dev`. Le superviseur
transmet toujours le même endpoint à Pulse Core, au worker et au watcher
fichiers.

Le hook terminal doit rester chargé dans chaque Zsh interactive, car `preexec`
et `precmd` ne peuvent pas fonctionner dans un processus de fond. Dans
`~/.zshrc`, supprimer l’ancienne ligne :

```zsh
source /Users/yugz/Projets/Pulse_V2/scripts/pulse_terminal_watcher.zsh
```

et la remplacer par :

```zsh
source /Users/yugz/Projets/Pulse/Pulse_Core/scripts/pulse_terminal_watcher.zsh
```

Le hook construit et écrit les événements terminal dans l’outbox. Il ne lance
plus son propre worker : `make dev` supervise l’unique worker. Après avoir
modifié `~/.zshrc`, ouvrir un nouveau terminal ou sourcer manuellement la
nouvelle ligne.

Depuis la racine du dépôt :

```bash
make dev
```

Cette commande effectue les préflight, démarre Pulse Core, attend que
`GET /status` identifie réellement Pulse, puis lance le worker outbox, le
watcher fichiers et l’observateur macOS Swift. Le watcher fichiers observe le
dossier depuis lequel `make dev` est lancé. `Ctrl-C` arrête les quatre
processus dans l’ordre inverse.

Commandes de diagnostic :

```bash
.venv/bin/python -m daemon_v2.producer_outbox status
.venv/bin/python -m daemon_v2.producer_outbox \
  inspect-dead-letter --limit 10
.venv/bin/python -m daemon_v2.producer_outbox \
  clear-dead-letter --http-status 403
```

La dernière commande supprime uniquement les dead-letters HTTP 403 ciblées.
Elle ne touche jamais aux événements pending. Aucune dead-letter n’est
supprimée ou rejouée automatiquement.

Test manuel :

1. lancer `make dev` et attendre les quatre messages `started` ;
2. changer d’application, exécuter une commande terminal et modifier un fichier ;
3. vérifier les lignes événementielles lisibles et le statut de l’outbox ;
4. faire `Ctrl-C` ;
5. vérifier avec `ps` qu’aucun processus Pulse Core, outbox worker, watcher
   fichiers ou `PulseApplicationObserver` ne subsiste.

L’observateur natif écoute
`NSWorkspace.didActivateApplicationNotification`, produit un état initial et
écrit dans l’outbox avant tout envoi. Il ne collecte ni titre de fenêtre,
document, URL ou contenu d’écran.

Le même processus écoute aussi les transitions publiques macOS de veille,
réveil et les notifications distribuées système de verrouillage. Les
identifiants `com.apple.screenIsLocked` et `com.apple.screenIsUnlocked` ne sont
pas exposés comme constantes AppKit fortement typées ; Pulse les observe via
`DistributedNotificationCenter`. Test manuel :

1. lancer `make dev-reload` ;
2. verrouiller la session avec `Ctrl-Cmd-Q`, puis la déverrouiller ;
3. mettre le Mac en veille, puis le réveiller ;
4. vérifier quatre lignes `screen_locked`, `screen_unlocked`, `system_sleep`
   et `system_wake`, chacune suivie d’un `POST /activities` en HTTP 201.

Pendant le diagnostic lock/unlock, la réception brute est aussi visible sur
stderr sous la forme `[macos-observer] received screen lock notification` ou
`received screen unlock notification`. Un doublon reçu est journalisé mais
n’est pas publié une seconde fois.

Ces événements ont un objet `details` vide. L’observateur ne fait aucun appel
HTTP : il remet leur JSON canonique à la même outbox durable que les événements
d’application.

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
- `make dev-reload` : lance Pulse et le redémarre lorsque les sources changent ;
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
http://127.0.0.1:8765/
```

La page locale affiche les blocs `Maintenant`, `Reprise`, `Aujourd’hui` et
`État système`, puis une timeline navigable. Elle regroupe les changements de
fichiers par vague de modification, résume les sessions, marque les changements
de projet et synthétise les applications actives. Le bloc `Reprise` complète la
trace enregistrée avec un contexte Git local lu passivement : état du dépôt,
branche et commits du jour. Cette lecture Git est best-effort, limitée par un
timeout court, et n’est pas écrite dans SQLite.

Vérifier l’état local sans démarrer de processus :

```bash
./scripts/status.sh
```

Le même état est disponible en JSON sur `http://127.0.0.1:8765/status`.

Réinitialiser explicitement la trace de développement, après avoir arrêté
Pulse :

```bash
./scripts/reset-dev.sh
```

Le script cible `~/.pulse_v2/trace.db`, respecte `PULSE_V2_DB_PATH`, demande
confirmation et refuse tout chemin situé sous `~/.pulse`.

## Envoyer une activité

```bash
curl -X POST http://127.0.0.1:8765/activities \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "terminal_finished",
    "occurred_at": "2026-07-03T19:30:00+02:00",
    "command": "pytest tests_v2",
    "exit_code": 0,
    "cwd": "/Users/yugz/Projets/Pulse/Pulse_Core"
  }'
```

Exemple d’activité fichier :

```bash
curl -X POST http://127.0.0.1:8765/activities \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "file_changed",
    "path": "/Users/yugz/Projets/Pulse/Pulse_Core/daemon_v2/daily_trace.py",
    "event": "modified",
    "workspace": "/Users/yugz/Projets/Pulse/Pulse_Core"
  }'
```

## Routes principales

| Route | Format | Rôle |
| --- | --- | --- |
| `/` | HTML | Vue vivante de la journée en cours |
| `/status` | JSON | État local du daemon et de la trace du jour |
| `/activities` | JSON | Ingestion d’une activité par `POST` |
| `/trace/today` | JSON | Trace structurée de la journée en cours |
| `/trace/today.md` | Markdown | Trace lisible de la journée en cours |
| `/days` | HTML | Liste des jours disponibles |
| `/trace/days` | JSON | Liste structurée des jours disponibles |
| `/day/YYYY-MM-DD` | HTML | Archive d’une journée |
| `/trace/YYYY-MM-DD` | JSON | Trace structurée d’une journée |
| `/trace/YYYY-MM-DD.md` | Markdown | Trace lisible d’une journée |

Lire la trace JSON du jour :

```bash
curl http://127.0.0.1:8765/trace/today
```

Pour une trace Markdown lisible, regroupée par session :

```bash
curl http://127.0.0.1:8765/trace/today.md
```

### Vue vivante et vue archive

La route `/` représente l’état courant. Elle affiche :

- `Maintenant` ;
- `Reprise` ;
- `Aujourd’hui` ;
- `État système` ;
- la timeline, ses résumés de session et ses séparateurs de projet ;
- un lien `Direct` en fin de navigation.

La route `/day/YYYY-MM-DD` représente une archive stable d’une journée. Elle
affiche `Journal du YYYY-MM-DD`, le résumé du jour et la timeline. Elle
n’affiche pas `Maintenant`, `Reprise` ni `État système`, et sa navigation se
termine par `Fin du jour`.

Les vues datées HTML et Markdown sont temporellement stables : elles n’affichent
pas `Maintenant` ni `Reprise`, et ne consultent pas l’état Git courant. La
qualification des projets distingue également le mode live du mode archive : la
vue live peut utiliser l’existence actuelle de `.git` comme preuve de projet,
tandis que les archives et `/days` se basent uniquement sur les signaux stockés
de la journée.

## Structure du code

```text
daemon_v2/
  analysis/
    projects.py
    terminal.py
    timeline.py
  renderers/
    html.py
    markdown.py
  app_watcher.py
  daily_trace.py
  file_watcher.py
  ingest.py
  main.py
  models.py
  routes.py
  session_tracker.py
  trace_store.py
```

- `main.py` crée l’application Flask et initialise le stockage.
- `routes.py` expose l’ingestion, les vues et les traces.
- `ingest.py` valide, normalise et masque les données sensibles des activités
  entrantes.
- `trace_store.py` encapsule le stockage SQLite append-only.
- `session_tracker.py` affecte les activités aux sessions.
- `daily_trace.py` construit la trace quotidienne, calcule les synthèses et
  conserve les façades publiques de rendu.
- `analysis/terminal.py` contient la classification des commandes terminal et
  le parsing des commandes Git observées.
- `analysis/projects.py` contient les helpers purs liés aux workspaces et aux
  notions de projet observé ou explicite.
- `analysis/timeline.py` contient les regroupements et sélections purs utilisés
  pour préparer les timelines.
- `renderers/html.py` et `renderers/markdown.py` produisent les représentations
  finales sans template engine.
- `app_watcher.py` et `file_watcher.py` collectent les signaux locaux ; le
  watcher terminal reste un script Zsh externe.

## Watcher terminal

Sourcer le watcher depuis chaque session Zsh interactive :

```bash
source /Users/yugz/Projets/Pulse/Pulse_Core/scripts/pulse_terminal_watcher.zsh
```

Pour le charger dans les futures sessions Zsh, ajouter soi-même cette ligne dans `~/.zshrc` :

```zsh
source /Users/yugz/Projets/Pulse/Pulse_Core/scripts/pulse_terminal_watcher.zsh
```

Le watcher enregistre la commande, le dossier courant, les heures de début et
de fin ainsi que le code de sortie. Il écrit immédiatement dans l’outbox, même
si Core est indisponible. Il doit être sourcé depuis Zsh ; `make dev` lance le
worker qui livre ensuite ces événements.

## Watcher de fichiers

Lancer manuellement le watcher par polling avec un workspace explicite :

```bash
.venv/bin/python -m daemon_v2.file_watcher /Users/yugz/Projets/Pulse/Pulse_Core
```

Il envoie les fichiers créés, modifiés et supprimés au daemon local Pulse. Les chemins techniques comme `.git`, `.venv`, les caches, `*.pyc`, `*.db` et `.DS_Store` sont ignorés. Le watcher continue de tourner silencieusement si le daemon est indisponible. L’arrêter avec `Ctrl-C`.

## Observateur d’application

Sur macOS, `make dev` lance `PulseApplicationObserver`, fondé sur
`NSWorkspace`. L’ancien watcher Python n’est plus lancé par le superviseur.

## Avant une migration SwiftUI

La migration vers SwiftUI/macOS n’est pas le chantier actuel. Elle pourra être
envisagée lorsque les blocs produit, les règles de synthèse et la navigation
seront suffisamment stabilisés dans l’interface HTML.

Avant cette étape, les priorités restent la fiabilité des données locales, la
lisibilité des résumés et la consolidation des contrats JSON. Les premières
briques d’analyse sont maintenant séparées dans `analysis/terminal.py`,
`analysis/projects.py` et `analysis/timeline.py`. Une extraction future de
`analysis/summary.py` est possible si les règles de synthèse continuent de
grossir, mais elle n’est pas nécessaire dans l’architecture actuelle.

## Limites actuelles

- Les entrées sont acceptées via l’API HTTP locale et les watchers optionnels terminal, fichiers et application.
- Les sessions utilisent une coupure fixe après 30 minutes d’inactivité.
- Les commandes Git restent observées via le terminal ; les commits effectués
  depuis VS Code ou un autre client Git sont visibles via la lecture Git passive,
  mais ne créent pas encore d’événements Git dédiés.
- Le projet courant repose encore sur des heuristiques de workspace ; les notions
  de workspace observé, workspace explicite et projet qualifié sont séparées
  dans le code, mais pas encore remplacées par une identité projet durable.
- Les commandes reçoivent un masquage basique des secrets, sans parsing shell
  avancé.
- Les watchers fonctionnent en best-effort : une indisponibilité momentanée du
  daemon peut entraîner la perte d’un événement, sans interrompre le watcher.
- SQLite est local et mono-machine ; il n’y a pas encore de système de rétention ou de migration.
- Les scans et agrégations SQLite restent adaptés au volume actuel ; leur coût
  devra être surveillé lorsque l’historique grandira.
- Les archives datées évitent les lectures live de Git et de `Reprise`, mais les
  règles de résumé restent des projections déterministes de la trace, pas des
  faits métier persistés.
- Pulse ne produit pas encore de synthèse intelligente : les résumés restent
  factuels et issus des signaux observés.
- Le daemon n’a pas d’authentification, car il écoute uniquement sur `127.0.0.1`.

.PHONY: dev dev-reload test status reset help

dev:
	./scripts/dev.sh

dev-reload:
	.venv/bin/python scripts/dev_reload.py

test:
	.venv/bin/python -m pytest tests_v2

status:
	./scripts/status.sh

reset:
	./scripts/reset-dev.sh

help:
	@printf '%s\n' \
		'make dev     Lance le daemon et les watchers' \
		'make dev-reload  Lance Pulse avec rechargement automatique' \
		'make test    Exécute les tests' \
		'make status  Affiche l’état local de Pulse' \
		'make reset   Réinitialise la trace de développement' \
		'make help    Affiche cette aide'

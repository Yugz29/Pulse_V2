"""Flask application factory and local daemon entry point."""

import os
from pathlib import Path

from flask import Flask

from .routes import api
from .runtime_config import core_base_url, core_host, core_port
from .trace_store import TraceStore


def select_database_path(database_path: str | Path | None = None) -> Path:
    if database_path is not None:
        return Path(database_path).expanduser()

    configured_path = os.environ.get("PULSE_V2_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser()

    return Path.home() / ".pulse_v2" / "trace.db"


def create_app(database_path: str | Path | None = None) -> Flask:
    app = Flask(__name__)
    path = select_database_path(database_path)
    app.config["DATABASE_PATH"] = path
    app.config["TRACE_STORE"] = TraceStore(path)
    app.config["CORE_BASE_URL"] = core_base_url()
    app.register_blueprint(api)
    return app


def main() -> None:
    app = create_app()
    print(f"Pulse V2 database: {app.config['DATABASE_PATH']}", flush=True)
    app.run(host=core_host(), port=core_port(), debug=False)


if __name__ == "__main__":
    main()

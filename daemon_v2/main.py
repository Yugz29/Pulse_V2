"""Flask application factory and local daemon entry point."""

import os
from pathlib import Path

from flask import Flask

from .routes import api
from .trace_store import TraceStore


def create_app(database_path: str | Path | None = None) -> Flask:
    app = Flask(__name__)
    path = database_path or os.environ.get("PULSE_DB_PATH", "data/pulse.sqlite3")
    app.config["TRACE_STORE"] = TraceStore(path)
    app.register_blueprint(api)
    return app


def main() -> None:
    create_app().run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()

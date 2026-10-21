from __future__ import annotations

import os
from pathlib import Path

from flask import Flask


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    repo_root = Path(__file__).resolve().parent.parent

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("OPS_SECRET_KEY", "dev-only-change-me"),
        REPO_ROOT=str(repo_root),
        OPS_DATA_ROOT=os.environ.get("OPS_DATA_ROOT", str(repo_root / "ops-data")),
        GIT_ENABLED=os.environ.get("OPS_GIT_ENABLED", "true").lower() == "true",
        GIT_REMOTE_PUSH=os.environ.get("OPS_GIT_REMOTE_PUSH", "false").lower() == "true",
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    from .cli import register_cli
    from .routes import bp
    from .service import OWNERS

    app.register_blueprint(bp)
    register_cli(app)

    @app.context_processor
    def inject_owner_roster():
        return {"owners": OWNERS}

    return app

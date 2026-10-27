from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from flask import Flask

PLACEHOLDER_SECRET = "dev-only-change-me"


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    # Where the records live. In the container the code is baked at /app while
    # the checkout (and every record) lives in the /data volume, so the record
    # root is decoupled from the importable code location.
    repo_root = Path(
        os.environ.get("OPS_REPO_ROOT") or Path(__file__).resolve().parent.parent
    ).resolve()

    try:
        session_days = int(os.environ.get("OPS_SESSION_DAYS", "30"))
    except ValueError:
        session_days = 30

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("OPS_SECRET_KEY", PLACEHOLDER_SECRET),
        REPO_ROOT=str(repo_root),
        OPS_DATA_ROOT=os.environ.get("OPS_DATA_ROOT", str(repo_root / "ops-data")),
        GIT_ENABLED=os.environ.get("OPS_GIT_ENABLED", "true").lower() == "true",
        GIT_REMOTE_PUSH=os.environ.get("OPS_GIT_REMOTE_PUSH", "false").lower() == "true",
        OPS_PASSWORD=os.environ.get("OPS_PASSWORD", ""),
        OPS_SESSION_DAYS=session_days,
        PERMANENT_SESSION_LIFETIME=timedelta(days=session_days),
        OPS_PUBLIC_HOST=os.environ.get("OPS_PUBLIC_HOST", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("OPS_COOKIE_SECURE", "false").lower() == "true",
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    # A deployment must never serve the board unprotected: no password, no service.
    if not app.config["TESTING"]:
        if not app.config.get("OPS_PASSWORD"):
            raise RuntimeError("OPS_PASSWORD is required — refusing to serve an unprotected board")
        if app.config.get("SECRET_KEY") in (None, "", PLACEHOLDER_SECRET):
            raise RuntimeError("OPS_SECRET_KEY must be a real random value, not the placeholder")

    from . import auth
    from .cli import register_cli
    from .routes import bp
    from .service import OWNERS

    app.register_blueprint(auth.bp)
    app.register_blueprint(bp)
    register_cli(app)

    @app.context_processor
    def inject_owner_roster():
        return {"owners": OWNERS, "auth_enabled": auth.auth_enabled()}

    return app

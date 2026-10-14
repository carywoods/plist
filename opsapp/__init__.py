from __future__ import annotations

import os
import tempfile
from pathlib import Path

from flask import Flask


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    repo_root = Path(__file__).resolve().parent.parent
    dev_database = Path(tempfile.gettempdir()) / f"plist-ops-{os.getuid()}" / "ops.db"
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("OPS_SECRET_KEY", "dev-only-change-me"),
        # The repository may be SMB/CIFS, where SQLite locking is unsafe. The
        # zero-config development DB is local; deployments must set a durable
        # local OPS_DATABASE path.
        DATABASE=os.environ.get("OPS_DATABASE", str(dev_database)),
        MIRROR_ROOT=os.environ.get("OPS_MIRROR_ROOT", str(repo_root / "ops-data")),
        REPO_ROOT=str(repo_root),
        GIT_ENABLED=os.environ.get("OPS_GIT_ENABLED", "true").lower() == "true",
        GIT_REMOTE_PUSH=os.environ.get("OPS_GIT_REMOTE_PUSH", "false").lower() == "true",
        AUTO_SEED=os.environ.get("OPS_AUTO_SEED", "true").lower() == "true",
        AUTO_IMPORT_LEGACY=os.environ.get("OPS_AUTO_IMPORT_LEGACY", "true").lower() == "true",
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    from . import db
    from .cli import register_cli
    from .routes import bp

    db.init_app(app)
    app.register_blueprint(bp)
    register_cli(app)

    with app.app_context():
        db.ensure_schema()
        seeded = app.config["AUTO_SEED"] and db.seed_demo_if_empty()
        imported = 0
        if app.config["AUTO_IMPORT_LEGACY"]:
            from .legacy import import_legacy_projects
            imported = import_legacy_projects()["imported"]
        if seeded or imported:
            from . import mirror
            message = "ops: Cary imported repository project registry" if imported else "ops: Cary seeded operational prototype"
            mirror.sync_all(message)

    return app

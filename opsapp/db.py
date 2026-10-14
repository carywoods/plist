from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
        # WAL's shared-memory locking is unreliable on common SMB/CIFS mounts.
        # Deploy the live DB on local persistent storage and keep the Git mirror
        # on the NAS; DELETE is the conservative fallback for development here.
        g.db.execute("PRAGMA journal_mode = DELETE")
    return g.db


def close_db(_error=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_schema() -> None:
    sql = (Path(__file__).parent / "schema.sql").read_text()
    get_db().executescript(sql)
    get_db().commit()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)


def row_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def next_id(kind: str, prefix: str) -> str:
    db = get_db()
    row = db.execute("SELECT value FROM counters WHERE kind = ?", (kind,)).fetchone()
    value = (row["value"] if row else 0) + 1
    db.execute(
        "INSERT INTO counters(kind, value) VALUES (?, ?) "
        "ON CONFLICT(kind) DO UPDATE SET value = excluded.value",
        (kind, value),
    )
    return f"{prefix}-{value:04d}"


def add_activity(actor: str, action: str, object_type: str, object_id: str,
                 summary: str, before: dict | None, after: dict | None) -> int:
    cur = get_db().execute(
        "INSERT INTO activity(occurred_at, actor, action, object_type, object_id, summary, before_json, after_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), actor, action, object_type, object_id, summary,
         json.dumps(before, sort_keys=True) if before else None,
         json.dumps(after, sort_keys=True) if after else None),
    )
    return cur.lastrowid


def seed_demo_if_empty() -> bool:
    db = get_db()
    if db.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
        return False
    stamp = now_iso()
    db.execute("INSERT INTO counters(kind, value) VALUES ('workstream', 2), ('project', 2), ('deliverable', 2), ('task', 5), ('decision', 1)")
    db.executemany(
        "INSERT INTO workstreams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("WORKSTREAM-0001", "Product Operations", "Products in delivery and operation", "Cary", "active", stamp, "Cary", stamp, "Cary", 1),
            ("WORKSTREAM-0002", "Infrastructure", "Internal systems and automation", "Cary", "active", stamp, "Cary", stamp, "Cary", 1),
        ],
    )
    db.executemany(
        "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("PROJECT-0001", "CommPilot", "Email assistant production validation", "WORKSTREAM-0001", "active", "Cary", 1, 5, 5, 3, "Production validation is accepted and rollout decision recorded", stamp, stamp, "live/comm-pilot.json", stamp, "Cary", stamp, "Cary", 1),
            ("PROJECT-0002", "Operations Control Prototype", "Make current work legible and executable", "WORKSTREAM-0002", "active", "Dax-A", 2, 5, 4, 3, "Cary can see next work; Dax-A can execute; Charlie can read the mirror", stamp, stamp, None, stamp, "Cary", stamp, "Cary", 1),
        ],
    )
    db.executemany(
        "INSERT INTO deliverables VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("DELIVERABLE-0001", "PROJECT-0001", "Classifier model review", "Validate the production classifier", "Dax-A", "active", 1, 5, 5, 3, None, "Validation results and a go/no-go recommendation are recorded", None, stamp, "Cary", stamp, "Cary", 1),
            ("DELIVERABLE-0002", "PROJECT-0002", "Working prototype", "Operational dashboard, API, audit and mirror", "Dax-A", "active", 2, 5, 4, 3, None, "Critical workflows pass automated tests", None, stamp, "Cary", stamp, "Cary", 1),
        ],
    )
    tasks = [
        ("TASK-0001", "PROJECT-0001", "DELIVERABLE-0001", "Run production validation", "Run the agreed classifier validation suite", "Dax-A", "ready", 1, 5, 5, 3, 0, 1, 0, None, None, None, None),
        ("TASK-0002", "PROJECT-0001", "DELIVERABLE-0001", "Approve rollout criteria", "Confirm acceptable failure thresholds", "Cary", "ready", 1, 5, 5, 1, 1, 0, 0, None, None, None, None),
        ("TASK-0003", "PROJECT-0002", "DELIVERABLE-0002", "Review NAS deployment", "Confirm service host and backup schedule", "Cary", "waiting", 3, 4, 3, 1, 1, 0, 0, None, None, None, None),
        ("TASK-0004", "PROJECT-0002", "DELIVERABLE-0002", "Document service credentials", "Waiting on deployment host selection", "Dax-A", "blocked", 4, 3, 2, 2, 0, 1, 1, "Deployment host has not been selected", None, None, None),
        ("TASK-0005", "PROJECT-0002", "DELIVERABLE-0002", "Inventory existing operational records", "Preserve and map legacy records", "Dax-A", "completed", 2, 4, 3, 2, 0, 1, 0, None, None, stamp, "Legacy JSON inventory documented"),
    ]
    db.executemany(
        "INSERT INTO tasks(id, project_id, deliverable_id, title, description, owner, status, priority, impact, urgency, effort, requires_cary, delegatable, blocked, blocker_reason, due_date, completed_at, completion_notes, created_at, created_by, updated_at, updated_by, version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Cary', ?, 'Cary', 1)",
        [t + (stamp, stamp) for t in tasks],
    )
    db.execute(
        "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("DECISION-0001", "PROJECT-0001", "Production rollout threshold", "Validation criteria need an accountable threshold", "Use the agreed false-positive ceiling", "pending", "Dax-A", None, None, None, stamp, "Dax-A", stamp, "Dax-A", 1),
    )
    add_activity("Cary", "seeded", "system", "demo", "Cary seeded the operational prototype", None, None)
    db.commit()
    return True

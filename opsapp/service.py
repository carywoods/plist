from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import mirror
from .db import add_activity, get_db, next_id, now_iso, row_dict

ACTORS = {"Cary", "Dax-A", "Charlie"}
PROJECT_STATUSES = {"active", "incubating", "parked", "blocked", "completed", "archived"}
TASK_STATUSES = {"ready", "claimed", "in_progress", "waiting", "blocked", "completed", "cancelled"}
DELIVERABLE_STATUSES = {"planned", "active", "blocked", "completed", "cancelled"}


class OpsError(RuntimeError):
    status_code = 400

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class NotFound(OpsError):
    status_code = 404


class Conflict(OpsError):
    status_code = 409


class Forbidden(OpsError):
    status_code = 403


class SyncFailure(OpsError):
    status_code = 503


def actor_allowed(actor: str, *, cary_only: bool = False) -> None:
    if actor not in ACTORS:
        raise Forbidden("Unknown actor. Use Cary, Dax-A, or Charlie.")
    if actor == "Charlie":
        raise Forbidden("Charlie is repository-read-only in this prototype.")
    if cary_only and actor != "Cary":
        raise Forbidden("This strategic action is reserved for Cary.")


def _require(data: dict, *fields: str) -> None:
    missing = [field for field in fields if data.get(field) in (None, "")]
    if missing:
        raise OpsError(f"Missing required field(s): {', '.join(missing)}")


def _bounded(value, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise OpsError(f"{field} must be an integer") from exc
    if not 1 <= number <= 5:
        raise OpsError(f"{field} must be between 1 and 5")
    return number


def _bool(value) -> int:
    if isinstance(value, str):
        return int(value.lower() in {"1", "true", "yes", "on"})
    return int(bool(value))


def _finish_mutation(kind: str, object_id: str, occurred_at: str, message: str) -> dict:
    try:
        return mirror.sync_change(kind, object_id, occurred_at, message)
    except Exception as exc:
        db = get_db()
        db.execute(
            "INSERT INTO sync_failures(occurred_at, object_type, object_id, error) VALUES (?, ?, ?, ?)",
            (now_iso(), kind, object_id, str(exc)),
        )
        db.commit()
        raise SyncFailure(
            f"Live database was updated, but repository synchronization failed: {exc}",
            details={"database_committed": True, "mirrored": False, "object_type": kind, "object_id": object_id},
        ) from exc


def _get(table: str, object_id: str) -> dict:
    row = get_db().execute(f"SELECT * FROM {table} WHERE id = ?", (object_id,)).fetchone()
    if row is None:
        raise NotFound(f"{object_id} was not found")
    return dict(row)


def list_records(table: str, where: str = "", params: tuple = ()) -> list[dict]:
    suffix = f" WHERE {where}" if where else ""
    return [dict(row) for row in get_db().execute(f"SELECT * FROM {table}{suffix} ORDER BY id", params)]


def create_project(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    _require(data, "name", "workstream_id", "status", "owner", "completion_definition")
    if data["status"] not in PROJECT_STATUSES:
        raise OpsError("Invalid project status")
    db, stamp = get_db(), now_iso()
    object_id = next_id("project", "PROJECT")
    values = (
        object_id, data["name"], data.get("description", ""), data["workstream_id"], data["status"], data["owner"],
        _bounded(data.get("priority"), "priority"), _bounded(data.get("impact"), "impact"),
        _bounded(data.get("urgency"), "urgency"), _bounded(data.get("effort"), "effort"),
        data["completion_definition"], stamp, data.get("last_reviewed"), data.get("legacy_source"),
        stamp, actor, stamp, actor, 1,
    )
    try:
        db.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
    except Exception as exc:
        db.rollback()
        raise OpsError(f"Could not create project: {exc}") from exc
    record = _get("projects", object_id)
    summary = f"{actor} created project {record['name']}"
    add_activity(actor, "created", "project", object_id, summary, None, record)
    db.commit()
    sync = _finish_mutation("project", object_id, stamp, f"ops: {actor} created {record['name']}")
    return record, sync


def create_workstream(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    _require(data, "name", "owner")
    db, stamp, object_id = get_db(), now_iso(), next_id("workstream", "WORKSTREAM")
    db.execute(
        "INSERT INTO workstreams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (object_id, data["name"], data.get("description", ""), data["owner"], data.get("status", "active"), stamp, actor, stamp, actor),
    )
    record = _get("workstreams", object_id)
    add_activity(actor, "created", "workstream", object_id, f"{actor} created workstream {record['name']}", None, record)
    db.commit()
    return record, _finish_mutation("workstream", object_id, stamp, f"ops: {actor} created {record['name']} workstream")


def create_deliverable(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    _require(data, "project_id", "title", "owner", "definition_of_done")
    _get("projects", data["project_id"])
    status = data.get("status", "planned")
    if status not in DELIVERABLE_STATUSES:
        raise OpsError("Invalid deliverable status")
    db, stamp, object_id = get_db(), now_iso(), next_id("deliverable", "DELIVERABLE")
    db.execute(
        "INSERT INTO deliverables VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 1)",
        (object_id, data["project_id"], data["title"], data.get("description", ""), data["owner"], status,
         _bounded(data.get("priority"), "priority"), _bounded(data.get("impact"), "impact"),
         _bounded(data.get("urgency"), "urgency"), _bounded(data.get("effort"), "effort"),
         data.get("due_date"), data["definition_of_done"], stamp, actor, stamp, actor),
    )
    record = _get("deliverables", object_id)
    add_activity(actor, "created", "deliverable", object_id, f"{actor} created deliverable {object_id}: {record['title']}", None, record)
    db.commit()
    return record, _finish_mutation("deliverable", object_id, stamp, f"ops: {actor} created {object_id}")


PROJECT_FIELDS = {"name", "description", "workstream_id", "status", "owner", "priority", "impact", "urgency", "effort", "completion_definition", "last_reviewed"}
TASK_FIELDS = {"project_id", "deliverable_id", "title", "description", "owner", "status", "priority", "impact", "urgency", "effort", "requires_cary", "delegatable", "blocked", "blocker_reason", "due_date", "completion_notes"}


def update_record(kind: str, object_id: str, data: dict, actor: str) -> tuple[dict, dict]:
    table = {"project": "projects", "task": "tasks"}[kind]
    actor_allowed(actor, cary_only=(kind == "project"))
    if "version" not in data:
        raise OpsError("version is required for optimistic concurrency")
    before = _get(table, object_id)
    if int(data["version"]) != before["version"]:
        raise Conflict(f"Version conflict: supplied {data['version']}, current version is {before['version']}", details={"current": before})
    fields = PROJECT_FIELDS if kind == "project" else TASK_FIELDS
    changes = {key: value for key, value in data.items() if key in fields}
    if not changes:
        raise OpsError("No mutable fields supplied")
    if "status" in changes:
        allowed = PROJECT_STATUSES if kind == "project" else TASK_STATUSES
        if changes["status"] not in allowed:
            raise OpsError("Invalid status")
    for field in ("priority", "impact", "urgency", "effort"):
        if field in changes:
            changes[field] = _bounded(changes[field], field)
    for field in ("requires_cary", "delegatable", "blocked"):
        if field in changes:
            changes[field] = _bool(changes[field])
    if changes.get("status") == "blocked" and not changes.get("blocker_reason", before.get("blocker_reason")):
        raise OpsError("blocker_reason is required when blocking a task")
    stamp = now_iso()
    changes.update(updated_at=stamp, updated_by=actor)
    assignments = ", ".join(f"{key} = ?" for key in changes)
    params = [*changes.values(), object_id, before["version"]]
    cursor = get_db().execute(
        f"UPDATE {table} SET {assignments}, version = version + 1 WHERE id = ? AND version = ?", params
    )
    if cursor.rowcount != 1:
        get_db().rollback()
        raise Conflict("Record changed while the update was in progress")
    after = _get(table, object_id)
    changed = [f"{key} {before.get(key)!r} -> {after.get(key)!r}" for key in changes if key not in {"updated_at", "updated_by"} and before.get(key) != after.get(key)]
    summary = f"{actor} updated {object_id}: " + "; ".join(changed)
    add_activity(actor, "updated", kind, object_id, summary, before, after)
    get_db().commit()
    name = after.get("name") or object_id
    sync = _finish_mutation(kind, object_id, stamp, f"ops: {actor} updated {name}")
    return after, sync


def create_task(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    _require(data, "project_id", "title", "owner")
    _get("projects", data["project_id"])
    if data.get("deliverable_id"):
        _get("deliverables", data["deliverable_id"])
    status = data.get("status", "ready")
    if status not in TASK_STATUSES:
        raise OpsError("Invalid task status")
    if status == "blocked" and not data.get("blocker_reason"):
        raise OpsError("blocker_reason is required when blocking a task")
    db, stamp, object_id = get_db(), now_iso(), next_id("task", "TASK")
    completed_at = stamp if status == "completed" else None
    db.execute(
        "INSERT INTO tasks(id, project_id, deliverable_id, title, description, owner, status, priority, impact, urgency, effort, requires_cary, delegatable, blocked, blocker_reason, due_date, completed_at, completion_notes, created_at, created_by, updated_at, updated_by, version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (object_id, data["project_id"], data.get("deliverable_id"), data["title"], data.get("description", ""), data["owner"], status,
         _bounded(data.get("priority"), "priority"), _bounded(data.get("impact"), "impact"), _bounded(data.get("urgency"), "urgency"), _bounded(data.get("effort"), "effort"),
         _bool(data.get("requires_cary")), _bool(data.get("delegatable", True)), int(status == "blocked"), data.get("blocker_reason"), data.get("due_date"), completed_at, data.get("completion_notes"), stamp, actor, stamp, actor),
    )
    record = _get("tasks", object_id)
    add_activity(actor, "created", "task", object_id, f"{actor} created {object_id}: {record['title']}", None, record)
    db.commit()
    return record, _finish_mutation("task", object_id, stamp, f"ops: {actor} created {object_id}")


def task_action(object_id: str, action: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    before = _get("tasks", object_id)
    if int(data.get("version", -1)) != before["version"]:
        raise Conflict(f"Version conflict: supplied {data.get('version')}, current version is {before['version']}", details={"current": before})
    if action == "claim":
        if before["status"] not in {"ready", "waiting"}:
            raise OpsError("Only ready or waiting tasks can be claimed")
        changes = {"status": "claimed", "owner": actor}
    elif action == "block":
        _require(data, "blocker_reason")
        changes = {"status": "blocked", "blocked": 1, "blocker_reason": data["blocker_reason"]}
    elif action == "complete":
        changes = {"status": "completed", "blocked": 0, "blocker_reason": None, "completed_at": now_iso(), "completion_notes": data.get("completion_notes", "")}
    else:
        raise OpsError("Unknown task action")
    stamp = now_iso()
    changes.update(updated_at=stamp, updated_by=actor)
    assignments = ", ".join(f"{key} = ?" for key in changes)
    cursor = get_db().execute(
        f"UPDATE tasks SET {assignments}, version = version + 1 WHERE id = ? AND version = ?",
        (*changes.values(), object_id, before["version"]),
    )
    if cursor.rowcount != 1:
        get_db().rollback()
        raise Conflict("Record changed while the update was in progress")
    after = _get("tasks", object_id)
    verb = {"claim": "claimed", "block": "blocked", "complete": "completed"}[action]
    summary = f"{actor} {verb} {object_id}"
    add_activity(actor, verb, "task", object_id, summary, before, after)
    get_db().commit()
    return after, _finish_mutation("task", object_id, stamp, f"ops: {actor} {verb} {object_id}")


def create_decision(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    _require(data, "title", "context")
    if data.get("project_id"):
        _get("projects", data["project_id"])
    db, stamp, object_id = get_db(), now_iso(), next_id("decision", "DECISION")
    db.execute(
        "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, NULL, ?, ?, ?, ?, 1)",
        (object_id, data.get("project_id"), data["title"], data["context"], data.get("recommendation", ""), actor, stamp, actor, stamp, actor),
    )
    record = _get("decisions", object_id)
    add_activity(actor, "requested", "decision", object_id, f"{actor} requested decision {object_id}: {record['title']}", None, record)
    db.commit()
    return record, _finish_mutation("decision", object_id, stamp, f"ops: {actor} requested {object_id}")


def decide(object_id: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    _require(data, "decision")
    before = _get("decisions", object_id)
    if int(data.get("version", -1)) != before["version"]:
        raise Conflict(f"Version conflict: supplied {data.get('version')}, current version is {before['version']}", details={"current": before})
    if before["status"] != "pending":
        raise OpsError("Decision is no longer pending")
    stamp = now_iso()
    cursor = get_db().execute(
        "UPDATE decisions SET status='decided', decision=?, decided_by=?, decided_at=?, updated_at=?, updated_by=?, version=version+1 WHERE id=? AND version=?",
        (data["decision"], actor, stamp, stamp, actor, object_id, before["version"]),
    )
    if cursor.rowcount != 1:
        get_db().rollback()
        raise Conflict("Decision changed while the update was in progress")
    after = _get("decisions", object_id)
    add_activity(actor, "decided", "decision", object_id, f"{actor} decided {object_id}", before, after)
    get_db().commit()
    return after, _finish_mutation("decision", object_id, stamp, f"ops: {actor} decided {object_id}")


def park_project(object_id: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    payload = {"version": data.get("version"), "status": "parked"}
    return update_record("project", object_id, payload, actor)


def next_task(owner: str | None = None) -> dict | None:
    params: list = []
    owner_sql = ""
    if owner:
        owner_sql = " AND t.owner = ?"
        params.append(owner)
    row = get_db().execute(
        "SELECT t.*, p.name AS project_name, d.title AS deliverable_title, "
        "(coalesce(t.impact, 3) * 3 + coalesce(t.urgency, 3) * 3 - coalesce(t.effort, 3)) AS suggested_score "
        "FROM tasks t JOIN projects p ON p.id = t.project_id "
        "LEFT JOIN deliverables d ON d.id = t.deliverable_id "
        "WHERE t.status = 'ready' AND t.blocked = 0 AND p.status = 'active'" + owner_sql +
        " ORDER BY CASE WHEN t.priority IS NULL THEN 1 ELSE 0 END, t.priority, suggested_score DESC, t.created_at LIMIT 1",
        tuple(params),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    return {
        "project": item["project_name"], "project_id": item["project_id"],
        "deliverable": item["deliverable_title"], "task_id": item["id"],
        "next_action": item["title"], "owner": item["owner"], "priority": item["priority"],
        "suggested_score": item["suggested_score"], "status": item["status"], "version": item["version"],
    }


def dashboard() -> dict:
    db = get_db()
    tasks = [dict(row) for row in db.execute("SELECT t.*, p.name project_name FROM tasks t JOIN projects p ON p.id=t.project_id ORDER BY coalesce(t.priority, 99), t.updated_at DESC")]
    projects = [dict(row) for row in db.execute("SELECT * FROM projects ORDER BY coalesce(priority, 99), name")]
    decisions = [dict(row) for row in db.execute("SELECT * FROM decisions WHERE status = 'pending' ORDER BY created_at")]
    needs_cary = [t for t in tasks if t["requires_cary"] and t["status"] not in {"completed", "cancelled"}]
    staff = [t for t in tasks if t["owner"] != "Cary" and t["status"] == "ready" and not t["blocked"]]
    waiting = [t for t in tasks if t["status"] in {"waiting", "blocked"} or t["blocked"]]
    recent = [t for t in tasks if t["status"] == "completed"][:8]
    upcoming = [p for p in projects if p["status"] == "incubating"]
    now = datetime.now(timezone.utc)
    risk = []
    for project in projects:
        if project["status"] != "active":
            continue
        if not project["owner"]:
            risk.append({"id": project["id"], "name": project["name"], "reason": "Active project has no owner"})
        actionable = [t for t in tasks if t["project_id"] == project["id"] and t["status"] in {"ready", "claimed", "in_progress"} and not t["blocked"]]
        if not actionable:
            risk.append({"id": project["id"], "name": project["name"], "reason": "Active project has no next action"})
        if not project["completion_definition"]:
            risk.append({"id": project["id"], "name": project["name"], "reason": "Active project has no completion definition"})
        child_activity = [t["updated_at"] for t in tasks if t["project_id"] == project["id"]]
        when = max([project["last_activity"] or project["updated_at"], *child_activity])
        if datetime.fromisoformat(when.replace("Z", "+00:00")) < now - timedelta(days=14):
            risk.append({"id": project["id"], "name": project["name"], "reason": "No activity for 14 days"})
    today = now.date().isoformat()
    for task in tasks:
        if task["due_date"] and task["due_date"] < today and task["status"] not in {"completed", "cancelled"}:
            risk.append({"id": task["id"], "name": task["title"], "reason": "Task is overdue"})
        if task["status"] == "blocked" and datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00")) < now - timedelta(days=7):
            risk.append({"id": task["id"], "name": task["title"], "reason": "Task blocked more than 7 days"})
    return {"needs_cary": needs_cary, "staff_queue": staff, "waiting": waiting, "upcoming": upcoming,
            "recent": recent, "at_risk": risk, "decisions": decisions, "projects": projects, "next": next_task("Dax-A")}

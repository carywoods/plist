from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import storage

ACTORS = {"Cary", "Dax-A", "Charlie"}
PROJECT_STATUSES = {"active", "incubating", "parked", "blocked", "completed", "archived", "live", "planning", "planned", "concept"}
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


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _finish_mutation(paths: list[Path], commit_message: str) -> dict:
    try:
        snapshot_path = storage.write_snapshot()
        all_paths = [*paths, snapshot_path]
        commit = storage.git_commit(all_paths, commit_message)
        return {
            "mirrored": True,
            "git_commit": commit,
            "files": [str(p) for p in all_paths],
        }
    except Exception as exc:
        raise SyncFailure(
            f"Record saved, but Git commit/synchronization failed: {exc}",
            details={"database_committed": True, "mirrored": False},
        ) from exc


def _get(kind: str, object_id: str) -> dict:
    kind_clean = kind.rstrip("s")
    if kind_clean == "project":
        item = storage.get_project(object_id)
    elif kind_clean == "task":
        item = storage.get_task(object_id)
    elif kind_clean == "decision":
        item = storage.get_decision(object_id)
    elif kind_clean == "deliverable":
        d_list = storage.list_deliverables()
        item = next((d for d in d_list if d["id"] == object_id), None)
    elif kind_clean == "workstream":
        w_list = storage.list_workstreams()
        item = next((w for w in w_list if w["id"] == object_id), None)
    else:
        raise OpsError(f"Unknown kind {kind}")

    if item is None:
        raise NotFound(f"{object_id} was not found")
    return item


def list_records(table: str, where: str = "", params: tuple = ()) -> list[dict]:
    kind = table.rstrip("s")
    if kind == "project":
        records = storage.list_projects()
    elif kind == "task":
        status_filter = None
        if "status = ?" in where and params:
            status_filter = params[0]
        records = storage.list_tasks(status=status_filter)
        if "project_id = ?" in where and params:
            records = [r for r in records if storage.matches_project_id(r.get("project_id"), params[0])]
    elif kind == "decision":
        status_filter = None
        if "status = ?" in where and params:
            status_filter = params[0]
        records = storage.list_decisions(status=status_filter)
        if "project_id = ?" in where and params:
            records = [r for r in records if storage.matches_project_id(r.get("project_id"), params[0])]
    elif kind == "deliverable":
        proj_filter = None
        if "project_id = ?" in where and params:
            proj_filter = params[0]
        records = storage.list_deliverables(project_id=proj_filter)
    elif kind == "workstream":
        records = storage.list_workstreams()
    else:
        records = []
    return records


# -----------------------------------------------------------------------------
# Project Operations
# -----------------------------------------------------------------------------

def create_project(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=False)
    _require(data, "name")
    extra_paths = []
    if data.get("id"):
        proj_id = data["id"]
    else:
        proj_id, ic_path = storage.allocate_next_ic()
        extra_paths.append(ic_path)

    payload = {
        "id": proj_id,
        "name": data["name"],
        "status": data.get("status", "active"),
        "priority": _bounded(data.get("priority"), "priority"),
        "owner": data.get("owner", actor),
        "summary": data.get("summary") or data.get("description", ""),
        "next_action": data.get("next_action", ""),
        "blocker": data.get("blocker"),
        "repo": data.get("repo"),
        "completion_definition": data.get("completion_definition", ""),
        "workstream_id": data.get("workstream_id", "WORKSTREAM-0001"),
    }
    try:
        record, path = storage.save_project(payload, actor)
    except storage.StorageConflict as exc:
        raise Conflict(str(exc), details={"current": exc.current_record}) from exc

    summary_text = f"{actor} created project {record['name']} ({record['id']})"
    _entry, act_path = storage.add_activity(actor, "created", "project", record["id"], summary_text, None, record)
    sync = _finish_mutation([path, act_path, *extra_paths], f"ops: {summary_text}")
    return record, sync


PROJECT_MUTABLE_FIELDS = {
    "name", "summary", "description", "status", "owner", "priority",
    "next_action", "blocker", "repo", "completion_definition", "workstream_id",
}

TASK_MUTABLE_FIELDS = {
    "project_id", "deliverable_id", "title", "description", "owner", "status",
    "priority", "impact", "urgency", "effort", "requires_cary", "delegatable",
    "blocked", "blocker_reason", "due_date", "completion_notes",
}


def update_record(kind: str, object_id: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=(kind == "project"))
    if "version" not in data:
        raise OpsError("version is required for optimistic concurrency")

    before = _get(kind, object_id)
    if int(data["version"]) != before["version"]:
        raise Conflict(
            f"Version conflict: supplied {data['version']}, current version is {before['version']}",
            details={"current": before},
        )

    fields = PROJECT_MUTABLE_FIELDS if kind == "project" else TASK_MUTABLE_FIELDS
    changes = {k: v for k, v in data.items() if k in fields}
    if not changes:
        raise OpsError("No mutable fields supplied")

    if "priority" in changes:
        changes["priority"] = _bounded(changes["priority"], "priority")

    merged = {**before, **changes, "version": before["version"]}

    if kind == "project":
        try:
            after, path = storage.save_project(merged, actor)
        except storage.StorageConflict as exc:
            raise Conflict(str(exc), details={"current": exc.current_record}) from exc
    elif kind == "task":
        if changes.get("status") == "blocked" and not changes.get("blocker_reason", before.get("blocker_reason")):
            raise OpsError("blocker_reason is required when blocking a task")
        try:
            after, path = storage.save_task(merged, actor)
        except storage.StorageConflict as exc:
            raise Conflict(str(exc), details={"current": exc.current_record}) from exc
    else:
        raise OpsError(f"Cannot update {kind}")

    changed = [
        f"{k} {before.get(k)!r} -> {after.get(k)!r}"
        for k in changes
        if k not in {"updated_at", "updated_by"} and before.get(k) != after.get(k)
    ]
    summary_text = f"{actor} updated {object_id}: " + "; ".join(changed)
    _entry, act_path = storage.add_activity(actor, "updated", kind, object_id, summary_text, before, after)
    sync = _finish_mutation([path, act_path], f"ops: {actor} updated {object_id}")
    return after, sync


def park_project(object_id: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    payload = {"version": data.get("version"), "status": "parked"}
    return update_record("project", object_id, payload, actor)


# -----------------------------------------------------------------------------
# Task Operations
# -----------------------------------------------------------------------------

def create_task(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    _require(data, "project_id", "title", "owner")
    _get("project", data["project_id"])

    task_id = storage.allocate_next_task_id()
    status = data.get("status", "ready")
    if status == "blocked" and not data.get("blocker_reason"):
        raise OpsError("blocker_reason is required when blocking a task")

    payload = {
        "id": task_id,
        "project_id": data["project_id"],
        "deliverable_id": data.get("deliverable_id"),
        "title": data["title"],
        "description": data.get("description", ""),
        "owner": data["owner"],
        "status": status,
        "priority": _bounded(data.get("priority"), "priority"),
        "impact": _bounded(data.get("impact"), "impact"),
        "urgency": _bounded(data.get("urgency"), "urgency"),
        "effort": _bounded(data.get("effort"), "effort"),
        "requires_cary": _bool(data.get("requires_cary")),
        "delegatable": _bool(data.get("delegatable", True)),
        "blocked": _bool(data.get("blocked", status == "blocked")),
        "blocker_reason": data.get("blocker_reason"),
        "due_date": data.get("due_date"),
        "completed_at": storage.now_iso() if status == "completed" else None,
        "completion_notes": data.get("completion_notes"),
    }
    try:
        record, path = storage.save_task(payload, actor)
    except storage.StorageConflict as exc:
        raise Conflict(str(exc), details={"current": exc.current_record}) from exc

    summary_text = f"{actor} created {task_id}: {record['title']}"
    _entry, act_path = storage.add_activity(actor, "created", "task", task_id, summary_text, None, record)
    sync = _finish_mutation([path, act_path], f"ops: {actor} created {task_id}")
    return record, sync


def task_action(object_id: str, action: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    before = _get("task", object_id)
    if int(data.get("version", -1)) != before["version"]:
        raise Conflict(
            f"Version conflict: supplied {data.get('version')}, current version is {before['version']}",
            details={"current": before},
        )

    if action == "claim":
        if before["status"] not in {"ready", "waiting"}:
            raise OpsError("Only ready or waiting tasks can be claimed")
        changes = {"status": "claimed", "owner": actor}
    elif action == "block":
        _require(data, "blocker_reason")
        changes = {"status": "blocked", "blocked": True, "blocker_reason": data["blocker_reason"]}
    elif action == "complete":
        changes = {
            "status": "completed",
            "blocked": False,
            "blocker_reason": None,
            "completed_at": storage.now_iso(),
            "completion_notes": data.get("completion_notes", ""),
        }
    else:
        raise OpsError(f"Unknown task action {action}")

    merged = {**before, **changes, "version": before["version"]}
    try:
        after, path = storage.save_task(merged, actor)
    except storage.StorageConflict as exc:
        raise Conflict(str(exc), details={"current": exc.current_record}) from exc

    verb = {"claim": "claimed", "block": "blocked", "complete": "completed"}[action]
    summary_text = f"{actor} {verb} {object_id}"
    _entry, act_path = storage.add_activity(actor, verb, "task", object_id, summary_text, before, after)
    sync = _finish_mutation([path, act_path], f"ops: {summary_text}")
    return after, sync


# -----------------------------------------------------------------------------
# Decision Operations
# -----------------------------------------------------------------------------

def create_decision(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    _require(data, "title", "context")
    if data.get("project_id"):
        _get("project", data["project_id"])

    d_id = storage.allocate_next_decision_id()
    payload = {
        "id": d_id,
        "project_id": data.get("project_id"),
        "title": data["title"],
        "context": data["context"],
        "recommendation": data.get("recommendation", ""),
        "status": "pending",
        "requested_by": actor,
    }
    record, path = storage.save_decision(payload, actor)
    summary_text = f"{actor} requested decision {d_id}: {record['title']}"
    _entry, act_path = storage.add_activity(actor, "requested", "decision", d_id, summary_text, None, record)
    sync = _finish_mutation([path, act_path], f"ops: {summary_text}")
    return record, sync


def decide(object_id: str, data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    _require(data, "decision")
    before = _get("decision", object_id)
    if int(data.get("version", -1)) != before["version"]:
        raise Conflict(
            f"Version conflict: supplied {data.get('version')}, current version is {before['version']}",
            details={"current": before},
        )
    if before["status"] != "pending":
        raise OpsError("Decision is no longer pending")

    stamp = storage.now_iso()
    merged = {
        **before,
        "status": "decided",
        "decision": data["decision"],
        "decided_by": actor,
        "decided_at": stamp,
        "version": before["version"],
    }
    after, path = storage.save_decision(merged, actor)
    summary_text = f"{actor} decided {object_id}"
    _entry, act_path = storage.add_activity(actor, "decided", "decision", object_id, summary_text, before, after)
    sync = _finish_mutation([path, act_path], f"ops: {summary_text}")
    return after, sync


# -----------------------------------------------------------------------------
# Deliverable & Workstream Operations
# -----------------------------------------------------------------------------

def create_deliverable(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor)
    _require(data, "project_id", "title", "owner", "definition_of_done")
    _get("project", data["project_id"])
    record, path = storage.save_deliverable(data, actor)
    summary_text = f"{actor} created deliverable {record['id']}: {record['title']}"
    _entry, act_path = storage.add_activity(actor, "created", "deliverable", record["id"], summary_text, None, record)
    sync = _finish_mutation([path, act_path], f"ops: {summary_text}")
    return record, sync


def create_workstream(data: dict, actor: str) -> tuple[dict, dict]:
    actor_allowed(actor, cary_only=True)
    _require(data, "name", "owner")
    record, path = storage.save_workstream(data, actor)
    summary_text = f"{actor} created workstream {record['name']}"
    _entry, act_path = storage.add_activity(actor, "created", "workstream", record["id"], summary_text, None, record)
    sync = _finish_mutation([path, act_path], f"ops: {summary_text}")
    return record, sync


# -----------------------------------------------------------------------------
# What Next? & Operational Dashboard
# -----------------------------------------------------------------------------

def next_task(owner: str | None = None) -> dict | None:
    projects = {p["id"]: p for p in storage.list_projects()}
    tasks = storage.list_tasks(status="ready")

    candidates = []
    for t in tasks:
        if t.get("blocked"):
            continue
        proj = projects.get(t.get("project_id"))
        # Active or live projects only
        if proj and proj.get("status") not in {"active", "live"}:
            continue
        if owner and t.get("owner") != owner:
            continue
        impact = t.get("impact") or 3
        urgency = t.get("urgency") or 3
        effort = t.get("effort") or 3
        score = (impact * 3) + (urgency * 3) - effort
        candidates.append({
            "project": proj["name"] if proj else "Unknown",
            "project_id": t.get("project_id"),
            "deliverable": "",
            "task_id": t["id"],
            "next_action": t["title"],
            "owner": t["owner"],
            "priority": t.get("priority"),
            "suggested_score": score,
            "status": t["status"],
            "version": t["version"],
            "created_at": t.get("created_at", ""),
        })

    # If no standalone tasks match, check active projects with next_action
    if not candidates:
        for p in projects.values():
            if p.get("status") in {"active", "live"} and p.get("next_action") and not p.get("blocker"):
                if owner and p.get("owner") != owner:
                    continue
                candidates.append({
                    "project": p["name"],
                    "project_id": p["id"],
                    "deliverable": "",
                    "task_id": f"NEXT-{p['id']}",
                    "next_action": p["next_action"],
                    "owner": p.get("owner", "Dax-A"),
                    "priority": p.get("priority"),
                    "suggested_score": 10,
                    "status": "ready",
                    "version": p.get("version", 1),
                    "created_at": p.get("created_at", ""),
                })

    if not candidates:
        return None

    # Priority sort: manual priority first (1..5, None last), then score desc, then created_at
    candidates.sort(
        key=lambda c: (
            c["priority"] is None,
            c["priority"] or 99,
            -c["suggested_score"],
            c["created_at"],
        )
    )
    return candidates[0]


def dashboard() -> dict:
    tasks = storage.list_tasks()
    projects = storage.list_projects()
    decisions = storage.list_decisions(status="pending")
    proj_map = {p["id"]: p for p in projects}

    for t in tasks:
        t["project_name"] = proj_map.get(t["project_id"], {}).get("name", t.get("project_id", ""))

    needs_cary = [
        t for t in tasks
        if t.get("requires_cary") and t.get("status") not in {"completed", "cancelled"}
    ]
    staff_queue = [
        t for t in tasks
        if t.get("owner") != "Cary" and t.get("status") == "ready" and not t.get("blocked")
    ]
    waiting = [
        t for t in tasks
        if t.get("status") in {"waiting", "blocked"} or t.get("blocked")
    ]
    recent = [
        t for t in tasks
        if t.get("status") == "completed"
    ][:8]
    upcoming = [
        p for p in projects
        if p.get("status") in {"incubating", "planning", "planned"}
    ]

    now = datetime.now(timezone.utc)
    risk = []

    for project in projects:
        if project["status"] not in {"active", "live"}:
            continue
        if not project.get("owner"):
            risk.append({"id": project["id"], "name": project["name"], "reason": "Active project has no owner"})

        # Check actionability via tasks or project next_action
        actionable_tasks = [
            t for t in tasks
            if t.get("project_id") == project["id"]
            and t.get("status") in {"ready", "claimed", "in_progress"}
            and not t.get("blocked")
        ]
        has_next_action = bool(project.get("next_action")) or bool(actionable_tasks)
        if not has_next_action:
            risk.append({"id": project["id"], "name": project["name"], "reason": "Active project has no next action"})

        if not project.get("completion_definition") and not project.get("summary"):
            risk.append({"id": project["id"], "name": project["name"], "reason": "Active project has no completion definition"})

        last_act = project.get("last_activity") or project.get("last_updated") or project.get("updated_at")
        if last_act:
            try:
                dt = datetime.fromisoformat(last_act.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < now - timedelta(days=14):
                    risk.append({"id": project["id"], "name": project["name"], "reason": "No activity for 14 days"})
            except ValueError:
                pass

    today = now.date().isoformat()
    for task in tasks:
        if task.get("due_date") and task["due_date"] < today and task.get("status") not in {"completed", "cancelled"}:
            risk.append({"id": task["id"], "name": task["title"], "reason": "Task is overdue"})
        if task.get("status") == "blocked":
            upd = task.get("updated_at")
            if upd:
                try:
                    dt = datetime.fromisoformat(upd.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < now - timedelta(days=7):
                        risk.append({"id": task["id"], "name": task["title"], "reason": "Task blocked more than 7 days"})
                except ValueError:
                    pass

    return {
        "needs_cary": needs_cary,
        "staff_queue": staff_queue,
        "waiting": waiting,
        "upcoming": upcoming,
        "recent": recent,
        "at_risk": risk,
        "decisions": decisions,
        "projects": projects,
        "next": next_task("Dax-A"),
    }

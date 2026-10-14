from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import current_app

LIFECYCLE_FOLDERS = ["live", "active", "planning", "planned", "concept"]

STATUS_TO_FOLDER = {
    "live": "live",
    "active": "active",
    "planning": "planning",
    "planned": "planned",
    "concept": "concept",
    "incubating": "planning",
    "parked": "concept",
    "blocked": "active",
    "completed": "active",
    "archived": "concept",
}

FOLDER_TO_STATUS = {
    "live": "active",
    "active": "active",
    "planning": "incubating",
    "planned": "incubating",
    "concept": "parked",
}


class StorageError(RuntimeError):
    pass


class StorageConflict(StorageError):
    def __init__(self, message: str, current_record: dict | None = None):
        super().__init__(message)
        self.current_record = current_record or {}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_root() -> Path:
    if current_app:
        return Path(current_app.config["REPO_ROOT"]).resolve()
    return Path(__file__).resolve().parent.parent


def ops_data_root() -> Path:
    return repo_root() / "ops-data"


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def normalize_priority(val: Any) -> int | None:
    if val is None or val == "":
        return None
    s = str(val).strip().upper()
    if s in {"PRIORITY", "P1", "1"}:
        return 1
    if s in {"P2", "2"}:
        return 2
    if s in {"P3", "3"}:
        return 3
    if s in {"P4", "4"}:
        return 4
    if s in {"P5", "5"}:
        return 5
    try:
        num = int(val)
        if 1 <= num <= 5:
            return num
    except (ValueError, TypeError):
        pass
    return None


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


# -----------------------------------------------------------------------------
# IC Number Tracking (next_ic.json)
# -----------------------------------------------------------------------------

def allocate_next_ic() -> str:
    path = repo_root() / "next_ic.json"
    next_num = 94
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
                next_num = int(d.get("next_ic_number", d.get("next_ic", 94)))
        except Exception:
            pass
    ic_id = f"IC-{next_num:06d}"
    updated_data = {
        "next_ic_number": next_num + 1,
        "next_id": f"IC-{next_num + 1:06d}",
        "updated_at": now_iso(),
        "owner": "Dax-A",
        "notes": "Tracks next available IC number for Dax-A to assign to new projects.",
    }
    atomic_write_json(path, updated_data)
    return ic_id


# -----------------------------------------------------------------------------
# Priority Overlays
# -----------------------------------------------------------------------------

def load_priority_overlays() -> dict[str, int]:
    overlays: dict[str, int] = {}
    p_dir = repo_root() / "priority"
    if not p_dir.is_dir():
        return overlays
    for f in p_dir.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as handle:
                d = json.load(handle)
                name = d.get("name")
                pri = normalize_priority(d.get("priority")) or 1
                if name:
                    overlays[name.strip().lower()] = pri
        except Exception:
            continue
    return overlays


# -----------------------------------------------------------------------------
# Projects Storage
# -----------------------------------------------------------------------------

def _normalize_project_dict(raw: dict, file_path: Path, folder: str, overlays: dict[str, int]) -> dict:
    stem_name = file_path.stem
    proj_id = raw.get("id")
    if not proj_id or proj_id == "NEW":
        proj_id = f"NEW-{stem_name}"

    name = raw.get("name") or stem_name
    priority = normalize_priority(raw.get("priority"))

    # Apply priority overlay if name matches
    if name.strip().lower() in overlays:
        overlay_pri = overlays[name.strip().lower()]
        if priority is None or overlay_pri < priority:
            priority = overlay_pri

    raw_status = raw.get("status") or folder
    # Map status to operational status if legacy folder name
    operational_status = FOLDER_TO_STATUS.get(raw_status, raw_status)

    summary = raw.get("summary") or raw.get("notes") or raw.get("description") or ""
    completion_definition = raw.get("completion_definition") or raw.get("meaning") or ""

    return {
        "id": proj_id,
        "name": name,
        "status": operational_status,
        "raw_status": raw_status,
        "lifecycle_folder": folder,
        "priority": priority,
        "owner": raw.get("owner"),
        "summary": summary,
        "description": raw.get("description") or summary,
        "next_action": raw.get("next_action") or "",
        "blocker": raw.get("blocker"),
        "repo": raw.get("repo"),
        "completion_definition": completion_definition,
        "last_activity": raw.get("last_activity") or raw.get("last_updated") or now_iso(),
        "last_updated": raw.get("last_updated") or raw.get("migrated_on") or now_iso()[:10],
        "created_at": raw.get("created_at") or now_iso(),
        "created_by": raw.get("created_by") or "system",
        "updated_at": raw.get("updated_at") or now_iso(),
        "updated_by": raw.get("updated_by") or "system",
        "version": int(raw.get("version", 1)),
        "workstream_id": raw.get("workstream_id", "WORKSTREAM-0001"),
        "_file_path": str(file_path.relative_to(repo_root())),
    }


def list_projects() -> list[dict]:
    root = repo_root()
    overlays = load_priority_overlays()
    projects: dict[str, dict] = {}

    for folder in LIFECYCLE_FOLDERS:
        folder_path = root / folder
        if not folder_path.is_dir():
            continue
        for p in sorted(folder_path.glob("*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    raw = json.load(f)
                    norm = _normalize_project_dict(raw, p, folder, overlays)
                    # Deduplicate by ID, preferring canonical numbered files
                    if norm["id"] not in projects or "ic-" in p.name.lower():
                        projects[norm["id"]] = norm
            except Exception:
                continue

    # Also check ops-data/projects for any standalone project records
    od_projects = ops_data_root() / "projects"
    if od_projects.is_dir():
        for p in sorted(od_projects.glob("*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    raw = json.load(f)
                    pid = raw.get("id")
                    if pid and pid not in projects:
                        norm = _normalize_project_dict(raw, p, "active", overlays)
                        projects[pid] = norm
            except Exception:
                continue

    result = list(projects.values())
    result.sort(key=lambda x: (x["priority"] is None, x["priority"] or 99, x["name"].lower()))
    return result


def get_project(project_id: str) -> dict | None:
    # Handle alias CommPilot (PROJECT-0001 <-> IC-000004)
    target_ids = {project_id}
    if project_id == "PROJECT-0001":
        target_ids.add("IC-000004")
    elif project_id == "IC-000004":
        target_ids.add("PROJECT-0001")

    for proj in list_projects():
        if proj["id"] in target_ids:
            return proj
    return None


def save_project(data: dict, actor: str) -> tuple[dict, Path]:
    proj_id = data["id"]
    existing = get_project(proj_id)

    if existing is not None:
        expected_ver = int(data.get("version", -1))
        if expected_ver != existing["version"]:
            raise StorageConflict(
                f"Version conflict: supplied {data.get('version')}, current is {existing['version']}",
                current_record=existing,
            )
        file_path = repo_root() / existing["_file_path"]
        new_version = existing["version"] + 1
        created_at = existing.get("created_at", now_iso())
        created_by = existing.get("created_by", actor)
    else:
        file_path = None
        new_version = 1
        created_at = now_iso()
        created_by = actor

    stamp = now_iso()
    status = data.get("status", "active")
    target_folder = STATUS_TO_FOLDER.get(status, "active")

    # Determine file location
    if file_path is None or not file_path.exists():
        slug = slugify(data.get("name", proj_id))
        filename = f"{proj_id.lower()}-{slug}.json" if not proj_id.lower().startswith("ic-") else f"{proj_id.lower()}-{slug}.json"
        target_path = repo_root() / target_folder / filename
    else:
        # Check if status change requires moving folders
        if existing and existing["lifecycle_folder"] != target_folder and target_folder in LIFECYCLE_FOLDERS:
            target_path = repo_root() / target_folder / file_path.name
        else:
            target_path = file_path

    record = {
        "id": proj_id,
        "name": data.get("name", existing.get("name") if existing else proj_id),
        "status": status,
        "priority": normalize_priority(data.get("priority")),
        "owner": data.get("owner"),
        "summary": data.get("summary") or data.get("notes") or data.get("description") or "",
        "next_action": data.get("next_action") or "",
        "blocker": data.get("blocker"),
        "repo": data.get("repo"),
        "completion_definition": data.get("completion_definition", ""),
        "last_activity": stamp,
        "last_updated": stamp[:10],
        "created_at": created_at,
        "created_by": created_by,
        "updated_at": stamp,
        "updated_by": actor,
        "version": new_version,
        "workstream_id": data.get("workstream_id", "WORKSTREAM-0001"),
    }

    atomic_write_json(target_path, record)

    # If moved across folders, unlink original
    if file_path and target_path != file_path and file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            pass

    record["_file_path"] = str(target_path.relative_to(repo_root()))
    record["lifecycle_folder"] = target_folder

    # Also keep ops-data/projects updated for compatibility
    od_path = ops_data_root() / "projects" / f"{proj_id}.json"
    try:
        atomic_write_json(od_path, record)
    except Exception:
        pass

    return record, target_path


# -----------------------------------------------------------------------------
# Tasks Storage (ops-data/tasks/)
# -----------------------------------------------------------------------------

def list_tasks(status: str | None = None, owner: str | None = None) -> list[dict]:
    tasks_dir = ops_data_root() / "tasks"
    if not tasks_dir.is_dir():
        return []
    tasks = []
    for f in sorted(tasks_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as handle:
                task = json.load(handle)
                if status and task.get("status") != status:
                    continue
                if owner and task.get("owner") != owner:
                    continue
                tasks.append(task)
        except Exception:
            continue
    tasks.sort(key=lambda t: (t.get("priority") is None, t.get("priority") or 99, t.get("updated_at", "")))
    return tasks


def get_task(task_id: str) -> dict | None:
    path = ops_data_root() / "tasks" / f"{task_id}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def allocate_next_task_id() -> str:
    tasks_dir = ops_data_root() / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for f in tasks_dir.glob("TASK-*.json"):
        m = re.match(r"TASK-(\d+)", f.stem)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"TASK-{highest + 1:04d}"


def save_task(data: dict, actor: str) -> tuple[dict, Path]:
    task_id = data["id"]
    existing = get_task(task_id)

    if existing is not None:
        expected_ver = int(data.get("version", -1))
        if expected_ver != existing["version"]:
            raise StorageConflict(
                f"Version conflict: supplied {data.get('version')}, current is {existing['version']}",
                current_record=existing,
            )
        new_version = existing["version"] + 1
        created_at = existing.get("created_at", now_iso())
        created_by = existing.get("created_by", actor)
    else:
        new_version = 1
        created_at = now_iso()
        created_by = actor

    stamp = now_iso()
    record = {
        "id": task_id,
        "project_id": data.get("project_id"),
        "deliverable_id": data.get("deliverable_id"),
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "owner": data.get("owner"),
        "status": data.get("status", "ready"),
        "priority": normalize_priority(data.get("priority")),
        "impact": data.get("impact"),
        "urgency": data.get("urgency"),
        "effort": data.get("effort"),
        "requires_cary": bool(data.get("requires_cary", False)),
        "delegatable": bool(data.get("delegatable", True)),
        "blocked": bool(data.get("blocked", data.get("status") == "blocked")),
        "blocker_reason": data.get("blocker_reason"),
        "due_date": data.get("due_date"),
        "completed_at": data.get("completed_at"),
        "completion_notes": data.get("completion_notes"),
        "created_at": created_at,
        "created_by": created_by,
        "updated_at": stamp,
        "updated_by": actor,
        "version": new_version,
    }

    path = ops_data_root() / "tasks" / f"{task_id}.json"
    atomic_write_json(path, record)
    return record, path


# -----------------------------------------------------------------------------
# Decisions Storage (ops-data/decisions/)
# -----------------------------------------------------------------------------

def list_decisions(status: str | None = None) -> list[dict]:
    d_dir = ops_data_root() / "decisions"
    if not d_dir.is_dir():
        return []
    decisions = []
    for f in sorted(d_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as handle:
                d = json.load(handle)
                if status and d.get("status") != status:
                    continue
                decisions.append(d)
        except Exception:
            continue
    decisions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return decisions


def get_decision(decision_id: str) -> dict | None:
    path = ops_data_root() / "decisions" / f"{decision_id}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def allocate_next_decision_id() -> str:
    d_dir = ops_data_root() / "decisions"
    d_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for f in d_dir.glob("DECISION-*.json"):
        m = re.match(r"DECISION-(\d+)", f.stem)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"DECISION-{highest + 1:04d}"


def save_decision(data: dict, actor: str) -> tuple[dict, Path]:
    d_id = data["id"]
    existing = get_decision(d_id)

    if existing is not None:
        expected_ver = int(data.get("version", -1))
        if expected_ver != existing["version"]:
            raise StorageConflict(
                f"Version conflict: supplied {data.get('version')}, current is {existing['version']}",
                current_record=existing,
            )
        new_version = existing["version"] + 1
        created_at = existing.get("created_at", now_iso())
        created_by = existing.get("created_by", actor)
    else:
        new_version = 1
        created_at = now_iso()
        created_by = actor

    stamp = now_iso()
    record = {
        "id": d_id,
        "project_id": data.get("project_id"),
        "title": data.get("title", ""),
        "context": data.get("context", ""),
        "recommendation": data.get("recommendation", ""),
        "status": data.get("status", "pending"),
        "requested_by": data.get("requested_by", actor),
        "decision": data.get("decision"),
        "decided_by": data.get("decided_by"),
        "decided_at": data.get("decided_at"),
        "created_at": created_at,
        "created_by": created_by,
        "updated_at": stamp,
        "updated_by": actor,
        "version": new_version,
    }

    path = ops_data_root() / "decisions" / f"{d_id}.json"
    atomic_write_json(path, record)
    return record, path


# -----------------------------------------------------------------------------
# Deliverables Storage (ops-data/deliverables/)
# -----------------------------------------------------------------------------

def list_deliverables(project_id: str | None = None) -> list[dict]:
    d_dir = ops_data_root() / "deliverables"
    if not d_dir.is_dir():
        return []
    items = []
    for f in sorted(d_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as handle:
                item = json.load(handle)
                if project_id and item.get("project_id") != project_id:
                    continue
                items.append(item)
        except Exception:
            continue
    items.sort(key=lambda x: x.get("id", ""))
    return items


def allocate_next_deliverable_id() -> str:
    d_dir = ops_data_root() / "deliverables"
    d_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for f in d_dir.glob("DELIVERABLE-*.json"):
        m = re.match(r"DELIVERABLE-(\d+)", f.stem)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"DELIVERABLE-{highest + 1:04d}"


def save_deliverable(data: dict, actor: str) -> tuple[dict, Path]:
    d_id = data.get("id") or allocate_next_deliverable_id()
    stamp = now_iso()
    record = {
        "id": d_id,
        "project_id": data["project_id"],
        "title": data["title"],
        "description": data.get("description", ""),
        "owner": data.get("owner", actor),
        "status": data.get("status", "planned"),
        "priority": normalize_priority(data.get("priority")),
        "impact": data.get("impact"),
        "urgency": data.get("urgency"),
        "effort": data.get("effort"),
        "due_date": data.get("due_date"),
        "definition_of_done": data.get("definition_of_done", ""),
        "completed_at": data.get("completed_at"),
        "created_at": data.get("created_at", stamp),
        "created_by": data.get("created_by", actor),
        "updated_at": stamp,
        "updated_by": actor,
        "version": int(data.get("version", 1)),
    }
    path = ops_data_root() / "deliverables" / f"{d_id}.json"
    atomic_write_json(path, record)
    return record, path


# -----------------------------------------------------------------------------
# Workstreams Storage (ops-data/workstreams/)
# -----------------------------------------------------------------------------

def list_workstreams() -> list[dict]:
    w_dir = ops_data_root() / "workstreams"
    if not w_dir.is_dir():
        return [
            {"id": "WORKSTREAM-0001", "name": "Product Operations", "owner": "Cary", "status": "active"},
            {"id": "WORKSTREAM-0002", "name": "Infrastructure", "owner": "Cary", "status": "active"},
            {"id": "WORKSTREAM-0003", "name": "Portfolio Initiatives", "owner": "Cary", "status": "active"},
        ]
    items = []
    for f in sorted(w_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as handle:
                items.append(json.load(handle))
        except Exception:
            continue
    return items


def save_workstream(data: dict, actor: str) -> tuple[dict, Path]:
    w_dir = ops_data_root() / "workstreams"
    w_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for f in w_dir.glob("WORKSTREAM-*.json"):
        m = re.match(r"WORKSTREAM-(\d+)", f.stem)
        if m:
            highest = max(highest, int(m.group(1)))
    w_id = data.get("id") or f"WORKSTREAM-{highest + 1:04d}"
    stamp = now_iso()
    record = {
        "id": w_id,
        "name": data["name"],
        "description": data.get("description", ""),
        "owner": data.get("owner", actor),
        "status": data.get("status", "active"),
        "created_at": data.get("created_at", stamp),
        "created_by": data.get("created_by", actor),
        "updated_at": stamp,
        "updated_by": actor,
        "version": int(data.get("version", 1)),
    }
    path = w_dir / f"{w_id}.json"
    atomic_write_json(path, record)
    return record, path


# -----------------------------------------------------------------------------
# Activity Logging (ops-data/activity/YYYY-MM.ndjson)
# -----------------------------------------------------------------------------

def add_activity(
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    summary: str,
    before: dict | None = None,
    after: dict | None = None,
) -> tuple[dict, Path]:
    stamp = now_iso()
    month = stamp[:7]
    path = ops_data_root() / "activity" / f"{month}.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "occurred_at": stamp,
        "actor": actor,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "summary": summary,
        "before": before,
        "after": after,
    }
    line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
    return entry, path


def list_activity(limit: int = 200) -> list[dict]:
    a_dir = ops_data_root() / "activity"
    if not a_dir.is_dir():
        return []
    lines = []
    for f in sorted(a_dir.glob("*.ndjson"), reverse=True):
        try:
            with open(f, encoding="utf-8") as handle:
                lines.extend(handle.read().splitlines())
        except Exception:
            continue
    items = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
            if len(items) >= limit:
                break
        except Exception:
            continue
    return items


# -----------------------------------------------------------------------------
# Snapshot & Git Operations
# -----------------------------------------------------------------------------

def write_snapshot(day: str | None = None) -> Path:
    day = day or datetime.now(timezone.utc).date().isoformat()
    path = ops_data_root() / "snapshots" / f"{day}.json"
    snapshot = {
        "generated_at": now_iso(),
        "projects": list_projects(),
        "tasks": list_tasks(),
        "decisions": list_decisions(),
        "deliverables": list_deliverables(),
        "workstreams": list_workstreams(),
        "activity": list_activity(limit=1000),
    }
    atomic_write_json(path, snapshot)
    return path


def git_commit(paths: list[Path], message: str) -> str | None:
    if not current_app or not current_app.config.get("GIT_ENABLED", True):
        return None
    repo = repo_root()
    relative_paths = []
    for p in paths:
        try:
            relative_paths.append(str(p.resolve().relative_to(repo)))
        except ValueError:
            continue

    if not relative_paths:
        return None

    try:
        subprocess.run(["git", "add", "--", *relative_paths], cwd=repo, check=True, capture_output=True, text=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *relative_paths], cwd=repo)
        if changed.returncode == 0:
            return None
        if changed.returncode != 1:
            raise StorageError("Unable to inspect staged git changes")

        subprocess.run(
            ["git", "commit", "-m", message, "--", *relative_paths],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

        if current_app.config.get("GIT_REMOTE_PUSH", False):
            subprocess.run(["git", "push"], cwd=repo, check=True, capture_output=True, text=True)
        return commit
    except (OSError, subprocess.CalledProcessError) as exc:
        err = getattr(exc, "stderr", None) or str(exc)
        raise StorageError(f"Git synchronization failed: {str(err).strip()}") from exc

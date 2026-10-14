from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from .db import get_db

TABLES = {
    "workstream": "workstreams",
    "project": "projects",
    "deliverable": "deliverables",
    "task": "tasks",
    "decision": "decisions",
}


class MirrorError(RuntimeError):
    pass


def _clean(record: dict) -> dict:
    result = {}
    for key, value in record.items():
        if key in {"requires_cary", "delegatable", "blocked"}:
            value = bool(value)
        result[key] = value
    return result


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _record_path(kind: str, object_id: str) -> Path:
    return Path(current_app.config["MIRROR_ROOT"]) / TABLES[kind] / f"{object_id}.json"


def write_record(kind: str, object_id: str) -> Path:
    table = TABLES[kind]
    row = get_db().execute(f"SELECT * FROM {table} WHERE id = ?", (object_id,)).fetchone()
    if row is None:
        raise MirrorError(f"Cannot mirror missing {kind} {object_id}")
    path = _record_path(kind, object_id)
    text = json.dumps(_clean(dict(row)), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_write(path, text)
    return path


def write_activity_month(occurred_at: str) -> Path:
    month = occurred_at[:7]
    rows = get_db().execute(
        "SELECT id, occurred_at, actor, action, object_type, object_id, summary, before_json, after_json "
        "FROM activity WHERE substr(occurred_at, 1, 7) = ? ORDER BY id",
        (month,),
    ).fetchall()
    lines = []
    for row in rows:
        item = dict(row)
        before_json = item.pop("before_json")
        after_json = item.pop("after_json")
        item["before"] = json.loads(before_json) if before_json else None
        item["after"] = json.loads(after_json) if after_json else None
        lines.append(json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    path = Path(current_app.config["MIRROR_ROOT"]) / "activity" / f"{month}.ndjson"
    _atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))
    return path


def snapshot_data() -> dict:
    data = {"schema_version": 1}
    for kind, table in TABLES.items():
        rows = get_db().execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        data[table] = [_clean(dict(row)) for row in rows]
    rows = get_db().execute(
        "SELECT id, occurred_at, actor, action, object_type, object_id, summary, before_json, after_json FROM activity ORDER BY id"
    ).fetchall()
    data["activity"] = []
    for row in rows:
        item = dict(row)
        before_json = item.pop("before_json")
        after_json = item.pop("after_json")
        item["before"] = json.loads(before_json) if before_json else None
        item["after"] = json.loads(after_json) if after_json else None
        data["activity"].append(item)
    return data


def write_snapshot(day: str | None = None) -> Path:
    day = day or datetime.now(timezone.utc).date().isoformat()
    path = Path(current_app.config["MIRROR_ROOT"]) / "snapshots" / f"{day}.json"
    _atomic_write(path, json.dumps(snapshot_data(), indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def _git(paths: list[Path], message: str) -> str | None:
    if not current_app.config["GIT_ENABLED"]:
        return None
    repo = Path(current_app.config["REPO_ROOT"]).resolve()
    relative = []
    for path in paths:
        try:
            relative.append(str(path.resolve().relative_to(repo)))
        except ValueError as exc:
            raise MirrorError(f"Mirror path is outside Git repository: {path}") from exc
    try:
        subprocess.run(["git", "add", "--", *relative], cwd=repo, check=True, capture_output=True, text=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *relative], cwd=repo)
        if changed.returncode == 0:
            return None
        if changed.returncode != 1:
            raise MirrorError("Unable to inspect staged mirror changes")
        result = subprocess.run(
            ["git", "commit", "-m", message, "--", *relative],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        if current_app.config["GIT_REMOTE_PUSH"]:
            subprocess.run(["git", "push"], cwd=repo, check=True, capture_output=True, text=True)
        return commit
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise MirrorError(f"Git synchronization failed: {detail.strip()}") from exc


def sync_change(kind: str, object_id: str, occurred_at: str, commit_message: str) -> dict:
    try:
        paths = [write_record(kind, object_id), write_activity_month(occurred_at), write_snapshot()]
        commit = _git(paths, commit_message)
        return {"mirrored": True, "git_commit": commit, "files": [str(p) for p in paths]}
    except Exception as exc:
        if isinstance(exc, MirrorError):
            raise
        raise MirrorError(f"Mirror generation failed: {exc}") from exc


def sync_all(commit_message: str = "ops: refresh operational mirror") -> dict:
    paths = []
    for kind, table in TABLES.items():
        for row in get_db().execute(f"SELECT id FROM {table} ORDER BY id"):
            paths.append(write_record(kind, row["id"]))
    months = [row[0] for row in get_db().execute("SELECT DISTINCT substr(occurred_at, 1, 7) FROM activity ORDER BY 1")]
    for month in months:
        paths.append(write_activity_month(f"{month}-01"))
    paths.append(write_snapshot())
    return {"mirrored": True, "git_commit": _git(paths, commit_message), "files": [str(p) for p in paths]}

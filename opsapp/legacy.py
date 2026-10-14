from __future__ import annotations

import json
from pathlib import Path

from flask import current_app

from .db import add_activity, get_db, now_iso

LIFECYCLE_STATUS = {
    "live": "active",
    "active": "active",
    "planning": "incubating",
    "planned": "incubating",
    "concept": "parked",
}


def discover_legacy_projects() -> tuple[list[dict], list[str]]:
    """Read canonical lifecycle JSON. Unnumbered aliases remain source context."""
    repo = Path(current_app.config["REPO_ROOT"])
    records: list[dict] = []
    aliases: list[str] = []
    for folder, app_status in LIFECYCLE_STATUS.items():
        for path in sorted((repo / folder).glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            relative = str(path.relative_to(repo))
            source_id = raw.get("id")
            if not source_id:
                aliases.append(relative)
                continue
            if source_id == "NEW":
                source_id = "PROJECT-NEW-0001"
            records.append({
                "id": source_id,
                "name": raw.get("name") or path.stem,
                "description": raw.get("notes") or "Imported from the existing repository project registry.",
                "status": app_status,
                "priority": _priority(raw.get("priority")),
                "legacy_source": relative,
                "recorded_on": raw.get("last_updated") or raw.get("migrated_on") or "2026-10-12",
            })
    return records, aliases


def _priority(value) -> int | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"PRIORITY", "P1", "1"}:
        return 1
    if normalized in {"P2", "2"}:
        return 2
    if normalized in {"P3", "3"}:
        return 3
    return None


def import_legacy_projects(*, dry_run: bool = False) -> dict:
    records, aliases = discover_legacy_projects()
    db = get_db()
    existing_ids = {row["id"] for row in db.execute("SELECT id FROM projects")}
    existing_sources = {row["legacy_source"] for row in db.execute("SELECT legacy_source FROM projects WHERE legacy_source IS NOT NULL")}
    candidates = [item for item in records if item["id"] not in existing_ids and item["legacy_source"] not in existing_sources]
    # The seeded CommPilot represents canonical IC-000004; link rather than duplicate.
    commpilot = next((item for item in candidates if item["id"] == "IC-000004"), None)
    linked_commpilot = False
    if commpilot and db.execute("SELECT 1 FROM projects WHERE id='PROJECT-0001' AND lower(name)='commpilot'").fetchone():
        candidates.remove(commpilot)
        linked_commpilot = True
    result = {
        "discovered": len(records),
        "aliases_ignored": aliases,
        "to_import": len(candidates),
        "imported": 0,
        "linked_existing": int(linked_commpilot),
    }
    if dry_run or (not candidates and not linked_commpilot):
        return result

    stamp = now_iso()
    db.execute(
        "INSERT OR IGNORE INTO workstreams(id, name, description, owner, status, created_at, created_by, updated_at, updated_by, version) "
        "VALUES ('WORKSTREAM-0003', 'Legacy Portfolio', 'Projects imported from the original lifecycle registry pending operational triage', 'Cary', 'active', ?, 'legacy-import', ?, 'legacy-import', 1)",
        (stamp, stamp),
    )
    db.execute(
        "INSERT INTO counters(kind, value) VALUES ('workstream', 3) ON CONFLICT(kind) DO UPDATE SET value=max(value, 3)"
    )
    if linked_commpilot:
        db.execute(
            "UPDATE projects SET legacy_source=?, updated_at=?, updated_by='legacy-import', version=version+1 WHERE id='PROJECT-0001'",
            (commpilot["legacy_source"], stamp),
        )
    for item in candidates:
        date_stamp = f"{item['recorded_on']}T00:00:00Z"
        db.execute(
            "INSERT INTO projects(id, name, description, workstream_id, status, owner, priority, impact, urgency, effort, completion_definition, last_activity, last_reviewed, legacy_source, created_at, created_by, updated_at, updated_by, version) "
            "VALUES (?, ?, ?, 'WORKSTREAM-0003', ?, NULL, ?, NULL, NULL, NULL, '', ?, NULL, ?, ?, 'legacy-import', ?, 'legacy-import', 1)",
            (item["id"], item["name"], item["description"], item["status"], item["priority"], date_stamp, item["legacy_source"], date_stamp, date_stamp),
        )
    result["imported"] = len(candidates)
    add_activity(
        "Cary", "imported", "system", "legacy-project-registry",
        f"Cary imported {len(candidates)} repository projects and linked {result['linked_existing']} existing project",
        None, {"discovered": len(records), "imported": len(candidates), "aliases_ignored": aliases},
    )
    db.commit()
    return result

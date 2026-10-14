from __future__ import annotations

import json
import subprocess

from opsapp import create_app, mirror
from opsapp.db import get_db


def create_task(client, base_records, **overrides):
    data = {
        "project_id": base_records["project"]["id"],
        "deliverable_id": base_records["deliverable"]["id"],
        "title": "Run validation", "owner": "Dax-A", "status": "ready",
        "priority": 1, "impact": 5, "urgency": 5, "effort": 2,
    }
    data.update(overrides)
    return client.post("/api/tasks", headers={"X-Actor": "Dax-A"}, json=data)


def test_create_and_update_project_with_optimistic_conflict(client, base_records):
    project = base_records["project"]
    updated = client.patch(f"/api/projects/{project['id']}", headers={"X-Actor": "Cary"}, json={
        "version": project["version"], "priority": 1,
    })
    assert updated.status_code == 200
    assert updated.json["data"]["priority"] == 1
    assert updated.json["data"]["version"] == 2

    stale = client.patch(f"/api/projects/{project['id']}", headers={"X-Actor": "Cary"}, json={
        "version": project["version"], "priority": 3,
    })
    assert stale.status_code == 409
    assert stale.json["current"]["version"] == 2


def test_task_claim_block_complete_and_activity(client, app, base_records):
    created = create_task(client, base_records)
    assert created.status_code == 201
    task = created.json["data"]

    claimed = client.post(f"/api/tasks/{task['id']}/claim", headers={"X-Actor": "Dax-A"}, json={"version": task["version"]})
    assert claimed.status_code == 200
    task = claimed.json["data"]
    assert task["status"] == "claimed"

    blocked = client.post(f"/api/tasks/{task['id']}/block", headers={"X-Actor": "Dax-A"}, json={"version": task["version"], "blocker_reason": "Need Cary approval"})
    assert blocked.status_code == 200
    task = blocked.json["data"]
    assert task["blocked"] == 1
    assert task["blocker_reason"] == "Need Cary approval"

    completed = client.post(f"/api/tasks/{task['id']}/complete", headers={"X-Actor": "Dax-A"}, json={"version": task["version"], "completion_notes": "Approved and verified"})
    assert completed.status_code == 200
    assert completed.json["data"]["status"] == "completed"
    assert completed.json["data"]["completion_notes"] == "Approved and verified"
    with app.app_context():
        actions = [row[0] for row in get_db().execute("SELECT action FROM activity WHERE object_id=? ORDER BY id", (task["id"],))]
    assert actions == ["created", "claimed", "blocked", "completed"]


def test_decision_request_and_cary_decision(client, base_records):
    made = client.post("/api/decisions", headers={"X-Actor": "Dax-A"}, json={
        "project_id": base_records["project"]["id"], "title": "Approve spend",
        "context": "A service subscription is needed", "recommendation": "Approve one month",
    })
    assert made.status_code == 201
    decision = made.json["data"]
    pending = client.get("/api/decisions/pending")
    assert pending.json["data"][0]["id"] == decision["id"]
    denied = client.post(f"/api/decisions/{decision['id']}/decide", headers={"X-Actor": "Dax-A"}, json={"version": 1, "decision": "Yes"})
    assert denied.status_code == 403
    resolved = client.post(f"/api/decisions/{decision['id']}/decide", headers={"X-Actor": "Cary"}, json={"version": 1, "decision": "Approve one month"})
    assert resolved.status_code == 200
    assert resolved.json["data"]["decided_by"] == "Cary"


def test_next_uses_manual_priority_and_excludes_blocked(client, base_records):
    create_task(client, base_records, title="Algorithmically attractive", priority=3, impact=5, urgency=5, effort=1)
    chosen = create_task(client, base_records, title="Cary manual priority", priority=1, impact=2, urgency=2, effort=5)
    create_task(client, base_records, title="Blocked priority zero", priority=1, status="blocked", blocker_reason="External dependency")
    result = client.get("/api/next?owner=Dax-A")
    assert result.status_code == 200
    assert result.json["task_id"] == chosen.json["data"]["id"]
    assert result.json["next_action"] == "Cary manual priority"


def test_dashboard_flags_active_project_without_next_action(client, base_records):
    board = client.get("/api/dashboard")
    assert board.status_code == 200
    assert any(item["id"] == base_records["project"]["id"] and "no next action" in item["reason"].lower() for item in board.json["at_risk"])


def test_mirror_activity_and_snapshot_are_readable(client, app, base_records):
    created = create_task(client, base_records)
    task = created.json["data"]
    task_path = app.config["MIRROR_ROOT"] + f"/tasks/{task['id']}.json"
    with open(task_path, encoding="utf-8") as handle:
        mirrored = json.load(handle)
    assert mirrored["id"] == task["id"]
    assert mirrored["blocked"] is False

    activity_files = list((__import__("pathlib").Path(app.config["MIRROR_ROOT"]) / "activity").glob("*.ndjson"))
    events = [json.loads(line) for line in activity_files[0].read_text().splitlines()]
    assert any(event["object_id"] == task["id"] for event in events)
    snapshot_files = list((__import__("pathlib").Path(app.config["MIRROR_ROOT"]) / "snapshots").glob("*.json"))
    snapshot = json.loads(snapshot_files[0].read_text())
    assert any(item["id"] == task["id"] for item in snapshot["tasks"])


def test_mirror_failure_is_explicit_and_database_commit_is_preserved(client, app, base_records, monkeypatch):
    def fail(*_args, **_kwargs):
        raise mirror.MirrorError("simulated disk failure")
    monkeypatch.setattr(mirror, "sync_change", fail)
    result = create_task(client, base_records, title="Committed but unsynced")
    assert result.status_code == 503
    assert result.json["database_committed"] is True
    assert result.json["mirrored"] is False
    object_id = result.json["object_id"]
    fetched = client.get(f"/api/tasks/{object_id}")
    assert fetched.status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM sync_failures WHERE object_id=?", (object_id,)).fetchone()[0] == 1


def test_permissions_keep_charlie_read_only_and_strategy_with_cary(client, base_records):
    charlie = client.post("/api/tasks", headers={"X-Actor": "Charlie"}, json={"project_id": base_records["project"]["id"], "title": "Write", "owner": "Charlie"})
    assert charlie.status_code == 403
    dax_park = client.post(f"/api/projects/{base_records['project']['id']}/park", headers={"X-Actor": "Dax-A"}, json={"version": 1})
    assert dax_park.status_code == 403


def test_git_commit_integration(tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Operator"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "operator@example.test"], cwd=repo, check=True)
    app = create_app({
        "TESTING": True, "DATABASE": str(tmp_path / "git.db"), "MIRROR_ROOT": str(repo / "ops-data"),
        "REPO_ROOT": str(repo), "GIT_ENABLED": True, "AUTO_SEED": False,
    })
    client = app.test_client()
    result = client.post("/api/workstreams", headers={"X-Actor": "Cary"}, json={"name": "Publishing", "owner": "Cary"})
    assert result.status_code == 201
    commit = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    assert commit == "ops: Cary created Publishing workstream"
    assert result.json["sync"]["git_commit"]


def test_pages_render_with_seed_data(tmp_path):
    app = create_app({
        "TESTING": True, "DATABASE": str(tmp_path / "seed.db"), "MIRROR_ROOT": str(tmp_path / "mirror"),
        "REPO_ROOT": str(tmp_path), "GIT_ENABLED": False, "AUTO_SEED": True,
    })
    client = app.test_client()
    for path in ["/", "/projects", "/projects/PROJECT-0001", "/tasks", "/decisions", "/activity"]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_existing_repository_json_is_imported_idempotently(tmp_path):
    repo = tmp_path / "repo"
    (repo / "active").mkdir(parents=True)
    (repo / "live").mkdir()
    (repo / "active" / "ic-000123-example.json").write_text(json.dumps({
        "id": "IC-000123", "name": "Existing Project", "status": "active",
        "priority": "P1", "last_updated": "2026-10-12",
    }))
    (repo / "live" / "example-alias.json").write_text(json.dumps({"name": "Existing Project", "status": "live"}))
    config = {
        "TESTING": True, "DATABASE": str(tmp_path / "import.db"), "MIRROR_ROOT": str(repo / "ops-data"),
        "REPO_ROOT": str(repo), "GIT_ENABLED": False, "AUTO_SEED": False, "AUTO_IMPORT_LEGACY": True,
    }
    app = create_app(config)
    assert app.test_client().get("/api/projects/IC-000123").status_code == 200
    assert (repo / "ops-data" / "projects" / "IC-000123.json").exists()
    app = create_app(config)
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM projects WHERE id='IC-000123'").fetchone()[0] == 1

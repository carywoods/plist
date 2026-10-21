from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opsapp import create_app
from opsapp import storage


def create_task(client, base_records, **overrides):
    data = {
        "project_id": base_records["project"]["id"],
        "deliverable_id": base_records["deliverable"]["id"],
        "title": "Run validation",
        "owner": "Dax-A",
        "status": "ready",
        "priority": 1,
        "impact": 5,
        "urgency": 5,
        "effort": 2,
    }
    data.update(overrides)
    return client.post("/api/tasks", headers={"X-Actor": "Dax-A"}, json=data)


def test_create_and_update_project_with_optimistic_conflict(client, base_records):
    project = base_records["project"]
    updated = client.patch(f"/api/projects/{project['id']}", headers={"X-Actor": "Cary"}, json={
        "version": project["version"],
        "priority": 1,
    })
    assert updated.status_code == 200
    assert updated.json["data"]["priority"] == 1
    assert updated.json["data"]["version"] == 2

    # Attempting to update with stale version should fail with 409
    stale = client.patch(f"/api/projects/{project['id']}", headers={"X-Actor": "Cary"}, json={
        "version": project["version"],
        "priority": 3,
    })
    assert stale.status_code == 409
    assert stale.json["current"]["version"] == 2


def test_task_claim_block_complete_and_activity(client, repo, base_records):
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
    assert task["blocked"] is True
    assert task["blocker_reason"] == "Need Cary approval"

    completed = client.post(f"/api/tasks/{task['id']}/complete", headers={"X-Actor": "Dax-A"}, json={"version": task["version"], "completion_notes": "Approved and verified"})
    assert completed.status_code == 200
    assert completed.json["data"]["status"] == "completed"
    assert completed.json["data"]["completion_notes"] == "Approved and verified"

    # Verify activity was written to ndjson
    act_files = list((repo / "ops-data" / "activity").glob("*.ndjson"))
    assert len(act_files) > 0
    actions = [json.loads(line)["action"] for line in act_files[0].read_text().splitlines() if json.loads(line)["object_id"] == task["id"]]
    assert actions == ["created", "claimed", "blocked", "completed"]


def test_decision_request_and_cary_decision(client, base_records):
    made = client.post("/api/decisions", headers={"X-Actor": "Dax-A"}, json={
        "project_id": base_records["project"]["id"],
        "title": "Approve spend",
        "context": "A service subscription is needed",
        "recommendation": "Approve one month",
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


def test_permissions_keep_charlie_read_only_and_strategy_with_cary(client, base_records):
    charlie = client.post("/api/tasks", headers={"X-Actor": "Charlie"}, json={"project_id": base_records["project"]["id"], "title": "Write", "owner": "Charlie"})
    assert charlie.status_code == 403
    dax_park = client.post(f"/api/projects/{base_records['project']['id']}/park", headers={"X-Actor": "Dax-A"}, json={"version": 1})
    assert dax_park.status_code == 403


def test_git_commit_integration(tmp_path):
    repo = tmp_path / "gitrepo"
    for folder in ["live", "active", "planning", "planned", "concept", "priority"]:
        (repo / folder).mkdir(parents=True, exist_ok=True)
    for folder in ["tasks", "decisions", "deliverables", "workstreams", "activity", "snapshots"]:
        (repo / "ops-data" / folder).mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Operator"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "operator@example.test"], cwd=repo, check=True)

    app = create_app({
        "TESTING": True,
        "REPO_ROOT": str(repo),
        "OPS_DATA_ROOT": str(repo / "ops-data"),
        "GIT_ENABLED": True,
    })
    client = app.test_client()
    result = client.post("/api/workstreams", headers={"X-Actor": "Cary"}, json={"name": "Publishing", "owner": "Cary"})
    assert result.status_code == 201

    commit = subprocess.run(["git", "log", "-1", "--pretty=%s"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    assert commit == "ops: Cary created workstream Publishing"
    assert result.json["sync"]["git_commit"]


def test_dax_can_create_project_and_bump_ic(client, repo):
    res = client.post("/api/projects", headers={"X-Actor": "Dax-A"}, json={
        "name": "Dax Initiative",
        "summary": "Project opened by Dax-A",
        "status": "active",
        "owner": "Dax-A",
        "completion_definition": "Done",
    })
    assert res.status_code == 201
    data = res.json["data"]
    assert data["id"] == "IC-000094"
    assert data["owner"] == "Dax-A"

    next_ic = json.loads((repo / "next_ic.json").read_text())
    assert next_ic["next_ic_number"] == 95
    assert next_ic["next_id"] == "IC-000095"

    assert not (repo / "ops-data" / "projects").exists()


def test_pages_render(client, base_records):
    for path in ["/", "/projects", f"/projects/{base_records['project']['id']}", "/tasks", "/decisions", "/activity"]:
        response = client.get(path)
        assert response.status_code == 200, path



def test_owner_roster_covers_staff_seats_and_rejects_unknown(client, base_records):
    project = base_records["project"]

    for seat in ("Emi", "Claude", "Charlie", "Lexi"):
        updated = client.patch(
            f"/api/projects/{project['id']}",
            headers={"X-Actor": "Cary"},
            json={"version": project["version"], "owner": seat},
        )
        assert updated.status_code == 200, seat
        assert updated.json["data"]["owner"] == seat
        project = updated.json["data"]

    task = create_task(client, base_records, owner="Emi")
    assert task.status_code == 201
    assert task.json["data"]["owner"] == "Emi"

    rejected = client.patch(
        f"/api/projects/{project['id']}",
        headers={"X-Actor": "Cary"},
        json={"version": project["version"], "owner": "Nobody"},
    )
    assert rejected.status_code == 400


def test_owner_dropdown_lists_full_roster(client, base_records):
    page = client.get("/projects")
    assert page.status_code == 200
    body = page.data.decode()
    for seat in ("Cary", "Dax-A", "Emi", "Claude", "Charlie", "Lexi"):
        assert f'value="{seat}"' in body, seat

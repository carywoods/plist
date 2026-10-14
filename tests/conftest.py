from __future__ import annotations

import json
from pathlib import Path

import pytest

from opsapp import create_app


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    for folder in ["live", "active", "planning", "planned", "concept", "priority"]:
        (r / folder).mkdir(parents=True, exist_ok=True)
    for folder in ["tasks", "decisions", "deliverables", "workstreams", "activity", "snapshots"]:
        (r / "ops-data" / folder).mkdir(parents=True, exist_ok=True)

    # Initialize next_ic.json
    (r / "next_ic.json").write_text(json.dumps({
        "next_ic_number": 94,
        "next_id": "IC-000094",
        "owner": "Dax-A",
    }))
    return r


@pytest.fixture
def app(repo):
    app = create_app({
        "TESTING": True,
        "REPO_ROOT": str(repo),
        "OPS_DATA_ROOT": str(repo / "ops-data"),
        "GIT_ENABLED": False,
        "SECRET_KEY": "test",
    })
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def base_records(client):
    ws = client.post("/api/workstreams", headers={"X-Actor": "Cary"}, json={"name": "Infrastructure", "owner": "Cary"})
    assert ws.status_code == 201
    workstream_id = ws.json["data"]["id"]

    project = client.post("/api/projects", headers={"X-Actor": "Cary"}, json={
        "name": "Control System",
        "summary": "Operational control system",
        "workstream_id": workstream_id,
        "status": "active",
        "owner": "Dax-A",
        "priority": 2,
        "completion_definition": "Critical workflows are verified",
    })
    assert project.status_code == 201

    deliverable = client.post("/api/deliverables", headers={"X-Actor": "Dax-A"}, json={
        "project_id": project.json["data"]["id"],
        "title": "Prototype",
        "owner": "Dax-A",
        "status": "active",
        "definition_of_done": "Tests pass",
    })
    assert deliverable.status_code == 201

    return {
        "workstream": ws.json["data"],
        "project": project.json["data"],
        "deliverable": deliverable.json["data"],
    }

from __future__ import annotations

import pytest

from opsapp import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "ops.db"),
        "MIRROR_ROOT": str(tmp_path / "repo" / "ops-data"),
        "REPO_ROOT": str(tmp_path / "repo"),
        "GIT_ENABLED": False,
        "AUTO_SEED": False,
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
        "name": "Control System", "description": "Operational control", "workstream_id": workstream_id,
        "status": "active", "owner": "Dax-A", "priority": 2,
        "impact": 5, "urgency": 4, "effort": 3,
        "completion_definition": "Critical workflows are verified",
    })
    assert project.status_code == 201
    deliverable = client.post("/api/deliverables", headers={"X-Actor": "Dax-A"}, json={
        "project_id": project.json["data"]["id"], "title": "Prototype", "owner": "Dax-A",
        "status": "active", "definition_of_done": "Tests pass",
    })
    assert deliverable.status_code == 201
    return {
        "workstream": ws.json["data"], "project": project.json["data"],
        "deliverable": deliverable.json["data"],
    }

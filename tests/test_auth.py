from __future__ import annotations

import pytest

from opsapp import create_app


@pytest.fixture
def secured(repo):
    return create_app({
        "TESTING": True,
        "REPO_ROOT": str(repo),
        "OPS_DATA_ROOT": str(repo / "ops-data"),
        "GIT_ENABLED": False,
        "SECRET_KEY": "test-secret",
        "OPS_PASSWORD": "correct horse",
    })


def test_refuses_to_start_without_password(monkeypatch):
    monkeypatch.delenv("OPS_PASSWORD", raising=False)
    monkeypatch.delenv("OPS_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_app({"TESTING": False, "SECRET_KEY": "real-enough-secret"})


def test_refuses_placeholder_secret(monkeypatch):
    monkeypatch.setenv("OPS_PASSWORD", "x")
    with pytest.raises(RuntimeError):
        create_app({"TESTING": False, "SECRET_KEY": "dev-only-change-me"})


def test_health_is_public(secured):
    response = secured.test_client().get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_ui_redirects_to_login(secured):
    response = secured.test_client().get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_api_returns_json_401(secured):
    response = secured.test_client().get("/api/projects")
    assert response.status_code == 401
    assert response.json["error"] == "authentication required"


def test_login_grants_access(secured):
    client = secured.test_client()
    bad = client.post("/login", data={"password": "wrong"})
    assert bad.status_code == 401

    good = client.post("/login", data={"password": "correct horse"})
    assert good.status_code == 302
    assert client.get("/").status_code == 200
    assert client.get("/api/projects").status_code == 200


def test_x_ops_key_header(secured):
    response = secured.test_client().get("/api/projects", headers={"X-Ops-Key": "correct horse"})
    assert response.status_code == 200


def test_basic_auth(secured):
    client = secured.test_client()
    from base64 import b64encode

    token = b64encode(b"cary:correct horse").decode()
    response = client.get("/api/projects", headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 200


def test_cross_origin_write_refused(secured):
    client = secured.test_client()
    client.post("/login", data={"password": "correct horse"})
    response = client.post(
        "/api/projects",
        headers={"Origin": "https://evil.example", "X-Actor": "Cary"},
        json={"name": "Nope"},
    )
    assert response.status_code == 403


def test_logout_clears_session(secured):
    client = secured.test_client()
    client.post("/login", data={"password": "correct horse"})
    assert client.get("/").status_code == 200
    client.get("/logout")
    assert client.get("/").status_code == 302

"""
Auth tests — simple local authentication + admin/user separation (CLAUDE.md security req).

Re-enables auth (the suite's autouse fixture disables it by default), then verifies:
- open routes (/health, /) need no token
- user routes (/chat, /events, /policy) reject missing/invalid tokens (401), accept user/admin
- admin routes (/approvals actions, /export) reject a user token (403), accept admin (200)
"""
import pytest
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.settings import settings
from gateway.routes.chat import get_assistant_adapter
from gateway.routes.classify import get_guard_adapter, get_scanner_adapter


class _Assistant:
    async def chat(self, messages):
        return {"content": "ok", "model": "mock"}

    async def health(self):
        return {"ollama_process": "ok", "assistant_model": "ok", "model_name": "mock"}


class _SafeGuard:
    async def classify(self, messages, direction):
        return {"overall_severity": "none", "categories": [], "llama_guard_label": "safe",
                "llama_guard_categories": [], "confidence": 0.0}

    async def health(self):
        return {}


class _CleanScanner:
    def scan(self, text):
        return []


ADMIN = {"Authorization": f"Bearer {settings.gateway_admin_token}"}
USER = {"Authorization": f"Bearer {settings.gateway_user_token}"}
BAD = {"Authorization": "Bearer nope"}


@pytest.fixture
def auth_client(tmp_path):
    settings.sqlite_db_path = str(tmp_path / "a.db")
    settings.jsonl_audit_path = str(tmp_path / "a.jsonl")
    settings.gateway_auth_enabled = True  # override the autouse default
    app.dependency_overrides[get_assistant_adapter] = lambda: _Assistant()
    app.dependency_overrides[get_guard_adapter] = lambda: _SafeGuard()
    app.dependency_overrides[get_scanner_adapter] = lambda: _CleanScanner()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    settings.gateway_auth_enabled = False


def test_health_is_open(auth_client):
    assert auth_client.get("/health").status_code == 200


def test_ui_is_open(auth_client):
    assert auth_client.get("/").status_code == 200


def test_chat_requires_token(auth_client):
    assert auth_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


def test_chat_rejects_invalid_token(auth_client):
    r = auth_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=BAD)
    assert r.status_code == 401


def test_chat_accepts_user_token(auth_client):
    r = auth_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=USER)
    assert r.status_code == 200


def test_events_requires_token(auth_client):
    assert auth_client.get("/events").status_code == 401
    assert auth_client.get("/events", headers=USER).status_code == 200


def test_policy_readable_by_user(auth_client):
    assert auth_client.get("/policy").status_code == 401
    assert auth_client.get("/policy", headers=USER).status_code == 200


def test_admin_action_forbidden_for_user(auth_client):
    # user token is authenticated but not authorised for admin actions -> 403
    r = auth_client.post("/approvals/none/approve", headers=USER)
    assert r.status_code == 403


def test_admin_action_unauthenticated_is_401(auth_client):
    assert auth_client.post("/approvals/none/approve").status_code == 401


def test_admin_action_allowed_for_admin(auth_client):
    # admin token passes auth; unknown approval id -> 404 (NOT 401/403)
    r = auth_client.post("/approvals/none/approve", headers=ADMIN)
    assert r.status_code == 404


def test_export_requires_admin(auth_client):
    assert auth_client.get("/export/jsonl").status_code == 401
    assert auth_client.get("/export/jsonl", headers=USER).status_code == 403
    assert auth_client.get("/export/jsonl", headers=ADMIN).status_code == 200

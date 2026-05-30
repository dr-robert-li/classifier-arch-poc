"""
Tests for the governance layer (Phase 3 / demo): high-severity PAUSE on /chat, the admin
approval actions (approve / reject / redact-resume / false-positive / escalate), the versioned
YAML policy, and audit export (raw + redacted). Offline — all adapters mocked.
"""
import pytest
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.routes.chat import get_assistant_adapter
from gateway.routes.classify import get_guard_adapter, get_scanner_adapter


class _Assistant:
    async def chat(self, messages):
        return {"content": "mock assistant reply", "model": "mock-llama"}

    async def health(self):
        return {"ollama_process": "ok", "assistant_model": "ok", "model_name": "mock-llama"}


class _SafeGuard:
    async def classify(self, messages, direction):
        return {"overall_severity": "none", "categories": [], "llama_guard_label": "safe",
                "llama_guard_categories": [], "confidence": 0.0}

    async def health(self):
        return {"guard_process": "ok", "guard_model": "ok"}


class _CleanScanner:
    def scan(self, text):
        return []


class _SecretScanner:
    """Always reports a high-severity secret -> forces a prompt-side pause."""
    def scan(self, text):
        return [{"category": "secrets", "severity": "high", "matched_value": "deadbeef"}]


@pytest.fixture
def high_client(tmp_path):
    """Client whose scanner forces high severity (prompt pauses)."""
    from gateway.settings import settings
    settings.sqlite_db_path = str(tmp_path / "g.db")
    settings.jsonl_audit_path = str(tmp_path / "g.jsonl")
    app.dependency_overrides[get_assistant_adapter] = lambda: _Assistant()
    app.dependency_overrides[get_guard_adapter] = lambda: _SafeGuard()
    app.dependency_overrides[get_scanner_adapter] = lambda: _SecretScanner()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def safe_client(tmp_path):
    from gateway.settings import settings
    settings.sqlite_db_path = str(tmp_path / "s.db")
    settings.jsonl_audit_path = str(tmp_path / "s.jsonl")
    app.dependency_overrides[get_assistant_adapter] = lambda: _Assistant()
    app.dependency_overrides[get_guard_adapter] = lambda: _SafeGuard()
    app.dependency_overrides[get_scanner_adapter] = lambda: _CleanScanner()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _pause(client, text="leak sk-AKIAEXAMPLE secret"):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": text}]})
    assert r.status_code == 202, f"expected pause, got {r.status_code}: {r.text}"
    return r.json()


# --- pause -----------------------------------------------------------------
def test_high_severity_prompt_pauses_before_generation(high_client):
    d = _pause(high_client)
    assert d["status"] == "paused" and d["side"] == "prompt"
    assert d["severity"] in ("high", "critical")
    assert "secrets" in d["categories"]
    # paused interaction is in the queue
    q = high_client.get("/approvals?status=pending").json()
    assert any(a["approval_id"] == d["approval_id"] for a in q)


def test_safe_prompt_is_delivered_not_paused(safe_client):
    r = safe_client.post("/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200
    assert r.json()["status"] == "delivered"


# --- admin actions ---------------------------------------------------------
def test_approve_resolves_and_generates(high_client):
    d = _pause(high_client)
    r = high_client.post(f"/approvals/{d['approval_id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["response"]  # generated on approval
    assert not high_client.get("/approvals?status=pending").json()


def test_reject_blocks(high_client):
    d = _pause(high_client)
    r = high_client.post(f"/approvals/{d['approval_id']}/reject")
    assert r.json()["status"] == "rejected"
    ev = high_client.get("/events?limit=1000").json()
    assert any(e["event_type"] == "approval.rejected" for e in ev)


def test_redact_resume_masks_and_records_redaction(high_client):
    d = _pause(high_client, text="my key sk-abcdEFGH1234ijklMNOP5678qrstUVWX and email a@b.com")
    r = high_client.post(f"/approvals/{d['approval_id']}/redact-resume")
    assert r.json()["status"] == "redacted_resumed"
    assert len(r.json()["spans"]) >= 1
    ev = high_client.get("/events?limit=1000").json()
    assert any(e["event_type"] == "approval.redacted_resumed" for e in ev)


def test_false_positive_allows(high_client):
    d = _pause(high_client)
    r = high_client.post(f"/approvals/{d['approval_id']}/false-positive")
    assert r.json()["status"] == "false_positive"
    ev = high_client.get("/events?limit=1000").json()
    assert any(e["event_type"] == "approval.false_positive_marked" for e in ev)


def test_escalate(high_client):
    d = _pause(high_client)
    r = high_client.post(f"/approvals/{d['approval_id']}/escalate")
    assert r.json()["status"] == "escalated"
    ev = high_client.get("/events?limit=1000").json()
    assert any(e["event_type"] == "approval.escalated" for e in ev)


def test_resolved_approval_is_404(high_client):
    d = _pause(high_client)
    high_client.post(f"/approvals/{d['approval_id']}/approve")
    assert high_client.post(f"/approvals/{d['approval_id']}/reject").status_code == 404


# --- policy + export -------------------------------------------------------
def test_policy_reflects_yaml(safe_client):
    p = safe_client.get("/policy").json()
    assert p["policy_version"] == "local-poc-v1"
    assert set(p["pause_severities"]) == {"high", "critical"}
    assert "secrets" in p["category_labels"]


def test_export_raw_and_redacted(high_client):
    _pause(high_client)
    raw = high_client.get("/export/jsonl?mode=raw").text.strip().splitlines()
    red = high_client.get("/export/jsonl?mode=redacted").text.strip().splitlines()
    assert len(raw) >= 1 and len(raw) == len(red)
    import json
    assert all(json.loads(l)["content"]["text_preview"] == "[REDACTED]" for l in red)

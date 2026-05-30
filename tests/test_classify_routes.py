"""
Integration tests for POST /classify/prompt and /classify/response routes (CLASS-05).

All tests use the `client` fixture (MockGuardAdapter + MockScannerAdapter) so they
run offline — no live Ollama required.

Additional tests use overridden fixtures (SecretsFoundScannerAdapter, UnavailableGuardAdapter)
to verify scanner findings and fail-closed behavior at the route level.
"""
import pytest

# ---------------------------------------------------------------------------
# /classify/prompt tests
# ---------------------------------------------------------------------------

def test_classify_prompt_safe_content_returns_safe(client):
    """POST /classify/prompt with benign text → 200 with label='safe'."""
    resp = client.post("/classify/prompt", json={"text": "how do I bake a cake"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "safe"
    assert body["llama_guard_label"] == "safe"


def test_classify_prompt_returns_classification_block_shape(client):
    """POST /classify/prompt returns all 7 required keys."""
    resp = client.post("/classify/prompt", json={"text": "hello world"})
    assert resp.status_code == 200
    body = resp.json()
    required_keys = {
        "label",
        "overall_severity",
        "categories",
        "llama_guard_label",
        "llama_guard_categories",
        "deterministic_findings",
        "confidence",
    }
    missing = required_keys - set(body.keys())
    assert not missing, f"Response missing keys: {missing}"


def test_classify_prompt_overall_severity_none_for_safe(client):
    """POST /classify/prompt with safe mock → overall_severity='none'."""
    resp = client.post("/classify/prompt", json={"text": "what is the weather today"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_severity"] == "none"
    assert body["categories"] == []
    assert body["deterministic_findings"] == []


def test_classify_prompt_with_optional_conversation_id(client):
    """POST /classify/prompt accepts optional conversation_id without error."""
    resp = client.post(
        "/classify/prompt",
        json={"text": "hello", "conversation_id": "test-conv-123"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /classify/response tests
# ---------------------------------------------------------------------------

def test_classify_response_safe_returns_safe(client):
    """POST /classify/response with benign text → 200 with label='safe'."""
    resp = client.post(
        "/classify/response",
        json={"text": "Here is a chocolate cake recipe.", "prompt_context": "how do I bake a cake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "safe"
    assert body["llama_guard_label"] == "safe"


def test_classify_response_returns_classification_block_shape(client):
    """POST /classify/response returns all 7 required keys."""
    resp = client.post(
        "/classify/response",
        json={"text": "The weather is sunny today.", "prompt_context": "what's the weather?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    required_keys = {
        "label",
        "overall_severity",
        "categories",
        "llama_guard_label",
        "llama_guard_categories",
        "deterministic_findings",
        "confidence",
    }
    missing = required_keys - set(body.keys())
    assert not missing, f"Response missing keys: {missing}"


def test_classify_response_without_prompt_context(client):
    """POST /classify/response without prompt_context → 200 (prompt_context defaults to empty)."""
    resp = client.post("/classify/response", json={"text": "some response text"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "safe"


# ---------------------------------------------------------------------------
# Fail-closed / scanner finding tests
# ---------------------------------------------------------------------------

def test_classify_prompt_with_scanner_finding_guard_unavailable(
    client_guard_unavailable_scanner_high,
):
    """
    /classify/prompt with UnavailableGuard + SecretsFoundScanner → 200 with
    overall_severity='high', label='unknown', findings non-empty.

    This is the Phase 2 fail-closed path: /classify returns the block with high severity
    and unknown guard label. The 503 enforcement is applied in chat.py (Phase 02-03).
    """
    resp = client_guard_unavailable_scanner_high.post(
        "/classify/prompt",
        json={"text": "AKIA1234567890ABCDEF"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "unknown"
    assert body["llama_guard_label"] == "unknown"
    assert body["overall_severity"] == "high"
    assert len(body["deterministic_findings"]) > 0


def test_classify_prompt_guard_unavailable_scanner_clean(
    client_guard_unavailable_scanner_low,
):
    """
    /classify/prompt with UnavailableGuard + empty scanner → 200 with
    overall_severity='none', label='unknown'.

    Guard-down + scanner-clean → block reflects no threat; caller can proceed.
    """
    resp = client_guard_unavailable_scanner_low.post(
        "/classify/prompt",
        json={"text": "hello world"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "unknown"
    assert body["overall_severity"] == "none"
    assert body["deterministic_findings"] == []


def test_classify_response_guard_unavailable_scanner_high(
    client_guard_unavailable_scanner_high,
):
    """
    /classify/response with UnavailableGuard + SecretsFoundScanner → 200 with
    overall_severity='high', label='unknown'.

    Verifies scanner findings propagate on the response-side classification path.
    """
    resp = client_guard_unavailable_scanner_high.post(
        "/classify/response",
        json={
            "text": "AKIA1234567890ABCDEF",
            "prompt_context": "what is my AWS key?",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_severity"] == "high"
    assert body["label"] == "unknown"
    assert len(body["deterministic_findings"]) > 0

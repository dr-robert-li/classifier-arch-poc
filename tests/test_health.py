"""
Tests for GET /health (expected response shape; no live Ollama required).

The health endpoint must:
- Return HTTP 200 always (even when Ollama is down — degraded, not 500).
- Include expected top-level keys: status, gateway, ollama_process, assistant_model, model_name.
- gateway is always "ok".
- status is one of: "ok" | "degraded".
"""


def test_health_returns_200_with_expected_shape(client):
    """GET /health returns 200 with the expected response structure."""
    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()

    for key in ("status", "gateway", "ollama_process", "assistant_model", "model_name"):
        assert key in body, f"Missing key '{key}' in health response: {body}"

    assert body["gateway"] == "ok"
    assert body["status"] in ("ok", "degraded"), f"Unexpected status: {body['status']}"


def test_health_with_mock_adapter_reports_ok(client):
    """With MockAssistantAdapter, health should report both ollama and model as ok."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    # MockAssistantAdapter.health() returns ollama_process='ok', assistant_model='ok'
    assert body["status"] == "ok"
    assert body["ollama_process"] == "ok"
    assert body["assistant_model"] == "ok"


def test_health_with_unavailable_adapter_returns_degraded(unavailable_client):
    """With UnavailableAssistantAdapter, health should report degraded."""
    response = unavailable_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["gateway"] == "ok"
    assert body["status"] == "degraded"

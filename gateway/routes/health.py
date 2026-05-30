"""
GET /health — gateway and Ollama availability probe.

Returns status fields for the Ollama process and the configured assistant model.
Never returns HTTP 500 — Ollama being down is an expected operational condition,
not an unhandled exception.
"""
from fastapi import APIRouter, Depends

from gateway.routes.chat import get_assistant_adapter

router = APIRouter()


@router.get("/health")
async def health(adapter=Depends(get_assistant_adapter)) -> dict:
    """
    Check gateway liveness and Ollama assistant-model availability.

    Uses the same get_assistant_adapter dependency as /chat so tests can
    override it via app.dependency_overrides to inject mock adapters.

    Response shape:
        {
            "status": "ok" | "degraded",
            "gateway": "ok",
            "ollama_process": "ok" | "error",
            "assistant_model": "ok" | "not_found" | "error",
            "model_name": "<OLLAMA_ASSISTANT_MODEL>"
        }
    """
    try:
        probe = await adapter.health()
    except Exception:
        probe = {
            "ollama_process": "error",
            "assistant_model": "error",
            "model_name": "unknown",
        }

    overall = (
        "ok"
        if probe.get("ollama_process") == "ok" and probe.get("assistant_model") == "ok"
        else "degraded"
    )

    return {
        "status": overall,
        "gateway": "ok",
        "ollama_process": probe.get("ollama_process", "error"),
        "assistant_model": probe.get("assistant_model", "error"),
        "model_name": probe.get("model_name", "unknown"),
    }

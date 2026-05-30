"""
POST /classify/prompt and /classify/response — standalone classification endpoints (CLASS-05).

These endpoints call the classification orchestrator directly. They do NOT touch conversation
or event state — they are standalone classification APIs for admin/testing use.

Dependency providers (get_guard_adapter, get_scanner_adapter) mirror the pattern
established by get_assistant_adapter in gateway/routes/chat.py. Tests override them via
app.dependency_overrides so offline tests never reach live Ollama.

NOTE: The router is declared here (even if initially empty after Task 2 wiring) so main.py
can import and mount it. Handlers are added in Task 3.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from gateway.classification.orchestrator import classify_text

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency injection providers (mirrors get_assistant_adapter pattern)
# ---------------------------------------------------------------------------

def get_guard_adapter(request: Request):
    """
    FastAPI dependency returning the guard adapter from app.state.

    Tests override via app.dependency_overrides[get_guard_adapter] to inject
    MockGuardAdapter without touching the real OllamaGuardAdapter/Ollama service.
    """
    return request.app.state.guard_adapter


def get_scanner_adapter(request: Request):
    """
    FastAPI dependency returning the scanner adapter from app.state.

    Tests override via app.dependency_overrides[get_scanner_adapter] to inject
    MockScannerAdapter without triggering live DLP scan side-effects.
    """
    return request.app.state.scanner_adapter


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ClassifyPromptRequest(BaseModel):
    text: str
    conversation_id: str | None = None


class ClassifyResponseRequest(BaseModel):
    text: str
    prompt_context: str | None = None


# ---------------------------------------------------------------------------
# Route handlers (Task 3 — added to this router)
# ---------------------------------------------------------------------------

@router.post("/classify/prompt")
async def classify_prompt(
    body: ClassifyPromptRequest,
    guard_adapter=Depends(get_guard_adapter),
    scanner_adapter=Depends(get_scanner_adapter),
) -> dict:
    """
    Classify an inbound prompt using Llama Guard 3 + DLP scanner.

    Returns a classification block with a top-level 'label' key equal to
    llama_guard_label, suitable for use in the safety dashboard.
    """
    messages_for_guard = [{"role": "user", "content": body.text}]
    block = await classify_text(
        text=body.text,
        messages_for_guard=messages_for_guard,
        direction="prompt",
        guard_adapter=guard_adapter,
        scanner_adapter=scanner_adapter,
    )
    return {"label": block["llama_guard_label"], **block}


@router.post("/classify/response")
async def classify_response(
    body: ClassifyResponseRequest,
    guard_adapter=Depends(get_guard_adapter),
    scanner_adapter=Depends(get_scanner_adapter),
) -> dict:
    """
    Classify an assistant response using Llama Guard 3 + DLP scanner.

    Builds the correct two-message array (user prompt_context + assistant text)
    for response-side classification so the guard model assesses the assistant turn.
    See Pitfall 3 in 02-RESEARCH.md: last message must be assistant role.
    """
    prompt_ctx = body.prompt_context or ""
    messages_for_guard = [
        {"role": "user", "content": prompt_ctx},
        {"role": "assistant", "content": body.text},
    ]
    block = await classify_text(
        text=body.text,
        messages_for_guard=messages_for_guard,
        direction="response",
        guard_adapter=guard_adapter,
        scanner_adapter=scanner_adapter,
    )
    return {"label": block["llama_guard_label"], **block}

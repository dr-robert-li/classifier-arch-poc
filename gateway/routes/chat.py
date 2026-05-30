"""
POST /chat — the core gateway route (GATE-01, GATE-02, REL-03).

Implements the exact capture order from RESEARCH "Capture Order for /chat":
1.  Resolve/generate conversation_id; upsert conversations row.
2.  Compute turn_index from completed response rows.
3.  Compute prompt + response message_ids (deterministic UUID5).
4.  Generate fresh per-request correlation_id (UUID4).
5.  INSERT PROMPT messages row (durable, before any events that reference it).
6.  Write prompt.received event (references prompt message_id).
7.  Write llm.request.started event.
8.  Call OllamaAssistantAdapter.chat() — if it raises, write system.error + HTTP 503.
9.  INSERT RESPONSE messages row (durable, BEFORE the response events).
10. Write llm.response.generated event (references response message_id).
11. Write response.delivered event.
12. Return HTTP 200 with conversation_id, message_id (response), response, model.

FK-safety invariant: the messages row an event references MUST be inserted before
that event. foreign_keys=ON with no DEFERRABLE enforcement means INSERT OR IGNORE
silently drops events that violate the FK — so insert rows BEFORE events.

Phase 1: include_history=False — only the current user message is sent to Ollama.
Failure path (503/REL-03) is implemented in plan 01-02.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gateway.adapters.protocols import IAssistantAdapter
from gateway.audit.ids import make_message_id
from gateway.audit.schema import build_event

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[Message]
    include_history: bool = False  # Phase 1 always False; flag for future history replay


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str    # response message UUID5
    response: str
    model: str


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

def get_assistant_adapter(request: Request) -> IAssistantAdapter:
    """
    FastAPI dependency returning the assistant adapter from app.state.

    Tests override this via app.dependency_overrides[get_assistant_adapter]
    to inject MockAssistantAdapter without touching the real Ollama service.
    """
    return request.app.state.assistant_adapter


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    adapter: IAssistantAdapter = Depends(get_assistant_adapter),
) -> ChatResponse | JSONResponse:
    """
    Route a chat turn through the gateway with full prompt/response capture.
    """
    from gateway.adapters.ollama_assistant import OllamaUnavailableError

    sink = request.app.state.event_sink
    settings = request.app.state.settings

    # --- Step 1: Resolve conversation_id; upsert conversations row ---------------
    conversation_id: str = body.conversation_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sink.upsert_conversation(
        conversation_id=conversation_id,
        created_at=now,
        assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
        status="active",
    )

    # --- Step 2: Compute turn_index from completed response rows -----------------
    turn_index = sink.count_completed_responses(conversation_id)

    # --- Step 3: Compute deterministic message_ids --------------------------------
    prompt_message_id = make_message_id(conversation_id, turn_index, "prompt")
    response_message_id = make_message_id(conversation_id, turn_index, "response")

    # --- Step 4: Fresh per-request correlation_id (UUID4) -----------------------
    correlation_id = str(uuid.uuid4())

    # Collect current user prompt text
    prompt_text = body.messages[-1].content if body.messages else ""

    # --- Step 5: INSERT PROMPT messages row (DURABLE — before any events) -------
    # This must precede prompt.received and llm.request.started (both reference
    # prompt_message_id). FK-safety invariant applies immediately.
    sink.insert_message(
        message_id=prompt_message_id,
        conversation_id=conversation_id,
        turn_index=turn_index,
        direction="prompt",
        content=prompt_text,
        created_at=now,
    )

    # --- Steps 6-7: Write prompt events (DURABLE, before Ollama call) -----------
    # Pitfall 3 guard: prompt.received MUST be written before the Ollama call.
    prompt_event = build_event(
        event_type="prompt.received",
        conversation_id=conversation_id,
        message_id=prompt_message_id,
        correlation_id=correlation_id,
        direction="prompt",
        text=prompt_text,
        assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
    )
    sink.write_event(prompt_event)

    llm_start_event = build_event(
        event_type="llm.request.started",
        conversation_id=conversation_id,
        message_id=prompt_message_id,
        correlation_id=correlation_id,
        direction="prompt",
        text=prompt_text,
        assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
    )
    sink.write_event(llm_start_event)

    # --- Step 8: Call the assistant model via the adapter ------------------------
    # Phase 1 sends only the current user message (include_history=False).
    messages_for_ollama = [m.model_dump() for m in body.messages]
    try:
        result = await adapter.chat(messages_for_ollama)
    except OllamaUnavailableError as exc:
        # REL-03: write system.error event; audit is preserved even on failure.
        # Failure path fully implemented in plan 01-02; here we handle the minimal case.
        error_event = build_event(
            event_type="system.error",
            conversation_id=conversation_id,
            message_id=prompt_message_id,
            correlation_id=correlation_id,
            direction="prompt",
            text="[error — content not generated]",
            assistant_model=settings.ollama_assistant_model,
            guard_model=settings.ollama_guard_model,
        )
        sink.write_event(error_event)
        return JSONResponse(
            status_code=503,
            content={
                "conversation_id": conversation_id,
                "message_id": prompt_message_id,
                "error": "assistant model unavailable",
                "audited": True,
            },
        )

    response_text = result["content"]
    response_model = result["model"]

    # --- Step 9: INSERT RESPONSE messages row BEFORE response events -------------
    # BLOCKER 2 fix: llm.response.generated + response.delivered reference
    # response_message_id. If written before this row, the FK would fail and
    # INSERT OR IGNORE would silently drop both events (rowcount 0, no JSONL).
    response_now = datetime.now(timezone.utc).isoformat()
    sink.insert_message(
        message_id=response_message_id,
        conversation_id=conversation_id,
        turn_index=turn_index,
        direction="response",
        content=response_text,
        created_at=response_now,
    )

    # --- Steps 10-11: Write response events (reference response_message_id) -----
    llm_response_event = build_event(
        event_type="llm.response.generated",
        conversation_id=conversation_id,
        message_id=response_message_id,
        correlation_id=correlation_id,
        direction="response",
        text=response_text,
        assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
    )
    sink.write_event(llm_response_event)

    delivered_event = build_event(
        event_type="response.delivered",
        conversation_id=conversation_id,
        message_id=response_message_id,
        correlation_id=correlation_id,
        direction="response",
        text=response_text,
        assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
    )
    sink.write_event(delivered_event)

    # --- Step 12: Return HTTP 200 -----------------------------------------------
    return ChatResponse(
        conversation_id=conversation_id,
        message_id=response_message_id,
        response=response_text,
        model=response_model,
    )

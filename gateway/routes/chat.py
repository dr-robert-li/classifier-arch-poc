"""
POST /chat — the core gateway route (GATE-01/02, REL-03, CLASS-01/02, APPR-01/02).

Capture order (FK-safe — message rows inserted before any event that references them):
1.  Resolve/generate conversation_id; upsert conversations row.
2.  turn_index from completed response rows; deterministic UUID5 message_ids.
3.  INSERT PROMPT messages row.
4.  Classify the PROMPT (Llama Guard 3 + DLP scanner) BEFORE generation.
5.  Write prompt.received + prompt.classification.started/completed (carry the classification).
6.  If prompt severity is high/critical -> PAUSE: create an approval request, write
    approval.requested, and return HTTP 202 WITHOUT calling the model (APPR-01).
7.  Otherwise call the assistant model. On failure -> system.error + HTTP 503 (REL-03).
8.  INSERT RESPONSE messages row; classify the RESPONSE BEFORE delivery.
9.  Write llm.response.generated + response.classification.started/completed.
10. If response severity is high/critical -> PAUSE: withhold the response, write
    response.blocked + approval.requested, return HTTP 202 (APPR-02).
11. Otherwise write response.delivered and return HTTP 200 with the response.

The withheld content + resume context for a paused turn is held in app.state.pending
(in-process, single-process POC). Approval rows persist to SQLite for the audit trail.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gateway.adapters.protocols import IAssistantAdapter
from gateway.audit.ids import make_message_id
from gateway.audit.schema import build_event
from gateway.classification.orchestrator import classify_text
from gateway import governance

router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[Message]
    include_history: bool = False


def get_assistant_adapter(request: Request) -> IAssistantAdapter:
    """Dependency returning the assistant adapter from app.state (test-overridable)."""
    return request.app.state.assistant_adapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_classification_events(sink, *, side, conversation_id, message_id, correlation_id,
                                 text, models, block):
    """Emit {side}.classification.started + .completed events carrying the block."""
    for suffix in ("started", "completed"):
        sink.write_event(build_event(
            event_type=f"{side}.classification.{suffix}",
            conversation_id=conversation_id,
            message_id=message_id,
            correlation_id=correlation_id,
            direction=side,
            text=text,
            assistant_model=models[0],
            guard_model=models[1],
            classification=block,
        ))


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    adapter: IAssistantAdapter = Depends(get_assistant_adapter),
):
    """Route a chat turn through the gateway with capture, classification, and pause."""
    from gateway.adapters.ollama_assistant import OllamaUnavailableError

    sink = request.app.state.event_sink
    settings = request.app.state.settings
    guard_adapter = request.app.state.guard_adapter
    scanner_adapter = request.app.state.scanner_adapter
    pending = request.app.state.pending
    conn = sink._conn
    models = (settings.ollama_assistant_model, settings.ollama_guard_model)

    # --- Step 1-3: identities + prompt message row ------------------------------
    conversation_id = body.conversation_id or str(uuid.uuid4())
    now = _now()
    sink.upsert_conversation(
        conversation_id=conversation_id, created_at=now,
        assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model, status="active",
    )
    turn_index = sink.count_completed_responses(conversation_id)
    prompt_message_id = make_message_id(conversation_id, turn_index, "prompt")
    response_message_id = make_message_id(conversation_id, turn_index, "response")
    correlation_id = str(uuid.uuid4())
    prompt_text = body.messages[-1].content if body.messages else ""
    messages_for_ollama = [m.model_dump() for m in body.messages]

    sink.insert_message(
        message_id=prompt_message_id, conversation_id=conversation_id,
        turn_index=turn_index, direction="prompt", content=prompt_text, created_at=now,
    )

    # --- Step 4: classify the PROMPT before generation (CLASS-01) ---------------
    block_p = await classify_text(
        text=prompt_text,
        messages_for_guard=[{"role": "user", "content": prompt_text}],
        direction="prompt", guard_adapter=guard_adapter, scanner_adapter=scanner_adapter,
    )
    sev_p = block_p["overall_severity"]

    # --- Step 5: prompt events carry the classification -------------------------
    sink.write_event(build_event(
        event_type="prompt.received", conversation_id=conversation_id,
        message_id=prompt_message_id, correlation_id=correlation_id, direction="prompt",
        text=prompt_text, assistant_model=models[0], guard_model=models[1],
        classification=block_p,
    ))
    _write_classification_events(
        sink, side="prompt", conversation_id=conversation_id, message_id=prompt_message_id,
        correlation_id=correlation_id, text=prompt_text, models=models, block=block_p,
    )

    # --- Step 6: PAUSE prompt before generation if high/critical (APPR-01) ------
    if governance.is_paused(sev_p):
        approval_id = str(uuid.uuid4())
        ev = build_event(
            event_type="approval.requested", conversation_id=conversation_id,
            message_id=prompt_message_id, correlation_id=correlation_id, direction="prompt",
            text=prompt_text, assistant_model=models[0], guard_model=models[1],
            classification=block_p, policy={"decision": "pause"},
            approval={"required": True, "approval_id": approval_id, "status": "pending"},
        )
        sink.write_event(ev)
        governance.insert_approval_request(conn, approval_id, ev["event_id"])
        pending[approval_id] = {
            "side": "prompt", "conversation_id": conversation_id,
            "prompt_message_id": prompt_message_id, "response_message_id": response_message_id,
            "turn_index": turn_index, "correlation_id": correlation_id,
            "messages_for_ollama": messages_for_ollama, "prompt_text": prompt_text,
            "classification": block_p, "event_id": ev["event_id"],
        }
        return JSONResponse(status_code=202, content={
            "status": "paused", "side": "prompt", "approval_id": approval_id,
            "conversation_id": conversation_id, "severity": sev_p,
            "categories": block_p["categories"],
            "message": "Prompt paused for human approval before the model runs.",
        })

    # --- Step 7: generate ------------------------------------------------------
    try:
        result = await adapter.chat(messages_for_ollama)
    except OllamaUnavailableError:
        sink.write_event(build_event(
            event_type="system.error", conversation_id=conversation_id,
            message_id=prompt_message_id, correlation_id=correlation_id, direction="prompt",
            text="[error — content not generated]", assistant_model=models[0],
            guard_model=models[1], classification=block_p,
        ))
        return JSONResponse(status_code=503, content={
            "conversation_id": conversation_id, "message_id": prompt_message_id,
            "error": "assistant model unavailable", "audited": True,
        })

    response_text = result["content"]
    response_model = result["model"]

    # --- Step 8: response message row + classify response (CLASS-02) -----------
    sink.insert_message(
        message_id=response_message_id, conversation_id=conversation_id,
        turn_index=turn_index, direction="response", content=response_text, created_at=_now(),
    )
    block_r = await classify_text(
        text=response_text,
        messages_for_guard=[
            {"role": "user", "content": prompt_text},
            {"role": "assistant", "content": response_text},
        ],
        direction="response", guard_adapter=guard_adapter, scanner_adapter=scanner_adapter,
    )
    sev_r = block_r["overall_severity"]

    sink.write_event(build_event(
        event_type="llm.response.generated", conversation_id=conversation_id,
        message_id=response_message_id, correlation_id=correlation_id, direction="response",
        text=response_text, assistant_model=models[0], guard_model=models[1],
        classification=block_r,
    ))
    _write_classification_events(
        sink, side="response", conversation_id=conversation_id, message_id=response_message_id,
        correlation_id=correlation_id, text=response_text, models=models, block=block_r,
    )

    # --- Step 10: PAUSE response before delivery if high/critical (APPR-02) -----
    if governance.is_paused(sev_r):
        approval_id = str(uuid.uuid4())
        ev = build_event(
            event_type="response.blocked", conversation_id=conversation_id,
            message_id=response_message_id, correlation_id=correlation_id, direction="response",
            text=response_text, assistant_model=models[0], guard_model=models[1],
            classification=block_r, policy={"decision": "pause"},
            approval={"required": True, "approval_id": approval_id, "status": "pending"},
        )
        sink.write_event(ev)
        governance.insert_approval_request(conn, approval_id, ev["event_id"])
        pending[approval_id] = {
            "side": "response", "conversation_id": conversation_id,
            "response_message_id": response_message_id, "correlation_id": correlation_id,
            "response_text": response_text, "classification": block_r, "event_id": ev["event_id"],
        }
        return JSONResponse(status_code=202, content={
            "status": "paused", "side": "response", "approval_id": approval_id,
            "conversation_id": conversation_id, "severity": sev_r,
            "categories": block_r["categories"],
            "message": "Response withheld pending human approval.",
        })

    # --- Step 11: deliver ------------------------------------------------------
    sink.write_event(build_event(
        event_type="response.delivered", conversation_id=conversation_id,
        message_id=response_message_id, correlation_id=correlation_id, direction="response",
        text=response_text, assistant_model=models[0], guard_model=models[1],
        classification=block_r,
    ))
    return {
        "status": "delivered", "conversation_id": conversation_id,
        "message_id": response_message_id, "response": response_text,
        "model": response_model, "severity": sev_r, "categories": block_r["categories"],
    }

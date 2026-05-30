"""
Admin / governance routes (APPR-03..08, AUDIT-03, GATE-05).

- GET  /approvals                         list approval requests (optionally ?status=pending)
- POST /approvals/{id}/approve            approve a paused interaction
- POST /approvals/{id}/reject             reject a paused interaction
- POST /approvals/{id}/redact-resume      redact sensitive content and resume
- POST /approvals/{id}/false-positive     mark finding as a false positive (treat as allow)
- POST /approvals/{id}/escalate           escalate (keep pending, flag for senior review)
- GET  /policy                            active pause policy + plain-language labels
- GET  /export/jsonl?mode=raw|redacted    export the audit log (redacted strips previews)

Resume context for a paused turn lives in app.state.pending (in-process POC). On approve /
redact-resume of a PROMPT pause the model is run now; on a RESPONSE pause the withheld text is
released (optionally redacted). Every action persists an admin_actions row + an approval.* event.
"""
import json
import uuid

from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from gateway.audit.schema import build_event
from gateway.auth import require_admin, require_user
from gateway.routes.chat import get_assistant_adapter
from gateway import governance

router = APIRouter()

ADMIN_ID = "safety-admin"


def _ctx_or_404(request: Request, approval_id: str):
    pending = request.app.state.pending
    ctx = pending.get(approval_id)
    if ctx is None:
        return None, JSONResponse(status_code=404, content={
            "error": "approval not found or already resolved",
            "approval_id": approval_id,
        })
    return ctx, None


def _emit_admin_event(sink, settings, *, event_type, ctx, text, classification=None,
                      contains_redactions=False, approval_status="resolved", approval_id=None):
    # Anchor to a message row that DEFINITELY exists: prompt-side always has the prompt
    # message; response-side always has the response message. Using response_message_id for a
    # prompt-side reject/escalate (no generation) would FK-fail and silently drop the event.
    msg_id = ctx.get("prompt_message_id") or ctx.get("response_message_id")
    side = ctx["side"]
    ev = build_event(
        event_type=event_type, conversation_id=ctx["conversation_id"],
        message_id=msg_id, correlation_id=ctx["correlation_id"], direction=side,
        text=text, assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
        classification=classification or ctx.get("classification"),
        contains_redactions=contains_redactions,
        approval={"required": True, "approval_id": approval_id, "status": approval_status},
    )
    sink.write_event(ev)
    return ev


async def _generate_and_deliver(request, ctx, *, messages, adapter, redacted=False):
    """Prompt-side resume: run the model now and deliver (used by approve / redact-resume / FP)."""
    sink = request.app.state.event_sink
    settings = request.app.state.settings
    result = await adapter.chat(messages)
    response_text = result["content"]
    from datetime import datetime, timezone
    sink.insert_message(
        message_id=ctx["response_message_id"], conversation_id=ctx["conversation_id"],
        turn_index=ctx["turn_index"], direction="response", content=response_text,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    sink.write_event(build_event(
        event_type="llm.response.generated", conversation_id=ctx["conversation_id"],
        message_id=ctx["response_message_id"], correlation_id=ctx["correlation_id"],
        direction="response", text=response_text, assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model,
    ))
    sink.write_event(build_event(
        event_type="response.delivered", conversation_id=ctx["conversation_id"],
        message_id=ctx["response_message_id"], correlation_id=ctx["correlation_id"],
        direction="response", text=response_text, assistant_model=settings.ollama_assistant_model,
        guard_model=settings.ollama_guard_model, contains_redactions=redacted,
    ))
    return response_text


@router.get("/approvals")
async def list_approvals(request: Request, status: str | None = Query(default=None), _role: str = Depends(require_user)):
    conn = request.app.state.event_sink._conn
    return governance.list_approvals(conn, status)


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, request: Request, _role: str = Depends(require_admin), assistant=Depends(get_assistant_adapter)):
    ctx, err = _ctx_or_404(request, approval_id)
    if err:
        return err
    sink = request.app.state.event_sink
    settings = request.app.state.settings
    conn = sink._conn
    delivered = None
    if ctx["side"] == "prompt":
        delivered = await _generate_and_deliver(request, ctx, messages=ctx["messages_for_ollama"], adapter=assistant)
    else:
        sink.write_event(build_event(
            event_type="response.delivered", conversation_id=ctx["conversation_id"],
            message_id=ctx["response_message_id"], correlation_id=ctx["correlation_id"],
            direction="response", text=ctx["response_text"],
            assistant_model=settings.ollama_assistant_model, guard_model=settings.ollama_guard_model,
        ))
        delivered = ctx["response_text"]
    _emit_admin_event(sink, settings, event_type="approval.approved", ctx=ctx,
                      text="[approved by admin]", approval_status="approved", approval_id=approval_id)
    governance.insert_admin_action(conn, action_id=str(uuid.uuid4()), approval_id=approval_id,
                                   event_id=ctx["event_id"], action_type="approve", actor_id=ADMIN_ID)
    governance.resolve_approval_request(conn, approval_id, "approved", "approved by admin")
    request.app.state.pending.pop(approval_id, None)
    return {"status": "approved", "approval_id": approval_id, "response": delivered}


@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, request: Request, _role: str = Depends(require_admin)):
    ctx, err = _ctx_or_404(request, approval_id)
    if err:
        return err
    sink = request.app.state.event_sink
    settings = request.app.state.settings
    conn = sink._conn
    if ctx["side"] == "response":
        _emit_admin_event(sink, settings, event_type="response.blocked", ctx=ctx,
                          text="[response blocked — rejected by admin]", approval_status="rejected",
                          approval_id=approval_id)
    _emit_admin_event(sink, settings, event_type="approval.rejected", ctx=ctx,
                      text="[rejected by admin]", approval_status="rejected", approval_id=approval_id)
    governance.insert_admin_action(conn, action_id=str(uuid.uuid4()), approval_id=approval_id,
                                   event_id=ctx["event_id"], action_type="reject", actor_id=ADMIN_ID)
    governance.resolve_approval_request(conn, approval_id, "rejected", "rejected by admin")
    request.app.state.pending.pop(approval_id, None)
    return {"status": "rejected", "approval_id": approval_id}


@router.post("/approvals/{approval_id}/redact-resume")
async def redact_resume(approval_id: str, request: Request, _role: str = Depends(require_admin), assistant=Depends(get_assistant_adapter)):
    ctx, err = _ctx_or_404(request, approval_id)
    if err:
        return err
    sink = request.app.state.event_sink
    settings = request.app.state.settings
    conn = sink._conn
    if ctx["side"] == "prompt":
        original = ctx["prompt_text"]
        redacted_text, spans = governance.redact(original)
        msgs = list(ctx["messages_for_ollama"])
        if msgs:
            msgs[-1] = {**msgs[-1], "content": redacted_text}
        delivered = await _generate_and_deliver(request, ctx, messages=msgs, adapter=assistant, redacted=True)
        ev_id = ctx["event_id"]
    else:
        original = ctx["response_text"]
        redacted_text, spans = governance.redact(original)
        sink.write_event(build_event(
            event_type="response.delivered", conversation_id=ctx["conversation_id"],
            message_id=ctx["response_message_id"], correlation_id=ctx["correlation_id"],
            direction="response", text=redacted_text,
            assistant_model=settings.ollama_assistant_model, guard_model=settings.ollama_guard_model,
            contains_redactions=True,
        ))
        delivered = redacted_text
        ev_id = ctx["event_id"]
    governance.insert_redaction(conn, redaction_id=str(uuid.uuid4()), event_id=ev_id,
                                original_sha256=governance._sha256(original),
                                redacted_sha256=governance._sha256(redacted_text),
                                spans=json.dumps(spans))
    _emit_admin_event(sink, settings, event_type="approval.redacted_resumed", ctx=ctx,
                      text=redacted_text, contains_redactions=True,
                      approval_status="redacted_resumed", approval_id=approval_id)
    governance.insert_admin_action(conn, action_id=str(uuid.uuid4()), approval_id=approval_id,
                                   event_id=ev_id, action_type="redact-resume", actor_id=ADMIN_ID,
                                   notes=f"{len(spans)} span(s) redacted")
    governance.resolve_approval_request(conn, approval_id, "redacted_resumed",
                                        f"{len(spans)} span(s) redacted and resumed")
    request.app.state.pending.pop(approval_id, None)
    return {"status": "redacted_resumed", "approval_id": approval_id,
            "redacted": delivered, "spans": spans}


@router.post("/approvals/{approval_id}/false-positive")
async def false_positive(approval_id: str, request: Request, _role: str = Depends(require_admin), assistant=Depends(get_assistant_adapter)):
    ctx, err = _ctx_or_404(request, approval_id)
    if err:
        return err
    sink = request.app.state.event_sink
    settings = request.app.state.settings
    conn = sink._conn
    delivered = None
    if ctx["side"] == "prompt":
        delivered = await _generate_and_deliver(request, ctx, messages=ctx["messages_for_ollama"], adapter=assistant)
    else:
        sink.write_event(build_event(
            event_type="response.delivered", conversation_id=ctx["conversation_id"],
            message_id=ctx["response_message_id"], correlation_id=ctx["correlation_id"],
            direction="response", text=ctx["response_text"],
            assistant_model=settings.ollama_assistant_model, guard_model=settings.ollama_guard_model,
        ))
        delivered = ctx["response_text"]
    _emit_admin_event(sink, settings, event_type="approval.false_positive_marked", ctx=ctx,
                      text="[marked false positive by admin]", approval_status="false_positive",
                      approval_id=approval_id)
    governance.insert_admin_action(conn, action_id=str(uuid.uuid4()), approval_id=approval_id,
                                   event_id=ctx["event_id"], action_type="false-positive",
                                   actor_id=ADMIN_ID, notes="classifier false positive")
    governance.resolve_approval_request(conn, approval_id, "false_positive",
                                        "marked false positive; allowed")
    request.app.state.pending.pop(approval_id, None)
    return {"status": "false_positive", "approval_id": approval_id, "response": delivered}


@router.post("/approvals/{approval_id}/escalate")
async def escalate(approval_id: str, request: Request, _role: str = Depends(require_admin)):
    ctx, err = _ctx_or_404(request, approval_id)
    if err:
        return err
    sink = request.app.state.event_sink
    settings = request.app.state.settings
    conn = sink._conn
    _emit_admin_event(sink, settings, event_type="approval.escalated", ctx=ctx,
                      text="[escalated by admin]", approval_status="escalated", approval_id=approval_id)
    governance.insert_admin_action(conn, action_id=str(uuid.uuid4()), approval_id=approval_id,
                                   event_id=ctx["event_id"], action_type="escalate", actor_id=ADMIN_ID)
    governance.resolve_approval_request(conn, approval_id, "escalated", "escalated for senior review")
    # Escalated interactions remain withheld; keep context so it can still be resolved later.
    return {"status": "escalated", "approval_id": approval_id}


@router.get("/policy")
async def get_policy(request: Request, _role: str = Depends(require_user)):
    return {**governance.POLICY, "category_labels": governance.CATEGORY_LABELS}


@router.get("/export/jsonl")
async def export_jsonl(request: Request, mode: str = Query(default="raw"), _role: str = Depends(require_admin)):
    """Export the append-only audit log. mode=redacted strips text previews."""
    settings = request.app.state.settings
    path = settings.jsonl_audit_path
    lines: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if mode == "redacted":
                    try:
                        ev = json.loads(line)
                        if "content" in ev:
                            ev["content"]["text_preview"] = "[REDACTED]"
                            ev["content"]["contains_redactions"] = True
                        # strip raw matched values defensively (already sha256, but be safe)
                        line = json.dumps(ev)
                    except Exception:
                        continue
                lines.append(line)
    except FileNotFoundError:
        return PlainTextResponse("", media_type="application/x-ndjson")
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""),
                             media_type="application/x-ndjson")

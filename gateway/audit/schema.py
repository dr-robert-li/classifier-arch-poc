"""
Audit event schema (AUDIT-01) matching SKILL.md Required Event Schema.

AuditEvent is the canonical Pydantic model for all structured events emitted by the gateway.
build_event() constructs a ready-to-write event dict with Phase 1 default blocks.

Phase 1 defaults for mandatory schema blocks (classification, policy, approval, siem) are
applied here so every event is schema-valid from day one, ready for Phase 2/3 classification
and approval logic to override them.
"""
import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from gateway.audit.ids import make_event_id


# ---------------------------------------------------------------------------
# Phase 1 default constant blocks (RESEARCH Phase 1 Default Values)
# ---------------------------------------------------------------------------

CLASSIFICATION_DEFAULTS: dict[str, Any] = {
    "overall_severity": "none",
    "categories": [],
    "llama_guard_label": "unknown",
    "llama_guard_categories": [],
    "deterministic_findings": [],
    "confidence": 0.0,
}

POLICY_DEFAULTS: dict[str, Any] = {
    "policy_version": "local-poc-v1",
    "decision": "allow",
    "matched_rules": [],
}

APPROVAL_DEFAULTS: dict[str, Any] = {
    "required": False,
    "approval_id": None,
    "status": "not_required",
}

SIEM_DEFAULTS: dict[str, Any] = {
    "schema_version": "1.0",
    "ecs_compatible": True,
    "source": "local-ai-safety-gateway",
}

# ---------------------------------------------------------------------------
# Actor defaults per event type (PLAN Task 1 — CRITICAL: build_event MUST
# populate actor on every event; write_event raises if actor is missing)
# ---------------------------------------------------------------------------

_ACTOR_MAP: dict[str, dict[str, str]] = {
    "prompt.received": {"type": "user", "id": "local-user"},
    "llm.request.started": {"type": "system", "id": "local-ai-safety-gateway"},
    "llm.response.generated": {"type": "assistant", "id": "local-ai-safety-gateway"},
    "response.delivered": {"type": "assistant", "id": "local-ai-safety-gateway"},
    "system.error": {"type": "system", "id": "local-ai-safety-gateway"},
}

_DEFAULT_ACTOR = {"type": "system", "id": "local-ai-safety-gateway"}


# ---------------------------------------------------------------------------
# Pydantic model (for type-checking / OpenAPI docs; not used in write path)
# ---------------------------------------------------------------------------

class ActorBlock(BaseModel):
    type: str  # user | assistant | admin | system
    id: str


class ModelBlock(BaseModel):
    assistant_model: str
    guard_model: str
    provider: str = "ollama"


class ContentBlock(BaseModel):
    direction: str  # prompt | response
    text_sha256: str
    text_preview: str
    contains_redactions: bool = False


class ClassificationBlock(BaseModel):
    overall_severity: str = "none"
    categories: list[str] = []
    llama_guard_label: str = "unknown"
    llama_guard_categories: list[str] = []
    deterministic_findings: list[Any] = []
    confidence: float = 0.0


class PolicyBlock(BaseModel):
    policy_version: str = "local-poc-v1"
    decision: str = "allow"
    matched_rules: list[str] = []


class ApprovalBlock(BaseModel):
    required: bool = False
    approval_id: str | None = None
    status: str = "not_required"


class SiemBlock(BaseModel):
    schema_version: str = "1.0"
    ecs_compatible: bool = True
    source: str = "local-ai-safety-gateway"


class AuditEvent(BaseModel):
    event_id: str
    event_type: str
    timestamp: str
    correlation_id: str
    conversation_id: str
    message_id: str
    actor: ActorBlock
    model: ModelBlock
    content: ContentBlock
    classification: ClassificationBlock = ClassificationBlock()
    policy: PolicyBlock = PolicyBlock()
    approval: ApprovalBlock = ApprovalBlock()
    siem: SiemBlock = SiemBlock()


# ---------------------------------------------------------------------------
# build_event helper
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_event(
    *,
    event_type: str,
    conversation_id: str,
    message_id: str,
    correlation_id: str,
    direction: str,
    text: str,
    assistant_model: str,
    guard_model: str,
    contains_redactions: bool = False,
    timestamp: str | None = None,
    classification: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a complete, schema-valid event dict ready for EventSink.write_event().

    Populates:
    - Deterministic event_id (UUID5 from conversation_id, message_id, event_type).
    - Actor block according to _ACTOR_MAP — MANDATORY; write_event raises if absent.
    - Phase 1 default classification/policy/approval/siem blocks.
    - SHA-256 hash of the full text and a 200-char preview.

    The caller must supply all identity fields (conversation_id, message_id,
    correlation_id) and the text content to hash/preview.
    """
    ts = timestamp or _utc_now()
    event_id = make_event_id(conversation_id, message_id, event_type)

    actor = _ACTOR_MAP.get(event_type, _DEFAULT_ACTOR)

    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": ts,
        "correlation_id": correlation_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "actor": dict(actor),  # copy to avoid mutation
        "model": {
            "assistant_model": assistant_model,
            "guard_model": guard_model,
            "provider": "ollama",
        },
        "content": {
            "direction": direction,
            "text_sha256": _sha256(text),
            "text_preview": text[:200],
            "contains_redactions": contains_redactions,
        },
        "classification": {**CLASSIFICATION_DEFAULTS, **(classification or {})},
        "policy": {**POLICY_DEFAULTS, **(policy or {})},
        "approval": {**APPROVAL_DEFAULTS, **(approval or {})},
        "siem": dict(SIEM_DEFAULTS),
    }

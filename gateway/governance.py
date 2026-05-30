"""
Governance helpers — pause policy, approval-request persistence, admin actions, redaction.

POC scope (local, single process):
- The pause decision is a deterministic code constant (PAUSE_SEVERITIES). Phase 3's "real"
  version would externalise this to a versioned YAML policy file; here it is a constant so the
  demo is reproducible.
- Approval state is persisted to SQLite (approval_requests / admin_actions / redactions) for the
  dashboard + audit trail. The *resume context* (the withheld response text, the original prompt
  messages, the classification) is kept in an in-process dict on app.state.pending because the
  gateway is a single local process for the POC. Restart loses in-flight pauses (acceptable for a
  demo); the audit rows persist.

All writes route through the EventSink's single long-lived connection (sink._conn) to avoid
WAL writer contention. Audit events are written via sink.write_event() so JSONL stays in sync.
"""
from __future__ import annotations

import re
import hashlib
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Pause policy (code constant — Phase 3 would externalise to YAML)
# ---------------------------------------------------------------------------
PAUSE_SEVERITIES: set[str] = {"high", "critical"}

POLICY: dict[str, Any] = {
    "policy_version": "local-poc-v1",
    "pause_severities": sorted(PAUSE_SEVERITIES),
    "description": (
        "Interactions whose overall_severity is high or critical are paused for human "
        "approval before the model generates (prompt side) or before the response is "
        "delivered (response side). Lower severities are logged and allowed."
    ),
}

# Plain-language labels for the non-technical admin UI.
CATEGORY_LABELS: dict[str, str] = {
    "dangerous_behavior": "Dangerous behaviour",
    "illegal_behavior": "Illegal behaviour",
    "child_safety": "Child safety",
    "specialized_advice": "Specialised advice",
    "privacy": "Privacy exposure",
    "intellectual_property": "Intellectual property",
    "hate_discrimination": "Hate / discrimination",
    "self_harm": "Self-harm",
    "sexual_content": "Sexual content",
    "elections": "Election misinformation",
    "secrets": "Leaked secret / credential",
    "dlp": "Sensitive data (DLP)",
    "bias_fairness": "Bias / fairness",
    "unknown": "Other / unclassified",
}


def is_paused(severity: str) -> bool:
    return severity in PAUSE_SEVERITIES


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# SQLite persistence (route everything through the EventSink writer connection)
# ---------------------------------------------------------------------------
def insert_approval_request(conn, approval_id: str, event_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO approval_requests (approval_id, event_id, status, created_at) "
        "VALUES (?, ?, 'pending', ?)",
        (approval_id, event_id, _utc_now()),
    )
    conn.commit()


def resolve_approval_request(conn, approval_id: str, status: str, resolution: str) -> None:
    conn.execute(
        "UPDATE approval_requests SET status = ?, resolved_at = ?, resolution = ? "
        "WHERE approval_id = ?",
        (status, _utc_now(), resolution, approval_id),
    )
    conn.commit()


def insert_admin_action(
    conn, *, action_id: str, approval_id: str, event_id: str | None,
    action_type: str, actor_id: str, notes: str = "",
) -> None:
    conn.execute(
        "INSERT INTO admin_actions (action_id, approval_id, event_id, action_type, actor_id, created_at, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (action_id, approval_id, event_id, action_type, actor_id, _utc_now(), notes),
    )
    conn.commit()


def insert_redaction(
    conn, *, redaction_id: str, event_id: str, original_sha256: str,
    redacted_sha256: str, spans: str,
) -> None:
    conn.execute(
        "INSERT INTO redactions (redaction_id, event_id, original_sha256, redacted_sha256, spans, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (redaction_id, event_id, original_sha256, redacted_sha256, spans, _utc_now()),
    )
    conn.commit()


def list_approvals(conn, status: str | None = None) -> list[dict]:
    """Return approval requests joined with their event payload for the queue UI."""
    if status:
        rows = conn.execute(
            "SELECT a.approval_id, a.event_id, a.status, a.created_at, a.resolved_at, a.resolution, "
            "e.conversation_id, e.event_type, e.severity, e.payload "
            "FROM approval_requests a JOIN events e ON a.event_id = e.event_id "
            "WHERE a.status = ? ORDER BY a.created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT a.approval_id, a.event_id, a.status, a.created_at, a.resolved_at, a.resolution, "
            "e.conversation_id, e.event_type, e.severity, e.payload "
            "FROM approval_requests a JOIN events e ON a.event_id = e.event_id "
            "ORDER BY a.created_at DESC",
        ).fetchall()
    import json
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            payload = {}
        cls = payload.get("classification", {})
        side = payload.get("content", {}).get("direction") or (
            "prompt" if "prompt" in r["event_type"] else "response"
        )
        out.append({
            "approval_id": r["approval_id"],
            "event_id": r["event_id"],
            "status": r["status"],
            "created_at": r["created_at"],
            "resolved_at": r["resolved_at"],
            "resolution": r["resolution"],
            "conversation_id": r["conversation_id"],
            "event_type": r["event_type"],
            "side": side,
            "severity": cls.get("overall_severity", r["severity"]),
            "categories": cls.get("categories", []),
            "category_labels": [CATEGORY_LABELS.get(c, c) for c in cls.get("categories", [])],
            "preview": payload.get("content", {}).get("text_preview", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Redaction (deterministic masking for redact-and-resume)
# ---------------------------------------------------------------------------
_REDACT_PATTERNS: list[tuple[str, str]] = [
    (r"sk-[A-Za-z0-9]{16,}", "openai_key"),
    (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
    (r"ghp_[A-Za-z0-9]{20,}", "github_token"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "jwt"),
    (r"-----BEGIN [A-Z ]+PRIVATE KEY-----", "private_key"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "email"),
    (r"\b(?:\d[ -]*?){13,16}\b", "card_number"),
    (r"\b(?:\+?\d[\d -]{7,}\d)\b", "phone"),
    (r"\b[A-Za-z0-9+/]{32,}={0,2}\b", "high_entropy"),
]


def redact(text: str) -> tuple[str, list[dict]]:
    """
    Mask sensitive substrings. Returns (redacted_text, spans).
    Each span: {category, start, end} — start/end are offsets in the ORIGINAL text.
    """
    spans: list[dict] = []
    for pattern, category in _REDACT_PATTERNS:
        for m in re.finditer(pattern, text):
            spans.append({"category": category, "start": m.start(), "end": m.end()})
    # Apply masks back-to-front so offsets stay valid.
    redacted = text
    for span in sorted(spans, key=lambda s: s["start"], reverse=True):
        redacted = redacted[: span["start"]] + f"‹redacted:{span['category']}›" + redacted[span["end"]:]
    return redacted, spans

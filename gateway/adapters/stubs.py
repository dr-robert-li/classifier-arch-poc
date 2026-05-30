"""
Null (no-op) stub implementations for Phase 2-4 adapters (GATE-04).

These stubs implement the Protocol shapes from adapters/protocols.py so the dependency
injection graph is complete from Phase 1. Phase 2-3 will replace them with real adapters.
"""
from typing import Any


class NullGuardAdapter:
    """Stub guard adapter — always returns 'unknown' with no findings (Phase 2 replaces)."""

    async def classify(self, messages: list[dict], direction: str) -> dict:
        return {
            "overall_severity": "none",
            "categories": [],
            "llama_guard_label": "unknown",
            "llama_guard_categories": [],
            "deterministic_findings": [],
            "confidence": 0.0,
        }


class NullScannerAdapter:
    """Stub DLP/secrets scanner — always returns empty findings (Phase 2 replaces)."""

    def scan(self, text: str) -> list[dict]:
        return []


class NullPolicyAdapter:
    """Stub policy adapter — always returns allow decision (Phase 3 replaces)."""

    def evaluate(self, classification: dict, direction: str) -> dict:
        return {
            "decision": "allow",
            "matched_rules": [],
        }


class NullApprovalAdapter:
    """Stub approval adapter — always returns not_required (Phase 3 replaces)."""

    def request_approval(self, event: dict) -> str:
        return ""

    def resolve_approval(self, approval_id: str, action: str, notes: str = "") -> dict:
        return {
            "approval_id": approval_id,
            "action": action,
            "status": "resolved",
        }

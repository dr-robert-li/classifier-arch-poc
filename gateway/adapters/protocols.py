"""
Adapter Protocol interfaces (GATE-04).

All six adapter interfaces are declared here even though only the assistant adapter and
event sink are implemented in Phase 1. This ensures Phase 2-3 adapters can slot in via
the same dependency injection graph without restructuring.

Interfaces:
- IAssistantAdapter  — calls Ollama /api/chat (OllamaAssistantAdapter in Phase 1)
- IEventSink         — writes audit events to SQLite + JSONL (EventSink)
- IGuardAdapter      — calls Llama Guard 3 for safety classification (Phase 2)
- IScannerAdapter    — deterministic DLP/secrets scanning (Phase 2)
- IPolicyAdapter     — evaluates policy YAML against classifications (Phase 3)
- IApprovalAdapter   — persists and resolves approval requests (Phase 3)
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class IAssistantAdapter(Protocol):
    """Adapter for the assistant language model (e.g. Ollama llama3.1)."""

    async def chat(self, messages: list[dict]) -> dict:
        """
        Send a messages list to the assistant model.

        Returns:
            {'content': str, 'model': str}

        Raises:
            OllamaUnavailableError on connection failure, timeout, or model-not-found.
        """
        ...


@runtime_checkable
class IEventSink(Protocol):
    """Adapter for writing structured audit events."""

    def write_event(self, event: dict) -> bool:
        """
        Write an audit event.

        Returns True if a new row was created (JSONL appended).
        Returns False if the event_id is a genuine PRIMARY-KEY duplicate (JSONL skipped).
        Raises ValueError on missing mandatory NOT NULL fields (caller-contract violation).
        Does NOT raise on SQLite/IO errors (logs and returns False).
        """
        ...


@runtime_checkable
class IGuardAdapter(Protocol):
    """Adapter for the safety classifier (e.g. Llama Guard 3 via Ollama). Phase 2."""

    async def classify(self, messages: list[dict], direction: str) -> dict:
        """
        Classify a prompt or response using the guard model.

        Returns a classification dict compatible with the classification schema block.
        """
        ...


@runtime_checkable
class IScannerAdapter(Protocol):
    """Adapter for deterministic DLP and secrets scanning. Phase 2."""

    def scan(self, text: str) -> list[dict]:
        """
        Scan text for DLP violations and secrets.

        Returns a list of finding dicts: [{'category': str, 'label': str, 'span': ...}]
        """
        ...


@runtime_checkable
class IPolicyAdapter(Protocol):
    """Adapter for evaluating classification findings against the policy YAML. Phase 3."""

    def evaluate(
        self, classification: dict, direction: str
    ) -> dict:
        """
        Evaluate classification results against the loaded policy.

        Returns a policy decision dict: {'decision': str, 'matched_rules': list}
        """
        ...


@runtime_checkable
class IApprovalAdapter(Protocol):
    """Adapter for persisting and resolving approval requests. Phase 3."""

    def request_approval(self, event: dict) -> str:
        """
        Create a pending approval request for a high-severity event.

        Returns the approval_id.
        """
        ...

    def resolve_approval(self, approval_id: str, action: str, notes: str = "") -> dict:
        """
        Resolve an approval request (approve / reject / redact_resume / etc.).

        Returns the resolved approval dict.
        """
        ...

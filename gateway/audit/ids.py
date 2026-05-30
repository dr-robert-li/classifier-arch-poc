"""
Deterministic UUID5 ID helpers for audit events and messages (AUDIT-04).

Using UUID5 (deterministic) rather than UUID4 (random) guarantees:
- The same logical event always yields the same event_id.
- SQLite INSERT OR IGNORE correctly deduplicates replayed events.
- JSONL does not accumulate duplicate lines on retry.

Namespace: uuid.NAMESPACE_DNS (stable, well-known).
"""
import uuid

_NAMESPACE = uuid.NAMESPACE_DNS


def make_event_id(conversation_id: str, message_id: str, event_type: str) -> str:
    """
    Deterministic UUID5 for an audit event.

    Key format: "{conversation_id}:{message_id}:{event_type}"

    Same inputs always produce the same UUID; different event_type values on
    the same message produce distinct IDs (no collision between e.g.
    prompt.received and llm.request.started).
    """
    key = f"{conversation_id}:{message_id}:{event_type}"
    return str(uuid.uuid5(_NAMESPACE, key))


def make_message_id(conversation_id: str, turn_index: int, direction: str) -> str:
    """
    Deterministic UUID5 for a messages row.

    Key format: "{conversation_id}:{turn_index}:{direction}"
    direction: 'prompt' or 'response'

    Because turn_index is derived from the count of COMPLETED response rows,
    a failed turn leaves turn_index unchanged and a retry produces the same
    message_id — enabling the INSERT OR IGNORE idempotency gate on both the
    messages and events tables.
    """
    key = f"{conversation_id}:{turn_index}:{direction}"
    return str(uuid.uuid5(_NAMESPACE, key))

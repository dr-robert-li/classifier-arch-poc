"""
Integration tests for POST /chat (SC-1, SC-3).

SC-1: POST /chat returns an assistant response generated via the adapter.
SC-3: Each turn produces 1 conversations row, 2 messages rows, and exactly 4 events rows
      (prompt.received, llm.request.started, llm.response.generated, response.delivered)
      plus 4 JSONL lines — the ==4 assertion catches BLOCKER 2 (FK-drop regression to 2).

Tests run offline via MockAssistantAdapter (no live Ollama required).
"""
import json


def test_chat_returns_200_with_expected_shape(client):
    """SC-1: POST /chat returns HTTP 200 with conversation_id, message_id, response, model."""
    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hello world"}]},
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert "conversation_id" in body, f"Missing conversation_id in {body}"
    assert "message_id" in body, f"Missing message_id in {body}"
    assert "response" in body, f"Missing response in {body}"
    assert "model" in body, f"Missing model in {body}"
    assert body["response"] == "mock response"
    assert body["model"] == "mock-llama"


def test_chat_returns_supplied_conversation_id(client):
    """If the caller supplies a conversation_id, it should be returned unchanged."""
    cid = "test-conversation-123"
    response = client.post(
        "/chat",
        json={
            "conversation_id": cid,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == cid


def test_chat_generates_conversation_id_if_absent(client):
    """If no conversation_id is supplied, the gateway generates and returns one."""
    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "auto id test"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]  # non-empty
    # Should look like a UUID
    assert len(body["conversation_id"]) == 36


def test_chat_writes_exactly_1_conversation_2_messages_4_events(client, tmp_path):
    """
    SC-3: One /chat turn must write:
    - 1 conversations row
    - 2 messages rows (1 prompt, 1 response)
    - EXACTLY 4 events rows with all four event_types present
    - JSONL line count == 4

    The ==4 assertion is BLOCKER 2 guard: if the response messages row is inserted
    AFTER the response events, the FK causes INSERT OR IGNORE to silently drop
    llm.response.generated and response.delivered (count drops to 2, not 4).
    """
    import sqlite3
    from gateway.settings import settings

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "audit test"}]},
    )
    assert response.status_code == 200
    cid = response.json()["conversation_id"]

    # Inspect SQLite
    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.row_factory = sqlite3.Row

    conv_count = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE conversation_id=?", (cid,)
    ).fetchone()[0]
    assert conv_count == 1, f"Expected 1 conversations row, got {conv_count}"

    msg_count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (cid,)
    ).fetchone()[0]
    assert msg_count == 2, f"Expected 2 messages rows, got {msg_count}"

    # Prompt and response directions both present
    directions = {
        row[0]
        for row in conn.execute(
            "SELECT direction FROM messages WHERE conversation_id=?", (cid,)
        ).fetchall()
    }
    assert directions == {"prompt", "response"}, f"Unexpected directions: {directions}"

    # Exactly 4 events
    event_count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE conversation_id=?", (cid,)
    ).fetchone()[0]
    assert event_count == 4, (
        f"Expected exactly 4 events, got {event_count}. "
        f"If 2, the response messages row was inserted AFTER the response events "
        f"(BLOCKER 2 FK-drop regression)."
    )

    # All four event_types present
    event_types = {
        row[0]
        for row in conn.execute(
            "SELECT event_type FROM events WHERE conversation_id=?", (cid,)
        ).fetchall()
    }
    expected_types = {
        "prompt.received",
        "llm.request.started",
        "llm.response.generated",
        "response.delivered",
    }
    assert event_types == expected_types, f"Event types mismatch: {event_types}"

    conn.close()

    # Inspect JSONL
    jsonl_path = settings.jsonl_audit_path
    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 4, (
        f"Expected 4 JSONL lines, got {len(lines)}. "
        f"JSONL and SQLite event counts must match."
    )

    # Every JSONL line is valid JSON with the required top-level fields
    for i, line in enumerate(lines):
        event = json.loads(line)
        for field in ("event_id", "event_type", "conversation_id", "message_id",
                      "timestamp", "actor", "classification", "policy", "siem"):
            assert field in event, f"Line {i} missing field '{field}': {event}"


def test_chat_prompt_messages_row_before_prompt_events(client, tmp_path):
    """
    FK-safety: the prompt messages row must be present before prompt.received
    and llm.request.started (which reference the prompt message_id).

    Verified indirectly by the event_count==4 assertion in the audit test above
    (if the prompt row were missing, prompt events would be FK-dropped too).
    This test additionally confirms both prompt-side events landed with the prompt direction.
    """
    import sqlite3
    from gateway.settings import settings

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "fk safety test"}]},
    )
    assert response.status_code == 200
    cid = response.json()["conversation_id"]

    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.row_factory = sqlite3.Row

    # Check prompt messages row exists
    prompt_row = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? AND direction='prompt'", (cid,)
    ).fetchone()
    assert prompt_row is not None, "Prompt messages row missing"

    # Check both prompt-side events (referencing the prompt message_id) are present
    prompt_events = conn.execute(
        "SELECT event_type FROM events WHERE message_id=? ORDER BY timestamp",
        (prompt_row["message_id"],),
    ).fetchall()
    prompt_event_types = {r[0] for r in prompt_events}
    assert "prompt.received" in prompt_event_types
    assert "llm.request.started" in prompt_event_types

    conn.close()


def test_chat_response_messages_row_before_response_events(client, tmp_path):
    """
    FK-safety: the response messages row must be present before llm.response.generated
    and response.delivered (which reference the response message_id).

    This is BLOCKER 2: if the response row is inserted after these events,
    INSERT OR IGNORE silently drops them (count drops to 2, not 4).
    """
    import sqlite3
    from gateway.settings import settings

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "response fk safety test"}]},
    )
    assert response.status_code == 200
    cid = response.json()["conversation_id"]

    conn = sqlite3.connect(settings.sqlite_db_path)
    conn.row_factory = sqlite3.Row

    # Check response messages row exists
    response_row = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? AND direction='response'", (cid,)
    ).fetchone()
    assert response_row is not None, "Response messages row missing"

    # Check both response-side events (referencing the response message_id) are present
    response_events = conn.execute(
        "SELECT event_type FROM events WHERE message_id=? ORDER BY timestamp",
        (response_row["message_id"],),
    ).fetchall()
    response_event_types = {r[0] for r in response_events}
    assert "llm.response.generated" in response_event_types, (
        "llm.response.generated missing — likely BLOCKER 2 FK-drop regression"
    )
    assert "response.delivered" in response_event_types, (
        "response.delivered missing — likely BLOCKER 2 FK-drop regression"
    )

    conn.close()


def test_chat_model_unavailable_returns_503_with_audit(unavailable_client, tmp_path):
    """
    REL-03: When the assistant adapter raises OllamaUnavailableError, the gateway:
    - Returns HTTP 503 with conversation_id, message_id, error, audited=True
    - Still writes prompt.received to SQLite (the attempt is audited before generation)
    """
    import sqlite3
    from gateway.settings import settings

    response = unavailable_client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "trigger unavailable"}]},
    )
    assert response.status_code == 503, f"Expected 503, got {response.status_code}"
    body = response.json()
    assert body.get("audited") is True, f"Expected audited=True in body: {body}"
    assert "conversation_id" in body
    assert "message_id" in body
    assert body.get("error") == "assistant model unavailable"

    # The attempt must be durably audited (prompt.received written before Ollama call)
    cid = body["conversation_id"]
    conn = sqlite3.connect(settings.sqlite_db_path)
    event_types = {
        row[0]
        for row in conn.execute(
            "SELECT event_type FROM events WHERE conversation_id=?", (cid,)
        ).fetchall()
    }
    conn.close()
    assert "prompt.received" in event_types, (
        f"prompt.received not found in events — REL-03 violated. Got: {event_types}"
    )

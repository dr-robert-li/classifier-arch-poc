"""
Integration tests for GET /events (Task 2, SC-3, Plan 01-02).

Verifies:
(a) GET /events returns a JSON list of persisted audit events.
(b) ?conversation_id= filter returns only that conversation's events.
(c) ?event_type= filter returns only matching event types.
(d) ?limit= param is honoured.
(e) /events reads correctly while the writer connection is open (WAL concurrent read).

All tests use the MockAssistantAdapter fixture — no live Ollama required.
"""
import json


def test_events_returns_list(client):
    """GET /events returns a JSON list (may be empty before any turns)."""
    response = client.get("/events")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    body = response.json()
    assert isinstance(body, list), f"Expected list, got {type(body).__name__}"


def test_events_returns_turn_events_after_chat(client):
    """After a /chat turn, GET /events returns that turn's events."""
    # Perform a chat turn
    chat_resp = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "events route test"}]},
    )
    assert chat_resp.status_code == 200
    cid = chat_resp.json()["conversation_id"]

    # Retrieve events
    events_resp = client.get("/events")
    assert events_resp.status_code == 200
    events = events_resp.json()

    # At least 4 events from this turn
    turn_events = [e for e in events if e.get("conversation_id") == cid]
    assert len(turn_events) == 4, (
        f"Expected 4 events for conversation {cid}, got {len(turn_events)}"
    )

    event_types = {e["event_type"] for e in turn_events}
    expected = {"prompt.received", "llm.request.started", "llm.response.generated", "response.delivered"}
    assert event_types == expected, f"Event types mismatch: {event_types}"


def test_events_conversation_id_filter(client):
    """GET /events?conversation_id=... returns only that conversation's events."""
    # Two turns in different conversations
    r1 = client.post(
        "/chat",
        json={"conversation_id": "filter-conv-A", "messages": [{"role": "user", "content": "turn A"}]},
    )
    r2 = client.post(
        "/chat",
        json={"conversation_id": "filter-conv-B", "messages": [{"role": "user", "content": "turn B"}]},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Filter to conversation A only
    resp = client.get("/events", params={"conversation_id": "filter-conv-A"})
    assert resp.status_code == 200
    events = resp.json()

    # All returned events must belong to conv-A
    for event in events:
        assert event.get("conversation_id") == "filter-conv-A", (
            f"Unexpected conversation in filtered response: {event.get('conversation_id')}"
        )
    assert len(events) == 4, f"Expected exactly 4 events for filter-conv-A, got {len(events)}"


def test_events_event_type_filter(client):
    """GET /events?event_type=prompt.received returns only prompt.received events."""
    client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "event type filter test"}]},
    )

    resp = client.get("/events", params={"event_type": "prompt.received"})
    assert resp.status_code == 200
    events = resp.json()

    assert len(events) >= 1, "Expected at least one prompt.received event"
    for event in events:
        assert event.get("event_type") == "prompt.received", (
            f"event_type filter returned wrong event_type: {event.get('event_type')}"
        )


def test_events_limit_param(client):
    """GET /events?limit=2 returns at most 2 events."""
    # Generate enough events (one turn = 4 events)
    client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "limit test"}]},
    )

    resp = client.get("/events", params={"limit": 2})
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) <= 2, f"Expected at most 2 events with limit=2, got {len(events)}"


def test_events_ordered_by_timestamp(client):
    """GET /events returns events ordered by timestamp ascending."""
    client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "ordering test"}]},
    )

    resp = client.get("/events")
    assert resp.status_code == 200
    events = resp.json()

    timestamps = [e["timestamp"] for e in events if "timestamp" in e]
    assert timestamps == sorted(timestamps), (
        f"Events not ordered by timestamp ascending: {timestamps}"
    )


def test_events_concurrent_read_while_writer_open(client):
    """
    WAL concurrent read test: GET /events succeeds while the writer EventSink
    connection is open (the writer connection is held open in app.state for the
    lifespan of the TestClient — this test confirms WAL allows concurrent reads).
    """
    # Perform a turn (writer creates events in the writer connection)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "concurrent read test"}]},
    )
    assert r.status_code == 200

    # Read back via GET /events — should succeed even with writer conn open
    resp = client.get("/events")
    assert resp.status_code == 200, (
        f"GET /events failed while writer connection is open: {resp.text}"
    )
    events = resp.json()
    assert isinstance(events, list)
    assert len(events) >= 4, f"Expected at least 4 events, got {len(events)}"


def test_events_payload_has_required_fields(client):
    """Each event in GET /events response has the required SKILL.md top-level fields."""
    client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "schema fields test"}]},
    )

    resp = client.get("/events")
    assert resp.status_code == 200
    events = resp.json()

    required_fields = {
        "event_id", "event_type", "conversation_id", "message_id",
        "timestamp", "actor", "classification", "policy", "siem",
    }
    for event in events:
        missing = required_fields - set(event.keys())
        assert not missing, f"Event missing required fields {missing}: {event}"

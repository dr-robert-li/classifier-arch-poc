"""
Unit tests for EventSink dual-write idempotency gate (SC-4, AUDIT-04, BLOCKER 3).

Verifies:
1. First write_event() call returns True and the row COUNT(*) == 1.
2. Second write_event() with the same event_id returns False (genuine PK duplicate)
   and COUNT(*) is still 1 (no double-insert, no second JSONL line).
3. write_event() raises ValueError when a mandatory NOT NULL field is missing or None.
4. Silent audit loss guard: write_event() NEVER returns False for a NOT NULL
   constraint violation (those raise before the INSERT, not after).
5. JSONL line count matches SQLite event count after idempotent write.
"""
import json
import os
import sqlite3
import tempfile

import pytest

from gateway.audit.db import get_connection, init_schema
from gateway.audit.event_sink import EventSink
from gateway.audit.schema import build_event


def _make_sink(tmp_path):
    """Helper: create a fresh EventSink with tmp_path-based paths."""
    db_path = str(tmp_path / "audit_test.db")
    jsonl_path = str(tmp_path / "audit_test.jsonl")
    sink = EventSink(db_path=db_path, jsonl_path=jsonl_path)
    return sink, db_path, jsonl_path


def _make_event(conversation_id="conv-test", turn_index=0):
    """Helper: build a minimal valid event dict."""
    from gateway.audit.ids import make_message_id

    message_id = make_message_id(conversation_id, turn_index, "prompt")
    return build_event(
        event_type="prompt.received",
        conversation_id=conversation_id,
        message_id=message_id,
        correlation_id="corr-test-001",
        direction="prompt",
        text="hello idempotency test",
        assistant_model="llama3.1",
        guard_model="llama-guard3",
    )


def _setup_conversation_and_message(sink, event):
    """Helper: insert the conversations + messages rows that events FK-reference."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    sink.upsert_conversation(
        conversation_id=event["conversation_id"],
        created_at=now,
        assistant_model="llama3.1",
        guard_model="llama-guard3",
        status="active",
    )
    sink.insert_message(
        message_id=event["message_id"],
        conversation_id=event["conversation_id"],
        turn_index=0,
        direction=event["content"]["direction"],
        content=event["content"]["text_preview"],
        created_at=now,
    )


class TestEventSinkIdempotency:

    def test_first_write_returns_true(self, tmp_path):
        """SC-4: First write of a new event_id returns True."""
        sink, db_path, jsonl_path = _make_sink(tmp_path)
        event = _make_event()
        _setup_conversation_and_message(sink, event)

        result = sink.write_event(event)

        assert result is True, f"Expected True (new row), got {result}"
        sink.close()

    def test_first_write_count_equals_1(self, tmp_path):
        """SC-4 / BLOCKER 3: After first write, SELECT COUNT(*) WHERE event_id=? == 1."""
        sink, db_path, jsonl_path = _make_sink(tmp_path)
        event = _make_event()
        _setup_conversation_and_message(sink, event)

        sink.write_event(event)

        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_id=?", (event["event_id"],)
        ).fetchone()[0]
        conn.close()

        assert count == 1, (
            f"Expected COUNT(*)==1 after first write, got {count}. "
            f"If 0, the event was silently dropped (silent audit loss — BLOCKER 3 violation)."
        )
        sink.close()

    def test_second_write_returns_false(self, tmp_path):
        """SC-4: Second write of the same event_id returns False (genuine PK duplicate)."""
        sink, db_path, jsonl_path = _make_sink(tmp_path)
        event = _make_event()
        _setup_conversation_and_message(sink, event)

        sink.write_event(event)        # first write
        result = sink.write_event(event)  # duplicate

        assert result is False, (
            f"Expected False (duplicate suppressed), got {result}. "
            f"A second True would indicate the deduplication gate is broken."
        )
        sink.close()

    def test_second_write_count_still_1(self, tmp_path):
        """SC-4: After two writes with the same event_id, COUNT(*) is still 1."""
        sink, db_path, jsonl_path = _make_sink(tmp_path)
        event = _make_event()
        _setup_conversation_and_message(sink, event)

        sink.write_event(event)
        sink.write_event(event)

        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_id=?", (event["event_id"],)
        ).fetchone()[0]
        conn.close()

        assert count == 1, (
            f"Expected COUNT(*)==1 after duplicate write, got {count}. "
            f"Double-insert breaks audit idempotency."
        )
        sink.close()

    def test_jsonl_has_exactly_one_line_after_duplicate_write(self, tmp_path):
        """SC-4: JSONL has exactly 1 line after two writes with the same event_id."""
        sink, db_path, jsonl_path = _make_sink(tmp_path)
        event = _make_event()
        _setup_conversation_and_message(sink, event)

        sink.write_event(event)
        sink.write_event(event)  # duplicate — should NOT append a second line

        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) == 1, (
            f"Expected 1 JSONL line after duplicate write, got {len(lines)}. "
            f"JSONL duplication breaks the dual-write idempotency gate."
        )
        sink.close()

    def test_different_event_types_produce_different_events(self, tmp_path):
        """SC-4: Two events with different event_types get different event_ids."""
        from gateway.audit.ids import make_message_id
        from gateway.audit.schema import build_event

        sink, db_path, jsonl_path = _make_sink(tmp_path)
        cid = "conv-multi"
        mid = make_message_id(cid, 0, "prompt")

        # Setup conversation and message for both events
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        sink.upsert_conversation(
            conversation_id=cid,
            created_at=now,
            assistant_model="llama3.1",
            guard_model="llama-guard3",
            status="active",
        )
        sink.insert_message(
            message_id=mid,
            conversation_id=cid,
            turn_index=0,
            direction="prompt",
            content="test",
            created_at=now,
        )

        event1 = build_event(
            event_type="prompt.received",
            conversation_id=cid,
            message_id=mid,
            correlation_id="corr-001",
            direction="prompt",
            text="test",
            assistant_model="llama3.1",
            guard_model="llama-guard3",
        )
        event2 = build_event(
            event_type="llm.request.started",
            conversation_id=cid,
            message_id=mid,
            correlation_id="corr-001",
            direction="prompt",
            text="test",
            assistant_model="llama3.1",
            guard_model="llama-guard3",
        )

        assert event1["event_id"] != event2["event_id"], (
            "Different event_types on the same message should produce different event_ids."
        )

        r1 = sink.write_event(event1)
        r2 = sink.write_event(event2)

        assert r1 is True
        assert r2 is True

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM events WHERE conversation_id=?", (cid,)).fetchone()[0]
        conn.close()
        assert count == 2, f"Expected 2 distinct event rows, got {count}"
        sink.close()


class TestJSONLSQLiteSync:
    """SC-3: after a successful /chat turn, JSONL line count == SQLite events rowcount."""

    def test_jsonl_line_count_equals_sqlite_events_after_turn(self, client):
        """SC-3: dual-store sync — one /chat turn → JSONL lines == SQLite event rows."""
        import sqlite3
        from gateway.settings import settings

        response = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "dual sync test"}]},
        )
        assert response.status_code == 200
        cid = response.json()["conversation_id"]

        # SQLite count
        conn = sqlite3.connect(settings.sqlite_db_path)
        sqlite_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE conversation_id=?", (cid,)
        ).fetchone()[0]
        conn.close()

        # JSONL count
        jsonl_count = 0
        with open(settings.jsonl_audit_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    import json as _json
                    event = _json.loads(stripped)
                    if event.get("conversation_id") == cid:
                        jsonl_count += 1

        assert jsonl_count == sqlite_count, (
            f"JSONL lines ({jsonl_count}) != SQLite events ({sqlite_count}) "
            f"— dual-store sync broken (SC-3)"
        )
        assert sqlite_count == 4, f"Expected 4 events for a successful turn, got {sqlite_count}"


class TestEventSinkValidation:

    def test_missing_event_id_raises_value_error(self, tmp_path):
        """write_event raises ValueError when event_id is missing (BLOCKER 3)."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event.pop("event_id")

        with pytest.raises(ValueError, match="event_id"):
            sink.write_event(event)

        sink.close()

    def test_missing_event_type_raises_value_error(self, tmp_path):
        """write_event raises ValueError when event_type is missing."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event.pop("event_type")

        with pytest.raises(ValueError):
            sink.write_event(event)

        sink.close()

    def test_missing_conversation_id_raises_value_error(self, tmp_path):
        """write_event raises ValueError when conversation_id is missing."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event.pop("conversation_id")

        with pytest.raises(ValueError, match="conversation_id"):
            sink.write_event(event)

        sink.close()

    def test_missing_message_id_raises_value_error(self, tmp_path):
        """write_event raises ValueError when message_id is missing."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event.pop("message_id")

        with pytest.raises(ValueError, match="message_id"):
            sink.write_event(event)

        sink.close()

    def test_missing_timestamp_raises_value_error(self, tmp_path):
        """write_event raises ValueError when timestamp is missing."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event.pop("timestamp")

        with pytest.raises(ValueError, match="timestamp"):
            sink.write_event(event)

        sink.close()

    def test_missing_actor_type_raises_value_error(self, tmp_path):
        """write_event raises ValueError when actor.type is missing (BLOCKER 1)."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event["actor"] = {"id": "local-user"}  # type omitted

        with pytest.raises(ValueError, match="actor_type"):
            sink.write_event(event)

        sink.close()

    def test_missing_actor_id_raises_value_error(self, tmp_path):
        """write_event raises ValueError when actor.id is missing."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event["actor"] = {"type": "user"}  # id omitted

        with pytest.raises(ValueError, match="actor_id"):
            sink.write_event(event)

        sink.close()

    def test_missing_actor_block_raises_value_error(self, tmp_path):
        """write_event raises ValueError when the entire actor block is absent."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event.pop("actor")

        with pytest.raises(ValueError):
            sink.write_event(event)

        sink.close()

    def test_none_field_raises_value_error(self, tmp_path):
        """write_event raises ValueError when a mandatory field is explicitly None."""
        sink, _, _ = _make_sink(tmp_path)
        event = _make_event()
        event["event_id"] = None

        with pytest.raises(ValueError, match="event_id"):
            sink.write_event(event)

        sink.close()

    def test_validation_raises_before_insert_so_count_remains_0(self, tmp_path):
        """
        Silent audit loss guard: a missing mandatory field raises BEFORE any INSERT.
        After the raise, COUNT(*) in events remains 0 — the row was never inserted
        and the failure did not masquerade as a duplicate (False return).
        """
        sink, db_path, _ = _make_sink(tmp_path)
        event = _make_event()
        _setup_conversation_and_message(sink, event)

        bad_event = dict(event)
        bad_event["event_id"] = None

        with pytest.raises(ValueError):
            sink.write_event(bad_event)

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()

        assert count == 0, (
            f"Expected 0 events after a ValueError raise (pre-INSERT validation), got {count}. "
            f"The raise must happen BEFORE the INSERT — not after OR IGNORE swallows it."
        )
        sink.close()

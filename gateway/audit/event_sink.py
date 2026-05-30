"""
EventSink: dual-write gate for SQLite + JSONL audit log (AUDIT-01, AUDIT-02, AUDIT-04).

Design:
- One long-lived SQLite connection (WAL mode, foreign_keys=ON) for all writes.
- SQLite INSERT OR IGNORE is the idempotency gate: only if rowcount==1 (new row)
  is the JSONL line appended.
- write_event() validates mandatory NOT NULL fields BEFORE the INSERT and RAISES
  on a missing/None field (caller-contract violation, kept distinct from operational
  faults so a NOT NULL constraint violation can never masquerade as a "duplicate").
- SQLite/IO errors are caught AFTER validation, logged, and return False (operational
  fault — degrades gracefully without raising).
- JSONL append uses fcntl.flock LOCK_EX + fsync + chmod 0o600 (POSIX/macOS).
"""
import fcntl
import json
import logging
import os
import stat
import sqlite3
from datetime import datetime, timezone

from gateway.audit.db import get_connection, init_schema
from gateway.settings import settings as _default_settings

logger = logging.getLogger(__name__)


class EventSink:
    """
    Dual-write audit sink: SQLite (idempotency gate) + JSONL (append-only log).

    Lifecycle:
    - Construct with db_path and jsonl_path; call close() on shutdown.
    - One connection is opened at construction time; all write_event() calls use it.
    """

    def __init__(self, db_path: str, jsonl_path: str) -> None:
        self._db_path = db_path
        self._jsonl_path = jsonl_path
        self._conn = get_connection(db_path)
        init_schema(self._conn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_event(self, event: dict) -> bool:
        """
        Write an audit event to SQLite and conditionally to JSONL.

        Returns:
            True  — new row created; JSONL line appended.
            False — genuine PRIMARY-KEY duplicate (INSERT OR IGNORE no-op); JSONL skipped.
                    Also False on SQLite/IO fault (logs error).

        Raises:
            ValueError — if any mandatory NOT NULL field is missing or None.
                         This is a caller-contract violation, not an operational fault.
                         Fields checked: event_id, event_type, conversation_id,
                         message_id, timestamp, actor_type (from event["actor"]["type"]),
                         actor_id (from event["actor"]["id"]).
        """
        # --- Step 1: Validate mandatory fields BEFORE the INSERT ---------------
        # actor_type and actor_id live inside the nested actor block; flatten for check.
        actor: dict = event.get("actor") or {}
        actor_type = actor.get("type")
        actor_id = actor.get("id")

        flat_required = {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "conversation_id": event.get("conversation_id"),
            "message_id": event.get("message_id"),
            "timestamp": event.get("timestamp"),
            "actor_type": actor_type,
            "actor_id": actor_id,
        }
        missing = [k for k, v in flat_required.items() if v is None]
        if missing:
            raise ValueError(
                f"write_event: mandatory NOT NULL field(s) missing/None: {missing} "
                f"(event_type={event.get('event_type')!r})"
            )

        # --- Step 2: INSERT OR IGNORE + JSONL gate (operational faults only) ---
        try:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events "
                "(event_id, event_type, conversation_id, message_id, timestamp, "
                "actor_type, actor_id, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["event_id"],
                    event["event_type"],
                    event["conversation_id"],
                    event["message_id"],
                    event["timestamp"],
                    actor_type,
                    actor_id,
                    json.dumps(event),
                ),
            )
            self._conn.commit()
            is_new = cur.rowcount == 1
            if is_new:
                self._append_jsonl(event)
            return is_new
        except Exception as exc:
            # SQLite/IO faults only — mandatory-field violations raised above.
            logger.error("Event write failed (operational): %s", exc)
            return False

    def upsert_conversation(
        self,
        *,
        conversation_id: str,
        created_at: str,
        assistant_model: str,
        guard_model: str,
        status: str = "active",
    ) -> None:
        """
        Upsert a conversations row, supplying ALL NOT NULL columns.

        Uses INSERT OR IGNORE so a re-submitted conversation_id is a safe no-op.
        Omitting assistant_model or guard_model (both NOT NULL) would make every
        INSERT a constraint violation silently suppressed by OR IGNORE — leaving
        the conversations row absent and cascading FK failures into messages.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO conversations "
            "(conversation_id, created_at, assistant_model, guard_model, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, created_at, assistant_model, guard_model, status),
        )
        self._conn.commit()

    def insert_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        turn_index: int,
        direction: str,
        content: str,
        created_at: str,
    ) -> None:
        """
        Insert a messages row, supplying ALL NOT NULL columns.

        Uses INSERT OR IGNORE so a retry of a failed turn (same
        (conversation_id, turn_index, direction) UNIQUE key) is a safe no-op,
        not a raise.

        content_sha256 is SHA-256 of the full content text.
        content_preview is the first 200 characters.
        """
        import hashlib

        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        content_preview = content[:200]

        self._conn.execute(
            "INSERT OR IGNORE INTO messages "
            "(message_id, conversation_id, turn_index, direction, "
            "content_sha256, content_preview, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                conversation_id,
                turn_index,
                direction,
                content_sha256,
                content_preview,
                created_at,
            ),
        )
        self._conn.commit()

    def count_events(self, conversation_id: str) -> int:
        """Return the number of events for a conversation (used in tests)."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else 0

    def count_completed_responses(self, conversation_id: str) -> int:
        """
        Count completed response messages for turn_index calculation.

        turn_index = number of COMPLETED response rows (direction='response').
        A failed turn (no response row written) leaves turn_index unchanged
        so a retry produces the same message_id and event_id.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND direction='response'",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else 0

    def get_event_types(self, conversation_id: str) -> list[str]:
        """Return event_type values for a conversation (used in tests)."""
        rows = self._conn.execute(
            "SELECT event_type FROM events WHERE conversation_id=? ORDER BY timestamp",
            (conversation_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        try:
            self._conn.close()
        except Exception as exc:
            logger.warning("EventSink.close error: %s", exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _append_jsonl(self, event: dict) -> None:
        """
        Atomic append to the JSONL audit log.

        Uses fcntl.flock LOCK_EX for exclusive write access.
        fsync ensures the line is durably on disk.
        File permissions are restricted to 0o600 on each write.
        """
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self._jsonl_path)), exist_ok=True)

        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        # Restrict permissions to owner read/write only (CLAUDE.md security requirement)
        os.chmod(self._jsonl_path, stat.S_IRUSR | stat.S_IWUSR)

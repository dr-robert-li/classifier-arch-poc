"""
SQLite connection management and schema initialisation (AUDIT-02).

Design decisions:
- Single long-lived WAL-mode connection for the EventSink write path.
- Row factory set to sqlite3.Row for dict-like access in read paths.
- File permissions restricted to owner only (0o600) per CLAUDE.md security requirements.
- All 8 tables created at startup (3 active + 5 deferred) for schema stability across phases.
"""
import os
import sqlite3
import stat


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database at db_path.

    - Creates parent directories as needed.
    - Enables WAL journal mode.
    - Enables foreign_keys=ON (immediate enforcement).
    - Sets row_factory to sqlite3.Row.
    - Chmods the db file to 0o600 (owner read/write only).
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    # Restrict file permissions to owner only (CLAUDE.md security requirement)
    os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """
    Execute the full DDL for all 8 tables and 3 indexes.

    Phase 1 active tables: conversations, messages, events
    Phase 2-4 deferred tables (created empty for schema stability):
        classifications, approval_requests, admin_actions, redactions, policy_versions
    All statements use CREATE TABLE IF NOT EXISTS (idempotent on restart).
    """
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        -- Phase 1 tables (active)

        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            created_at      TEXT NOT NULL,
            actor_id        TEXT NOT NULL DEFAULT 'local-user',
            assistant_model TEXT NOT NULL,
            guard_model     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'active',
            turn_count      INTEGER NOT NULL DEFAULT 0,
            metadata        TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            message_id      TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            turn_index      INTEGER NOT NULL,
            direction       TEXT NOT NULL CHECK(direction IN ('prompt', 'response')),
            content_sha256  TEXT NOT NULL,
            content_preview TEXT,
            created_at      TEXT NOT NULL,
            UNIQUE(conversation_id, turn_index, direction)
        );

        CREATE TABLE IF NOT EXISTS events (
            event_id        TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            message_id      TEXT NOT NULL REFERENCES messages(message_id),
            timestamp       TEXT NOT NULL,
            actor_type      TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            severity        TEXT NOT NULL DEFAULT 'none',
            payload         TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_conversation ON events(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

        -- Phase 2-4 tables: created now (empty) for schema stability

        CREATE TABLE IF NOT EXISTS classifications (
            classification_id   TEXT PRIMARY KEY,
            event_id            TEXT NOT NULL REFERENCES events(event_id),
            overall_severity    TEXT NOT NULL DEFAULT 'none',
            categories          TEXT,
            llama_guard_label   TEXT,
            deterministic_findings TEXT,
            confidence          REAL,
            created_at          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approval_requests (
            approval_id     TEXT PRIMARY KEY,
            event_id        TEXT NOT NULL REFERENCES events(event_id),
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL,
            resolved_at     TEXT,
            resolution      TEXT
        );

        CREATE TABLE IF NOT EXISTS admin_actions (
            action_id       TEXT PRIMARY KEY,
            approval_id     TEXT REFERENCES approval_requests(approval_id),
            event_id        TEXT REFERENCES events(event_id),
            action_type     TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS redactions (
            redaction_id    TEXT PRIMARY KEY,
            event_id        TEXT NOT NULL REFERENCES events(event_id),
            original_sha256 TEXT NOT NULL,
            redacted_sha256 TEXT NOT NULL,
            spans           TEXT NOT NULL,
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS policy_versions (
            policy_version  TEXT PRIMARY KEY,
            content         TEXT NOT NULL,
            activated_at    TEXT NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.commit()

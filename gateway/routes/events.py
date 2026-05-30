"""
GET /events — read route for persisted audit events (Plan 01-02, GATE-05).

Design:
- Opens a SEPARATE read-only SQLite connection per request using get_connection().
  WAL mode allows concurrent reads while the EventSink writer connection is open.
  The writer connection (app.state.event_sink._conn) is never reused here.
- Returns the deserialized payload JSON per row so callers get the full event dict.
- Supports optional filtering by conversation_id, event_type, and a limit (default 100).
- Results are ordered by timestamp ascending (chronological audit trail).

Security:
- No write path exposed — read-only SELECT queries only.
- No Ollama references — this module is SQLite-only (GATE-03 boundary preserved).
"""
import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Query, Request

from gateway.audit.db import get_connection

router = APIRouter()


@router.get("/events")
async def list_events(
    request: Request,
    conversation_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=10000),
) -> list[dict]:
    """
    Return persisted audit events from SQLite.

    Query params:
        conversation_id (str, optional): Filter to a specific conversation.
        event_type (str, optional): Filter to a specific event type.
        limit (int, optional): Maximum number of events to return (default 100, max 10000).

    Returns:
        JSON array of event dicts (deserialized from the payload column), ordered by timestamp.
    """
    settings = request.app.state.settings

    # Open a SEPARATE read connection — WAL allows concurrent reads while
    # the EventSink writer connection is open in app.state.
    conn = get_connection(settings.sqlite_db_path)
    try:
        # Build query dynamically based on filters
        where_clauses: list[str] = []
        params: list = []

        if conversation_id is not None:
            where_clauses.append("conversation_id = ?")
            params.append(conversation_id)

        if event_type is not None:
            where_clauses.append("event_type = ?")
            params.append(event_type)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params.append(limit)

        query = (
            f"SELECT payload FROM events {where_sql} "
            f"ORDER BY timestamp ASC LIMIT ?"
        )

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    # Deserialize the payload JSON column back into event dicts
    events = []
    for row in rows:
        try:
            events.append(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            # Skip malformed rows (should not occur with a correct writer)
            continue

    return events

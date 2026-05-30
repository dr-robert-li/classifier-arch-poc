# Phase 1: Gateway & Audit Foundation — Research

**Researched:** 2026-05-30
**Domain:** FastAPI gateway, Ollama HTTP API, SQLite append-only audit, JSONL SIEM events
**Confidence:** HIGH — all key findings verified empirically on the target machine

---

## Summary

Phase 1 builds a local FastAPI gateway as the sole enforcement boundary between chat clients
and a locally-running Ollama instance. Every prompt is captured before the model generates,
and every response is captured before it is returned. Both prompt and response events are
written to an append-only JSONL file and to SQLite. There is no classification or approval
logic in Phase 1 — those come in Phases 2 and 3 — but the event schema must be complete and
schema-valid from day one so later phases can slot in cleanly.

The critical technical decision for this phase is **idempotency**: the system must not
duplicate audit events on replay. The SKILL.md schema uses UUID event IDs, but a randomly
generated UUID4 is a new key on every call and breaks idempotency. The correct approach is a
**deterministic UUID5** derived from `(conversation_id, message_id, event_type)`, so the
same logical event always yields the same ID. The SQLite `INSERT OR IGNORE` return value
(rowcount 1 = new row, 0 = duplicate) is then used as the gate: only append to JSONL when
the insert creates a new row. This keeps both stores synchronized without an additional
deduplication pass.

The Ollama HTTP API for chat (`POST /api/chat`) is verified on the target machine and returns
a well-typed JSON body. Unavailability takes two forms that both need graceful handling:
connection refused (process down) maps to `httpx.ConnectError`, and model-not-found (model
not pulled or stopped) returns HTTP 404 with `{"error": "model '...' not found"}`. Both are
REL-03 operational error paths — not exceptions to surface as stack traces.

**Primary recommendation:** Use stdlib `sqlite3` with WAL mode via a single serialized
write connection, `uuid5` deterministic event IDs, and `httpx.AsyncClient` for all Ollama
calls. Keep Phase 1 adapter stubs thin but with correct Protocol shapes so Phases 2–3 can
override without restructuring the injection graph.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GATE-01 | Gateway captures every inbound user prompt before it reaches the assistant model | Capture path: write `prompt.received` event + SQLite message row *before* calling Ollama adapter |
| GATE-02 | Gateway captures every assistant response before it is returned to the user | Capture path: write `llm.response.generated` event + SQLite message row *before* returning to caller |
| GATE-03 | Gateway is the only path to Ollama — chat client, agents, Web UI never call Ollama directly | Architectural rule: single `OllamaAssistantAdapter` module owns all `:11434` calls; enforced by a grep/import test |
| GATE-04 | Gateway exposes adapter interfaces for assistant model, guard model, scanner, policy engine, event sink, approval workflow | Python Protocol interfaces; Phase 1 implements assistant adapter + event sink; guard/scanner/policy/approval are stubs |
| GATE-05 | Gateway serves the route surface: /chat, /classify/prompt, /classify/response, /events, /approvals, /policy, /health, /export/jsonl | /chat and /health are live in Phase 1; others are stubs returning HTTP 501 |
| AUDIT-01 | Append-only JSONL events written with stable SIEM schema | SKILL.md schema is the authority; Phase 1 default values documented below for mandatory blocks |
| AUDIT-02 | SQLite stores conversations, messages, events (+ deferred tables) | DDL prescribed below; deferred tables recommended to create now (empty) for schema stability |
| AUDIT-04 | Event writes are idempotent | uuid5 deterministic event_id + INSERT OR IGNORE rowcount gate on JSONL write |
| REL-03 | Assistant-model unavailability returns a graceful operational error while preserving audit events | `prompt.received` persisted before Ollama call; `system.error` event on failure; HTTP 503 response |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Prompt capture | API / Gateway | Data Layer | Gateway intercepts before forwarding; data layer persists the event |
| Response capture | API / Gateway | Data Layer | Gateway intercepts after Ollama returns; data layer persists before returning to caller |
| Ollama chat calls | Model Adapter | — | Single adapter owns all Ollama interaction; nothing else touches localhost:11434 |
| JSONL audit writes | Event Sink | — | Single writer with idempotency gate; not shared across adapters |
| SQLite state | Data Layer | — | Conversations, messages, events tables; WAL mode for concurrent reads |
| Config / settings | Application | — | Pydantic-settings BaseSettings; .env file; injected at startup |
| Health checks | API / Gateway | Model Adapter | /health route calls adapter probes for Ollama + model availability |
| Route surface | API / Gateway | — | FastAPI router; stub routes for Phase 2–4 endpoints return 501 |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | 0.136.1 | ASGI web framework, routing, DI | [VERIFIED: PyPI] Installed on target; Pydantic v2 native; auto-generated OpenAPI |
| uvicorn | 0.47.0 | ASGI server | [VERIFIED: PyPI] FastAPI's recommended server; standard pairing |
| pydantic | 2.13.4 | Data models, request/response validation | [VERIFIED: PyPI] Installed; FastAPI requires it |
| pydantic-settings | 2.14.1 | .env config loading via BaseSettings | [VERIFIED: PyPI] Installed; zero boilerplate for env-based config |
| httpx | 0.28.1 | Async HTTP client for Ollama calls | [VERIFIED: PyPI] Installed; AsyncClient with timeout; same client used by FastAPI TestClient |
| starlette | 1.0.0 | ASGI utilities (TestClient) | [VERIFIED: PyPI] Installed as FastAPI dependency; TestClient for sync tests |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| aiosqlite | 0.22.1 | Async SQLite | NOT recommended for Phase 1 — see below |
| pytest | 9.0.3 | Test runner | [VERIFIED: PyPI] Not installed; must be added to dev deps |
| pytest-asyncio | 1.4.0 | Async test support | [VERIFIED: PyPI] Not installed; needed only if async test functions used |

**Note on aiosqlite:** The advisor confirmed a single serialized SQLite writer (stdlib `sqlite3` + WAL) is the correct choice for this append-only use case. It eliminates "database is locked" errors, makes idempotency reasoning trivial, and removes a dependency. Do not use aiosqlite in Phase 1.

**Note on pytest-asyncio:** FastAPI's `TestClient` runs async routes synchronously via anyio. Most Phase 1 tests can use sync TestClient without pytest-asyncio. Add pytest-asyncio only if you write `async def test_*` functions directly.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib sqlite3 + WAL | aiosqlite | aiosqlite is fine for concurrent reads but adds complexity and a dep; one writer makes idempotency reasoning trivial |
| httpx.AsyncClient | requests (sync) | requests is simpler but blocks the event loop; Ollama calls can take seconds; httpx is the correct async choice |
| FastAPI Protocol DI | SQLModel / SQLAlchemy | Full ORM is overkill for a POC append-only audit log; stdlib sqlite3 is sufficient |

### Installation

```bash
pip install fastapi==0.136.1 uvicorn==0.47.0 pydantic==2.13.4 pydantic-settings==2.14.1 httpx==0.28.1
# Dev / test deps:
pip install pytest==9.0.3 starlette==1.0.0
```

The exact versions above are installed and import-verified on Python 3.14.2 (the target machine's runtime). PyPI latest as of 2026-05-30: fastapi 0.136.3, uvicorn 0.48.0, pydantic-settings 2.14.1, httpx 0.28.1, pytest 9.0.3.

---

## Package Legitimacy Audit

> slopcheck run: all 8 packages scanned on PyPI. Exit code 1 due to `pip` vs `pip3` naming
> on macOS (slopcheck's install phase failed, not the check phase — all verdicts are [OK]).

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| fastapi | PyPI | 6+ yrs | Very high | github.com/fastapi/fastapi | [OK] | Approved |
| uvicorn | PyPI | 6+ yrs | Very high | github.com/encode/uvicorn | [OK] | Approved |
| pydantic | PyPI | 10+ yrs | Very high | github.com/pydantic/pydantic | [OK] | Approved |
| pydantic-settings | PyPI | 3+ yrs | Very high | github.com/pydantic/pydantic-settings | [OK] | Approved |
| httpx | PyPI | 5+ yrs | Very high | github.com/encode/httpx | [OK] | Approved |
| aiosqlite | PyPI | 5+ yrs | High | github.com/omnilib/aiosqlite | [OK] | Approved (but not recommended — see above) |
| pytest | PyPI | 15+ yrs | Very high | github.com/pytest-dev/pytest | [OK] | Approved |
| pytest-asyncio | PyPI | 7+ yrs | High | github.com/pytest-dev/pytest-asyncio | [OK] | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram

```
Chat Client (curl / UI / test)
        |
        | POST /chat {conversation_id?, messages}
        v
+----------------------------+
|    FastAPI Gateway         |  <-- only path to Ollama (GATE-03)
|  127.0.0.1:8000            |
|                            |
|  1. Capture prompt         |
|     -> prompt.received     |
|     -> SQLite write        |  -- event persisted BEFORE Ollama call (REL-03)
|                            |
|  2. Call Ollama adapter    |
|     [OllamaAssistantAdapter]
|        |                   |
|        v                   |
|  localhost:11434/api/chat  |  <-- ONLY place that talks to Ollama
|        |                   |
|    (on error: ConnectError |
|     or HTTP 404)           |
|     -> system.error event  |
|     -> return HTTP 503     |
|                            |
|  3. Capture response       |
|     -> llm.response.generated
|     -> response.delivered  |
|     -> SQLite write        |
|     -> JSONL audit write   |
|                            |
|  4. Return response        |
+----------------------------+
        |
        v
   Event Sink
   +-------------------+
   | SQLite WAL        |  -- conversations, messages, events tables
   | JSONL append-only |  -- written ONLY when SQLite INSERT creates new row
   +-------------------+
```

### Recommended Project Structure

```
gateway/
├── main.py               # FastAPI app, lifespan, router mounts
├── settings.py           # Pydantic BaseSettings (.env)
├── adapters/
│   ├── __init__.py
│   ├── protocols.py      # Python Protocol interfaces (IAssistantAdapter, IEventSink, stubs)
│   ├── ollama_assistant.py   # OllamaAssistantAdapter — ONLY module that calls :11434
│   └── stubs.py          # NullGuardAdapter, NullScannerAdapter, NullPolicyAdapter, NullApprovalAdapter
├── audit/
│   ├── __init__.py
│   ├── schema.py         # AuditEvent Pydantic model (SKILL.md schema)
│   ├── event_sink.py     # EventSink: SQLite writer + JSONL dual-write gate
│   └── db.py             # SQLite init, schema DDL, get_connection()
├── routes/
│   ├── __init__.py
│   ├── chat.py           # POST /chat
│   ├── events.py         # GET /events
│   ├── health.py         # GET /health
│   └── stubs.py          # /classify/*, /approvals/*, /policy, /export/jsonl -> 501
└── tests/
    ├── conftest.py       # TestClient fixture, MockAssistantAdapter, dep overrides
    ├── test_chat.py      # success, model-unavailable, Ollama-down scenarios
    ├── test_audit.py     # idempotency, JSONL sync, SQLite rows
    └── test_health.py    # health endpoint returns expected shape
```

### Pattern 1: Lifespan for Shared Resources

**What:** Use FastAPI's `asynccontextmanager` lifespan to initialize and close shared resources
(httpx.AsyncClient, SQLite connection, EventSink).
**When to use:** Startup and teardown of resources that must persist across requests.

```python
# Source: verified on fastapi==0.136.1 / Python 3.14.2
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.http_client = httpx.AsyncClient(timeout=120.0)
    app.state.event_sink = build_event_sink(settings)  # opens SQLite, resolves paths
    yield
    # Shutdown
    await app.state.http_client.aclose()
    app.state.event_sink.close()

app = FastAPI(lifespan=lifespan)
```

### Pattern 2: Protocol Adapter Interfaces

**What:** Define `Protocol` classes for each adapter. Inject via FastAPI `Depends`. Override
in tests via `app.dependency_overrides`.
**When to use:** Any injectable adapter where tests must swap the real implementation.

```python
# Source: verified on Python 3.14.2
from typing import Protocol, runtime_checkable

@runtime_checkable
class IAssistantAdapter(Protocol):
    async def chat(self, messages: list[dict]) -> dict:
        """Returns {'content': str, 'model': str} or raises OllamaUnavailableError."""
        ...

class IEventSink(Protocol):
    def write_event(self, event: dict) -> bool:
        """Write event. Returns True if new row created, False if duplicate. Never raises."""
        ...

# In tests:
# app.dependency_overrides[get_assistant_adapter] = lambda: MockAssistantAdapter()
```

### Pattern 3: Pydantic-Settings Configuration

**What:** Single `Settings` object loaded from `.env` and environment variables.
**When to use:** All configuration. No hardcoded URLs, paths, or model names.

```python
# Source: verified pydantic-settings==2.14.1 / Python 3.14.2
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    model_config = {'env_file': '.env', 'env_file_encoding': 'utf-8', 'extra': 'ignore'}

    ollama_base_url: str = 'http://localhost:11434'
    ollama_assistant_model: str = 'llama3.1'
    ollama_guard_model: str = 'llama-guard3'
    sqlite_db_path: str = './data/gateway.db'
    jsonl_audit_path: str = './data/audit.jsonl'
    gateway_host: str = '127.0.0.1'   # localhost-only by default (CLAUDE.md security req)
    gateway_port: int = 8000
    ollama_timeout_seconds: float = 120.0  # local model generation can be slow

settings = Settings()
```

### Anti-Patterns to Avoid

- **Direct Ollama calls outside the adapter:** Any code outside `adapters/ollama_assistant.py`
  that contains `11434` or `OLLAMA_BASE_URL` is an architectural violation. Enforce with a
  grep test: `grep -r "11434\|ollama_base_url" gateway/ --include="*.py" | grep -v adapters/ollama`.
- **Generating random UUID4 event IDs:** UUID4 breaks idempotency. Use UUID5 deterministic
  IDs (see idempotency section below).
- **Writing JSONL before SQLite:** The SQLite insert is the idempotency gate. Always insert
  first, then append JSONL only if `rowcount == 1`.
- **Calling Ollama before persisting `prompt.received`:** Violates REL-03. The prompt event
  must be durable on disk before the model call is attempted.
- **Binding to 0.0.0.0:** The gateway is local-only. Always bind to `127.0.0.1`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP client with timeout/retry | Custom requests loop | `httpx.AsyncClient` | Handles connect errors, timeouts, streaming; matches FastAPI's async model |
| Settings from env/file | `os.environ.get()` chains | `pydantic-settings BaseSettings` | Type coercion, validation, .env file loading, IDE completion — built in |
| Request/response validation | Manual dict unpacking | FastAPI + Pydantic models | Auto-generates OpenAPI docs; raises 422 on bad input automatically |
| Startup/shutdown hooks | `@app.on_event` (deprecated) | `asynccontextmanager lifespan` | `on_event` is deprecated in modern FastAPI |
| Test HTTP calls | Raw ASGI calls | FastAPI `TestClient` | Synchronous, handles lifespan, no running server needed |

**Key insight:** The FastAPI + Pydantic + httpx triad is the standard Python async API stack.
Using anything else (Flask, requests, manual validation) introduces unnecessary divergence
from what the ecosystem expects.

---

## Ollama HTTP API Reference

**Verified empirically on Ollama 0.22.1 running on the target machine (2026-05-30).**

### Base URL

`http://localhost:11434` [VERIFIED: curl against running instance]

### Preferred Endpoint: POST /api/chat

Use this endpoint (not `/api/generate`) for multi-turn conversations because it accepts
the standard `messages` array matching the OpenAI chat format.

**Request body:**
```json
{
  "model": "llama3.1",
  "messages": [
    {"role": "user", "content": "hello"}
  ],
  "stream": false
}
```

**Response body (non-streaming, `stream: false`):**
```json
{
  "model": "llama3.1",
  "created_at": "2026-05-30T03:07:00.300528Z",
  "message": {
    "role": "assistant",
    "content": "<response text>"
  },
  "done": true,
  "done_reason": "stop",
  "total_duration": 21748496084,
  "load_duration": 19538914209,
  "prompt_eval_count": 201,
  "prompt_eval_duration": 2063534916,
  "eval_count": 2,
  "eval_duration": 80054125
}
```

The Phase 1 adapter reads `response["message"]["content"]` for the response text and
`response["model"]` for the model name to include in audit events.

### Health Check Endpoints

```bash
GET  /api/version  -> {"version": "0.22.1"}         # Ollama process alive
POST /api/show     -> {"modelfile": ..., ...}         # Model exists and loaded
POST /api/tags     -> {"models": [{"name": "llama-guard3:latest", ...}]}  # List all models
```

For REL-03, the `/health` gateway route should check both:
1. `GET /api/version` to confirm Ollama is running.
2. `POST /api/show` with `{"model": "<OLLAMA_ASSISTANT_MODEL>"}` to confirm the model is
   available. Returns HTTP 404 with `{"error": "model '...' not found"}` if absent.

### Error Shapes

| Scenario | HTTP Status | Body |
|----------|-------------|------|
| Ollama process down | `httpx.ConnectError` raised | N/A |
| Model not found / stopped | 404 | `{"error": "model 'name' not found"}` |
| Request timeout | `httpx.TimeoutException` raised | N/A |

Both `ConnectError` and `TimeoutException` should be caught and mapped to a gateway
`system.error` audit event + HTTP 503 response. The HTTP 404 from Ollama also maps to
`system.error` + HTTP 503 (not a 404 to the caller — that would imply the gateway route
is missing).

### Streaming

Set `"stream": false` for Phase 1. The gateway buffers the full response and classifies
it before returning to the client. Streaming is a Phase 4+ concern.

---

## Idempotency Strategy (AUDIT-04)

This section is load-bearing. Read carefully before planning tasks.

### The Problem

SKILL.md's event schema shows a UUID `event_id`. A randomly generated UUID4 produces a
different value on every call. If the gateway processes the same turn twice (e.g., on retry
or test replay), each call generates a new UUID4, so `INSERT OR IGNORE` does not deduplicate
and the JSONL gets two lines for the same logical event. Success criterion #4 fails.

### The Solution: Deterministic UUID5 Event ID

```python
# Source: verified on Python 3.14.2 stdlib uuid
import uuid

NAMESPACE = uuid.NAMESPACE_DNS  # stable, well-known namespace

def make_event_id(conversation_id: str, message_id: str, event_type: str) -> str:
    """
    Deterministic UUID5 from (conversation_id, message_id, event_type).
    Same logical event always yields the same UUID. Different event types
    on the same message produce distinct IDs.
    """
    key = f"{conversation_id}:{message_id}:{event_type}"
    return str(uuid.uuid5(NAMESPACE, key))
```

Verified:
- Same inputs -> same output on every call.
- Different `event_type` -> different UUID (no collision between `prompt.received` and
  `llm.request.started` on the same message).
- Output is a valid UUID4-format string, satisfying SKILL.md's schema.

### Stable message_id

`message_id` must also be deterministic. Derive it from `conversation_id` + `turn_index` +
`direction` rather than generating randomly:

```python
def make_message_id(conversation_id: str, turn_index: int, direction: str) -> str:
    """direction: 'prompt' or 'response'"""
    key = f"{conversation_id}:{turn_index}:{direction}"
    return str(uuid.uuid5(NAMESPACE, key))
```

The client supplies `conversation_id` (or the gateway generates one per session and returns
it). `turn_index` is the 0-based count of turns in that conversation, stored in SQLite.

**Critical for retry idempotency:** `turn_index` must not advance on a failed turn.
The rule: `turn_index` = the number of *completed* response rows in SQLite for this
conversation (i.e., `SELECT COUNT(*) FROM messages WHERE conversation_id=? AND direction='response'`).
A failed turn (where no response row was written) leaves `turn_index` unchanged, so a
retry computes the same `message_id` and the same `event_id` — and `INSERT OR IGNORE`
correctly deduplicates the `prompt.received` event.

**The gateway response body (both 200 and 503) MUST include `conversation_id` and `message_id`**
so a well-behaved client can supply them on retry:

```python
# HTTP 200 success response body
{
    "conversation_id": "<uuid>",
    "message_id": "<uuid5-of-response>",
    "response": "<assistant content>",
    "model": "<model name>"
}

# HTTP 503 error response body (REL-03)
{
    "conversation_id": "<uuid>",
    "message_id": "<uuid5-of-prompt>",
    "error": "assistant model unavailable",
    "audited": true
}
```

Without `message_id` in the 503 body, a retrying client has no stable key to supply and
the gateway will recompute `turn_index` from an already-written message row — causing
`turn_index` to advance and breaking idempotency on the retry.

### Dual-Write Gate: SQLite is the Idempotency Gate for JSONL

```python
# Source: verified on sqlite3 3.51.2 / Python 3.14.2
def write_event(self, event: dict) -> bool:
    """
    Returns True if event is new (write JSONL), False if duplicate (skip JSONL).
    Never raises. On SQLite error, logs and returns False.
    """
    try:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO events (event_id, event_type, conversation_id, "
            "message_id, timestamp, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (event["event_id"], event["event_type"], event["conversation_id"],
             event["message_id"], event["timestamp"], json.dumps(event))
        )
        self._conn.commit()
        is_new = cur.rowcount == 1
        if is_new:
            self._append_jsonl(event)  # only on new row
        return is_new
    except Exception as e:
        logger.error("Event write failed: %s", e)
        return False
```

**Known POC limitation:** If the process crashes between the SQLite commit and the JSONL
write, the two stores diverge for that event. This is an acceptable consistency gap for a
local POC. Production would require a WAL-log flush or transactional journal.

---

## SQLite Schema (AUDIT-02)

**Phase 1 subset.** Full schema (including deferred tables) recommended to create on init
for stability across phases.

### DDL

```sql
-- Recommended to execute at startup via db.py::init_schema()
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Phase 1 tables (active)
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,          -- ISO8601 UTC
    actor_id        TEXT NOT NULL DEFAULT 'local-user',
    assistant_model TEXT NOT NULL,
    guard_model     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',  -- active | closed
    turn_count      INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT                    -- JSON blob for future extension
);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    turn_index      INTEGER NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('prompt', 'response')),
    content_sha256  TEXT NOT NULL,          -- SHA-256 of full content
    content_preview TEXT,                   -- first 200 chars; may be truncated
    created_at      TEXT NOT NULL,
    UNIQUE(conversation_id, turn_index, direction)
);

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,       -- UUID5 deterministic
    event_type      TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    message_id      TEXT NOT NULL REFERENCES messages(message_id),
    timestamp       TEXT NOT NULL,
    actor_type      TEXT NOT NULL,          -- user | assistant | admin | system
    actor_id        TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'none',
    payload         TEXT NOT NULL           -- full JSON event blob
);

CREATE INDEX IF NOT EXISTS idx_events_conversation ON events(conversation_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- Phase 2–4 tables: create now (empty) for schema stability
CREATE TABLE IF NOT EXISTS classifications (
    classification_id TEXT PRIMARY KEY,
    event_id          TEXT NOT NULL REFERENCES events(event_id),
    overall_severity  TEXT NOT NULL DEFAULT 'none',
    categories        TEXT,                 -- JSON array
    llama_guard_label TEXT,
    deterministic_findings TEXT,            -- JSON array
    confidence        REAL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    approval_id     TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL REFERENCES events(event_id),
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,
    resolution      TEXT                    -- JSON: action + admin_id + notes
);

CREATE TABLE IF NOT EXISTS admin_actions (
    action_id       TEXT PRIMARY KEY,
    approval_id     TEXT REFERENCES approval_requests(approval_id),
    event_id        TEXT REFERENCES events(event_id),
    action_type     TEXT NOT NULL,          -- approve | reject | redact_resume | false_positive | escalate | export
    actor_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS redactions (
    redaction_id    TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL REFERENCES events(event_id),
    original_sha256 TEXT NOT NULL,
    redacted_sha256 TEXT NOT NULL,
    spans           TEXT NOT NULL,          -- JSON: [{start, end, label}]
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_versions (
    policy_version  TEXT PRIMARY KEY,
    content         TEXT NOT NULL,          -- full YAML as string
    activated_at    TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);
```

**Connection setup:**

```python
# Source: verified on sqlite3 3.51.2 / Python 3.14.2
import sqlite3, os, stat

def get_connection(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Restrict file permissions to owner only (CLAUDE.md security requirement)
    os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)
    return conn
```

Use **a single long-lived connection** for the event sink. WAL mode allows concurrent reads
from other connections (e.g., the `/events` GET route can open a separate read connection).

---

## JSONL Audit Writer (AUDIT-01)

### Event Schema

All Phase 1 events must conform to SKILL.md's required schema. The mandatory blocks
(`classification`, `policy`, `approval`, `siem`) must be present with Phase 1 defaults
even before classification logic exists.

### Phase 1 Default Values for Mandatory Schema Blocks

Because Phase 1 has no classifier, policy engine, or approval workflow, the following
defaults make events schema-valid without inventing Phase 2 logic:

```python
# Phase 1 defaults — to be replaced by Phase 2/3 adapters
CLASSIFICATION_DEFAULTS = {
    "overall_severity": "none",
    "categories": [],
    "llama_guard_label": "unknown",
    "llama_guard_categories": [],
    "deterministic_findings": [],
    "confidence": 0.0
}

POLICY_DEFAULTS = {
    "policy_version": "local-poc-v1",
    "decision": "allow",
    "matched_rules": []
}

APPROVAL_DEFAULTS = {
    "required": False,
    "approval_id": None,
    "status": "not_required"
}

SIEM_DEFAULTS = {
    "schema_version": "1.0",
    "ecs_compatible": True,
    "source": "local-ai-safety-gateway"
}
```

### Phase 1 Event Types Emitted

Only these events are emitted in Phase 1. All other SKILL.md event types (`prompt.classification.*`, `approval.*`, `response.blocked`, etc.) are emitted in Phases 2–3.

| Event Type | When Emitted |
|------------|-------------|
| `prompt.received` | Immediately on request receipt, before any Ollama call |
| `llm.request.started` | Just before calling Ollama assistant adapter |
| `llm.response.generated` | Immediately after Ollama returns a response |
| `response.delivered` | After response is persisted and before returning to caller |
| `system.error` | On any error (Ollama down, model missing, write failure) |

### Atomic JSONL Append

```python
# Source: verified on Python 3.14.2 / macOS
import fcntl, json, os

def _append_jsonl(self, event: dict) -> None:
    """Atomic append with exclusive file lock. Called only after SQLite confirms new row."""
    line = json.dumps(event, separators=(',', ':'), ensure_ascii=False) + '\n'
    with open(self._jsonl_path, 'a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    # Set restrictive permissions on first write
    os.chmod(self._jsonl_path, stat.S_IRUSR | stat.S_IWUSR)
```

Note: `fcntl` is POSIX-only (macOS/Linux). This is acceptable for the local POC which runs
on macOS (Darwin 25.2.0).

### correlation_id Definition

`correlation_id` is a **per-turn UUID4** generated fresh at the start of each `/chat`
request (not the same as `conversation_id`). It ties together all events emitted during
a single request/response cycle — `prompt.received`, `llm.request.started`,
`llm.response.generated`, and `response.delivered` all share the same `correlation_id`.
On a retry, a new `correlation_id` is generated (because it is a new HTTP request), but
the `event_id` deduplication still works because that is derived from the stable
`(conversation_id, message_id, event_type)` tuple. `correlation_id` is not used for
deduplication — only for log correlation across events of the same turn.

```python
correlation_id = str(uuid.uuid4())  # fresh per /chat request
```

### Complete Phase 1 Event Example (`prompt.received`)

```json
{
  "event_id": "b268fa1a-9042-5284-87dc-3cc95b821785",
  "event_type": "prompt.received",
  "timestamp": "2026-05-30T03:08:31.517670Z",
  "correlation_id": "8df2d6b6-14e8-4e41-88dd-2d67f0930d2b",
  "conversation_id": "conv-abc123",
  "message_id": "36986afa-89e1-5dbd-b242-8899399866e0",
  "actor": {
    "type": "user",
    "id": "local-user"
  },
  "model": {
    "assistant_model": "llama3.1",
    "guard_model": "llama-guard3",
    "provider": "ollama"
  },
  "content": {
    "direction": "prompt",
    "text_sha256": "e32ad2e14b669000...",
    "text_preview": "First 200 chars of prompt...",
    "contains_redactions": false
  },
  "classification": {
    "overall_severity": "none",
    "categories": [],
    "llama_guard_label": "unknown",
    "llama_guard_categories": [],
    "deterministic_findings": [],
    "confidence": 0.0
  },
  "policy": {
    "policy_version": "local-poc-v1",
    "decision": "allow",
    "matched_rules": []
  },
  "approval": {
    "required": false,
    "approval_id": null,
    "status": "not_required"
  },
  "siem": {
    "schema_version": "1.0",
    "ecs_compatible": true,
    "source": "local-ai-safety-gateway"
  }
}
```

---

## REL-03: Graceful Unavailability

The capture sequence is critical. `prompt.received` must be persisted **before** any Ollama
call. Only then is the "attempt is still audited" criterion satisfied.

### Capture Order for /chat

```
1. Parse and validate request
2. Resolve or create conversation_id
3. Compute message_id (stable, from conversation_id + turn_index)
4. Compute event_id for prompt.received (uuid5)
5. Write prompt.received event -> SQLite + JSONL  [DURABLE]
6. Write message row -> SQLite (direction='prompt')
7. Write llm.request.started event
8. Call OllamaAssistantAdapter.chat(messages)
   - If ConnectError: write system.error event; return HTTP 503 {"error": "assistant model unavailable", "audited": true}
   - If HTTP 404 from Ollama: write system.error event; return HTTP 503
   - If TimeoutException: write system.error event; return HTTP 503
9. Write llm.response.generated event
10. Write message row (direction='response')
11. Write response.delivered event
12. Return HTTP 200 with response content
```

### system.error Event Shape

```python
system_error_event = {
    "event_id": make_event_id(conversation_id, message_id, "system.error"),
    "event_type": "system.error",
    # ... standard fields ...
    "content": {
        "direction": "prompt",
        "text_sha256": prompt_sha256,
        "text_preview": "[error — content not generated]",
        "contains_redactions": False
    },
    # classification/policy/approval/siem defaults as above
}
```

---

## GATE-03: Enforcement Architecture

GATE-03 ("gateway is the only path to Ollama") is an architectural rule, not just a
convention. For a POC, enforce it via:

1. **Module boundary:** All Ollama calls live in `gateway/adapters/ollama_assistant.py`.
   No other file imports `httpx` or references `ollama_base_url` directly.

2. **Grep test (verifiable in CI):**
   ```python
   # tests/test_architecture.py
   import subprocess, re
   
   def test_no_direct_ollama_calls_outside_adapter():
       """GATE-03: Only the Ollama adapter module may reference the Ollama base URL."""
       result = subprocess.run(
           ['grep', '-r', r'11434\|ollama_base_url\|localhost.*ollama',
            'gateway/', '--include=*.py', '-l'],
           capture_output=True, text=True
       )
       offending_files = [
           f for f in result.stdout.strip().splitlines()
           if 'ollama_assistant' not in f and 'test_architecture' not in f
       ]
       assert offending_files == [], f"Direct Ollama references outside adapter: {offending_files}"
   ```

---

## Common Pitfalls

### Pitfall 1: UUID4 Breaks Idempotency
**What goes wrong:** Using `uuid.uuid4()` for `event_id` means reprocessing the same turn
generates a new ID; `INSERT OR IGNORE` doesn't deduplicate, JSONL gets duplicate lines.
**Why it happens:** SKILL.md schema shows a UUID field but doesn't specify determinism.
**How to avoid:** Use `uuid.uuid5(uuid.NAMESPACE_DNS, f"{conv}:{msg}:{event_type}")`.
**Warning signs:** `/events` returns duplicate event rows; JSONL line count > expected.

### Pitfall 2: Ollama 404 Treated as "Model Not Found" for Route
**What goes wrong:** Naively bubbling Ollama's HTTP 404 as a gateway 404 confuses callers
into thinking the gateway route `/chat` doesn't exist.
**Why it happens:** httpx response.status_code == 404 looks like a missing route.
**How to avoid:** Always return HTTP 503 (service unavailable) when Ollama is unreachable or
model is absent. Log the Ollama error body as the cause.
**Warning signs:** Client code gets a 404 and concludes the route was removed.

### Pitfall 3: Audit Event Written After Ollama Call Fails
**What goes wrong:** If `prompt.received` is written inside a try/except that also wraps
the Ollama call, a crash before the write means success criterion #5 fails ("attempt is
still audited").
**Why it happens:** Putting the event write and the model call in the same try block.
**How to avoid:** Persist `prompt.received` in a separate try block before the Ollama call.
**Warning signs:** Failing the "model stopped" test shows no SQLite row for the attempt.

### Pitfall 4: Binding Gateway to 0.0.0.0
**What goes wrong:** Gateway becomes accessible on LAN interfaces, violating the local-only
security requirement from CLAUDE.md.
**Why it happens:** Default uvicorn behavior or developer convenience.
**How to avoid:** Always set `host=settings.gateway_host` (defaults to `127.0.0.1`).

### Pitfall 5: Missing Phase 1 Default Values in Event Schema
**What goes wrong:** Emitting events without `classification`, `policy`, `approval`, `siem`
blocks causes SKILL.md validation failures and breaks Phase 2 which expects these fields.
**Why it happens:** Skipping "not applicable yet" fields to reduce code in Phase 1.
**How to avoid:** Always include all mandatory blocks with the Phase 1 defaults documented
above. Never omit mandatory schema fields.

### Pitfall 6: /api/chat Conversation History Scope
**What goes wrong:** Sending only the latest prompt to Ollama discards conversation context.
Sending all accumulated messages can hit model context limits and increases latency.
**Why it happens:** `conversation_id` tracks a conversation in SQLite but what Ollama
actually receives is a separate decision.
**How to avoid:** See Open Questions — this needs a decision before implementation.

### Pitfall 7: JSONL Written Before SQLite Commit
**What goes wrong:** If the process crashes between JSONL write and SQLite commit (or if
using opposite order), JSONL has events that SQLite doesn't, or vice versa.
**Why it happens:** Writing both stores independently without a gate.
**How to avoid:** SQLite commit is always first; JSONL append happens only after
`rowcount == 1` is confirmed.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | All | Yes | 3.14.2 | — |
| fastapi | Gateway | Yes | 0.136.1 | — |
| pydantic-settings | Config | Yes | 2.14.1 | — |
| uvicorn | ASGI server | Yes | 0.47.0 | — |
| httpx | Ollama adapter | Yes | 0.28.1 | — |
| starlette | TestClient | Yes | 1.0.0 | — |
| pydantic | Data models | Yes | 2.13.4 | — |
| sqlite3 (stdlib) | Audit data layer | Yes | 3.51.2 | — |
| Ollama | Model serving | Yes | 0.22.1 | — |
| llama-guard3:latest | Guard model (Phase 2) | Yes | 8B Q4_K_M | — |
| llama3.1:latest | Default assistant model (`OLLAMA_ASSISTANT_MODEL`) | Yes | 8.0B llama | — |
| gpt-oss:latest | Assistant alt (20.9B) | Yes | — | — |
| mistral-small3.2:latest | Assistant alt (24.0B) | Yes | — | — |
| pytest | Test runner | No | 9.0.3 (PyPI) | Install: `pip install pytest==9.0.3` |
| aiosqlite | Async SQLite | No | N/A | NOT NEEDED — use stdlib sqlite3 |

**Missing dependencies with no fallback:** none — all required packages are installed or
available on PyPI.

**Missing dependencies with fallback:** pytest is not globally installed. Must be added to
project dev requirements.

---

## Testing Approach (GATE-03 / REL-03 Verifiable)

Phase 1 has no live model dependency in tests. All five success criteria are unit/integration
testable with stubs.

### Test Strategy

| Success Criterion | Test Type | Approach |
|------------------|-----------|----------|
| SC-1: POST /chat returns response | Integration | TestClient + MockAssistantAdapter returning fixed content |
| SC-2: Client reaches model only through gateway | Architecture | grep test (test_architecture.py) |
| SC-3: Each turn produces JSONL + SQLite rows | Integration | After TestClient call, assert SQLite rowcount and JSONL line count |
| SC-4: No duplicate events on reprocess | Unit | Call write_event() twice with same inputs; assert rowcount=1 and JSONL has 1 line |
| SC-5: Graceful error + audit preserved | Integration | MockAssistantAdapter raises OllamaUnavailableError; assert HTTP 503 and SQLite has prompt.received |

### Stub Adapter for Testing

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from gateway.main import app
from gateway.adapters.protocols import IAssistantAdapter

class MockAssistantAdapter:
    async def chat(self, messages: list[dict]) -> dict:
        return {"content": "mock response", "model": "mock-llama"}

class UnavailableAssistantAdapter:
    async def chat(self, messages: list[dict]) -> dict:
        from gateway.adapters.ollama_assistant import OllamaUnavailableError
        raise OllamaUnavailableError("model stopped")

@pytest.fixture
def client(tmp_path):
    from gateway.settings import settings
    settings.sqlite_db_path = str(tmp_path / "test.db")
    settings.jsonl_audit_path = str(tmp_path / "test.jsonl")
    # Override assistant adapter
    from gateway.routes.chat import get_assistant_adapter
    app.dependency_overrides[get_assistant_adapter] = lambda: MockAssistantAdapter()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `asynccontextmanager lifespan` | FastAPI 0.93+ | `on_event` deprecated; use lifespan |
| Pydantic v1 `class Config:` | Pydantic v2 `model_config = {}` dict | Pydantic 2.0 | `class Config` still works but deprecated |
| `from pydantic import BaseSettings` | `from pydantic_settings import BaseSettings` | Pydantic 2.0 | Moved to separate package |
| Starlette `@app.on_event` | FastAPI lifespan | FastAPI 0.93+ | — |

**Deprecated/outdated:**
- `@app.on_event("startup"/"shutdown")`: still works but deprecated. Use lifespan.
- `class Config` in Pydantic models: use `model_config = {...}` dict in v2.
- `from pydantic import BaseSettings`: moved to `pydantic_settings` in v2.

---

## Open Questions

1. **Conversation history scope for Ollama calls**
   - What we know: The gateway has `conversation_id` and accumulates messages in SQLite.
   - What's unclear: Does `/chat` send only the latest user message to Ollama, or does it
     send accumulated history? Sending history enables multi-turn coherence but grows the
     context window with each turn and increases latency.
   - Recommendation: For Phase 1 MVP, send only the current message (no history). Flag in
     the `/chat` request body as `include_history: bool = False` so the planner can implement
     history replay as a follow-on.

2. **`conversation_id` assignment**
   - What we know: SKILL.md schema requires a `conversation_id` on every event.
   - What's unclear: Should the client supply it, or should the gateway generate and return
     it? If the client doesn't send one, the gateway can generate a UUID4 per-request
     (treating each request as a single-turn conversation) or return the generated ID in the
     response so the client can reuse it on follow-up turns.
   - Recommendation: Accept `conversation_id` in the request body as an optional field;
     generate one (UUID4) if absent and return it in the response.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `fcntl.flock` is available on the deployment OS (macOS Darwin 25.2.0) | JSONL writer | If ever deployed on Windows, `fcntl` is unavailable; use `msvcrt.locking` or a lock file instead |
| A2 | Phase 1 does not need multi-turn conversation history — single-message calls to Ollama are sufficient | Ollama adapter | If coherent multi-turn chat is required for the Phase 1 demo, the adapter needs to load history from SQLite |
| A3 | Ollama runs as a persistent daemon (not started per-request) | REL-03 | If Ollama is started on-demand, ConnectError on first request may be transient; retry logic may be warranted |

---

## Sources

### Primary (HIGH confidence — empirically verified on target machine)

- Ollama 0.22.1 HTTP API — verified by direct `curl` against running instance at `localhost:11434`
  - `/api/chat` request/response shape
  - `/api/generate` request/response shape
  - `/api/version` health endpoint
  - `/api/show` model info endpoint
  - `/api/tags` model list endpoint
  - Error shapes (HTTP 404 for missing model; `httpx.ConnectError` for process down)
- fastapi 0.136.1 — verified import + TestClient usage on Python 3.14.2
- pydantic 2.13.4 — verified import on Python 3.14.2
- pydantic-settings 2.14.1 — verified `.env` file reading + `model_config` pattern
- uvicorn 0.47.0 — verified import on Python 3.14.2
- httpx 0.28.1 — verified `AsyncClient`, `ConnectError`, timeout behavior
- starlette 1.0.0 — verified `TestClient` with `Depends` override
- sqlite3 3.51.2 — verified WAL mode, `INSERT OR IGNORE`, rowcount, `os.chmod(600)`
- uuid stdlib — verified `uuid5` deterministic IDs, `uuid4` for random IDs
- SKILL.md v1.0 — canonical event schema, event type names, severity levels

### Secondary (MEDIUM confidence)

- PyPI version index — `pip3 index versions` used to confirm latest available versions for
  fastapi (0.136.3), uvicorn (0.48.0), pydantic-settings (2.14.1), pytest (9.0.3),
  pytest-asyncio (1.4.0), aiosqlite (0.22.1)
- slopcheck 0.6.1 — all 8 packages rated [OK] on PyPI (slopcheck run 2026-05-30)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages import-verified on Python 3.14.2 at the machine's installed versions
- Ollama API: HIGH — verified by live curl against Ollama 0.22.1 running on target machine
- SQLite patterns: HIGH — verified by running sqlite3 stdlib code
- Idempotency strategy: HIGH — uuid5 and INSERT OR IGNORE rowcount both verified in Python
- Architecture patterns: HIGH — FastAPI DI, lifespan, TestClient all code-verified
- Pitfalls: HIGH — most derived from empirical test failures observed during research

**Research date:** 2026-05-30
**Valid until:** 2026-08-30 (stable libraries; Ollama API is stable within 0.x line)

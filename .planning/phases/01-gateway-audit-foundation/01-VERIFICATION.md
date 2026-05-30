---
phase: 01-gateway-audit-foundation
verified: 2026-05-30T04:35:43Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification: false
---

# Phase 1: Gateway & Audit Foundation Verification Report

**Phase Goal:** A local FastAPI gateway is the only path to Ollama; sending a prompt returns an assistant response and every prompt/response turn is captured to append-only JSONL and SQLite.
**Verified:** 2026-05-30T04:35:43Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Pytest Result

```
38 passed in 0.31s
```

All 38 tests pass. Command: `.venv/bin/pytest -v`

---

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC-1 | POST /chat returns an assistant response generated via Ollama | VERIFIED | `test_chat_returns_200_with_expected_shape`: HTTP 200, body has `response`+`conversation_id`+`message_id`+`model`. OllamaAssistantAdapter.chat() POSTs to `{base_url}/api/chat`, parses `message.content`/`model` (ollama_assistant.py:56-83). Tested via MockAssistantAdapter (offline); live Ollama runtime not exercised — see note below. |
| SC-2 | Chat client reaches assistant model only through gateway | VERIFIED | `test_no_direct_ollama_calls_outside_adapter` (test_architecture.py): grep confirms `11434`/`ollama_base_url` appear only in `gateway/adapters/ollama_assistant.py` and `gateway/settings.py` (config field, not HTTP call). No other module imports httpx and calls Ollama. GATE-03 enforced as a CI-level static check. |
| SC-3 | Each turn produces JSONL audit event and SQLite rows | VERIFIED | `test_chat_writes_exactly_1_conversation_2_messages_4_events`: asserts 1 conversation, 2 messages (prompt+response), exactly 4 events (`prompt.received`, `llm.request.started`, `llm.response.generated`, `response.delivered`), JSONL line count == 4. `test_jsonl_line_count_equals_sqlite_events_after_turn` (test_audit.py): dual-store sync confirmed. |
| SC-4 | Re-processing the same turn does not duplicate audit events | VERIFIED | `TestEventSinkIdempotency::test_first_write_count_equals_1` + `test_second_write_count_still_1` + `test_jsonl_has_exactly_one_line_after_duplicate_write`: UUID5 `INSERT OR IGNORE` gate confirmed. `test_chat_retry_failed_turn_does_not_duplicate_events` (test_chat.py): retrying a 503 with same `conversation_id` keeps `prompt.received` and `system.error` rowcount at 1 and JSONL line count at 1. |
| SC-5 | With assistant model stopped, /chat returns graceful 503 and attempt is still audited | VERIFIED | `test_chat_model_unavailable_returns_503_with_audit`: HTTP 503 body has `conversation_id`, `message_id`, `error: "assistant model unavailable"`, `audited: true`. `test_chat_model_unavailable_writes_system_error_event`: `prompt.received` + `system.error` events present in SQLite; 0 response-direction messages rows; `llm.response.generated` absent. Both use `UnavailableAssistantAdapter` (raises `OllamaUnavailableError`). |

**Score: 5/5 truths verified**

---

### Required Artifacts

| Artifact | Purpose | Exists | Substantive | Wired | Status |
|----------|---------|--------|-------------|-------|--------|
| `gateway/main.py` | FastAPI app, lifespan, router mounts, 127.0.0.1 binding | Yes | Yes (lifespan opens httpx.AsyncClient + EventSink + OllamaAssistantAdapter, stores on app.state) | Yes (all 4 routers mounted) | VERIFIED |
| `gateway/adapters/ollama_assistant.py` | Only module calling Ollama :11434 | Yes | Yes (POST /api/chat, maps ConnectError/Timeout/404 → OllamaUnavailableError, health probe) | Yes (injected via get_assistant_adapter in chat route) | VERIFIED |
| `gateway/audit/event_sink.py` | Dual-write: SQLite INSERT OR IGNORE gates JSONL append | Yes | Yes (validates mandatory fields, raises ValueError pre-INSERT, fcntl.flock, fsync, chmod 0o600) | Yes (used in chat.py write_event calls, upsert_conversation, insert_message) | VERIFIED |
| `gateway/audit/db.py` | SQLite DDL, WAL, foreign_keys=ON, chmod 0o600 | Yes | Yes (8 tables: conversations, messages, events + 5 deferred; 3 indexes) | Yes (imported by EventSink.__init__ and events.py) | VERIFIED |
| `gateway/audit/schema.py` | AuditEvent Pydantic model + build_event with SKILL.md blocks | Yes | Yes (all 13 required SKILL.md fields present; Phase-1 defaults: classification, policy, approval, siem) | Yes (build_event called in chat.py for every event type) | VERIFIED |
| `gateway/routes/chat.py` | POST /chat capture-then-call-then-capture flow | Yes | Yes (10-step capture order: FK-safe message insert before events, OllamaUnavailableError → 503 + system.error) | Yes (imported in main.py, get_assistant_adapter Depends injection) | VERIFIED |
| `gateway/routes/events.py` | GET /events — read route over separate WAL read connection | Yes | Yes (conversation_id/event_type/limit filters, separate get_connection() per request) | Yes (mounted in main.py) | VERIFIED |
| `gateway/routes/stubs.py` | 501 Not Implemented for Phase 2-4 routes | Yes | Yes (8 stub routes: /classify/prompt, /classify/response, /approvals, /approvals/{id}/approve, /approvals/{id}/reject, /approvals/{id}/redact-resume, /policy, /export/jsonl) | Yes (mounted in main.py) | VERIFIED |
| `gateway/adapters/protocols.py` | All 6 adapter Protocol interfaces (GATE-04) | Yes | Yes (IAssistantAdapter, IEventSink, IGuardAdapter, IScannerAdapter, IPolicyAdapter, IApprovalAdapter — all @runtime_checkable) | Yes (IAssistantAdapter used as type hint in chat.py) | VERIFIED |

---

### Key Link Verification

| From | To | Via | Status | Evidence |
|------|----|-----|--------|----------|
| `gateway/routes/chat.py` | `gateway/adapters/ollama_assistant.py` | `get_assistant_adapter` Depends injection | WIRED | chat.py:65-72 defines dependency; conftest overrides it; import of `OllamaUnavailableError` at chat.py:88 |
| `gateway/routes/chat.py` | `gateway/audit/event_sink.py` | `sink.write_event` before and after Ollama call | WIRED | chat.py:141, 153, 173, 212, 224 — 5 write_event calls covering all 4 event types + system.error |
| `gateway/audit/event_sink.py` | `gateway/audit/ids.py` | `make_event_id` UUID5 determinism | WIRED | schema.py:170 calls make_event_id; event_sink.py uses the event_id produced by build_event |
| `gateway/routes/chat.py` | `gateway/adapters/ollama_assistant.py` | `OllamaUnavailableError` caught → system.error event → HTTP 503 | WIRED | chat.py:160-182 |
| `gateway/routes/events.py` | `gateway/audit/db.py` | separate WAL read connection | WIRED | events.py:49 `conn = get_connection(settings.sqlite_db_path)` |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `gateway/routes/chat.py` | `result` (assistant response) | `adapter.chat(messages_for_ollama)` → OllamaAssistantAdapter.chat() → POST /api/chat | Yes (parses `data["message"]["content"]` + `data["model"]` from Ollama JSON) | FLOWING (via mock in tests; structurally correct for live Ollama) |
| `gateway/routes/events.py` | `rows` | `SELECT payload FROM events ... LIMIT ?` on real SQLite db | Yes (full event JSON stored in payload column) | FLOWING |
| `gateway/audit/event_sink.py` | `is_new` | `INSERT OR IGNORE` rowcount gate | Yes (rowcount==1 is only true on actual new row) | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| UUID5 determinism | `.venv/bin/python -c "from gateway.audit.ids import make_event_id; a=make_event_id('c','m','prompt.received'); b=make_event_id('c','m','prompt.received'); c=make_event_id('c','m','llm.response.generated'); assert a==b and a!=c; print('ids-ok')"` | ids-ok | PASS |
| DDL creates all 8 tables | `.venv/bin/python -c "..."` (init_schema check) | ddl-ok. Tables: admin_actions, approval_requests, classifications, conversations, events, messages, policy_versions, redactions | PASS |
| EventSink idempotency | Manual Python spot-check: first write → True, COUNT==1; second write → False, COUNT==1, JSONL lines==1 | idempotency-ok | PASS |
| Actor block on all event types | build_event for all 5 Phase-1 types | All 5 types have actor.type + actor.id | PASS |
| SKILL.md schema all 13 fields | build_event output field check | All required fields present, Phase-1 defaults correct | PASS |
| ValueError on missing actor | sink.write_event with actor popped | ValueError raised: "actor_type, actor_id" | PASS |
| GATE-03 boundary grep | `grep -rEl "11434\|ollama_base_url" gateway/ --include=*.py | grep -vE "adapters/ollama_assistant|gateway/settings.py"` → empty | gate03-ok | PASS |

---

### Requirements Coverage

| Requirement | Plan | Description | Status | Evidence |
|-------------|------|-------------|--------|----------|
| GATE-01 | 01-01 | Gateway captures every inbound user prompt before it reaches assistant model | SATISFIED | chat.py steps 5-7: PROMPT messages row + prompt.received event written BEFORE adapter.chat() call (chat.py:117-153) |
| GATE-02 | 01-01 | Gateway captures every assistant response before returned to user | SATISFIED | chat.py steps 9-11: RESPONSE messages row + llm.response.generated + response.delivered written before HTTP 200 return (chat.py:191-224) |
| GATE-03 | 01-01 | Gateway is the only path to Ollama | SATISFIED | test_no_direct_ollama_calls_outside_adapter passes; only ollama_assistant.py + settings.py reference the Ollama URL |
| GATE-04 | 01-01 | Gateway exposes adapter interfaces for all 6 adapter types | SATISFIED | protocols.py declares IAssistantAdapter, IEventSink, IGuardAdapter, IScannerAdapter, IPolicyAdapter, IApprovalAdapter |
| GATE-05 | 01-01, 01-02 | Full route surface exposed | SATISFIED | /chat, /health, /events (live); /classify/prompt, /classify/response, /approvals, /approvals/{id}/approve, /approvals/{id}/reject, /approvals/{id}/redact-resume, /policy, /export/jsonl (501 stubs) |
| AUDIT-01 | 01-01, 01-02 | Append-only JSONL events with stable SIEM schema | SATISFIED | event_sink.py _append_jsonl: fcntl.flock + fsync + chmod 0o600; schema.py has all 13 SKILL.md required fields; siem block with schema_version=1.0 |
| AUDIT-02 | 01-01 | SQLite stores conversations, messages, events (+ 5 deferred tables) | SATISFIED | db.py DDL: all 8 tables created; test_chat verifies 1 conv + 2 msg + 4 event rows per turn |
| AUDIT-04 | 01-02 | Event writes are idempotent | SATISFIED | UUID5 event_id + INSERT OR IGNORE; TestEventSinkIdempotency suite (10 tests) + test_chat_retry_failed_turn_does_not_duplicate_events |
| REL-03 | 01-02 | Assistant-model unavailability returns graceful error while preserving audit events | SATISFIED | OllamaUnavailableError → system.error event written → HTTP 503 with audited:true body; prompt.received persisted before Ollama call |

---

### Anti-Patterns Found

No `TBD`, `FIXME`, or `XXX` debt markers in any gateway source file.
No stub return patterns (`return null`, `return {}`, `return []`) in non-stub gateway code.
All Phase 2-4 routes return HTTP 501 via a shared `_NOT_IMPLEMENTED` sentinel — correctly scoped.

---

### Findings

**WARNING (non-blocking): `system.error` event sha256 hashes the error placeholder, not the original prompt.**

- File: `gateway/routes/chat.py:163-172`
- Plan 01-02 specified: `text_sha256 = SHA-256 of the prompt`, `text_preview = '[error — content not generated]'`
- Implementation: `text="[error — content not generated]"` is passed to `build_event`, which derives both `text_sha256` and `text_preview` from this argument — so `text_sha256` is the hash of the placeholder string, not the original prompt.
- Impact: Minor spec fidelity issue. The original prompt's sha256 is correctly preserved in the `prompt.received` event and the `messages` row (content_sha256), so SC-5 ("attempt still audited") is fully met. This does not block Phase 1 completion.
- Suggested fix for Phase 2: pass `prompt_text` as the text argument and `text_preview` override as `"[error — content not generated]"` (or add a separate `text_preview` argument to `build_event`).

**INFO: Live Ollama not exercised by the test suite.**

All tests use `MockAssistantAdapter` / `UnavailableAssistantAdapter` via `app.dependency_overrides`. The adapter code is structurally correct (POST to `/api/chat`, response parsing, error mapping), but no end-to-end test fires a real Ollama request. This is by design for an offline POC test suite. Before demo, verify: `ollama pull llama3.1 && uvicorn gateway.main:app --host 127.0.0.1 --port 8000`, then `curl -s http://localhost:8000/chat -d '{"messages":[{"role":"user","content":"hello"}]}' -H "Content-Type: application/json"`.

---

### Human Verification Required

None for automated criteria. All 5 success criteria and all 9 requirements are verifiable programmatically and have been verified.

One recommended runtime smoke-test (not blocking):

**Test:** Start Ollama with a real model and send a POST /chat request.
**Expected:** HTTP 200 with actual assistant-generated content, and a real JSONL event is appended.
**Why human:** Requires a live Ollama process and a pulled model — cannot be run in an offline test environment.

---

## Summary

Phase 1 delivers a complete, well-tested audit gateway. All 5 success criteria are met with direct evidence from the test suite and source code inspection. All 9 Phase-1 requirements (GATE-01..05, AUDIT-01, AUDIT-02, AUDIT-04, REL-03) are satisfied. The codebase has no unresolved debt markers, no stub implementations in active code paths, and the FK-safety ordering invariant (messages rows before events) is enforced and independently tested. The single WARNING (system.error sha256) is a minor spec-fidelity issue that does not affect audit completeness. Status: **PASSED**.

---

_Verified: 2026-05-30T04:35:43Z_
_Verifier: Claude (gsd-verifier)_

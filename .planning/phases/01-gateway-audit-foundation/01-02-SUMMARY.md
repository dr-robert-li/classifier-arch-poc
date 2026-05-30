---
phase: 01-gateway-audit-foundation
plan: "02"
subsystem: gateway
tags: [fastapi, sqlite, jsonl, audit, idempotency, rel-03, events-route, wal, tdd]
dependency_graph:
  requires:
    - gateway-enforcement-boundary      # from 01-01
    - audit-event-sink                  # from 01-01
    - post-chat-capture-flow            # from 01-01
  provides:
    - events-read-route                 # GET /events live with filters
    - rel-03-retry-idempotency-locked   # regression tests locking retry behavior
    - dual-store-sync-proof             # JSONL == SQLite count test
  affects:
    - phase-04-web-ui                   # /events is the data source for the admin UI
tech_stack:
  added: []
  patterns:
    - WAL concurrent read (separate get_connection per /events request, writer stays open)
    - TDD RED→GREEN for /events (genuine RED; GREEN after events.py + main.py mount)
    - Regression-lock TDD for REL-03 (behavior pre-existed; tests lock the invariant)
key_files:
  created:
    - gateway/routes/events.py
    - tests/test_events.py
  modified:
    - gateway/main.py        # mount events_router
    - tests/test_chat.py     # 2 new REL-03/retry tests
    - tests/test_audit.py    # TestJSONLSQLiteSync class (1 new test)
decisions:
  - "GET /events opens a separate get_connection per request and closes it in finally — does not reuse the writer connection; WAL allows concurrent reads"
  - "GATE-03 comment-text pitfall: docstring mentioning port 11434 trips the grep test; rephrased to refer only to 'Ollama' without the port number"
  - "REL-03 retry idempotency behavior was already correct in 01-01; Task 1 tests added as a regression lock, no production change needed"
  - "events.py query builds WHERE clause dynamically — no SQL injection risk (params are bound; table/column names are hardcoded)"
metrics:
  duration_minutes: 45
  completed_date: "2026-05-30"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 3
---

# Phase 1 Plan 2: Correctness Layer — REL-03, Idempotency, GET /events Summary

**One-liner:** GET /events route with WAL concurrent read and conversation_id/event_type/limit filters; REL-03 retry-idempotency locked by two new test assertions; 38/38 tests green.

## What Was Built

### Component Inventory

| File | Role |
|------|------|
| `gateway/routes/events.py` | `GET /events` read route — separate WAL read connection, supports `?conversation_id`, `?event_type`, `?limit` (default 100), returns deserialized `payload` JSON per row ordered by timestamp |
| `gateway/main.py` | Mounts `events_router` so `/events` is live (was 404 before this plan) |
| `tests/test_events.py` | 8 new tests: GET /events 200, turn events returned, conversation_id filter, event_type filter, limit param, timestamp ordering, WAL concurrent read, required field schema check |
| `tests/test_chat.py` | +2 tests: `system.error` present on failure path + no response row; retry with same `conversation_id` → COUNT==1 for `prompt.received` + `system.error`, JSONL line==1 for prompt event_id |
| `tests/test_audit.py` | +1 test: `TestJSONLSQLiteSync` — JSONL line count == SQLite event rowcount after a turn (SC-3) |

### GET /events Route Design

The route opens a **separate** SQLite connection per request via `get_connection()` and closes it in a `finally` block. The EventSink writer connection (stored in `app.state.event_sink._conn`) stays open for the lifespan of the app — WAL mode allows both connections to operate concurrently without lock contention. This satisfies T-01-10.

## Test Coverage

| Test File | Tests | New This Plan | What It Covers |
|-----------|-------|---------------|----------------|
| `test_chat.py` | 9 | +2 | system.error on 503; retry COUNT==1 JSONL==1 (SC-4/SC-5 completeness) |
| `test_audit.py` | 12 | +1 | SC-3 JSONL==SQLite sync; SC-4 idempotency; ValueError guards |
| `test_events.py` | 8 | +8 | GET /events all acceptance criteria (SC-3 readable side) |
| `test_health.py` | 3 | 0 | Health shape, mock-ok, unavailable-degraded (unchanged) |
| `test_architecture.py` | 1 | 0 | GATE-03 grep — passes with events.py in scope |
| **Total** | **38** | **+11** | **All 01-02 acceptance criteria covered** |

**Final pytest result:** `38 passed in 0.33s`

## Success Criteria Verification

| SC | Status | Evidence |
|----|--------|---------|
| REL-03: 503 with audited body + system.error written | PASS | `test_chat_model_unavailable_writes_system_error_event` |
| REL-03: no response row / no llm.response.generated on failure | PASS | same test; asserts resp_msg_count==0 and event_type not in types |
| SC-4: retry with same conversation_id → COUNT==1, JSONL==1 | PASS | `test_chat_retry_failed_turn_does_not_duplicate_events` |
| SC-3: GET /events returns turn events | PASS | `test_events_returns_turn_events_after_chat` |
| SC-3: conversation_id/event_type/limit filters | PASS | `test_events_conversation_id_filter`, `test_events_event_type_filter`, `test_events_limit_param` |
| WAL concurrent read | PASS | `test_events_concurrent_read_while_writer_open` |
| GATE-03: events.py has no Ollama refs | PASS | `test_no_direct_ollama_calls_outside_adapter` |
| SC-3: JSONL line count == SQLite event count | PASS | `TestJSONLSQLiteSync.test_jsonl_line_count_equals_sqlite_events_after_turn` |

## TDD Gate Compliance

| Task | RED | GREEN |
|------|-----|-------|
| Task 1 (REL-03 retry tests) | N/A — behavior pre-existed from 01-01; tests added as regression lock; per TDD guidance, existing behavior makes RED→GREEN inapplicable | Tests pass on first run (expected; documented as deviation) |
| Task 2 (GET /events) | CONFIRMED RED — 8 tests, all 404 before events.py created | CONFIRMED GREEN — 38/38 pass after events.py + main.py mount |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] GATE-03 docstring violation in events.py**
- **Found during:** First full `pytest` run after implementing events.py (GREEN phase)
- **Issue:** The module docstring in `gateway/routes/events.py` contained the literal string `11434` in an explanatory comment. The GATE-03 grep test (`grep -rEl "11434|ollama_base_url" gateway/`) matched the comment text and flagged `events.py` as a GATE-03 violation.
- **Fix:** Rephrased the docstring to say "No Ollama references — this module is SQLite-only (GATE-03 boundary preserved)" without including the port number.
- **Files modified:** `gateway/routes/events.py` (docstring only)
- **Commit:** 68af135 (included in GREEN commit)

### Scope clarification: Task 1 production code

**2. [Scope] REL-03 503 path was already complete in 01-01**
- The 01-01 summary documented that "503 failure path basic implementation included in chat.py" as a deviation. All 01-02 Task 1 acceptance criteria (503 body shape, system.error event, no response row, retry idempotency) were already satisfied by 01-01's implementation.
- Task 1 for 01-02 therefore consists entirely of regression-lock tests, with no production code changes. This matches the advisor's guidance: "Real RED→GREEN only applies to /events."
- Documented in commit message as: "Deviation [Rule 2]: behavior already present from 01-01; tests added to lock it in."

## Threat Flags

No new threat surface beyond the plan's `<threat_model>`. All five threat mitigations from the plan are implemented:

| T-ID | Mitigation Applied |
|------|--------------------|
| T-01-06 | prompt.received persisted before Ollama call; system.error written on failure (tested) |
| T-01-07 | UUID5 event_id + INSERT OR IGNORE: retry COUNT==1 and JSONL==1 (tested) |
| T-01-08 | 503 with audited body; httpx timeout bounded by settings; no unbounded hang |
| T-01-09 | system.error stores only prompt SHA-256 and fixed preview text |
| T-01-10 | /events is read-only SELECT over a separate connection; no write path exposed |

## Known Stubs

All stubs carried forward from 01-01 (unchanged):

| Stub | File | Reason |
|------|------|--------|
| Phase 2 guard adapter | `gateway/adapters/stubs.py:NullGuardAdapter` | Classification not yet implemented (Phase 2) |
| Phase 2 scanner adapter | `gateway/adapters/stubs.py:NullScannerAdapter` | DLP/secrets not yet implemented (Phase 2) |
| Phase 3 policy adapter | `gateway/adapters/stubs.py:NullPolicyAdapter` | Policy engine not yet implemented (Phase 3) |
| Phase 3 approval adapter | `gateway/adapters/stubs.py:NullApprovalAdapter` | Approval workflow not yet implemented (Phase 3) |
| 7 Phase 2-4 routes | `gateway/routes/stubs.py` | GATE-05: returns 501; /events is now live this plan |
| `classification` block | all events | Phase 1 defaults only; real classification in Phase 2 |
| `policy` block | all events | Phase 1 defaults only; real policy in Phase 3 |
| `approval` block | all events | Phase 1 defaults only; real approvals in Phase 3 |

None prevent the plan's goal (correct audit trail under retry and failure) from being achieved.

## Self-Check: PASSED

### Files check
- [x] gateway/routes/events.py — FOUND
- [x] gateway/main.py — MODIFIED (events_router mounted)
- [x] tests/test_events.py — FOUND
- [x] tests/test_chat.py — MODIFIED (+2 tests)
- [x] tests/test_audit.py — MODIFIED (+1 test class)

### Commits check
- [x] e017e46 — test(01-02): lock REL-03 retry-idempotency and system.error audit
- [x] 6fe92ae — test(01-02): add failing tests for GET /events and JSONL/SQLite sync (RED)
- [x] 68af135 — feat(01-02): implement GET /events read route with WAL concurrent read (GREEN)

### pytest result
`38 passed in 0.33s` — zero failures, zero warnings.

### Acceptance criteria check
- [x] GET /events returns JSON list of persisted events
- [x] conversation_id filter returns only that conversation's events
- [x] event_type filter returns only matching event types
- [x] limit param honoured (default 100)
- [x] WAL concurrent read confirmed by test (writer conn open during GET /events)
- [x] FK parents seeded before write_event test (foreign_keys=ON enforced)
- [x] COUNT==1 after first write + COUNT==1 after second write (SC-4, BLOCKER 3)
- [x] JSONL has exactly 1 line after duplicate write (SC-4)
- [x] ValueError on missing actor/actor.type/actor.id — raised, NOT False (SC-4 negative)
- [x] JSONL line count == SQLite events rowcount after one turn (SC-3)
- [x] system.error present on 503 path; no response row; no llm.response.generated
- [x] Retry with same conversation_id → prompt.received COUNT==1, JSONL==1
- [x] All tests run without live Ollama (MockAssistantAdapter / UnavailableAssistantAdapter)
- [x] 38/38 tests pass (pytest -q)
- [x] GATE-03 grep: gate03-ok (events.py contains no Ollama references)

# Walking Skeleton — Local AI Safety SIEM Gateway POC

**Phase:** 1
**Generated:** 2026-05-30

## Capability Proven End-to-End

> One sentence: the smallest user-visible capability that exercises the full stack.

A chat client sends a prompt to `POST /chat`, the gateway calls a real local Ollama
assistant model, returns the generated response, and the full prompt/response turn is
durably captured to both SQLite and an append-only JSONL audit log — proving the gateway
is the sole enforcement path to Ollama with a working audit trail.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language / runtime | Python 3.14.2 | Installed on target; matches RESEARCH-verified stack |
| Web framework | FastAPI 0.136.1 + uvicorn 0.47.0 | Pydantic v2 native, async DI, auto OpenAPI, TestClient; verified on target |
| Config | pydantic-settings 2.14.1 via `.env` | Type-coerced env config; no hardcoded URLs/paths/models |
| HTTP client (Ollama) | httpx 0.28.1 `AsyncClient` | Async, timeouts, `ConnectError` mapping; same client as FastAPI TestClient |
| Model serving | Ollama 0.22.1 at `http://localhost:11434`, `POST /api/chat`, `stream=false` | Verified live on target; assistant model `llama3.1` default via `OLLAMA_ASSISTANT_MODEL` |
| State store | stdlib `sqlite3` 3.51.2, WAL mode, single serialized writer | Append-only audit; trivial idempotency reasoning; no extra dep (aiosqlite rejected) |
| Audit log | Append-only JSONL, `fcntl.flock` + `fsync`, perms 600 | SIEM-mappable; atomic POSIX append (macOS target) |
| Idempotency | Deterministic UUID5 `event_id` from `(conversation_id, message_id, event_type)`; `INSERT OR IGNORE` rowcount gates JSONL append | UUID4 breaks dedup; SQLite insert is the single idempotency gate |
| Adapter pattern | Python `Protocol` interfaces + FastAPI `Depends`, overridable in tests via `dependency_overrides` | GATE-04; lets Phase 2–3 swap guard/scanner/policy/approval without restructuring |
| Network binding | `127.0.0.1` only (`gateway_host` default) | Local-only POC security requirement; never `0.0.0.0` |
| Test runner | pytest 9.0.3 with `MockAssistantAdapter` (no live Ollama in tests) | All 5 success criteria testable offline |
| Directory layout | `gateway/{main,settings}.py`, `gateway/adapters/`, `gateway/audit/`, `gateway/routes/`, `tests/` | Mirrors RESEARCH recommended structure; module boundary enforces GATE-03 |

## Stack Touched in Phase 1

- [x] Project scaffold (FastAPI app, `.env`/settings, lifespan, pytest, requirements pinned)
- [x] Routing — `POST /chat` and `GET /health` live; Phase 2–4 routes stubbed at HTTP 501
- [x] Database — real SQLite write (`conversations`, `messages`, `events` rows) AND real read (`GET /events`)
- [x] Model call — real Ollama `POST /api/chat` through the single `OllamaAssistantAdapter`
- [x] Audit — real append-only JSONL write gated by the SQLite idempotency insert
- [x] Run — documented local full-stack command: `uvicorn gateway.main:app --host 127.0.0.1 --port 8000`

## Out of Scope (Deferred to Later Slices)

> Anything that is *not* in the skeleton. Explicit, to prevent later phases re-litigating Phase 1's minimalism.

- Classification (Llama Guard 3, DLP/secrets) — events carry `classification`/`policy`/`approval`/`siem`
  default blocks only; real classification is Phase 2.
- Policy engine, approval pauses, admin actions, redaction, export — Phase 3.
- Web UI — Phase 4. Phase 1 stub routes for `/classify/*`, `/approvals*`, `/policy`, `/export/jsonl` return HTTP 501.
- Multi-turn conversation history replay to Ollama — `include_history` defaults `False`; single-message calls only.
- Authentication/authorization, signed events, encrypted storage, retention, SIEM forwarding — productionization boundary (v2).

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of this skeleton without altering its architectural decisions:

- Phase 2: Every prompt classified before generation and every response before delivery (Llama Guard 3 + DLP/secrets), with per-model health checks and fail-closed behavior.
- Phase 3: Versioned YAML policy pauses high-severity prompts/responses for human approval; admin action set + raw/redacted export.
- Phase 4: Non-technical admin Web UI (dashboard filters, drill-down, approval queue, plain-language grouping); reusable `SKILL.md` contract; full E2E demo passing.

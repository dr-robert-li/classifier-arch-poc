# Roadmap: Local AI Safety SIEM Gateway POC

## Overview

A local-first safety gateway built as vertical MVP slices. Phase 1 stands up the FastAPI gateway as the sole path to Ollama and proves every chat turn is captured and audited. Phase 2 adds local classification — Llama Guard 3 plus deterministic DLP/secrets — so every turn is risk-scored. Phase 3 turns scoring into enforcement: a versioned policy engine pauses high-severity prompts and responses for human approval with full admin actions and export. Phase 4 surfaces it all in a non-technical admin Web UI and proves the whole thing with the reusable SKILL.md contract, risky-prompt fixtures, and a passing end-to-end demo.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 1: Gateway & Audit Foundation** - Chat passes through the gateway; every turn captured to JSONL + SQLite
- [ ] **Phase 2: Local Classification** - Llama Guard 3 + deterministic DLP/secrets score every prompt and response
- [ ] **Phase 3: Policy, Approval & Export** - High-severity turns pause for human approval; admin resolves and exports audit
- [ ] **Phase 4: Admin Web UI, Skill & E2E Demo** - Non-technical review surface, governance skill, and the full demo passing

## Phase Details

### Phase 1: Gateway & Audit Foundation
**Goal**: A local FastAPI gateway is the only path to Ollama; sending a prompt returns an assistant response and every prompt/response turn is captured to append-only JSONL and SQLite.
**Mode**: mvp
**UI hint**: no
**Depends on**: Nothing (first phase)
**Requirements**: GATE-01, GATE-02, GATE-03, GATE-04, GATE-05, AUDIT-01, AUDIT-02, AUDIT-04, REL-03
**Success Criteria** (what must be TRUE):
  1. Sending a prompt to `POST /chat` returns an assistant response generated via Ollama
  2. The chat client reaches the assistant model only through the gateway, never Ollama directly
  3. Each prompt and response turn produces a JSONL audit event and SQLite rows (conversations, messages, events)
  4. Re-processing the same turn does not duplicate audit events (idempotent writes)
  5. With the assistant model stopped, `/chat` returns a graceful operational error and the attempt is still audited
**Plans**: 2 plans

Plans:
- [x] 01-01-PLAN.md — Walking skeleton: end-to-end `/chat` → real Ollama → SQLite + JSONL capture; `/health`; adapter Protocols; 501 stub route surface (GATE-01..05, AUDIT-01, AUDIT-02)
- [x] 01-02-PLAN.md — Idempotent writes + REL-03 graceful unavailability (503, still audited) + `/events` read route (AUDIT-04, REL-03, AUDIT-01, GATE-05)

### Phase 2: Local Classification
**Goal**: Every prompt is classified before generation and every response before delivery, combining Llama Guard 3 (mapped to a canonical taxonomy) with a deterministic DLP/secrets scanner, with model health checks and fail-closed behavior.
**Mode**: mvp
**UI hint**: no
**Depends on**: Phase 1
**Requirements**: CLASS-01, CLASS-02, CLASS-03, CLASS-04, CLASS-05, REL-01, REL-02
**Success Criteria** (what must be TRUE):
  1. Every prompt is classified by Llama Guard 3 before generation and every response before delivery
  2. Guard output is parsed and mapped to the canonical taxonomy and stored on the event
  3. A prompt containing an API key, JWT, or email is flagged by the deterministic DLP/secrets scanner
  4. `/health` reports Ollama and per-model status; a missing guard model surfaces a clear error
  5. With Llama Guard unavailable, a high-risk workflow fails closed rather than passing through
**Plans**: 3 plans

Plans:
- [ ] 02-01-PLAN.md — Primitives: canonical taxonomy + Llama Guard output parser, Ollama guard adapter, deterministic DLP/secrets scanner (GATE-03 allowlist) (CLASS-03, CLASS-04)
- [ ] 02-02-PLAN.md — Merge orchestrator + fail-closed fallback, guard/scanner DI wiring + conftest mocks, `/classify/prompt` and `/classify/response` routes (CLASS-05, REL-02)
- [ ] 02-03-PLAN.md — Chat-flow integration (4→8 classification events + severity column), per-model `/health`, fail-closed gating; breaking-test updates (CLASS-01, CLASS-02, REL-01)

### Phase 3: Policy, Approval & Export
**Goal**: A versioned YAML policy drives high-severity pauses on both prompt and response sides, an admin resolves paused interactions through the full action set, and audit records can be exported raw or redacted.
**Mode**: mvp
**UI hint**: no
**Depends on**: Phase 2
**Requirements**: APPR-01, APPR-02, APPR-03, APPR-04, APPR-05, APPR-06, APPR-07, APPR-08, AUDIT-03
**Success Criteria** (what must be TRUE):
  1. A high-severity prompt pauses before the model generates and creates an approval request
  2. A high-severity response is withheld from the user until it is cleared
  3. An admin can approve, reject, redact-and-resume, mark false-positive, and escalate a paused interaction via API
  4. Severity, pause, and redaction behavior is driven by the versioned YAML policy file
  5. Audit records can be exported in both raw and redacted modes
**Plans**: 3 plans

Plans:
- [ ] 03-01: YAML policy engine — category severity, pause rules, redaction rules, false-positive handling, policy versioning
- [ ] 03-02: Prompt-side and response-side approval pause; `approval_requests` state; `/approvals` queue route
- [ ] 03-03: Admin actions (approve/reject/redact-resume/false-positive/escalate) + raw & redacted `/export/jsonl`

### Phase 4: Admin Web UI, Skill & E2E Demo
**Goal**: A non-technical safety administrator can review, filter, and resolve interactions in a browser; a reusable SKILL.md governance contract exists; and the full end-to-end demo passes.
**Mode**: mvp
**UI hint**: yes
**Depends on**: Phase 3
**Requirements**: UI-01, UI-02, UI-03, UI-04, SKILL-01, SKILL-02, SKILL-03
**Success Criteria** (what must be TRUE):
  1. An admin can open the dashboard and filter events by category, severity, status, and conversation, and drill into a conversation
  2. The pending-approval queue lists paused interactions and resolving one updates its state
  3. Raw classifier labels are shown grouped into plain-language categories with an expandable raw payload
  4. A reusable `SKILL.md` governance contract exists and instructs agents to route risky interactions through the gateway
  5. The full demo passes: safe prompt allowed → high-risk prompt paused → high-risk response paused → redacted resume → false positive marked → audit exported
**Plans**: 3 plans

Plans:
- [ ] 04-01: Web UI event dashboard — filters (category/severity/status/conversation/timestamp/model/policy/approval), drill-down, plain-language grouping with raw payload
- [ ] 04-02: Pending-approval queue UI wired to approval actions
- [ ] 04-03: SKILL.md governance contract + risky-prompt fixtures + end-to-end demo run

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Gateway & Audit Foundation | 2/2 | Complete | 2026-05-30 |
| 2. Local Classification | 0/3 | Not started | - |
| 3. Policy, Approval & Export | 0/3 | Not started | - |
| 4. Admin Web UI, Skill & E2E Demo | 0/3 | Not started | - |

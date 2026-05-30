# Local AI Safety SIEM Gateway POC

## What This Is

A local-first proof of concept that puts a FastAPI gateway between a chat client and a local Ollama model. It captures every prompt and response, classifies both for safety/governance risk using local Llama Guard 3 plus deterministic DLP/secrets scanners, pauses high-severity interactions for human approval, and writes append-only SIEM-style audit logs (JSONL + SQLite). A simple Web UI lets a non-technical safety & ethics administrator review a queue, approve/reject/redact-and-resume, mark false positives, and export audit records. A reusable `SKILL.md` defines the governance contract for agents.

## Core Value

Every AI prompt and response is captured, classified, and — when high-severity — *blocked pending human approval* before it reaches the model or the user, with an immutable audit trail. If everything else fails, the enforcement boundary (capture + pause + audit) must hold.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. -->

**Capture & Gateway**
- [ ] Capture every inbound user prompt before it reaches the assistant model
- [ ] Capture every assistant response before it is returned to the user
- [ ] Gateway is the only path to Ollama; chat client / agents / Web UI never call Ollama directly
- [ ] Gateway exposes adapter interfaces for assistant model, guard model, scanner, policy engine, event sink, approval workflow

**Classification**
- [ ] Classify prompts and responses with local Llama Guard 3 served by Ollama
- [ ] Parse Llama Guard output and map it into a canonical risk taxonomy
- [ ] Deterministic DLP/secrets scanner: API keys, private keys, JWTs, cloud credentials, bearer tokens, high-entropy strings, emails, phone numbers, credit-card-like values
- [ ] Cover categories: bias/fairness (advisory), dangerous behavior, DLP, secrets, illegal behavior, privacy, self-harm, hate/discrimination, sexual content, election misinformation, IP risk, specialized-advice risk

**Approval / Enforcement**
- [ ] Pause prompt before model generation on high-severity prompt risk
- [ ] Pause response before delivery on high-severity response risk (response stored restricted, never returned until cleared)
- [ ] Admin actions: approve, reject, redact-and-resume, mark false positive, escalate, export
- [ ] Versioned YAML policy file: category severity, pause rules, redaction rules, false-positive handling

**Audit / SIEM**
- [ ] Append-only JSONL events with stable SIEM-compatible schema (timestamp, correlation ID, conversation ID, actor, event type, classifier result, policy version, severity, action)
- [ ] SQLite state: conversations, messages, events, classifications, approval_requests, admin_actions, redactions, policy_versions
- [ ] Redacted export mode + raw audit export mode

**Web UI**
- [ ] Event dashboard with filters (category, severity, status, conversation, timestamp, model, policy version, approval state) + conversation drill-down
- [ ] Pending-approval queue for non-technical admins, grouping raw labels into plain-language categories with expandable raw payload

**Reliability / Health**
- [ ] Ollama + per-model health checks; clear setup errors surfaced in UI
- [ ] Fail-closed for high-risk workflows when Llama Guard unavailable; graceful operational error if assistant model unavailable (audit preserved)
- [ ] Idempotent event writes

**Skill / Fixtures / Demo**
- [ ] Reusable `SKILL.md` governance contract for structured prompt/response safety logging
- [ ] Sample risky-prompt fixtures: dangerous, illegal, DLP, secrets, bias, privacy, benign false positive
- [ ] End-to-end demo passes: safe prompt allowed → high-risk prompt paused → high-risk response paused → redacted resume → false positive marked → audit exported

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Live Monday.com integration — POC structures tasks for future board import only; no `monday_sync` adapter in v1
- AWS Bedrock / GCP Vertex / any external model API — POC is local-only, cost limited to local compute
- Vector store (OpenSearch, Splunk HEC, Security Lake, BigQuery) — not needed for initial POC; JSONL kept SIEM-mappable for later
- Production auth/authz, signed audit events, encrypted storage, structured retention, SIEM forwarding, cloud IAM — documented as the productionization boundary, not built in v1
- Ollama install + model pull as a build phase — env assumed: Ollama running with assistant model + Llama Guard 3 already pulled (health checks still included)
- Bias/fairness as an enforced gate — kept as a configurable advisory rubric until validated

## Context

- Reference architecture already written in repo `CLAUDE.md` (the source PRD for this POC) — build that spec as the v1 target.
- Model-serving plane: Ollama. Assistant model configurable via `.env` (`OLLAMA_ASSISTANT_MODEL` e.g. llama3.1 / mistral / qwen2.5); guard model `OLLAMA_GUARD_MODEL=llama-guard3`. Llama Guard 3 uses an MLCommons-style hazard taxonomy (violent/non-violent crimes, privacy, hate, self-harm, sexual, elections, etc.).
- Suggested gateway routes: `/chat`, `/classify/prompt`, `/classify/response`, `/events`, `/approvals`, `/approvals/{id}/approve`, `/approvals/{id}/reject`, `/approvals/{id}/redact-resume`, `/policy`, `/health`, `/export/jsonl`.
- Data layer: SQLite (state) + JSONL (append-only SIEM events). No vector store.
- Skill is the governance *contract*; gateway is the enforcement *boundary*. Skills alone cannot enforce capture/block/pause — all clients must route through the gateway.
- Audience for the UI: non-technical safety & ethics administrator. Plain-language category grouping with raw classifier payload in an expandable technical section.
- **Governance skill already installed**: `.claude/skills/ai-safety-siem-logger/SKILL.md` (v1.0). It is the authoritative source for the canonical event-type names (`prompt.received`, `prompt.classification.completed`, `approval.requested`, `approval.redacted_resumed`, `response.blocked`, etc.), the operating principles (route through gateway, classify before proceeding, pause high-severity, fail closed when guard unavailable), and the local architecture assumptions. The gateway's event schema (AUDIT-01) and taxonomy (CLASS-05) must conform to this contract; SKILL-01 is largely pre-satisfied by this file and primarily needs verification/integration rather than authoring.

## Constraints

- **Deployment**: Local-first, single developer workstation. Bind services to localhost by default. No cloud dependency for first demo.
- **Tech stack**: Python / FastAPI gateway; Ollama for assistant + Llama Guard 3; SQLite + JSONL data layer; YAML policy file.
- **Environment**: Ollama running with assistant model + Llama Guard 3 already pulled (per scoping decision).
- **Privacy**: No prompt, response, or classification data leaves the local machine.
- **Security (POC)**: Local-only services; restrictive file permissions on SQLite + JSONL; admin routes separated from chat routes; simple local auth before any productionization.
- **Performance**: Sub-2s deterministic scan latency target; best-effort local guard-model latency depending on model size/hardware. Deterministic scanning synchronous; guard prompt-classify before generation, response-classify before delivery.
- **Cost**: Local compute only. No paid model/API calls in v1.
- **Safety architecture**: Policy + approval logic lives in deterministic gateway code, never in model prompts. Assistant model can never override classifier/policy decisions (prompt-injection resistance).

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Build the repo `CLAUDE.md` spec as the v1 target | User confirmed spec == v1 scope, not just a north star | — Pending |
| Done bar = full E2E demo (allow → prompt pause → response pause → redact-resume → false positive → export) | Single observable acceptance test for "working POC" | — Pending |
| Assume Ollama + models pre-pulled; no install/pull phase | User confirmed local env has assistant model + Llama Guard 3 ready | — Pending |
| Hybrid enforcement: Llama Guard + deterministic DLP/secrets, canonical taxonomy | Guard output alone too coarse for DLP/secrets; deterministic layer fills gaps | — Pending |
| Gateway is sole enforcement boundary; clients never call Ollama directly | Skills cannot enforce capture/block; deterministic gateway must own policy | — Pending |
| Bias/fairness advisory-only in v1 | Not validated enough to gate on | — Pending |
| Fail-closed when Llama Guard unavailable for high-risk flows | Safety over availability | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-30 after initialization*

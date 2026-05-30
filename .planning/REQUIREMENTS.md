# Requirements: Local AI Safety SIEM Gateway POC

**Defined:** 2026-05-30
**Core Value:** Every AI prompt and response is captured, classified, and — when high-severity — blocked pending human approval before it reaches the model or the user, with an immutable audit trail.

## v1 Requirements

Requirements for the initial POC. Each maps to a roadmap phase. Acceptance bar = full end-to-end demo passes.

### Gateway

- [ ] **GATE-01**: Gateway captures every inbound user prompt before it reaches the assistant model
- [ ] **GATE-02**: Gateway captures every assistant response before it is returned to the user
- [ ] **GATE-03**: Gateway is the only path to Ollama — chat client, agents, and Web UI never call Ollama directly
- [ ] **GATE-04**: Gateway exposes adapter interfaces for assistant model, guard model, scanner, policy engine, event sink, and approval workflow
- [ ] **GATE-05**: Gateway serves the route surface: `/chat`, `/classify/prompt`, `/classify/response`, `/events`, `/approvals` (+ approve/reject/redact-resume), `/policy`, `/health`, `/export/jsonl`

### Classification

- [ ] **CLASS-01**: Prompts are classified with local Llama Guard 3 via Ollama before generation
- [ ] **CLASS-02**: Responses are classified with local Llama Guard 3 via Ollama before delivery
- [ ] **CLASS-03**: Llama Guard output is parsed and mapped into a canonical risk taxonomy
- [ ] **CLASS-04**: Deterministic DLP/secrets scanner detects API keys, private keys, JWTs, cloud credentials, bearer tokens, high-entropy strings, emails, phone numbers, and credit-card-like values
- [ ] **CLASS-05**: Events are classified across the full category set (bias [advisory], dangerous behavior, DLP, secrets, illegal behavior, privacy, self-harm, hate/discrimination, sexual content, election misinformation, IP risk, specialized-advice risk)

### Policy & Approval

- [ ] **APPR-01**: High-severity prompt risk pauses execution before model generation
- [ ] **APPR-02**: High-severity response risk pauses delivery — response stored restricted, never returned until cleared
- [ ] **APPR-03**: Admin can approve a paused interaction
- [ ] **APPR-04**: Admin can reject a paused interaction
- [ ] **APPR-05**: Admin can redact-and-resume a paused interaction
- [ ] **APPR-06**: Admin can mark a finding as a false positive
- [ ] **APPR-07**: Admin can escalate a paused interaction
- [ ] **APPR-08**: A versioned YAML policy file governs category severity, pause rules, redaction rules, and false-positive handling

### Audit / SIEM

- [ ] **AUDIT-01**: Append-only JSONL events are written with a stable SIEM schema (timestamp, correlation ID, conversation ID, actor, event type, classifier result, policy version, severity, action)
- [ ] **AUDIT-02**: SQLite stores conversations, messages, events, classifications, approval_requests, admin_actions, redactions, and policy_versions
- [ ] **AUDIT-03**: Both redacted-export and raw audit-export modes are available
- [ ] **AUDIT-04**: Event writes are idempotent

### Web UI

- [ ] **UI-01**: Event dashboard filters by category, severity, status, conversation, timestamp, model, policy version, and approval state
- [ ] **UI-02**: Dashboard supports conversation drill-down
- [ ] **UI-03**: Pending-approval queue is available for non-technical safety administrators
- [ ] **UI-04**: UI groups raw classifier labels into plain-language categories with raw payload in an expandable technical section

### Reliability

- [ ] **REL-01**: Ollama and per-model health checks run; setup errors are surfaced clearly in the UI
- [ ] **REL-02**: High-risk workflows fail closed when Llama Guard is unavailable
- [ ] **REL-03**: Assistant-model unavailability returns a graceful operational error while preserving audit events

### Skill / Fixtures / Demo

- [ ] **SKILL-01**: Reusable `SKILL.md` governance contract instructs agents to emit structured events and route risky interactions through the gateway
- [ ] **SKILL-02**: Sample risky-prompt fixtures exist for dangerous, illegal, DLP, secrets, bias, privacy, and benign false-positive cases
- [ ] **SKILL-03**: End-to-end demo passes: safe prompt allowed → high-risk prompt paused → high-risk response paused → redacted resume → false positive marked → audit exported

## v2 Requirements

Deferred to future release. Tracked, not in current roadmap.

### Productionization

- **PROD-01**: Simple local authentication on admin routes
- **PROD-02**: Signed audit events and encrypted storage
- **PROD-03**: Log rotation and retention settings
- **PROD-04**: SIEM forwarding (Logstash / OpenSearch / Splunk HEC / CloudWatch / Security Lake)

### Cloud Extension

- **CLOUD-01**: AWS Bedrock productionization notes (IAM, VPC endpoints, OpenSearch/Security Lake, Step Functions approval flow)
- **CLOUD-02**: `monday_sync` adapter mapping high-severity approvals/escalations/policy updates to board items

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Live Monday.com integration | POC structures tasks for future import only; no live sync in v1 |
| External model APIs (Bedrock / Vertex / OpenAI) | POC is local-only; cost limited to local compute |
| Vector store / search analytics layer | Not needed for initial POC; JSONL kept SIEM-mappable for later |
| Production auth/authz, signed events, encrypted storage, retention, SIEM forwarding, cloud IAM | The productionization boundary — documented, not built in v1 |
| Ollama install + model pull as a build phase | Env assumed ready (models pre-pulled); health checks included instead |
| Bias/fairness as an enforced gate | Advisory rubric only until validated |

## Traceability

Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (to be mapped) | — | Pending |

**Coverage:**
- v1 requirements: 32 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 32 ⚠️

---
*Requirements defined: 2026-05-30*
*Last updated: 2026-05-30 after initial definition*

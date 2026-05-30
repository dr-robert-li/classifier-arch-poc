---
name: ai-safety-siem-logger
description: "Use when an agent must emit structured prompt and response telemetry for SIEM-style ingestion, classify AI interactions for safety risks, DLP, secrets, bias, illegal behavior, dangerous behavior, and route high-severity interactions through a local approval gateway."
license: MIT
compatibility: "Designed for local-first AI safety gateway POCs using Ollama, Llama Guard 3, SQLite, JSONL audit logs, and a simple Web UI."
metadata:
  author: Robert Li
  version: '1.0'
---

# AI Safety SIEM Logger

## When to Use This Skill

Use this skill when the user wants AI prompts and responses to be captured, classified, logged, and reviewed through a local safety governance workflow. Apply it when the task mentions SIEM ingestion, safety telemetry, prompt/response logging, DLP, secrets detection, bias classification, dangerous behavior, illegal behavior, human approval, Llama Guard, Ollama, local audit logs, or a safety and ethics administrator Web UI.

Do not treat this skill as the enforcement boundary. This skill defines the behavior contract and structured event format. A local gateway or middleware layer must enforce capture, classification, blocking, approval, redaction, and SIEM delivery.

## Core Operating Principles

1. Route all prompt and response traffic through the configured local safety gateway. Do not call the assistant model directly when a gateway is available.
2. Emit structured events for every major lifecycle step: prompt received, prompt classified, approval requested, approval resolved, response generated, response classified, response delivered, response blocked, and audit exported.
3. Classify both prompts and responses before allowing the workflow to proceed.
4. Pause only high-severity findings by default.
5. Pause high-severity prompt risk before model generation.
6. Pause high-severity response risk before returning the response to the user.
7. Use local Llama Guard 3 through Ollama for safety classification when available.
8. Use deterministic DLP and secrets scanning alongside model-based classification.
9. Preserve an append-only audit trail. Never silently drop or rewrite safety events.
10. If the guard classifier is unavailable, fail closed for workflows that could expose sensitive, dangerous, or illegal content.

## Default Local Architecture Assumptions

- Gateway: local FastAPI service.
- Assistant model: Ollama model configured with `OLLAMA_ASSISTANT_MODEL`.
- Guard model: Ollama Llama Guard 3 configured with `OLLAMA_GUARD_MODEL`.
- State store: SQLite.
- SIEM-style audit log: append-only JSONL.
- Administrator interface: local Web UI backed by gateway APIs.
- Policy: YAML file defining categories, severity mappings, pause rules, redaction rules, and export behavior.

## Canonical Event Types

Use these event names consistently:

- `prompt.received`
- `prompt.classification.started`
- `prompt.classification.completed`
- `approval.requested`
- `approval.approved`
- `approval.rejected`
- `approval.redacted_resumed`
- `approval.false_positive_marked`
- `approval.escalated`
- `llm.request.started`
- `llm.response.generated`
- `response.classification.started`
- `response.classification.completed`
- `response.delivered`
- `response.blocked`
- `audit.exported`
- `policy.updated`
- `system.error`

## Canonical Risk Categories

Normalize classifier and scanner outputs into these categories:

- `bias_fairness`
- `dangerous_behavior`
- `illegal_behavior`
- `dlp`
- `secrets`
- `privacy`
- `hate_discrimination`
- `self_harm`
- `sexual_content`
- `child_safety`
- `elections`
- `intellectual_property`
- `specialized_advice`
- `prompt_injection`
- `unknown`

## Severity Levels

Use the following severity scale:

- `none`: No meaningful risk detected.
- `low`: Minor concern; log only.
- `medium`: Potential concern; log and show in review dashboard.
- `high`: Material safety, legal, privacy, DLP, secrets, or abuse risk; pause for administrator decision.
- `critical`: Severe or urgent risk; pause, escalate, and restrict export to redacted mode unless explicitly authorized.

By default, only `high` and `critical` findings pause the workflow.

## Required Event Schema

Every emitted event must be valid JSON and include these fields:

```json
{
  "event_id": "uuid",
  "event_type": "prompt.received",
  "timestamp": "2026-05-30T02:30:00Z",
  "correlation_id": "uuid",
  "conversation_id": "uuid",
  "message_id": "uuid",
  "actor": {
    "type": "user|assistant|admin|system",
    "id": "local-user-or-admin-id"
  },
  "model": {
    "assistant_model": "llama3.1",
    "guard_model": "llama-guard3",
    "provider": "ollama"
  },
  "content": {
    "direction": "prompt|response",
    "text_sha256": "sha256-hash",
    "text_preview": "redacted or truncated preview",
    "contains_redactions": false
  },
  "classification": {
    "overall_severity": "none|low|medium|high|critical",
    "categories": ["privacy", "secrets"],
    "llama_guard_label": "safe|unsafe|unknown",
    "llama_guard_categories": [],
    "deterministic_findings": [],
    "confidence": 0.0
  },
  "policy": {
    "policy_version": "local-poc-v1",
    "decision": "allow|pause|block|redact|escalate",
    "matched_rules": []
  },
  "approval": {
    "required": false,
    "approval_id": null,
    "status": "not_required|pending|approved|rejected|redacted_resumed|escalated"
  },
  "siem": {
    "schema_version": "1.0",
    "ecs_compatible": true,
    "source": "local-ai-safety-gateway"
  }
}
```

## Prompt Handling Workflow

1. Receive the prompt through the local gateway.
2. Emit `prompt.received`.
3. Run deterministic DLP and secrets scanning.
4. Run Llama Guard 3 prompt classification through Ollama.
5. Normalize all findings into canonical categories and severity.
6. Emit `prompt.classification.completed`.
7. If severity is `high` or `critical`, emit `approval.requested`, persist a pending approval record, and do not call the assistant model.
8. If severity is below `high`, call the configured assistant model through Ollama.
9. Emit `llm.request.started` before generation.

## Response Handling Workflow

1. Receive the assistant model response from Ollama.
2. Emit `llm.response.generated`.
3. Run deterministic DLP and secrets scanning on the response.
4. Run Llama Guard 3 response classification through Ollama.
5. Normalize findings into canonical categories and severity.
6. Emit `response.classification.completed`.
7. If severity is `high` or `critical`, emit `approval.requested`, persist a pending approval record, and do not return the response to the user.
8. If approved, emit `approval.approved` and then `response.delivered`.
9. If rejected, emit `approval.rejected` and `response.blocked`.
10. If redacted and resumed, emit `approval.redacted_resumed` and then `response.delivered` with `contains_redactions=true`.

## Administrator Actions

The administrator Web UI should support these decisions:

- `approve`: Allow the original prompt or response to proceed.
- `reject`: Block the prompt or response.
- `redact_and_resume`: Replace sensitive or unsafe spans and continue.
- `mark_false_positive`: Record a policy/classifier miss without changing the original audit event.
- `escalate`: Send the case to a higher-risk review queue.
- `export`: Export raw or redacted JSONL audit records.

Every administrator action must emit a new audit event. Do not mutate prior audit events.

## DLP and Secrets Scanner Guidance

At minimum, scan for:

- API keys and bearer tokens.
- AWS access key IDs and secret-looking strings.
- Google API keys and service account material.
- Private key blocks.
- JWTs.
- SSH keys.
- GitHub tokens.
- Slack tokens.
- Database connection strings.
- Emails, phone numbers, and credit-card-like values.
- High-entropy strings above the configured threshold.

Secrets and credential findings should normally be `high` severity unless the policy explicitly downgrades a known test fixture.

## Output Rules for Agents

When operating under this skill:

1. Do not reveal full secrets in chat output.
2. Do not bypass the gateway for convenience.
3. Do not claim safety approval unless an approval event exists.
4. Do not summarize away high-severity findings without preserving structured event fields.
5. Do not emit final user-facing content if the response classification requires approval.
6. If a required local gateway is unavailable, report the blocker and avoid processing sensitive content directly.

## Example Policy Defaults

```yaml
policy_version: local-poc-v1
pause_threshold: high
prompt_gate:
  pause_on:
    - secrets
    - dlp
    - dangerous_behavior
    - illegal_behavior
    - child_safety
    - self_harm
response_gate:
  pause_on:
    - secrets
    - dlp
    - dangerous_behavior
    - illegal_behavior
    - child_safety
    - self_harm
    - hate_discrimination
actions:
  allow_medium: true
  require_approval_for_high: true
  escalate_critical: true
exports:
  default_mode: redacted
```

## Example JSONL Event

```json
{"event_id":"3d6a7a69-1c2c-42e5-bf6d-0cc5c0a36e5f","event_type":"approval.requested","timestamp":"2026-05-30T02:35:00Z","correlation_id":"8df2d6b6-14e8-4e41-88dd-2d67f0930d2b","conversation_id":"local-demo-001","message_id":"msg-002","actor":{"type":"system","id":"local-ai-safety-gateway"},"model":{"assistant_model":"llama3.1","guard_model":"llama-guard3","provider":"ollama"},"content":{"direction":"prompt","text_sha256":"redacted","text_preview":"User prompt contained a likely credential.","contains_redactions":true},"classification":{"overall_severity":"high","categories":["secrets"],"llama_guard_label":"safe","llama_guard_categories":[],"deterministic_findings":["possible_api_key"],"confidence":0.95},"policy":{"policy_version":"local-poc-v1","decision":"pause","matched_rules":["pause_on_secrets_high"]},"approval":{"required":true,"approval_id":"apr-001","status":"pending"},"siem":{"schema_version":"1.0","ecs_compatible":true,"source":"local-ai-safety-gateway"}}
```

## Validation Checklist

Before considering the workflow operational, verify:

- A safe prompt produces prompt, classification, response, response classification, and delivery events.
- A high-severity prompt pauses before model generation.
- A high-severity response pauses before delivery.
- Administrator approval resumes the workflow and emits an approval event.
- Administrator rejection blocks the workflow and emits a blocked event.
- Redact-and-resume preserves the original audit hash and emits a redacted delivery event.
- JSONL export works in raw and redacted modes.
- SQLite contains the same event IDs as the JSONL audit stream.
- The Web UI can filter by severity, category, approval state, and conversation.

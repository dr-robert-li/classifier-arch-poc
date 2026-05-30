# Architectural Design Pattern: Local AI Safety SIEM Gateway POC

## 1. Executive Summary & Intent
- **Context:** This pattern defines a local-first proof of concept for capturing all AI prompts and responses, classifying them for safety and governance risks, pausing high-severity interactions for human approval, and emitting structured SIEM-style logs that can be visualized by a non-technical safety and ethics administrator. The verified POC target is a hybrid skill plus gateway architecture using Ollama locally for both the assistant model and Llama Guard 3, with JSONL and SQLite as the initial local SIEM-style data layer.
- **Solution Intent:** Implement a local FastAPI gateway as the enforcement boundary for prompt/response capture, Ollama-based Llama Guard 3 classification, deterministic DLP/secrets scanning, high-severity approval pauses, append-only audit logging, and a simple Web UI for review queues and categorization. The reusable skill defines the structured governance contract, while the gateway enforces logging, classification, pause, approval, denial, redaction, resume, and SIEM event emission.

## 2. Refined Engineering Requirements
- **Functional Requirements:**
  - Capture every inbound user prompt before it reaches the assistant model.
  - Capture every assistant response before it is returned to the user.
  - Classify both prompts and responses using local Llama Guard 3 served by Ollama.
  - Supplement model-based classification with deterministic DLP and secrets scanning for API keys, private keys, JWTs, cloud credentials, bearer tokens, high-entropy strings, emails, phone numbers, and credit-card-like values.
  - Classify events across bias/fairness risk, dangerous behavior, DLP, secrets, illegal behavior, privacy exposure, self-harm, hate/discrimination, sexual content, election misinformation, intellectual property risk, and specialized advice risk.
  - Pause execution for high-severity prompt risk before model generation.
  - Pause response delivery for high-severity response risk before returning content to the user.
  - Allow a safety and ethics administrator to approve, reject, redact-and-resume, mark false positive, escalate, and export audit records.
  - Emit append-only JSONL events using a stable SIEM-compatible schema.
  - Store workflow state in SQLite for conversations, events, classifications, approvals, redactions, and administrator actions.
  - Provide a simple local Web UI for non-technical administrators to filter by category, severity, status, conversation, timestamp, model, policy version, and approval state.
  - Provide a reusable `SKILL.md` file that instructs agents to emit structured events and route risky interactions through the local gateway.
- **Non-Functional Requirements:**
  - **Deployment Scope:** Local-first POC designed for a developer workstation; no cloud dependency required for first demo.
  - **Latency:** Target sub-2-second deterministic scan latency and best-effort local guard model latency depending on Ollama model size and hardware.
  - **Availability:** Gateway should degrade gracefully if the assistant model is unavailable by showing an operational error and preserving audit events; if Llama Guard is unavailable, default to fail-closed for high-risk workflows.
  - **Auditability:** Every decision must produce an immutable event with timestamp, correlation ID, conversation ID, actor, event type, classifier result, policy version, severity, and action taken.
  - **Privacy:** No prompt, response, or classification data leaves the local machine in the POC.
  - **Security:** The gateway, Web UI, SQLite database, and JSONL audit log are local-only by default; admin routes require at least simple local authentication before productionization.
  - **Extensibility:** Gateway must expose adapter interfaces for assistant model, guard model, scanner, policy engine, event sink, and approval workflow.
  - **SIEM Compatibility:** JSONL event format should be compatible with later Logstash, OpenSearch, Splunk HEC, CloudWatch, or AWS Security Lake mapping.
  - **Cost:** POC cost should be limited to local compute. No AWS Bedrock, GCP Vertex AI, or external model API calls are required for the first implementation.
  - **Sustainability:** Local inference should use appropriately sized quantized models where possible and avoid unnecessary duplicate classification passes.

## 3. Target Cloud Architecture
- **Generative AI Layer:** The POC uses Ollama as the local model-serving plane. The assistant model is configurable through `.env`, for example `OLLAMA_ASSISTANT_MODEL=llama3.1`, `mistral`, or `qwen2.5`. The guard model is configured as `OLLAMA_GUARD_MODEL=llama-guard3`. Llama Guard is used for both prompt-side and response-side safety classification. Llama Guard is designed for input and output moderation in human-AI conversations and supports prompt and response classification: https://ai.meta.com/research/publications/llama-guard-llm-based-input-output-safeguard-for-human-ai-conversations/. Llama Guard 3 uses an MLCommons-style hazard taxonomy covering categories such as violent crimes, non-violent crimes, privacy, hate, self-harm, sexual content, elections, and related risks: https://huggingface.co/meta-llama/Llama-Guard-3-1B-INT4.
- **Compute & Integration Layer:** Use a local FastAPI gateway as the enforcement point. Recommended routes include `/chat`, `/classify/prompt`, `/classify/response`, `/events`, `/approvals`, `/approvals/{id}/approve`, `/approvals/{id}/reject`, `/approvals/{id}/redact-resume`, `/policy`, `/health`, and `/export/jsonl`. The Web UI should call only the gateway APIs. The chat client and any agent skill workflow must not call Ollama directly.
- **Data & Vector Store:** Use SQLite for local state and JSONL for append-only SIEM-style events. Suggested SQLite tables are `conversations`, `messages`, `events`, `classifications`, `approval_requests`, `admin_actions`, `redactions`, and `policy_versions`. No vector store is required for the initial POC. A later production extension can add Amazon OpenSearch Serverless, OpenSearch Dashboards, Splunk HEC, AWS Security Lake, or GCP BigQuery for analytics and retention.
- **Monday.com API Bridge:** The POC does not require live Monday.com integration, but task breakdowns should be structured for board import or future API sync. A future bridge can map gateway tasks and approval workflow items into Monday.com using columns for Item, Status, Timeline, Dependencies, Target Component, Risk Category, Severity, and Owner. For live integration, implement a `monday_sync` adapter that creates board items for high-severity unresolved approvals, escalations, policy updates, and remediation tasks.

## 4. Risks, Limitations & Mitigation Strategies
- **Risk/Limitation:** Skills alone cannot enforce full capture, blocking, or approval pause.
  - *Mitigation:* Treat the skill as the governance contract and the FastAPI gateway as the enforcement boundary. Require all chat clients, agents, and Web UI flows to call the gateway rather than Ollama directly.
- **Risk/Limitation:** Llama Guard model output may be inconsistent or insufficiently granular for enterprise bias, DLP, or secrets use cases.
  - *Mitigation:* Normalize guard output into a canonical taxonomy and pair it with deterministic DLP/secrets scanners. Keep bias/fairness as a configurable POC rubric and explicitly mark it as advisory until validated.
- **Risk/Limitation:** DLP and secrets false positives can interrupt benign workflows.
  - *Mitigation:* Pause only high-severity findings in the POC. Allow administrators to mark false positives, redact-and-resume, and record policy feedback for tuning.
- **Risk/Limitation:** High-severity response risk may be detected only after the assistant model has generated unsafe content.
  - *Mitigation:* Do not return the response to the user until response classification completes. Store the response as restricted pending review and provide redact-and-resume controls.
- **Risk/Limitation:** Local Ollama model availability, memory pressure, or model download state can make demos brittle.
  - *Mitigation:* Add health checks for Ollama and each configured model. Surface setup errors clearly in the Web UI. Use small or quantized models for demos and document `ollama pull` prerequisites.
- **Risk/Limitation:** JSONL logs may contain sensitive prompts, responses, or secrets.
  - *Mitigation:* Store audit files locally with restrictive file permissions. Support redacted export mode. Add log rotation and a retention setting before using the POC with real sensitive data.
- **Risk/Limitation:** A non-technical administrator may misinterpret classifier labels.
  - *Mitigation:* The UI should group raw model labels into plain-language categories, show severity and recommended action, and keep raw classifier payloads in an expandable technical section.
- **Risk/Limitation:** Prompt injection can attempt to bypass safety instructions.
  - *Mitigation:* Keep policy and approval logic outside model prompts in deterministic gateway code. Never let the assistant model override classifier or policy decisions.
- **Risk/Limitation:** POC is local-only and not production-authenticated.
  - *Mitigation:* Document the boundary clearly. Before production, add authentication, authorization, signed audit events, encrypted storage, structured retention, SIEM forwarding, and cloud IAM controls.

## 5. AWS Well-Architected Validation
- **Security:** For the local POC, bind services to localhost by default, restrict SQLite and JSONL file permissions, keep all prompts and responses local, and separate administrator actions from normal chat actions. For AWS productionization, place the gateway behind API Gateway or ALB, use ECS Fargate or Lambda, encrypt data with KMS, use VPC endpoints for Bedrock and OpenSearch, apply least-privilege IAM roles, and store secrets in AWS Secrets Manager.
- **Reliability & Performance:** The local POC should include Ollama health checks, model availability checks, deterministic scanner fallback, fail-closed behavior when the guard model is unavailable, and idempotent event writes. For production, add queue-based buffering through SQS or Kinesis, retry policies, dead-letter queues, multi-AZ data stores, and response caching for repeated classifier checks where safe.
- **Cost Optimization:** The POC uses local compute only. For AWS production, start with serverless or small ECS tasks, right-size Bedrock model usage, batch low-priority evaluations, apply retention policies to logs, and avoid duplicative guard passes. Use OpenSearch Serverless only if search/analytics needs justify it; otherwise, begin with S3 plus Athena or CloudWatch Logs Insights.
- **Operational Excellence:** Use a versioned YAML policy file, structured logs, health endpoints, test fixtures for known risky prompts, and admin action audit trails. Maintain runbooks for model setup, policy update, false-positive review, and audit export.
- **Performance Efficiency:** Keep deterministic scanning synchronous and lightweight. Run Llama Guard prompt classification before generation and response classification before delivery. Use configurable timeouts and model sizes to fit local hardware.
- **Sustainability:** Prefer compact or quantized local models for the POC, avoid unnecessary repeated classification, rotate logs, and allow sampling or batch analysis only for low-risk analytics views.

## 6. Implementation Roadmap & Monday.com Tasks
*Provide exact task strings ready for import/syncing to Monday.com boards.*

| Task Name / Action Item | Target Component | Estimated Effort | Dependencies |
| :--- | :--- | :--- | :--- |
| Create local FastAPI gateway project scaffold | Gateway / API | 3 Hours | None |
| Define canonical AI safety event schema for prompts, responses, classifications, approvals, and admin actions | Observability / SIEM | 4 Hours | None |
| Implement SQLite schema for conversations, messages, events, classifications, approval requests, admin actions, redactions, and policy versions | Data Layer | 4 Hours | Event schema |
| Implement append-only JSONL audit writer with correlation IDs and policy version fields | SIEM Log Layer | 3 Hours | Event schema |
| Implement Ollama assistant adapter with configurable `OLLAMA_ASSISTANT_MODEL` | Model Adapter | 4 Hours | Gateway scaffold |
| Implement Ollama Llama Guard 3 adapter with configurable `OLLAMA_GUARD_MODEL` | Safety Classifier | 5 Hours | Gateway scaffold; Ollama model available |
| Implement Llama Guard output parser and canonical risk taxonomy mapper | Safety Classifier | 4 Hours | Llama Guard adapter |
| Implement deterministic DLP and secrets scanner for API keys, JWTs, private keys, cloud credentials, bearer tokens, emails, phone numbers, and high-entropy strings | DLP / Secrets | 1 Day | Event schema |
| Create YAML policy file for category severity, pause rules, redaction rules, and false-positive handling | Policy Engine | 4 Hours | Classifier mapper; scanner |
| Enforce prompt-side high-severity approval pause before assistant model execution | Approval Workflow | 5 Hours | Policy engine; SQLite schema |
| Enforce response-side high-severity approval pause before response delivery | Approval Workflow | 5 Hours | Ollama assistant adapter; policy engine |
| Implement approval actions: approve, reject, redact-and-resume, mark false positive, escalate, and export | Admin Workflow | 1 Day | Approval workflow |
| Build local Web UI event dashboard with category filters, severity filters, status filters, and conversation drill-down | Web UI | 1.5 Days | Events API |
| Build Web UI pending approval queue for non-technical safety administrators | Web UI | 1 Day | Approval actions API |
| Add redacted JSONL export and raw audit export modes | Audit Export | 4 Hours | JSONL writer; redaction logic |
| Add local setup documentation for Ollama, assistant model, Llama Guard 3, gateway, and Web UI | Documentation | 4 Hours | Core services |
| Create reusable `SKILL.md` governance contract for structured prompt/response safety logging | Agent Skill | 3 Hours | Event schema; policy decisions |
| Add sample risky prompt fixtures for dangerous behavior, illegal behavior, DLP, secrets, bias, privacy, and benign false positives | Test Fixtures | 4 Hours | Policy engine |
| Run end-to-end demo test: safe prompt allowed, high-risk prompt paused, high-risk response paused, redacted resume, false positive marked, audit exported | QA / Demo | 1 Day | Web UI; approval workflow |
| Prepare future AWS Bedrock productionization notes with IAM, VPC endpoints, OpenSearch/Security Lake, and Step Functions approval flow | Cloud Extension | 4 Hours | POC architecture complete |

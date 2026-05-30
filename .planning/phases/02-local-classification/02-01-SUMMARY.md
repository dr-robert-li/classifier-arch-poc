---
phase: 02-local-classification
plan: "01"
subsystem: gateway-classification
tags: [llama-guard3, taxonomy, dlp, secrets, scanner, ollama, adapter, gate-03, tdd]
dependency_graph:
  requires:
    - adapter-protocol-interfaces   # IGuardAdapter, IScannerAdapter from 01-01
    - gateway-enforcement-boundary  # settings.py + ollama_assistant pattern to mirror
  provides:
    - canonical-taxonomy            # SCODE_TO_CANONICAL, SEVERITY_ORDER, parse_guard_output, map_scodes
    - llama-guard-adapter           # OllamaGuardAdapter + GuardUnavailableError
    - dlp-secrets-scanner           # DlpScannerAdapter with regex + entropy + Luhn
    - gate-03-guard-allowlisted     # ollama_guard.py in GATE-03 allowed set
  affects:
    - phase-02-02-orchestrator      # classify_text() merges guard + scanner outputs
    - phase-02-03-chat-integration  # chat.py classifies prompts and responses
    - phase-03-approval             # GuardUnavailableError triggers fail-closed logic
tech_stack:
  added: []
  patterns:
    - TDD RED→GREEN per task (test commit then implementation commit)
    - Pure stdlib classification (re, math, hashlib) — no new packages
    - asyncio.run() for Python 3.14 compatibility (get_event_loop() removed)
    - GATE-03 allowlist pattern for legitimate second Ollama caller
key_files:
  created:
    - gateway/classification/__init__.py
    - gateway/classification/taxonomy.py
    - gateway/adapters/ollama_guard.py
    - gateway/adapters/dlp_scanner.py
    - tests/test_classification.py
  modified:
    - tests/test_architecture.py
decisions:
  - "asyncio.run() used instead of get_event_loop().run_until_complete() — Python 3.14 removed implicit event loop"
  - "dict.fromkeys() used for map_scodes dedup — order-preserving (not set()) so output is deterministic for tests"
  - "DLP scanner: matched_value = sha256(matched_text) always; never stores raw secret (T-02-02)"
  - "Bearer token regex case-insensitive ((?i) prefix) to catch Authorization: bearer variants"
  - "High-entropy check adds unique-char gate (≥10) beyond all-lowercase reject (Pitfall 7 from research)"
  - "Credit card scan uses separate helper _scan_credit_cards for Luhn gate — keeps main scan loop clean"
  - "Guard adapter confidence is [ASSUMED] heuristic: 1.0=safe, 0.9=unsafe, 0.0=unknown (no logprobs from Ollama)"
metrics:
  duration_minutes: 30
  completed_date: "2026-05-30"
  tasks_completed: 3
  tasks_total: 3
  files_created: 5
  files_modified: 1
---

# Phase 2 Plan 1: Classification Primitives — Taxonomy, Guard Adapter, DLP Scanner Summary

**One-liner:** Canonical 14-code Llama Guard 3 taxonomy mapper, Ollama guard adapter with GuardUnavailableError, and deterministic DLP/secrets scanner with SHA-256 redaction and Luhn validation — 41 new offline unit tests; 79/79 total tests green.

## What Was Built

### Component Inventory

| File | Role |
|------|------|
| `gateway/classification/__init__.py` | Package marker for classification subpackage |
| `gateway/classification/taxonomy.py` | `SEVERITY_ORDER`, `SCODE_TO_CANONICAL` (14 S-codes), `max_severity()`, `parse_guard_output()`, `map_scodes()` |
| `gateway/adapters/ollama_guard.py` | `OllamaGuardAdapter` (mirrors assistant adapter pattern) + `GuardUnavailableError`; GATE-03 compliant |
| `gateway/adapters/dlp_scanner.py` | `DlpScannerAdapter` — 13 regex patterns + credit card Luhn + Shannon entropy; SHA-256 matched values |
| `tests/test_classification.py` | 41 unit tests covering all three tasks (no live Ollama required) |
| `tests/test_architecture.py` | Added `gateway/adapters/ollama_guard.py` to GATE-03 `allowed` set |

### Taxonomy Module

`taxonomy.py` exposes four public symbols:

- **`SEVERITY_ORDER`**: `["none", "low", "medium", "high", "critical"]` — ordered rank list used as index for comparisons.
- **`SCODE_TO_CANONICAL`**: 14-entry dict mapping S1–S14 to `(canonical_category, default_severity)`. S5 (Defamation) and S14 (Code Interpreter Abuse) map to `"unknown"/medium` per research recommendation [ASSUMED].
- **`max_severity(a, b)`**: returns higher of two severity strings by SEVERITY_ORDER index; unknown values treated as rank 0.
- **`parse_guard_output(raw)`**: strips → "safe"→`("safe",[])`, startswith "unsafe"→split codes, else→`("unknown",[])`. Never raises.
- **`map_scodes(s_codes)`**: maps list of S-codes to `(deduped_categories, overall_severity)`. Uses `dict.fromkeys()` for order-preserving dedup.

### Guard Adapter

`OllamaGuardAdapter` exactly mirrors `OllamaAssistantAdapter`:
- Constructor: `(http_client: httpx.AsyncClient, settings: Settings)`
- `classify(messages, direction)`: POST `/api/chat`; maps `ConnectError`/`TimeoutException`/HTTP 404 → `GuardUnavailableError`; HTTP 200 with unparseable content → `label="unknown", confidence=0.0` (no raise); normal parse via `parse_guard_output` + `map_scodes`.
- `health()`: GET `/api/version` → `ollama_process`, POST `/api/show` → `guard_model`; never raises.
- Return dict shape: `{llama_guard_label, llama_guard_categories, categories, overall_severity, confidence}`.

### DLP Scanner

`DlpScannerAdapter.scan(text)` runs three detection layers:

1. **Regex patterns** (13 compiled at module load): AWS access key, Google API key, GitHub token, Slack token, JWT, PEM private key, SSH public key, Bearer token, Azure connection string, GCP service account, database URL, email address, phone number. All patterns use explicit length quantifiers (ReDoS-bounded, T-02-03).

2. **Luhn-validated credit cards**: 13-19 digit candidate regex followed by Luhn check; Luhn-invalid runs are silently dropped.

3. **Shannon entropy scan**: tokens ≥20 chars from `[A-Za-z0-9+/=\-_]` charset; threshold 4.5 bits/char; rejects all-lowercase alpha tokens and tokens with <10 unique characters (Pitfall 7 guard).

Security contract (T-02-02): every finding's `matched_value` is `sha256(matched_text).hexdigest()` — 64-char lowercase hex. Raw matched text is never stored, logged, or returned.

## Test Coverage

| Test File | Tests | What It Covers |
|-----------|-------|----------------|
| `test_classification.py` | 41 | parse_guard_output (8), SCODE_TO_CANONICAL (7), max_severity (5), map_scodes (4), OllamaGuardAdapter (9), DlpScannerAdapter (9) |
| `test_architecture.py` | 1 | GATE-03 with ollama_guard.py allowlisted |
| **Phase 1 total** | **38** | Unchanged — all still passing |
| **Grand total** | **79** | All green; no regressions |

**Final pytest result:** `79 passed in 0.46s`

## TDD Gate Compliance

| Task | RED commit | GREEN commit |
|------|-----------|-------------|
| All three tasks (single RED/GREEN pair) | 318a830 — failing tests for all three tasks | 1b8030e (taxonomy) + 924c4c2 (guard) + 373f31a (scanner) |

Note: All RED tests were committed as a single batch before any implementation began (one RED commit covers all three tasks). GREEN commits are per-task as required.

## Success Criteria Verification

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `parse_guard_output`, `SCODE_TO_CANONICAL` (14), `max_severity`, `map_scodes` present | PASS | 23 taxonomy/parser tests green |
| Every S-code maps to SKILL.md canonical category | PASS | S5/S14→`unknown`; all others verified against SKILL.md |
| Guard adapter implements IGuardAdapter Protocol | PASS | `classify()`+`health()` match Protocol signature |
| GuardUnavailableError on ConnectError/Timeout/404 | PASS | `test_guard_adapter_raises_on_connect_error/http_404` |
| Unparseable guard content → unknown/0.0, no raise | PASS | `test_guard_adapter_classify_unknown_content` |
| GATE-03 allowlist updated; architecture test passes | PASS | `test_architecture.py` 1 passed |
| Scanner detects AWS key, JWT, email | PASS | `test_scanner_detects_*` tests green |
| Luhn-valid card flagged; invalid not flagged | PASS | `test_scanner_luhn_valid/invalid_*` |
| matched_value is SHA-256 (64-char hex) not raw | PASS | `test_scanner_matched_value_is_sha256_not_raw` |
| High-entropy blob flagged; lowercase word not flagged | PASS | `test_scanner_high_entropy/lowercase_*` |
| Full 79-test suite green (no regression) | PASS | `79 passed in 0.46s` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Python 3.14 asyncio.run() compatibility**
- **Found during:** Task 2 GREEN — first pytest run after creating ollama_guard.py
- **Issue:** Test code used `asyncio.get_event_loop().run_until_complete(...)` which raises `RuntimeError: There is no current event loop in thread 'MainThread'` in Python 3.14 (implicit event loop creation was removed).
- **Fix:** Replaced all `asyncio.get_event_loop().run_until_complete(...)` calls with `asyncio.run(...)` in the guard adapter tests in test_classification.py.
- **Files modified:** `tests/test_classification.py`
- **Commits:** Updated in same task as RED tests were adjusted.

## Known Stubs

None — all three primitives are complete implementations, not stubs. The `[ASSUMED]` tags in confidence heuristic (guard adapter) and entropy threshold (scanner) are documented assumptions, not functional stubs.

## Threat Flags

No new threat surface beyond the plan's `<threat_model>`. All four mitigations implemented:

| T-ID | Mitigation Applied |
|------|--------------------|
| T-02-01 | `parse_guard_output` returns `("unknown",[])` on any non-conforming guard output; control flow never depends on model text |
| T-02-02 | `matched_value` = `sha256(matched_text).hexdigest()` in every code path; raw text never stored |
| T-02-03 | All regex patterns use explicit length quantifiers (e.g. `{16}`, `{35}`, `{2,500}`); entropy + Luhn are O(n) |
| T-02-05 | Guard adapter logs only status/exception strings; no raw prompt/response content in logs |

## Self-Check: PASSED

### Files check
- [x] gateway/classification/__init__.py — FOUND
- [x] gateway/classification/taxonomy.py — FOUND
- [x] gateway/adapters/ollama_guard.py — FOUND
- [x] gateway/adapters/dlp_scanner.py — FOUND
- [x] tests/test_classification.py — FOUND
- [x] tests/test_architecture.py — MODIFIED (ollama_guard.py in allowed set)

### Commits check
- [x] 318a830 — test(02-01): add failing tests (RED)
- [x] 1b8030e — feat(02-01): implement canonical taxonomy module and Llama Guard output parser
- [x] 924c4c2 — feat(02-01): implement OllamaGuardAdapter + GATE-03 allowlist update
- [x] 373f31a — feat(02-01): implement DlpScannerAdapter with regex + entropy + Luhn detection

### pytest result
`79 passed in 0.46s` — zero failures, zero warnings, 38 Phase 1 tests preserved.

### Acceptance criteria check
- [x] `parse_guard_output`: safe/unsafe/unknown/whitespace/empty — all covered
- [x] `SCODE_TO_CANONICAL`: exactly 14 keys, correct types, SKILL.md categories
- [x] `max_severity`: ordering correct, unknown treated as rank 0
- [x] `map_scodes`: dedup via dict.fromkeys, empty returns none/[], unknown codes skipped
- [x] `OllamaGuardAdapter`: ConnectError/Timeout/404 → GuardUnavailableError; health never raises
- [x] GATE-03 allowlist updated; `test_no_direct_ollama_calls_outside_adapter` passes
- [x] `DlpScannerAdapter`: AWS key, JWT, email flagged; Luhn valid/invalid; SHA-256 hashes; entropy on/off
- [x] 79/79 tests pass (pytest -q)

"""
Classification orchestrator — merges guard adapter + scanner into one canonical block.

This is a thin coordination function. It:
  1. Runs the deterministic scanner (sync, fast) first so scanner findings survive guard outage.
  2. Calls the guard adapter (async) wrapped in try/except GuardUnavailableError (fail-closed).
  3. Merges results into a single canonical classification block.

The orchestrator never references Ollama directly — it calls guard_adapter.classify().
The returned dict keys match CLASSIFICATION_DEFAULTS in gateway/audit/schema.py exactly.

Fail-closed behavior (REL-02 / T-02-07):
  - Guard unavailable (GuardUnavailableError) → substitute a safe guard stub result.
  - Scanner findings still preserved → if scanner severity is high/critical, overall_severity
    reflects that even when the guard is down (scanner survives guard-down).
  - The orchestrator NEVER raises — callers decide what to do with the block.
"""
from gateway.adapters.ollama_guard import GuardUnavailableError
from gateway.classification.taxonomy import max_severity

# ---------------------------------------------------------------------------
# Guard fallback block used when GuardUnavailableError is raised
# ---------------------------------------------------------------------------
_GUARD_UNAVAILABLE_RESULT: dict = {
    "llama_guard_label": "unknown",
    "llama_guard_categories": [],
    "categories": [],
    "overall_severity": "none",
    "confidence": 0.0,
}


def _max_finding_severity(findings: list[dict]) -> str:
    """
    Return the maximum severity across a list of scanner findings.

    Uses taxonomy.max_severity (monotone ordering: none < low < medium < high < critical).
    Returns "none" for an empty findings list.
    """
    severity = "none"
    for finding in findings:
        severity = max_severity(severity, finding.get("severity", "none"))
    return severity


async def classify_text(
    *,
    text: str,
    messages_for_guard: list[dict],
    direction: str,
    guard_adapter,    # IGuardAdapter — any object with .classify(messages, direction) -> dict
    scanner_adapter,  # IScannerAdapter — any object with .scan(text) -> list[dict]
) -> dict:
    """
    Run scanner + guard, merge into one canonical classification block.

    The scanner always runs first (sync, sub-millisecond) so its findings survive
    a guard outage. The guard call is wrapped in try/except GuardUnavailableError —
    this function NEVER raises that exception; guard-down is expressed via the
    returned block's llama_guard_label="unknown" and (potentially) scanner-elevated
    overall_severity.

    Args:
        text: Raw text string to scan (for the deterministic scanner).
        messages_for_guard: Conversation messages array to pass to the guard model.
            - Prompt-side: [{"role": "user", "content": text}]
            - Response-side: [{"role": "user", "content": prompt}, {"role": "assistant", "content": text}]
        direction: "prompt" or "response" — passed through to guard_adapter.classify().
        guard_adapter: Object implementing IGuardAdapter (classify + health methods).
        scanner_adapter: Object implementing IScannerAdapter (scan method).

    Returns:
        dict with keys matching CLASSIFICATION_DEFAULTS:
            overall_severity      — max of guard severity and scanner max-finding severity
            categories            — deduplicated union of guard categories + scanner finding categories
            llama_guard_label     — "safe" | "unsafe" | "unknown"
            llama_guard_categories — raw S-code list from guard
            deterministic_findings — scanner findings list (each finding's matched_value is sha256)
            confidence            — guard heuristic confidence (0.0 when guard unavailable)
    """
    # 1. Deterministic scanner first — sync, fast, and isolated from guard availability.
    #    Scanner findings survive regardless of guard status (T-02-07 invariant).
    findings = scanner_adapter.scan(text)

    # 2. Guard model — async, may be unavailable.
    #    GuardUnavailableError is caught here; all other exceptions propagate (unexpected errors).
    try:
        guard_result = await guard_adapter.classify(messages_for_guard, direction)
    except GuardUnavailableError:
        # Fail-closed: substitute a safe stub for the guard result.
        # Scanner findings (step 1) are preserved — if scanner found high severity,
        # overall_severity will reflect it in step 3 below.
        guard_result = dict(_GUARD_UNAVAILABLE_RESULT)

    # 3. Merge: overall_severity = max(guard_severity, max scanner-finding severity).
    scanner_max_sev = _max_finding_severity(findings)
    overall_severity = max_severity(
        guard_result.get("overall_severity", "none"),
        scanner_max_sev,
    )

    # 4. Merge category lists — deduplicated, order-preserving (dict.fromkeys, not set()).
    #    Guard categories come first; scanner categories follow.
    combined = (
        guard_result.get("categories", [])
        + [f["category"] for f in findings]
    )
    categories = list(dict.fromkeys(combined))

    return {
        "overall_severity": overall_severity,
        "categories": categories,
        "llama_guard_label": guard_result.get("llama_guard_label", "unknown"),
        "llama_guard_categories": guard_result.get("llama_guard_categories", []),
        "deterministic_findings": findings,
        "confidence": guard_result.get("confidence", 0.0),
    }

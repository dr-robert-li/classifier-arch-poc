"""
Canonical AI safety taxonomy: S-code mapping, severity ordering, and Llama Guard parser.

This module is pure stdlib — no HTTP, no Ollama references (GATE-03 preserved).

Exports:
    SEVERITY_ORDER      — ordered list of severity levels (index = rank)
    SCODE_TO_CANONICAL  — dict mapping each Llama Guard 3 S-code to (canonical_category, default_severity)
    max_severity(a, b)  — returns the higher of two severity strings
    parse_guard_output(raw) — parses raw llama-guard3 output into (label, s_codes)
    map_scodes(s_codes)     — maps S-codes to (canonical_categories_deduped, overall_severity)
"""

# ---------------------------------------------------------------------------
# Severity scale — index is rank; higher index = higher severity.
# Phase 3 will externalise this to YAML; Phase 2 ships it as a code constant.
# ---------------------------------------------------------------------------
SEVERITY_ORDER: list[str] = ["none", "low", "medium", "high", "critical"]


# ---------------------------------------------------------------------------
# S-Code → Canonical Taxonomy Mapping
# Source: Llama Guard 3 Ollama template, verified live via /api/show (2026-05-30).
# Each value is (canonical_category, default_severity).
#
# S5  (Defamation)           → "unknown"/medium   [ASSUMED — no clean canonical match]
# S14 (Code Interpreter Abuse) → "unknown"/medium  [ASSUMED — no clean canonical match]
# ---------------------------------------------------------------------------
SCODE_TO_CANONICAL: dict[str, tuple[str, str]] = {
    "S1":  ("dangerous_behavior",    "high"),      # Violent Crimes
    "S2":  ("illegal_behavior",      "high"),      # Non-Violent Crimes
    "S3":  ("illegal_behavior",      "critical"),  # Sex Crimes
    "S4":  ("child_safety",          "critical"),  # Child Exploitation
    "S5":  ("unknown",               "medium"),    # Defamation [ASSUMED]
    "S6":  ("specialized_advice",    "medium"),    # Specialized Advice
    "S7":  ("privacy",               "medium"),    # Privacy
    "S8":  ("intellectual_property", "low"),       # Intellectual Property
    "S9":  ("dangerous_behavior",    "critical"),  # Indiscriminate Weapons
    "S10": ("hate_discrimination",   "high"),      # Hate
    "S11": ("self_harm",             "high"),      # Self-Harm
    "S12": ("sexual_content",        "medium"),    # Sexual Content
    "S13": ("elections",             "medium"),    # Elections
    "S14": ("unknown",               "medium"),    # Code Interpreter Abuse [ASSUMED]
}


def max_severity(a: str, b: str) -> str:
    """
    Return the higher of two severity strings by SEVERITY_ORDER rank.

    Any value not in SEVERITY_ORDER is treated as index 0 (below 'low'),
    which prevents KeyError on unexpected/unknown values.
    """
    ai = SEVERITY_ORDER.index(a) if a in SEVERITY_ORDER else 0
    bi = SEVERITY_ORDER.index(b) if b in SEVERITY_ORDER else 0
    return SEVERITY_ORDER[max(ai, bi)]


def parse_guard_output(raw: str) -> tuple[str, list[str]]:
    """
    Parse raw llama-guard3 output into (label, s_codes).

    Verified output formats (empirically tested on Ollama 0.22.1, 2026-05-30):
        "safe"             → ("safe", [])
        "unsafe\\nS1"      → ("unsafe", ["S1"])
        "unsafe\\nS9,S7"   → ("unsafe", ["S9", "S7"])

    Never raises. Any unexpected format → ("unknown", []).

    Args:
        raw: Raw string from data["message"]["content"] in the Ollama response.

    Returns:
        (label, s_codes) where label is "safe" | "unsafe" | "unknown".
    """
    text = raw.strip()

    if text == "safe":
        return "safe", []

    if text.startswith("unsafe"):
        lines = text.splitlines()
        if len(lines) >= 2:
            codes = [c.strip() for c in lines[1].split(",") if c.strip()]
            return "unsafe", codes
        return "unsafe", []

    return "unknown", []


def map_scodes(s_codes: list[str]) -> tuple[list[str], str]:
    """
    Map a list of S-codes to (canonical_categories_deduped, overall_severity).

    - Unrecognised S-codes (not in SCODE_TO_CANONICAL) are silently skipped.
    - Duplicate canonical categories are collapsed (order-preserving via dict.fromkeys).
    - overall_severity is the maximum severity across all recognised codes.
    - Returns ([], "none") for an empty or entirely-unrecognised s_codes list.

    Args:
        s_codes: List of raw S-code strings, e.g. ["S1", "S9"].

    Returns:
        (categories, overall_severity) where categories is a deduplicated list.
    """
    categories: list[str] = []
    severity = "none"

    for code in s_codes:
        entry = SCODE_TO_CANONICAL.get(code)
        if entry is None:
            continue  # Unrecognised code — skip gracefully
        category, code_severity = entry
        categories.append(category)
        severity = max_severity(severity, code_severity)

    # Deduplicate while preserving insertion order (dict.fromkeys is O(n) and stable)
    categories = list(dict.fromkeys(categories))

    return categories, severity

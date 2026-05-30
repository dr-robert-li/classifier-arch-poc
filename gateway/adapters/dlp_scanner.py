"""
DlpScannerAdapter — deterministic DLP and secrets scanning (Phase 2, IScannerAdapter).

Pure Python: stdlib re, math, hashlib only. No Ollama calls. Synchronous.
No Ollama URL references (GATE-03 boundary preserved).

Security contract (T-02-02):
  Every finding's matched_value stores only sha256(matched_text).hexdigest() — 64-char hex.
  Raw matched text is NEVER stored, logged, or returned to the caller.

ReDoS contract (T-02-03):
  All regex patterns use explicit length quantifiers and avoid unbounded backtracking.
  Shannon entropy and Luhn checks are O(n). No catastrophic backtracking.

Pattern provenance [ASSUMED]:
  Patterns modeled after detection rules used by open-source secret-scanning tools
  (gitleaks, detect-secrets). Re-implemented inline to keep the POC offline-first and
  avoid package legitimacy gating. Individual patterns may have edge-case false positives
  that should be tuned against Phase 4 fixtures.
"""
import hashlib
import math
import re
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Pattern registry: each entry defines one detection rule.
# Order matters: more specific patterns (e.g. AWS key, JWT) before generic entropy.
# ---------------------------------------------------------------------------

class _PatternDef(NamedTuple):
    label: str
    category: str
    severity: str
    pattern: re.Pattern


# Compile all patterns at module load time (O(1) per scan call).
# All patterns use explicit quantifiers to bound backtracking (ReDoS mitigation).
_PATTERNS: list[_PatternDef] = [
    # -----------------------------------------------------------------------
    # Secrets — high severity
    # -----------------------------------------------------------------------
    _PatternDef(
        label="aws_access_key_id",
        category="secrets",
        severity="high",
        pattern=re.compile(r"AKIA[0-9A-Z]{16}"),
    ),
    _PatternDef(
        label="google_api_key",
        category="secrets",
        severity="high",
        pattern=re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    ),
    _PatternDef(
        label="github_token",
        category="secrets",
        severity="high",
        # gh[pousr]_ prefix covers fine-grained, oauth, user, server, refresh tokens
        pattern=re.compile(r"gh[pousr]_[A-Za-z0-9]{36,100}"),
    ),
    _PatternDef(
        label="slack_token",
        category="secrets",
        severity="high",
        # xox[boaprs]- covers bot, oauth, app, personal, remote, server tokens
        pattern=re.compile(r"xox[boaprs]-[0-9A-Za-z\-]{10,200}"),
    ),
    _PatternDef(
        label="jwt",
        category="secrets",
        severity="high",
        # JWT: three base64url segments separated by dots; header always starts with eyJ
        pattern=re.compile(
            r"eyJ[A-Za-z0-9\-_]{2,500}\.[A-Za-z0-9\-_]{2,500}\.[A-Za-z0-9\-_]{2,500}"
        ),
    ),
    _PatternDef(
        label="private_key_block",
        category="secrets",
        severity="high",
        # PEM private key header (RSA, EC, OpenSSH, or bare PRIVATE KEY)
        pattern=re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
    ),
    _PatternDef(
        label="ssh_key",
        category="secrets",
        severity="high",
        # SSH public keys (low-risk but worth flagging for context-awareness)
        pattern=re.compile(r"ssh-rsa AAAA[0-9A-Za-z+/]{50,500}"),
    ),
    _PatternDef(
        label="bearer_token",
        category="secrets",
        severity="high",
        # Authorization header value; case-insensitive Bearer prefix
        pattern=re.compile(
            r"(?i)Bearer [A-Za-z0-9\-._~+/]{20,500}"
        ),
    ),
    _PatternDef(
        label="azure_connection_string",
        category="secrets",
        severity="high",
        # Azure storage connection string AccountKey segment
        pattern=re.compile(r"AccountKey=[A-Za-z0-9+/=]{44,100}"),
    ),
    _PatternDef(
        label="gcp_service_account",
        category="secrets",
        severity="high",
        # GCP service account JSON marker
        pattern=re.compile(r'"type"\s*:\s*"service_account"'),
    ),
    # -----------------------------------------------------------------------
    # DLP — high severity
    # -----------------------------------------------------------------------
    _PatternDef(
        label="database_url",
        category="dlp",
        severity="high",
        # Connection string with embedded credentials: scheme://user:pass@host
        pattern=re.compile(
            r"(?:postgres|mysql|mongodb|redis|mssql)://[^@\s]{1,200}:[^@\s]{1,200}@"
        ),
    ),
    # -----------------------------------------------------------------------
    # DLP — low severity (PII)
    # -----------------------------------------------------------------------
    _PatternDef(
        label="email_address",
        category="dlp",
        severity="low",
        # RFC-5321 simplified; bounded quantifiers prevent backtracking
        pattern=re.compile(
            r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,10}"
        ),
    ),
    _PatternDef(
        label="phone_number",
        category="dlp",
        severity="low",
        # International and US formats; bounded quantifiers
        pattern=re.compile(
            r"(?:\+?1[\s\-.]?)?\(?[0-9]{3}\)?[\s\-.]?[0-9]{3}[\s\-.]?[0-9]{4}"
        ),
    ),
    # NOTE: credit_card is handled separately after Luhn validation — see _scan_credit_cards
]

# Standalone credit-card candidate pattern (needs Luhn confirmation before adding as finding)
_CREDIT_CARD_CANDIDATE = re.compile(
    # 13-19 digits with optional spaces or dashes between groups
    r"\b(?:[0-9][\s\-]?){13,19}[0-9]\b"
)

# Token pattern for Shannon entropy scan (≥20 chars from base64-like charset)
_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=\-_]{20,500}")


# ---------------------------------------------------------------------------
# Helper: SHA-256 matched value (T-02-02 security contract)
# ---------------------------------------------------------------------------

def _sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of the matched text. Never returns raw text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Helper: Shannon entropy
# ---------------------------------------------------------------------------

def _shannon_entropy(s: str) -> float:
    """Shannon entropy in bits per character (0.0 for empty string)."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    length = len(s)
    return -sum(
        (count / length) * math.log2(count / length) for count in freq.values()
    )


def _is_high_entropy(token: str, threshold: float = 4.5) -> bool:
    """
    True if token's Shannon entropy exceeds the threshold AND it looks like a secret.

    False-positive guards (Pitfall 7):
    - All-lowercase alpha tokens are natural language, not secrets.
    - Tokens with fewer than 10 unique characters lack the diversity secrets have.

    Threshold [ASSUMED]: 4.5 bits/char separates typical secrets (5.5–6.0 bits/char)
    from natural language (3.5–4.0 bits/char). Should be tuned against Phase 4 fixtures.
    """
    if _shannon_entropy(token) < threshold:
        return False
    # Reject all-lowercase alphabetic tokens (natural language heuristic)
    if token.isalpha() and token.islower():
        return False
    # Reject tokens with low character diversity (< 10 unique chars)
    if len(set(token)) < 10:
        return False
    return True


# ---------------------------------------------------------------------------
# Helper: Luhn algorithm for credit card validation
# ---------------------------------------------------------------------------

def _luhn_check(number_str: str) -> bool:
    """
    Return True if the digit string passes the Luhn algorithm.

    Accepts strings with spaces and dashes (strips them first).
    Returns False for digit counts outside 13–19 (not a plausible card number).
    """
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Credit card scan helper
# ---------------------------------------------------------------------------

def _scan_credit_cards(text: str) -> list[dict]:
    """
    Find candidate 13–19-digit runs; return only those that pass Luhn.

    Yields finding dicts with the standard shape (matched_value = SHA-256).
    """
    findings = []
    for match in _CREDIT_CARD_CANDIDATE.finditer(text):
        raw = match.group(0)
        # Strip spaces and dashes to get the digit string
        digits_only = re.sub(r"[\s\-]", "", raw)
        if not _luhn_check(digits_only):
            continue  # skip Luhn-invalid runs — avoids most false positives
        findings.append({
            "category": "dlp",
            "label": "credit_card",
            "span": [match.start(), match.end()],
            "severity": "high",
            "matched_value": _sha256_hex(digits_only),  # hash only the digits
        })
    return findings


# ---------------------------------------------------------------------------
# DlpScannerAdapter
# ---------------------------------------------------------------------------

class DlpScannerAdapter:
    """
    Deterministic DLP and secrets scanner (implements IScannerAdapter).

    Detects:
    - API keys: AWS access key, Google API key, GitHub token, Slack token
    - Auth tokens: JWT (eyJ…), Bearer tokens
    - Private keys: PEM blocks, SSH public key blobs
    - Cloud credentials: Azure connection string, GCP service account JSON
    - Database URLs with embedded credentials
    - PII: email addresses, phone numbers
    - Financial: credit card numbers (Luhn-validated)
    - High-entropy strings: Shannon entropy ≥ 4.5 bits/char over ≥ 20-char tokens

    Security contract (T-02-02):
      matched_value is ALWAYS sha256(matched_text).hexdigest() — a 64-char hex string.
      Raw matched text is never stored, logged, or returned.

    Performance: synchronous, stdlib-only, sub-2s on any reasonably sized prompt or response.
    """

    def scan(self, text: str) -> list[dict]:
        """
        Scan text for DLP violations and secrets.

        Args:
            text: The prompt or response text to scan.

        Returns:
            List of finding dicts, each with keys:
                category    — "secrets" | "dlp"
                label       — specific finding label (e.g. "aws_access_key_id")
                span        — [start, end] character offsets in text
                severity    — "high" | "medium" | "low"
                matched_value — sha256 hex of the matched substring (NEVER raw text)
        """
        findings: list[dict] = []

        # 1. Regex-pattern scan for all registered patterns
        for pat_def in _PATTERNS:
            for match in pat_def.pattern.finditer(text):
                raw_match = match.group(0)
                findings.append({
                    "category": pat_def.category,
                    "label": pat_def.label,
                    "span": [match.start(), match.end()],
                    "severity": pat_def.severity,
                    "matched_value": _sha256_hex(raw_match),
                })

        # 2. Credit card scan (requires Luhn validation — done in helper)
        findings.extend(_scan_credit_cards(text))

        # 3. Shannon entropy scan for unlabelled high-entropy tokens
        for match in _ENTROPY_TOKEN_RE.finditer(text):
            token = match.group(0)
            if _is_high_entropy(token):
                findings.append({
                    "category": "secrets",
                    "label": "high_entropy_string",
                    "span": [match.start(), match.end()],
                    "severity": "medium",
                    "matched_value": _sha256_hex(token),
                })

        return findings

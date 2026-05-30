"""
Unit tests for Phase 2 classification primitives.

Coverage:
- Task 1: canonical taxonomy (SCODE_TO_CANONICAL, SEVERITY_ORDER, max_severity, map_scodes)
           and Llama Guard output parser (parse_guard_output)
- Task 2: OllamaGuardAdapter (classify safe/unsafe/unknown, GuardUnavailableError, health)
- Task 3: DlpScannerAdapter (AWS key, JWT, email, high-entropy, Luhn card, false positives)

All tests run offline — no live Ollama required.
"""
import hashlib


# ---------------------------------------------------------------------------
# Task 1: parse_guard_output tests
# ---------------------------------------------------------------------------

def test_parse_guard_safe_output():
    """parse_guard_output('safe') returns ('safe', [])."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("safe")
    assert label == "safe"
    assert codes == []


def test_parse_guard_safe_with_whitespace():
    """parse_guard_output handles leading/trailing whitespace around 'safe'."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("  safe  ")
    assert label == "safe"
    assert codes == []


def test_parse_guard_unsafe_single_code():
    """parse_guard_output('unsafe\\nS1') returns ('unsafe', ['S1'])."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("unsafe\nS1")
    assert label == "unsafe"
    assert codes == ["S1"]


def test_parse_guard_unsafe_multiple_codes():
    """parse_guard_output('unsafe\\nS9,S7') returns ('unsafe', ['S9', 'S7'])."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("unsafe\nS9,S7")
    assert label == "unsafe"
    assert codes == ["S9", "S7"]


def test_parse_guard_unsafe_no_codes_line():
    """parse_guard_output with only 'unsafe' (no second line) returns ('unsafe', [])."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("unsafe")
    assert label == "unsafe"
    assert codes == []


def test_parse_guard_unknown_output():
    """parse_guard_output with garbage text returns ('unknown', []) without raising."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("garbage text that is not safe or unsafe")
    assert label == "unknown"
    assert codes == []


def test_parse_guard_empty_string():
    """parse_guard_output with empty string returns ('unknown', []) without raising."""
    from gateway.classification.taxonomy import parse_guard_output
    label, codes = parse_guard_output("")
    assert label == "unknown"
    assert codes == []


# ---------------------------------------------------------------------------
# Task 1: SCODE_TO_CANONICAL tests
# ---------------------------------------------------------------------------

def test_scode_mapping_all_14_codes_present():
    """SCODE_TO_CANONICAL has exactly 14 entries, S1 through S14."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL
    expected_codes = {f"S{i}" for i in range(1, 15)}
    assert set(SCODE_TO_CANONICAL.keys()) == expected_codes


def test_scode_mapping_values_are_tuples():
    """Each SCODE_TO_CANONICAL entry is a (category, severity) 2-tuple."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL, SEVERITY_ORDER
    for code, value in SCODE_TO_CANONICAL.items():
        assert isinstance(value, tuple), f"{code}: expected tuple, got {type(value)}"
        assert len(value) == 2, f"{code}: expected 2-tuple, got {len(value)}-tuple"
        category, severity = value
        assert isinstance(category, str), f"{code}: category must be str"
        assert severity in SEVERITY_ORDER, (
            f"{code}: severity '{severity}' not in SEVERITY_ORDER"
        )


def test_scode_s1_dangerous_behavior_high():
    """S1 (Violent Crimes) → dangerous_behavior / high."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL
    assert SCODE_TO_CANONICAL["S1"] == ("dangerous_behavior", "high")


def test_scode_s3_illegal_behavior_critical():
    """S3 (Sex Crimes) → illegal_behavior / critical."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL
    assert SCODE_TO_CANONICAL["S3"] == ("illegal_behavior", "critical")


def test_scode_s4_child_safety_critical():
    """S4 (Child Exploitation) → child_safety / critical."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL
    assert SCODE_TO_CANONICAL["S4"] == ("child_safety", "critical")


def test_scode_s9_dangerous_behavior_critical():
    """S9 (Indiscriminate Weapons) → dangerous_behavior / critical."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL
    assert SCODE_TO_CANONICAL["S9"] == ("dangerous_behavior", "critical")


def test_scode_s5_and_s14_are_unknown():
    """S5 (Defamation) and S14 (Code Interpreter Abuse) map to 'unknown' category."""
    from gateway.classification.taxonomy import SCODE_TO_CANONICAL
    assert SCODE_TO_CANONICAL["S5"][0] == "unknown"
    assert SCODE_TO_CANONICAL["S14"][0] == "unknown"


# ---------------------------------------------------------------------------
# Task 1: max_severity tests
# ---------------------------------------------------------------------------

def test_max_severity_low_and_high_returns_high():
    """max_severity('low', 'high') == 'high'."""
    from gateway.classification.taxonomy import max_severity
    assert max_severity("low", "high") == "high"


def test_max_severity_high_and_low_returns_high():
    """max_severity('high', 'low') == 'high' (order-independent)."""
    from gateway.classification.taxonomy import max_severity
    assert max_severity("high", "low") == "high"


def test_max_severity_none_and_none_returns_none():
    """max_severity('none', 'none') == 'none'."""
    from gateway.classification.taxonomy import max_severity
    assert max_severity("none", "none") == "none"


def test_max_severity_critical_dominates():
    """max_severity('high', 'critical') == 'critical'."""
    from gateway.classification.taxonomy import max_severity
    assert max_severity("high", "critical") == "critical"


def test_max_severity_unknown_treated_as_none():
    """Unknown severity values are treated as index 0 (below 'low')."""
    from gateway.classification.taxonomy import max_severity
    # 'bogus' is not in SEVERITY_ORDER → index 0 → 'none' equivalent
    # max('bogus', 'low') == 'low'
    assert max_severity("bogus_unknown_val", "low") == "low"
    # max('bogus', 'bogus') → returns SEVERITY_ORDER[0] == 'none'
    assert max_severity("bogus_unknown_val", "another_bogus") == "none"


# ---------------------------------------------------------------------------
# Task 1: map_scodes tests
# ---------------------------------------------------------------------------

def test_map_scodes_empty_returns_none_severity():
    """map_scodes([]) returns ([], 'none')."""
    from gateway.classification.taxonomy import map_scodes
    categories, severity = map_scodes([])
    assert categories == []
    assert severity == "none"


def test_map_scodes_single_code():
    """map_scodes(['S1']) returns (['dangerous_behavior'], 'high')."""
    from gateway.classification.taxonomy import map_scodes
    categories, severity = map_scodes(["S1"])
    assert "dangerous_behavior" in categories
    assert severity == "high"


def test_map_scodes_multiple_codes_deduplicated():
    """S1 + S9 both map to dangerous_behavior — result deduplicates."""
    from gateway.classification.taxonomy import map_scodes
    categories, severity = map_scodes(["S1", "S9"])
    assert categories.count("dangerous_behavior") == 1
    assert severity == "critical"  # S9 is critical, dominates S1's high


def test_map_scodes_unknown_code_is_skipped():
    """Unrecognised S-codes (e.g. 'S99') are skipped — no KeyError."""
    from gateway.classification.taxonomy import map_scodes
    categories, severity = map_scodes(["S99"])
    assert categories == []
    assert severity == "none"


# ---------------------------------------------------------------------------
# Task 2: OllamaGuardAdapter tests (imports deferred to avoid collection errors)
# ---------------------------------------------------------------------------

def test_guard_adapter_classify_safe(monkeypatch):
    """classify with guard returning 'safe' → label='safe', severity='none', confidence=1.0."""
    import httpx

    def handler(request):
        return httpx.Response(200, json={
            "model": "llama-guard3",
            "message": {"role": "assistant", "content": "safe"},
            "done": True,
        })

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings

    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        adapter.classify([{"role": "user", "content": "hello"}], "prompt")
    )
    assert result["llama_guard_label"] == "safe"
    assert result["llama_guard_categories"] == []
    assert result["categories"] == []
    assert result["overall_severity"] == "none"
    assert result["confidence"] == 1.0


def test_guard_adapter_classify_unsafe_single_code(monkeypatch):
    """classify with guard returning 'unsafe\\nS1' → dangerous_behavior, high, confidence=0.9."""
    import httpx

    def handler(request):
        return httpx.Response(200, json={
            "model": "llama-guard3",
            "message": {"role": "assistant", "content": "unsafe\nS1"},
            "done": True,
        })

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings

    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        adapter.classify([{"role": "user", "content": "make a bomb"}], "prompt")
    )
    assert result["llama_guard_label"] == "unsafe"
    assert result["llama_guard_categories"] == ["S1"]
    assert "dangerous_behavior" in result["categories"]
    assert result["overall_severity"] == "high"
    assert result["confidence"] == 0.9


def test_guard_adapter_classify_unsafe_multiple_codes(monkeypatch):
    """classify with 'unsafe\\nS9,S7' → both categories resolved, critical severity."""
    import httpx

    def handler(request):
        return httpx.Response(200, json={
            "model": "llama-guard3",
            "message": {"role": "assistant", "content": "unsafe\nS9,S7"},
            "done": True,
        })

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings

    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        adapter.classify([{"role": "user", "content": "harmful"}], "prompt")
    )
    assert result["llama_guard_label"] == "unsafe"
    assert set(result["llama_guard_categories"]) == {"S9", "S7"}
    assert "dangerous_behavior" in result["categories"]
    assert "privacy" in result["categories"]
    assert result["overall_severity"] == "critical"


def test_guard_adapter_classify_unknown_content(monkeypatch):
    """classify with unparseable guard content → label='unknown', confidence=0.0, no raise."""
    import httpx

    def handler(request):
        return httpx.Response(200, json={
            "model": "llama-guard3",
            "message": {"role": "assistant", "content": "I cannot determine the safety of this."},
            "done": True,
        })

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings

    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        adapter.classify([{"role": "user", "content": "hello"}], "prompt")
    )
    assert result["llama_guard_label"] == "unknown"
    assert result["confidence"] == 0.0


def test_guard_adapter_raises_on_connect_error():
    """ConnectError → GuardUnavailableError raised."""
    import httpx
    from gateway.adapters.ollama_guard import OllamaGuardAdapter, GuardUnavailableError
    from gateway.settings import Settings
    import asyncio

    def handler(request):
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    with pytest.raises(GuardUnavailableError):
        asyncio.get_event_loop().run_until_complete(
            adapter.classify([{"role": "user", "content": "test"}], "prompt")
        )


def test_guard_adapter_raises_on_http_404():
    """HTTP 404 from Ollama → GuardUnavailableError raised."""
    import httpx
    from gateway.adapters.ollama_guard import OllamaGuardAdapter, GuardUnavailableError
    from gateway.settings import Settings
    import asyncio

    def handler(request):
        return httpx.Response(404, text="model not found")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    with pytest.raises(GuardUnavailableError):
        asyncio.get_event_loop().run_until_complete(
            adapter.classify([{"role": "user", "content": "test"}], "prompt")
        )


def test_guard_adapter_health_all_ok():
    """health() returns dict with 'ok' statuses when Ollama responds correctly."""
    import httpx
    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings
    import asyncio

    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.22.1"})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"modelfile": "..."})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    result = asyncio.get_event_loop().run_until_complete(adapter.health())
    assert result["ollama_process"] == "ok"
    assert result["guard_model"] == "ok"
    assert "model_name" in result


def test_guard_adapter_health_model_not_found():
    """health() returns guard_model='not_found' when /api/show returns 404."""
    import httpx
    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings
    import asyncio

    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.22.1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    result = asyncio.get_event_loop().run_until_complete(adapter.health())
    assert result["ollama_process"] == "ok"
    assert result["guard_model"] == "not_found"


def test_guard_adapter_health_never_raises():
    """health() never raises even when Ollama is completely down."""
    import httpx
    from gateway.adapters.ollama_guard import OllamaGuardAdapter
    from gateway.settings import Settings
    import asyncio

    def handler(request):
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    settings = Settings()
    adapter = OllamaGuardAdapter(http_client, settings)

    # Must not raise
    result = asyncio.get_event_loop().run_until_complete(adapter.health())
    assert isinstance(result, dict)
    assert result["ollama_process"] == "error"


# ---------------------------------------------------------------------------
# Task 3: DlpScannerAdapter tests
# ---------------------------------------------------------------------------

def test_scanner_detects_aws_access_key():
    """scan() flags AKIA... pattern as aws_access_key_id in secrets category."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    findings = scanner.scan("My key is AKIA1234567890ABCDEF for AWS access")
    labels = [f["label"] for f in findings]
    assert "aws_access_key_id" in labels
    # Verify the finding properties
    aws_finding = next(f for f in findings if f["label"] == "aws_access_key_id")
    assert aws_finding["category"] == "secrets"
    assert aws_finding["severity"] == "high"


def test_scanner_detects_jwt():
    """scan() flags a JWT triple-dot pattern as jwt in secrets category."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    findings = scanner.scan(f"Authorization: Bearer {jwt_token}")
    labels = [f["label"] for f in findings]
    assert "jwt" in labels
    jwt_finding = next(f for f in findings if f["label"] == "jwt")
    assert jwt_finding["category"] == "secrets"
    assert jwt_finding["severity"] == "high"


def test_scanner_detects_email():
    """scan() flags an email address as email_address in dlp category with low severity."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    findings = scanner.scan("contact me at jane@example.com for more info")
    labels = [f["label"] for f in findings]
    assert "email_address" in labels
    email_finding = next(f for f in findings if f["label"] == "email_address")
    assert email_finding["category"] == "dlp"
    assert email_finding["severity"] == "low"


def test_scanner_luhn_valid_card_flagged():
    """A valid Luhn card number is flagged as credit_card with high severity."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    # 4111111111111111 is a well-known Luhn-valid Visa test card
    findings = scanner.scan("Card: 4111111111111111")
    labels = [f["label"] for f in findings]
    assert "credit_card" in labels
    card_finding = next(f for f in findings if f["label"] == "credit_card")
    assert card_finding["severity"] == "high"


def test_scanner_luhn_invalid_card_not_flagged():
    """A 16-digit run that fails Luhn is NOT flagged as a credit card."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    # 1234567890123456 fails Luhn
    findings = scanner.scan("Number: 1234567890123456")
    card_findings = [f for f in findings if f["label"] == "credit_card"]
    assert card_findings == [], (
        f"Luhn-invalid number should not be flagged, got: {card_findings}"
    )


def test_scanner_matched_value_is_sha256_not_raw():
    """Every finding's matched_value is a 64-char hex SHA-256, never the raw secret."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    findings = scanner.scan("My AWS key: AKIA1234567890ABCDEF and email: test@test.com")
    assert len(findings) > 0, "Expected at least one finding"
    for finding in findings:
        mv = finding["matched_value"]
        assert len(mv) == 64, (
            f"matched_value should be 64-char SHA-256 hex, got len={len(mv)}: {mv!r}"
        )
        # Must be valid lowercase hex
        assert all(c in "0123456789abcdef" for c in mv), (
            f"matched_value contains non-hex chars: {mv!r}"
        )
        # Must NOT be the raw secret value (AKIA... would be 20 chars, not 64)
        assert mv != "AKIA1234567890ABCDEF"


def test_scanner_high_entropy_base64_flagged():
    """A high-entropy base64 token (≥20 chars, ≥4.5 bits/char) is flagged as high_entropy_string."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    # This is a realistic secret: random base64, high entropy, long enough
    # 32-char mixed base64 with numbers and mixed case — entropy well above 4.5
    high_entropy_token = "aB3xK9mZpQ7rN2wYsJ6vL1tC8uE0iO4h"
    findings = scanner.scan(f"secret={high_entropy_token}")
    labels = [f["label"] for f in findings]
    assert "high_entropy_string" in labels, (
        f"Expected high_entropy_string finding, got labels: {labels}"
    )


def test_scanner_lowercase_word_not_high_entropy():
    """An all-lowercase alphabetic token is NOT flagged as high entropy."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    # Long lowercase word — natural language, should not trip entropy check
    findings = scanner.scan("thequickbrownfoxjumpsoverthelazydog")
    entropy_findings = [f for f in findings if f["label"] == "high_entropy_string"]
    assert entropy_findings == [], (
        f"Lowercase word should not be flagged as high entropy: {entropy_findings}"
    )


def test_scanner_finding_shape_complete():
    """Each finding dict has all required keys: category, label, span, severity, matched_value."""
    from gateway.adapters.dlp_scanner import DlpScannerAdapter
    scanner = DlpScannerAdapter()
    findings = scanner.scan("AWS: AKIA1234567890ABCDEF")
    assert len(findings) > 0
    for finding in findings:
        for key in ("category", "label", "span", "severity", "matched_value"):
            assert key in finding, f"Finding missing key '{key}': {finding}"
        assert isinstance(finding["span"], list)
        assert len(finding["span"]) == 2
        start, end = finding["span"]
        assert isinstance(start, int) and isinstance(end, int)
        assert end > start


# Import pytest at module level for pytest.raises
import pytest

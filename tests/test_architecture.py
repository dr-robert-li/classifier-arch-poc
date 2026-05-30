"""
Architecture enforcement tests (GATE-03).

SC-2: The gateway is the only path to Ollama.

Verifies that no module outside gateway/adapters/ollama_assistant.py references
the Ollama base URL (11434 or ollama_base_url). Exclusions:
- gateway/adapters/ollama_assistant.py  — the ONLY allowed location for Ollama calls
- gateway/settings.py                   — holds the config field (value, not HTTP call)
- tests/test_architecture.py            — this file references the pattern for inspection

Any other gateway/*.py containing these strings is an architectural violation.
"""
import subprocess


def test_no_direct_ollama_calls_outside_adapter():
    """
    GATE-03: Only gateway/adapters/ollama_assistant.py may reference the Ollama base URL.
    gateway/settings.py holds the config value (a config field is not an HTTP call).
    """
    result = subprocess.run(
        [
            "grep",
            "-rEl",
            r"11434|ollama_base_url",
            "gateway/",
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
    )

    # Files that are explicitly allowed to reference 11434 or ollama_base_url
    allowed = {
        "gateway/adapters/ollama_assistant.py",  # the enforced boundary
        "gateway/settings.py",                   # config field, not an HTTP call
    }

    offending_files = [
        f.strip()
        for f in result.stdout.strip().splitlines()
        if f.strip() not in allowed
    ]

    assert offending_files == [], (
        f"GATE-03 violation — direct Ollama references outside allowed modules: "
        f"{offending_files}"
    )

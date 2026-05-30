"""
Shared pytest fixtures and adapter stubs for offline testing.

MockAssistantAdapter:  always returns a fixed response (no live Ollama needed).
UnavailableAssistantAdapter: always raises OllamaUnavailableError (for 503 tests).
MockGuardAdapter:      always returns a safe classification block (no live Ollama).
MockScannerAdapter:    always returns an empty findings list.
HighSeverityGuardAdapter: returns an unsafe/high classification block (for REL-02 tests).
UnavailableGuardAdapter: always raises GuardUnavailableError (for fail-closed tests).
SecretsFoundScannerAdapter: returns one high-severity secrets finding.

The `client` fixture:
- Injects tmp_path-based db + jsonl paths into settings so tests are isolated.
- Overrides get_assistant_adapter, get_guard_adapter, get_scanner_adapter dependencies.
- Uses TestClient with lifespan=True so startup/shutdown hooks run.
- Clears dependency_overrides after the fixture is torn down.

ATOMICITY NOTE (02-02): get_guard_adapter and get_scanner_adapter are overridden in ALL
client fixtures at the same time main.py wires real guard+scanner adapters. This ensures
offline tests never reach live Ollama via the real adapters.
"""
import pytest
from fastapi.testclient import TestClient

from gateway.main import app


# ---------------------------------------------------------------------------
# Assistant adapter stubs (Phase 1 — unchanged)
# ---------------------------------------------------------------------------

class MockAssistantAdapter:
    """Offline stub — returns a fixed response without calling Ollama."""

    async def chat(self, messages: list[dict]) -> dict:
        return {"content": "mock response", "model": "mock-llama"}

    async def health(self) -> dict:
        return {
            "ollama_process": "ok",
            "assistant_model": "ok",
            "model_name": "mock-llama",
        }


class UnavailableAssistantAdapter:
    """Offline stub — always raises OllamaUnavailableError (simulates model down)."""

    async def chat(self, messages: list[dict]) -> dict:
        from gateway.adapters.ollama_assistant import OllamaUnavailableError
        raise OllamaUnavailableError("model stopped in test")

    async def health(self) -> dict:
        return {
            "ollama_process": "error",
            "assistant_model": "error",
            "model_name": "mock-llama",
        }


# ---------------------------------------------------------------------------
# Guard adapter stubs (Phase 2 — new)
# ---------------------------------------------------------------------------

class MockGuardAdapter:
    """Offline stub — always returns a safe classification block."""

    async def classify(self, messages: list[dict], direction: str) -> dict:
        return {
            "llama_guard_label": "safe",
            "llama_guard_categories": [],
            "categories": [],
            "overall_severity": "none",
            "confidence": 1.0,
        }

    async def health(self) -> dict:
        return {
            "ollama_process": "ok",
            "guard_model": "ok",
            "model_name": "mock-guard",
        }


class HighSeverityGuardAdapter:
    """Offline stub — returns an unsafe/high-severity block for REL-02 tests."""

    async def classify(self, messages: list[dict], direction: str) -> dict:
        return {
            "llama_guard_label": "unsafe",
            "llama_guard_categories": ["S1"],
            "categories": ["dangerous_behavior"],
            "overall_severity": "high",
            "confidence": 0.9,
        }

    async def health(self) -> dict:
        return {
            "ollama_process": "ok",
            "guard_model": "ok",
            "model_name": "mock-guard-high",
        }


class UnavailableGuardAdapter:
    """Offline stub — always raises GuardUnavailableError for fail-closed tests."""

    async def classify(self, messages: list[dict], direction: str) -> dict:
        from gateway.adapters.ollama_guard import GuardUnavailableError
        raise GuardUnavailableError("guard model stopped in test")

    async def health(self) -> dict:
        return {
            "ollama_process": "error",
            "guard_model": "error",
            "model_name": "mock-guard",
        }


# ---------------------------------------------------------------------------
# Scanner adapter stubs (Phase 2 — new)
# ---------------------------------------------------------------------------

class MockScannerAdapter:
    """Offline stub — always returns an empty findings list."""

    def scan(self, text: str) -> list[dict]:
        return []


class SecretsFoundScannerAdapter:
    """Offline stub — returns one high-severity secrets finding with sha256 matched_value."""

    def scan(self, text: str) -> list[dict]:
        import hashlib
        sha = hashlib.sha256(b"AKIA1234567890ABCDEF").hexdigest()
        return [{
            "category": "secrets",
            "label": "aws_access_key_id",
            "span": [0, 20],
            "severity": "high",
            "matched_value": sha,
        }]


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """
    TestClient fixture with all mock adapters and isolated tmp_path data stores.

    Overrides all three adapter DI deps (assistant, guard, scanner) so no test
    in the standard client fixture ever reaches live Ollama or live DLP scanning.
    Clears dependency_overrides after the test.
    """
    from gateway.settings import settings
    from gateway.routes.chat import get_assistant_adapter
    from gateway.routes.classify import get_guard_adapter, get_scanner_adapter

    # Point data stores to tmp_path so tests are isolated and fast
    settings.sqlite_db_path = str(tmp_path / "test.db")
    settings.jsonl_audit_path = str(tmp_path / "test.jsonl")

    # Override all adapter dependencies (ATOMICITY: all three at once)
    app.dependency_overrides[get_assistant_adapter] = lambda: MockAssistantAdapter()
    app.dependency_overrides[get_guard_adapter] = lambda: MockGuardAdapter()
    app.dependency_overrides[get_scanner_adapter] = lambda: MockScannerAdapter()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def unavailable_client(tmp_path):
    """
    TestClient fixture with UnavailableAssistantAdapter for 503/REL-03 tests.

    Guard and scanner are mocked as safe/empty so the assistant-unavailable path
    is isolated without guard interference.
    """
    from gateway.settings import settings
    from gateway.routes.chat import get_assistant_adapter
    from gateway.routes.classify import get_guard_adapter, get_scanner_adapter

    settings.sqlite_db_path = str(tmp_path / "unavail.db")
    settings.jsonl_audit_path = str(tmp_path / "unavail.jsonl")

    app.dependency_overrides[get_assistant_adapter] = lambda: UnavailableAssistantAdapter()
    app.dependency_overrides[get_guard_adapter] = lambda: MockGuardAdapter()
    app.dependency_overrides[get_scanner_adapter] = lambda: MockScannerAdapter()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def client_guard_unavailable_scanner_high(tmp_path):
    """
    Composite fixture: MockAssistant + UnavailableGuard + SecretsFoundScanner.

    For fail-closed (REL-02) tests: guard down + scanner finds a secret.
    The orchestrator should surface overall_severity="high" with llama_guard_label="unknown".
    Phase 2: /classify returns 200 with the block (503 enforcement is in chat.py, Phase 02-03).
    """
    from gateway.settings import settings
    from gateway.routes.chat import get_assistant_adapter
    from gateway.routes.classify import get_guard_adapter, get_scanner_adapter

    settings.sqlite_db_path = str(tmp_path / "unavail_high.db")
    settings.jsonl_audit_path = str(tmp_path / "unavail_high.jsonl")

    app.dependency_overrides[get_assistant_adapter] = lambda: MockAssistantAdapter()
    app.dependency_overrides[get_guard_adapter] = lambda: UnavailableGuardAdapter()
    app.dependency_overrides[get_scanner_adapter] = lambda: SecretsFoundScannerAdapter()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def client_guard_unavailable_scanner_low(tmp_path):
    """
    Composite fixture: MockAssistant + UnavailableGuard + MockScanner (no findings).

    For fail-closed (REL-02) tests: guard down + scanner clean → proceed with warning.
    Phase 2: /classify returns 200 with the block (scanner-clean path).
    """
    from gateway.settings import settings
    from gateway.routes.chat import get_assistant_adapter
    from gateway.routes.classify import get_guard_adapter, get_scanner_adapter

    settings.sqlite_db_path = str(tmp_path / "unavail_low.db")
    settings.jsonl_audit_path = str(tmp_path / "unavail_low.jsonl")

    app.dependency_overrides[get_assistant_adapter] = lambda: MockAssistantAdapter()
    app.dependency_overrides[get_guard_adapter] = lambda: UnavailableGuardAdapter()
    app.dependency_overrides[get_scanner_adapter] = lambda: MockScannerAdapter()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()

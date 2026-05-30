"""
Shared pytest fixtures and adapter stubs for offline testing.

MockAssistantAdapter:  always returns a fixed response (no live Ollama needed).
UnavailableAssistantAdapter: always raises OllamaUnavailableError (for 503 tests).

The `client` fixture:
- Injects tmp_path-based db + jsonl paths into settings so tests are isolated.
- Overrides get_assistant_adapter dependency with MockAssistantAdapter.
- Uses TestClient with lifespan=True so startup/shutdown hooks run.
- Clears dependency_overrides after the fixture is torn down.
"""
import pytest
from fastapi.testclient import TestClient

from gateway.main import app


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


@pytest.fixture
def client(tmp_path):
    """
    TestClient fixture with MockAssistantAdapter and isolated tmp_path data stores.

    Overrides settings paths so each test uses a fresh, isolated SQLite db and JSONL file.
    Clears dependency_overrides after the test.
    """
    from gateway.settings import settings
    from gateway.routes.chat import get_assistant_adapter

    # Point data stores to tmp_path so tests are isolated and fast
    settings.sqlite_db_path = str(tmp_path / "test.db")
    settings.jsonl_audit_path = str(tmp_path / "test.jsonl")

    # Override assistant adapter dependency
    app.dependency_overrides[get_assistant_adapter] = lambda: MockAssistantAdapter()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def unavailable_client(tmp_path):
    """
    TestClient fixture with UnavailableAssistantAdapter for 503/REL-03 tests.
    """
    from gateway.settings import settings
    from gateway.routes.chat import get_assistant_adapter

    settings.sqlite_db_path = str(tmp_path / "unavail.db")
    settings.jsonl_audit_path = str(tmp_path / "unavail.jsonl")

    app.dependency_overrides[get_assistant_adapter] = lambda: UnavailableAssistantAdapter()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()

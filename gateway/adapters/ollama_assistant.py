"""
OllamaAssistantAdapter — the ONLY module that calls Ollama at localhost:11434 (GATE-03).

No other module in the gateway package may reference the Ollama base URL, port 11434,
or ollama_base_url. This boundary is enforced by test_architecture.py.
"""
import logging

import httpx

from gateway.settings import Settings

logger = logging.getLogger(__name__)


class OllamaUnavailableError(Exception):
    """
    Raised when the Ollama service is unreachable, times out, or the
    configured assistant model is not found.

    Callers must catch this and return HTTP 503 — never let it propagate as
    an unhandled 500. The gateway maps all Ollama unavailability reasons to
    503 (not 404, even if Ollama returned 404 for a missing model).
    """


class OllamaAssistantAdapter:
    """
    Adapter for the Ollama assistant language model.

    - All HTTP traffic to Ollama is isolated in this class (GATE-03).
    - Takes a shared httpx.AsyncClient (from app.state) for connection reuse.
    - Passes the Settings object (not raw URL) so main.py does not independently
      reference ollama_base_url and trip the GATE-03 grep test.
    """

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = http_client
        self._settings = settings

    async def chat(self, messages: list[dict]) -> dict:
        """
        POST to Ollama /api/chat with stream=false.

        Args:
            messages: List of {'role': str, 'content': str} dicts.

        Returns:
            {'content': str, 'model': str}

        Raises:
            OllamaUnavailableError on ConnectError, TimeoutException, or HTTP 404
            (model not found). HTTP 404 from Ollama maps to OllamaUnavailableError,
            not a 404 to the caller.
        """
        url = f"{self._settings.ollama_base_url}/api/chat"
        payload = {
            "model": self._settings.ollama_assistant_model,
            "messages": messages,
            "stream": False,
        }
        try:
            response = await self._client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                f"Ollama process unreachable at {self._settings.ollama_base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError(
                f"Ollama request timed out ({self._settings.ollama_timeout_seconds}s): {exc}"
            ) from exc

        if response.status_code == 404:
            body = response.text
            raise OllamaUnavailableError(
                f"Ollama model not found (HTTP 404): {body}"
            )

        response.raise_for_status()
        data = response.json()

        return {
            "content": data["message"]["content"],
            "model": data["model"],
        }

    async def health(self) -> dict:
        """
        Probe Ollama process liveness and assistant-model availability.

        Returns a dict with keys:
            ollama_process: 'ok' | 'error'
            assistant_model: 'ok' | 'not_found' | 'error'
            model_name: str

        Never raises — all failures are reported as status strings so /health
        never returns HTTP 500 when Ollama is down.
        """
        base = self._settings.ollama_base_url
        model = self._settings.ollama_assistant_model
        result: dict = {
            "ollama_process": "error",
            "assistant_model": "error",
            "model_name": model,
        }

        try:
            version_resp = await self._client.get(f"{base}/api/version")
            if version_resp.status_code == 200:
                result["ollama_process"] = "ok"
            else:
                return result
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.debug("Ollama process health check failed: %s", exc)
            return result

        try:
            show_resp = await self._client.post(
                f"{base}/api/show", json={"model": model}
            )
            if show_resp.status_code == 200:
                result["assistant_model"] = "ok"
            elif show_resp.status_code == 404:
                result["assistant_model"] = "not_found"
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.debug("Ollama model health check failed: %s", exc)

        return result

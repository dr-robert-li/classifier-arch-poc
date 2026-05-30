"""
OllamaGuardAdapter — calls Llama Guard 3 via Ollama for safety classification (GATE-03).

This adapter is explicitly allowed to reference ollama_base_url because it is one of the
two legitimate Ollama callers (alongside ollama_assistant.py). It is added to the GATE-03
allowlist in tests/test_architecture.py.

No other module in the gateway package may reference the Ollama base URL, port 11434,
or ollama_base_url. This boundary is enforced by test_architecture.py.
"""
import logging

import httpx

from gateway.classification.taxonomy import (
    parse_guard_output,
    map_scodes,
)
from gateway.settings import Settings

logger = logging.getLogger(__name__)


class GuardUnavailableError(Exception):
    """
    Raised when the Llama Guard model is unreachable, times out, or is not found.

    Callers (orchestrator in Phase 2, chat route in Phase 3) must catch this and
    apply the fail-closed policy: if the scanner independently finds high/critical
    severity content, return HTTP 503; otherwise proceed with a logged warning.

    Analogous to OllamaUnavailableError for the assistant adapter.
    """


class OllamaGuardAdapter:
    """
    Adapter for Llama Guard 3 safety classification via Ollama /api/chat.

    - All HTTP traffic to the guard model is isolated in this class (GATE-03).
    - Mirrors OllamaAssistantAdapter in constructor signature, error mapping, and health().
    - Takes a shared httpx.AsyncClient (from app.state) for connection reuse.
    - Passes the Settings object (not raw URL) so main.py does not independently
      reference ollama_base_url and trip the GATE-03 grep test.

    classify() output shape:
        {
            "llama_guard_label":    "safe" | "unsafe" | "unknown",
            "llama_guard_categories": ["S1", "S10"],   # raw S-codes
            "categories":           ["dangerous_behavior", "hate_discrimination"],
            "overall_severity":     "high",
            "confidence":           0.9,               # [ASSUMED] heuristic; see method
        }

    Note: `deterministic_findings` is NOT included — that field is added by DlpScannerAdapter.
    """

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = http_client
        self._settings = settings

    async def classify(self, messages: list[dict], direction: str) -> dict:
        """
        Classify a conversation using Llama Guard 3 via Ollama /api/chat.

        The caller is responsible for building the correct messages array:
          - Prompt-side: [{"role": "user", "content": "<prompt>"}]
          - Response-side: [{"role": "user", "content": "<prompt>"},
                            {"role": "assistant", "content": "<response>"}]
        The `direction` parameter is accepted for interface symmetry (IGuardAdapter);
        no branching on it is needed inside classify().

        Args:
            messages: Conversation messages array for the guard model.
            direction: "prompt" or "response" (caller-set; accepted for interface symmetry).

        Returns:
            Classification result dict (see class docstring for shape).

        Raises:
            GuardUnavailableError: on ConnectError, TimeoutException, or HTTP 404.
            Other HTTP errors (e.g. 500) are re-raised as-is (not caught here).
        """
        url = f"{self._settings.ollama_base_url}/api/chat"
        payload = {
            "model": self._settings.ollama_guard_model,
            "messages": messages,
            "stream": False,
        }

        try:
            response = await self._client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise GuardUnavailableError(
                f"Ollama guard process unreachable at {self._settings.ollama_base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise GuardUnavailableError(
                f"Ollama guard request timed out ({self._settings.ollama_timeout_seconds}s): {exc}"
            ) from exc

        if response.status_code == 404:
            body = response.text
            raise GuardUnavailableError(
                f"Guard model not found (HTTP 404): {body}"
            )

        response.raise_for_status()
        data = response.json()

        raw_content = data["message"]["content"]
        label, s_codes = parse_guard_output(raw_content)
        categories, overall_severity = map_scodes(s_codes)

        # [ASSUMED] Confidence is a heuristic — Ollama /api/chat for llama-guard3 does
        # not return logprobs or a statistical probability score. Values chosen:
        #   1.0 = "safe"    (clean verdict — high confidence)
        #   0.9 = "unsafe"  (flagged — high confidence in the unsafe verdict)
        #   0.0 = "unknown" (guard output unrecognised — fail-safe low confidence)
        if label == "safe":
            confidence = 1.0
        elif label == "unsafe":
            confidence = 0.9
        else:
            confidence = 0.0

        return {
            "llama_guard_label": label,
            "llama_guard_categories": s_codes,
            "categories": categories,
            "overall_severity": overall_severity,
            "confidence": confidence,
        }

    async def health(self) -> dict:
        """
        Probe Ollama process liveness and guard-model availability.

        Mirrors OllamaAssistantAdapter.health() exactly:
          1. GET /api/version → ollama_process "ok" | "error"
          2. POST /api/show {"model": guard_model} → guard_model "ok" | "not_found" | "error"

        Returns a dict with keys:
            ollama_process: 'ok' | 'error'
            guard_model:    'ok' | 'not_found' | 'error'
            model_name:     str (the configured guard model name)

        Never raises — all failures are reported as status strings so /health
        never returns HTTP 500 when Ollama is down.
        """
        base = self._settings.ollama_base_url
        model = self._settings.ollama_guard_model
        result: dict = {
            "ollama_process": "error",
            "guard_model": "error",
            "model_name": model,
        }

        try:
            version_resp = await self._client.get(f"{base}/api/version")
            if version_resp.status_code == 200:
                result["ollama_process"] = "ok"
            else:
                return result
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.debug("Ollama guard process health check failed: %s", exc)
            return result

        try:
            show_resp = await self._client.post(
                f"{base}/api/show", json={"model": model}
            )
            if show_resp.status_code == 200:
                result["guard_model"] = "ok"
            elif show_resp.status_code == 404:
                result["guard_model"] = "not_found"
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.debug("Ollama guard model health check failed: %s", exc)

        return result

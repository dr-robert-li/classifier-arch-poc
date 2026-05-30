"""
Policy engine — loads the versioned YAML policy and exposes the pause decision,
redaction toggle, false-positive handling, category labels, and policy version.

This replaces the earlier hard-coded constant: the pause rule, redaction behaviour,
and plain-language labels are now driven from gateway/policy/policy.yaml so the policy
is a versioned, auditable artifact (CLAUDE.md functional requirement).
"""
from __future__ import annotations

import os
from typing import Any

import yaml

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "policy.yaml")

# Fallback used only if the YAML file is missing/unreadable (keeps the gateway up).
_FALLBACK: dict[str, Any] = {
    "version": "fallback-v0",
    "pause": {"severities": ["high", "critical"]},
    "description": "Fallback policy (policy.yaml not found): pause high/critical.",
    "severity_overrides": {},
    "redaction": {"enabled": True},
    "false_positive": {"allowed": True, "record_in_audit": True},
    "category_labels": {},
}


class PolicyEngine:
    def __init__(self, data: dict[str, Any], source: str):
        self._data = data
        self.source = source

    @classmethod
    def load(cls, path: str | None = None) -> "PolicyEngine":
        path = path or _DEFAULT_PATH
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return cls(data, path)
        except Exception:
            return cls(dict(_FALLBACK), "fallback")

    # --- decisions -----------------------------------------------------------
    @property
    def version(self) -> str:
        return self._data.get("version", "unknown")

    @property
    def pause_severities(self) -> set[str]:
        return set(self._data.get("pause", {}).get("severities", ["high", "critical"]))

    def is_paused(self, severity: str) -> bool:
        return severity in self.pause_severities

    @property
    def redaction_enabled(self) -> bool:
        return bool(self._data.get("redaction", {}).get("enabled", True))

    @property
    def false_positive_allowed(self) -> bool:
        return bool(self._data.get("false_positive", {}).get("allowed", True))

    @property
    def category_labels(self) -> dict[str, str]:
        return dict(self._data.get("category_labels", {}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.version,
            "pause_severities": sorted(self.pause_severities),
            "description": self._data.get("description", ""),
            "redaction": self._data.get("redaction", {}),
            "false_positive": self._data.get("false_positive", {}),
            "category_labels": self.category_labels,
            "source": self.source,
        }


# Module-level singleton loaded once at import.
POLICY_ENGINE = PolicyEngine.load()

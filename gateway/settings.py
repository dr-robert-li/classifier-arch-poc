"""
Gateway settings loaded from .env and environment variables via pydantic-settings.

All configuration is centralised here. No module other than gateway/adapters/ollama_assistant.py
should reference the Ollama base URL directly (GATE-03).
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # Ollama model serving (ollama_base_url is used ONLY by OllamaAssistantAdapter — GATE-03)
    ollama_base_url: str = "http://localhost:11434"
    ollama_assistant_model: str = "llama3.1"
    ollama_guard_model: str = "llama-guard3"
    ollama_timeout_seconds: float = 120.0

    # Data store paths
    sqlite_db_path: str = "./data/gateway.db"
    jsonl_audit_path: str = "./data/audit.jsonl"

    # Gateway binding — 127.0.0.1 by default (never 0.0.0.0; CLAUDE.md security requirement)
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8000

    # Simple local authentication (CLAUDE.md: "admin routes require at least simple local
    # authentication"; "separate administrator actions from normal chat actions").
    # Two tokens give a lightweight admin/user separation. Override in .env for anything real.
    gateway_auth_enabled: bool = True
    gateway_admin_token: str = "admin-local-dev-token"
    gateway_user_token: str = "user-local-dev-token"


settings = Settings()

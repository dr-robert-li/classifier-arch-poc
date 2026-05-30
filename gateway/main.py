"""
FastAPI gateway application with lifespan resource management.

Architecture:
- All shared resources (httpx.AsyncClient, EventSink, OllamaAssistantAdapter) are
  initialised in the lifespan context and stored on app.state.
- The OllamaAssistantAdapter receives the full settings object so main.py itself does not
  reference the Ollama URL (GATE-03 boundary preserved).
- Routes are mounted via separate APIRouter instances (chat, health, stubs).
- The gateway binds to 127.0.0.1 by default (never 0.0.0.0 — CLAUDE.md security req).

Run command (local development):
    uvicorn gateway.main:app --host 127.0.0.1 --port 8000
"""
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from gateway.adapters.dlp_scanner import DlpScannerAdapter
from gateway.adapters.ollama_assistant import OllamaAssistantAdapter
from gateway.adapters.ollama_guard import OllamaGuardAdapter
from gateway.audit.event_sink import EventSink
from gateway.routes.chat import router as chat_router
from gateway.routes.classify import router as classify_router
from gateway.routes.events import router as events_router
from gateway.routes.health import router as health_router
from gateway.routes.stubs import router as stubs_router
from gateway.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: open shared resources and attach them to app.state.
    Shutdown: close them cleanly.

    Resources:
    - httpx.AsyncClient     — shared connection pool for Ollama HTTP calls
    - EventSink             — long-lived SQLite connection + JSONL path
    - OllamaAssistantAdapter — wraps the client + settings (GATE-03 boundary)
    - settings              — stored on app.state so routes can access config
    """
    # Startup
    http_client = httpx.AsyncClient(timeout=settings.ollama_timeout_seconds)
    event_sink = EventSink(
        db_path=settings.sqlite_db_path,
        jsonl_path=settings.jsonl_audit_path,
    )
    # Pass the settings object (not the raw URL field) so main.py
    # does not independently reference the Ollama URL (GATE-03).
    assistant_adapter = OllamaAssistantAdapter(
        http_client=http_client,
        settings=settings,
    )
    guard_adapter = OllamaGuardAdapter(
        http_client=http_client,
        settings=settings,
    )
    scanner_adapter = DlpScannerAdapter()

    app.state.http_client = http_client
    app.state.event_sink = event_sink
    app.state.assistant_adapter = assistant_adapter
    app.state.guard_adapter = guard_adapter
    app.state.scanner_adapter = scanner_adapter
    app.state.settings = settings

    yield

    # Shutdown
    await http_client.aclose()
    event_sink.close()


app = FastAPI(
    title="AI Safety SIEM Gateway",
    description=(
        "Local-first gateway for capturing, classifying, and auditing AI prompts "
        "and responses with a SIEM-compatible event log."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Mount routers
app.include_router(chat_router)
app.include_router(classify_router)
app.include_router(events_router)
app.include_router(health_router)
app.include_router(stubs_router)

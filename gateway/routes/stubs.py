"""
Stub routes for Phase 2-4 endpoints (GATE-05).

All routes return HTTP 501 Not Implemented. Their presence establishes the full
route surface so later phases can implement them without restructuring the API.

Phase 2: /classify/prompt, /classify/response
Phase 3: /approvals, /approvals/{id}/approve, /approvals/{id}/reject,
          /approvals/{id}/redact-resume, /policy, /export/jsonl
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_NOT_IMPLEMENTED = JSONResponse(
    status_code=501,
    content={"detail": "Not implemented — planned for a future phase."},
)


@router.post("/classify/prompt")
async def classify_prompt() -> JSONResponse:
    """Phase 2: Classify an inbound prompt using Llama Guard 3 + DLP scanner."""
    return _NOT_IMPLEMENTED


@router.post("/classify/response")
async def classify_response() -> JSONResponse:
    """Phase 2: Classify an assistant response using Llama Guard 3 + DLP scanner."""
    return _NOT_IMPLEMENTED


@router.get("/approvals")
async def list_approvals() -> JSONResponse:
    """Phase 3: List pending approval requests."""
    return _NOT_IMPLEMENTED


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str) -> JSONResponse:
    """Phase 3: Approve a pending approval request."""
    return _NOT_IMPLEMENTED


@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str) -> JSONResponse:
    """Phase 3: Reject a pending approval request."""
    return _NOT_IMPLEMENTED


@router.post("/approvals/{approval_id}/redact-resume")
async def redact_resume(approval_id: str) -> JSONResponse:
    """Phase 3: Redact sensitive content and resume the workflow."""
    return _NOT_IMPLEMENTED


@router.get("/policy")
async def get_policy() -> JSONResponse:
    """Phase 3: Return the active policy version."""
    return _NOT_IMPLEMENTED


@router.get("/export/jsonl")
async def export_jsonl() -> JSONResponse:
    """Phase 3: Export audit events in JSONL format (raw or redacted)."""
    return _NOT_IMPLEMENTED

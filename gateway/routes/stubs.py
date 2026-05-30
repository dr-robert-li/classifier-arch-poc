"""
Route surface placeholder (GATE-05).

All originally-stubbed endpoints are now implemented:
- /classify/prompt, /classify/response  -> gateway/routes/classify.py
- /approvals (+ approve/reject/redact-resume/false-positive/escalate),
  /policy, /export/jsonl                  -> gateway/routes/admin.py

This empty router is kept so main.py's import/mount remains stable; it registers no routes.
"""
from fastapi import APIRouter

router = APIRouter()

"""History never bypasses current user ownership or current role scope."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import current_user
from app.conversation.service import get_context
from app.history.service import (
    build_transcript,
    clear_owned,
    conversation_results,
    get_owned,
    list_owned,
    permitted,
)

router = APIRouter(prefix="/api")


@router.get("/conversations")
def conversations(
    request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    results = list_owned(request.app.state.storage, user["id"], user["role"])
    latest: dict[str, dict[str, Any]] = {}
    for item in results:
        latest.setdefault(item["conversation_id"], item)
    return {"conversations": list(latest.values())}


@router.delete("/conversations")
def clear_conversations(
    request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, int]:
    """Delete everything this user saved; others' data and the audit log stay.

    Demo only (shared one-click accounts). Elsewhere saved results are the record that
    ties each answer to its SQL and parameters, so they are kept."""
    if not request.app.state.settings.demo_login_enabled:
        raise HTTPException(status_code=403, detail="Deleting saved data is disabled")
    return {"deleted": clear_owned(request.app.state.storage, user["id"])}


@router.get("/conversations/{conversation_id}")
def conversation(
    conversation_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    storage = request.app.state.storage
    saved = conversation_results(storage, user["id"], conversation_id, user["role"])
    context = get_context(storage, conversation_id, user["id"])
    transcript = build_transcript(saved, (context or {}).get("turns") or [])
    if not transcript:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "conversation_id": conversation_id,
        "results": [{"id": s["id"], "question": s["question"]} for s in saved],
        "transcript": transcript,
    }


@router.get("/results/{result_id}")
def result(
    result_id: str, request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    saved = get_owned(request.app.state.storage, result_id, user["id"])
    if saved is None:
        raise HTTPException(status_code=404, detail="Result not found")
    if not permitted(saved, user["role"]):
        raise HTTPException(status_code=403, detail="Data is outside your access scope")
    payload: dict[str, Any] = dict(saved["payload"])
    payload.update(saved=True, result_id=result_id)
    return payload

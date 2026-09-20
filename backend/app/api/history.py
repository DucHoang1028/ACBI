"""History never bypasses current user ownership or current role scope."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import current_user
from app.history.service import get_owned, list_owned, permitted

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


@router.get("/conversations/{conversation_id}")
def conversation(
    conversation_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    results = [
        item
        for item in list_owned(request.app.state.storage, user["id"], user["role"])
        if item["conversation_id"] == conversation_id
    ]
    if not results:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation_id": conversation_id, "results": results}


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

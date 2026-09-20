"""HTTP surface for questions; the pipeline itself lives in query.orchestrator."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth import current_user
from app.presentation.contract import AskRequest
from app.query.orchestrator import answer

router = APIRouter(prefix="/api")


@router.post("/chat/ask")
def ask(
    body: AskRequest, request: Request, user: dict[str, Any] = Depends(current_user)
) -> Any:
    status_code, payload = answer(body, user, request.app.state)
    return JSONResponse(status_code=status_code, content=payload)

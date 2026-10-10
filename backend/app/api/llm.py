"""Which AI providers can answer now, and a way to ask them again after a rest."""

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import current_user

router = APIRouter(prefix="/api")
CHECK_EVERY = 20.0  # seconds between two checks of one provider
last_check: dict[str, float] = {}


class CheckRequest(BaseModel):
    provider: str = Field(max_length=20)


def listing(request: Request) -> dict[str, Any]:
    client = request.app.state.llm
    providers = (
        client.status() if client is not None and hasattr(client, "status") else []
    )
    return {"providers": providers}


@router.get("/llm/status")
def llm_status(
    request: Request, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    return listing(request)


@router.post("/llm/check")
def llm_check(
    body: CheckRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Send one tiny request per key of a provider and report what answers."""
    client = request.app.state.llm
    known = {p["id"] for p in listing(request)["providers"]}
    if client is None or body.provider not in known:
        raise HTTPException(status_code=404, detail="Unknown provider")
    now = time.monotonic()
    if now - last_check.get(body.provider, -CHECK_EVERY) < CHECK_EVERY:
        raise HTTPException(status_code=429, detail="Checked a moment ago")
    last_check[body.provider] = now
    client.probe(body.provider)
    return listing(request)

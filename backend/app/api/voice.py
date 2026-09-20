"""Upload audio for editable transcription; never auto-run the resulting question."""

import logging
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.ai.budget import RequestBudget
from app.api.auth import current_user

router = APIRouter(prefix="/api")
logger = logging.getLogger("acbi.voice")
MIME = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
    "audio/x-m4a": "m4a",
}
MAX_AUDIO = 10 * 1024 * 1024


@router.post("/chat/voice")
async def voice(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form("vi"),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, str]:
    if user["role"] == "it_admin":
        raise HTTPException(
            status_code=403, detail="Business data is outside your scope"
        )
    if language not in {"vi", "en"}:
        raise HTTPException(status_code=422, detail="Unsupported language")
    content_type = (file.content_type or "").split(";", 1)[0]
    if content_type not in MIME:
        raise HTTPException(status_code=415, detail="Unsupported audio format")
    audio = await file.read(MAX_AUDIO + 1)
    if not audio or len(audio) > MAX_AUDIO:
        raise HTTPException(status_code=413, detail="Audio must be 1 byte to 10 MB")
    stt = request.app.state.stt
    if stt is None:
        raise HTTPException(status_code=503, detail="Transcription is unavailable")
    request_id = str(uuid4())
    budget = RequestBudget(request.app.state.settings.request_timeout_seconds, 1)
    try:
        transcript = stt.transcribe(
            audio, f"recording.{MIME[content_type]}", language, budget
        )
        if not transcript or len(transcript) > 1000:
            raise HTTPException(
                status_code=422, detail="Re-record or type your question"
            )
        return {"transcript": transcript, "request_id": request_id}
    except (httpx.HTTPError, RuntimeError, ValueError):
        logger.warning("Voice request %s failed", request_id)
        raise HTTPException(
            status_code=503, detail="Transcription failed; re-record or type"
        ) from None
    finally:
        logger.info("request_id=%s stt_calls=%d", request_id, budget.calls)

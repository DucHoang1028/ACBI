"""Request and response contract shared by the API and the pipeline."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None
    language: Literal["vi", "en"] = "en"


def response(
    status: str, message: str, request_id: str, conversation_id: str
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "answer_text": None,
        "table": [],
        "viz_config": None,
        "sources": None,
        "request_id": request_id,
        "conversation_id": conversation_id,
        "saved": False,
    }

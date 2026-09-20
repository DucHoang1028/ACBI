"""Vietnamese/English speech transcription, kept separate from question execution."""

from typing import Protocol

import httpx

from app.ai.budget import RequestBudget
from app.core.config import Settings


class STTClient(Protocol):
    def transcribe(
        self, audio: bytes, filename: str, language: str, budget: RequestBudget
    ) -> str: ...


class FakeSTT:
    def __init__(self, transcript: str):
        self.transcript = transcript

    def transcribe(
        self, audio: bytes, filename: str, language: str, budget: RequestBudget
    ) -> str:
        budget.consume()
        return self.transcript


class GroqSTT:
    def __init__(self, settings: Settings):
        self.key = settings.groq_api_key.get_secret_value()
        self.model = settings.stt_model

    def transcribe(
        self, audio: bytes, filename: str, language: str, budget: RequestBudget
    ) -> str:
        budget.consume()
        with httpx.Client(timeout=min(20, budget.remaining())) as client:
            response = client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.key}"},
                files={"file": (filename, audio)},
                data={
                    "model": self.model,
                    "language": language,
                    "response_format": "json",
                },
            )
        response.raise_for_status()
        budget.remaining()
        return str(response.json().get("text", "")).strip()

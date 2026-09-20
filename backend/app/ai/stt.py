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
        self.keys = settings.groq_keys()
        self.model = settings.stt_model
        self.first = 0

    def transcribe(
        self, audio: bytes, filename: str, language: str, budget: RequestBudget
    ) -> str:
        budget.consume()
        error: Exception | None = None
        for step in range(len(self.keys)):
            index = (self.first + step) % len(self.keys)
            try:
                with httpx.Client(timeout=min(20, budget.remaining())) as client:
                    response = client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self.keys[index]}"},
                        files={"file": (filename, audio)},
                        data={
                            "model": self.model,
                            "language": language,
                            "response_format": "json",
                        },
                    )
                response.raise_for_status()
                budget.remaining()
                self.first = index
                return str(response.json().get("text", "")).strip()
            except httpx.HTTPStatusError as failure:
                if failure.response.status_code in (400, 413, 422):
                    raise  # bad audio or request: another key cannot help
                error = failure
            except httpx.TransportError as failure:
                error = failure
            self.first = (index + 1) % len(self.keys)
        raise error or RuntimeError("Groq API key is not configured")

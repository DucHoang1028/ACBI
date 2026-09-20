import json

import httpx
import pytest
from app.ai import client as client_module
from app.ai.budget import RequestBudget
from app.ai.client import GroqClient, SummaryProposal
from app.core.config import Settings

REAL_CLIENT = httpx.Client


def make(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, int]) -> tuple:
    """A GroqClient whose HTTP layer answers per key with a fixed status."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        seen.append(key)
        status = statuses[key]
        if status != 200:
            return httpx.Response(status, headers={"retry-after": "5"}, json={})
        content = json.dumps({"text": f"answered by {key}"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 100},
            },
        )

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        return REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=5)

    monkeypatch.setattr(client_module.httpx, "Client", factory)
    settings = Settings(
        _env_file=None,
        groq_api_key="k1",
        groq_api_keys="k2, k3,k1",
        warehouse_password="x",
        app_db_password="x",
    )
    return GroqClient(settings), seen


def ask(groq: GroqClient) -> str:
    return groq.complete(
        "summary",
        SummaryProposal,
        SummaryProposal.model_json_schema(),
        "s",
        {},
        RequestBudget(10, 3),
    ).text


def test_keys_are_deduplicated_in_order() -> None:
    settings = Settings(
        _env_file=None,
        groq_api_key="k1",
        groq_api_keys="k2, k3,k1",
        warehouse_password="x",
        app_db_password="x",
    )
    assert settings.groq_keys() == ["k1", "k2", "k3"]


def test_rate_limited_key_hands_over_to_next_and_stays_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groq, seen = make(monkeypatch, {"k1": 429, "k2": 200, "k3": 200})
    assert ask(groq) == "answered by k2"
    assert seen == ["k1", "k2"]
    seen.clear()
    assert ask(groq) == "answered by k2"  # sticks with the working key
    assert seen == ["k2"]


def test_invalid_key_and_server_error_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groq, seen = make(monkeypatch, {"k1": 401, "k2": 503, "k3": 200})
    assert ask(groq) == "answered by k3"
    assert seen == ["k1", "k2", "k3"]


def test_all_keys_failing_raises_and_bad_request_does_not_rotate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groq, seen = make(monkeypatch, {"k1": 429, "k2": 429, "k3": 429})
    with pytest.raises(httpx.HTTPStatusError):
        ask(groq)
    assert seen == ["k1", "k2", "k3"]

    groq, seen = make(monkeypatch, {"k1": 400, "k2": 200, "k3": 200})
    with pytest.raises(httpx.HTTPStatusError):
        ask(groq)
    assert seen == ["k1"]

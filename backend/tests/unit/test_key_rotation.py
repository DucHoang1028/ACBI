import json

import httpx
import pytest
from app.ai import client as client_module
from app.ai.budget import RequestBudget
from app.ai.client import GroqClient, LLMBusy, SummaryProposal
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
    with pytest.raises(LLMBusy):  # a rate limit is a wait, not a failure
        ask(groq)
    assert seen == ["k1", "k2", "k3"]

    groq, seen = make(monkeypatch, {"k1": 400, "k2": 200, "k3": 200})
    with pytest.raises(httpx.HTTPStatusError):
        ask(groq)
    assert seen == ["k1"]


def test_schema_failure_from_the_model_is_regenerated_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:  # Groq answers 400 when the model breaks the schema
            return httpx.Response(400, json={"error": {"code": "json_validate_failed"}})
        content = json.dumps({"text": "second try"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {}},
        )

    monkeypatch.setattr(
        client_module.httpx,
        "Client",
        lambda *a, **k: REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=5),
    )
    settings = Settings(
        _env_file=None,
        groq_api_key="k1",
        warehouse_password="x",
        app_db_password="x",
    )
    assert ask(GroqClient(settings)) == "second try"
    assert len(calls) == 2


def test_wait_time_says_when_a_busy_key_has_room_again() -> None:
    from app.ai.keys import KeyPool

    pool = KeyPool(["a", "b"], rpm=2, tpm=1000)
    assert pool.wait_time(100) == 0
    for state in pool.states:
        pool.reserve(state, 100)
        pool.reserve(state, 100)
    assert not pool.available(100)
    assert 55 < pool.wait_time(100) <= 60  # the oldest call leaves the window
    pool.failed(pool.states[0], 429, 5.0)
    assert 55 < pool.wait_time(100) <= 60
    assert pool.wait_time(5000) == float("inf")  # can never fit


def test_providers_are_ordered_and_named_by_settings() -> None:
    from app.ai.client import GEMINI_URL, LITEROUTER_URL, provider_keys

    common = {"_env_file": None, "warehouse_password": "x", "app_db_password": "x"}
    settings = Settings(
        groq_api_key="g1",
        groq_api_keys="g2",
        gemini_api_keys="m1,m2",
        gemini_model="model-a",
        literouter_api_key="l1",
        llm_provider_order="gemini,groq,literouter",
        **common,
    )
    states = provider_keys(settings)
    assert [s.key for s in states] == ["m1", "m2", "g1", "g2", "l1"]
    assert states[0].url == GEMINI_URL and states[0].json_object
    assert states[2].url == "" and not states[2].json_object
    assert states[4].url == LITEROUTER_URL
    groq_only = Settings(groq_api_key="g1", **common)
    assert [s.key for s in provider_keys(groq_only)] == ["g1"]


def test_several_gemini_models_multiply_the_keys() -> None:
    from app.ai.client import provider_keys

    settings = Settings(
        _env_file=None,
        warehouse_password="x",
        app_db_password="x",
        gemini_api_keys="m1,m2",
        gemini_model="new,old",
        llm_provider_order="gemini",
    )
    states = provider_keys(settings)
    assert [(s.key, s.model) for s in states] == [
        ("m1", "new"),
        ("m2", "new"),
        ("m1", "old"),
        ("m2", "old"),
    ]


def test_invented_fields_are_dropped_for_providers_without_strict_schemas() -> None:
    from app.ai.client import SummaryProposal, _known_fields

    kept = _known_fields(SummaryProposal, '{"text": "ok", "type": "extra"}')
    assert SummaryProposal.model_validate_json(kept).text == "ok"
    assert _known_fields(SummaryProposal, "not json") == "not json"


def test_a_key_with_a_gap_is_not_offered_again_until_the_gap_has_passed() -> None:
    from app.ai.keys import KeyPool, KeyState

    pool = KeyPool.of([KeyState(key="l1", label="l1", rpm=8, tpm=10**6, gap=7.0)])
    assert pool.available(100)
    pool.reserve(pool.states[0], 100)
    assert pool.available(100) == []
    assert 6.0 < pool.wait_time(100) <= 7.0


def test_any_provider_key_switches_the_model_on() -> None:
    common = {"_env_file": None, "warehouse_password": "x", "app_db_password": "x"}
    assert not Settings(**common).has_llm_keys()
    assert Settings(gemini_api_keys="g", **common).has_llm_keys()
    assert Settings(literouter_api_key="l", **common).has_llm_keys()
    assert Settings(groq_api_key="k", **common).has_llm_keys()

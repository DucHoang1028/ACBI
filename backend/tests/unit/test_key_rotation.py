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


def single_provider(
    monkeypatch: pytest.MonkeyPatch, status: int, body: dict, **keys: str
) -> GroqClient:
    """A client whose every call is answered with one fixed error."""
    monkeypatch.setattr(
        client_module.httpx,
        "Client",
        lambda *a, **k: REAL_CLIENT(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body)),
            timeout=5,
        ),
    )
    common = {"_env_file": None, "warehouse_password": "x", "app_db_password": "x"}
    return GroqClient(Settings(**common, **keys))  # type: ignore[arg-type]


def test_a_rate_limit_sent_as_403_is_a_short_wait_not_a_broken_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    groq = single_provider(
        monkeypatch,
        403,
        {
            "error": "[LiteRouter] Rate limit exceeded for your tier (7 seconds "
            "between messages)."
        },
        literouter_api_key="l1",
        llm_provider_order="literouter",
    )
    with pytest.raises(LLMBusy):  # "AI busy, ask again", not a technical failure
        ask(groq)
    rest = groq.pool.states[0].rest_until - time.monotonic()
    assert 0 < rest <= 7.5  # the provider's own gap, not the hour a dead key gets


def test_a_daily_quota_rests_the_key_for_an_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    groq = single_provider(
        monkeypatch,
        429,
        {
            "error": {
                "code": 429,
                "details": [
                    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                ],
            }
        },
        gemini_api_keys="m1",
        gemini_model="model-a",
        llm_provider_order="gemini",
    )
    with pytest.raises(LLMBusy):
        ask(groq)
    # Asking again every minute only spends time on a key that is out for the day.
    assert groq.pool.states[0].rest_until - time.monotonic() > 3000


def test_any_provider_key_switches_the_model_on() -> None:
    common = {"_env_file": None, "warehouse_password": "x", "app_db_password": "x"}
    assert not Settings(**common).has_llm_keys()
    assert Settings(gemini_api_keys="g", **common).has_llm_keys()
    assert Settings(literouter_api_key="l", **common).has_llm_keys()
    assert Settings(groq_api_key="k", **common).has_llm_keys()


def two_providers(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, int]) -> tuple:
    """Gemini, Groq and a local Ollama, each answering with a fixed status."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        seen.append(key)
        if statuses[key] != 200:
            return httpx.Response(
                statuses[key], headers={"retry-after": "300"}, json={}
            )
        content = json.dumps({"text": f"answered by {key}"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    monkeypatch.setattr(
        client_module.httpx,
        "Client",
        lambda *a, **k: REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=5),
    )
    settings = Settings(
        _env_file=None,
        gemini_api_keys="m1",
        gemini_model="model-a",
        groq_api_key="g1",
        ollama_url="http://localhost:11434/",
        llm_provider_order="gemini,groq,ollama",
        warehouse_password="x",
        app_db_password="x",
    )
    return GroqClient(settings), seen


def ask_as(groq: GroqClient, provider: str | None) -> str:
    budget = RequestBudget(10, 3)
    budget.provider = provider
    return groq.complete(
        "summary", SummaryProposal, SummaryProposal.model_json_schema(), "s", {}, budget
    ).text


def test_only_the_provider_the_user_picked_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groq, seen = two_providers(monkeypatch, {"m1": 200, "g1": 200, "ollama": 200})
    assert ask_as(groq, "ollama") == "answered by ollama"
    assert seen == ["ollama"]
    assert ask_as(groq, None) == "answered by ollama"  # sticks with the last good key
    assert ask_as(groq, "auto") == "answered by ollama"
    seen.clear()
    assert ask_as(groq, "groq") == "answered by g1" and seen == ["g1"]


def test_a_resting_provider_is_listed_with_its_wait_and_returns_when_it_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groq, _ = two_providers(monkeypatch, {"m1": 429, "g1": 200, "ollama": 200})
    assert ask_as(groq, None) == "answered by g1"  # gemini 429 hands over
    listed = {p["id"]: p for p in groq.status()}
    assert listed["gemini"]["state"] == "resting"
    assert 250 <= listed["gemini"]["back_in_seconds"] <= 300
    assert listed["groq"]["state"] == "ready" and listed["ollama"]["state"] == "ready"
    with pytest.raises(LLMBusy):
        ask_as(groq, "gemini")  # the picked provider is not replaced by another
    groq.pool.states[0].rest_until = 0.0
    assert {p["id"]: p["state"] for p in groq.status()}["gemini"] == "ready"


def test_probe_clears_a_rest_when_the_provider_answers_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = {"m1": 429, "g1": 200, "ollama": 200}
    groq, _ = two_providers(monkeypatch, statuses)
    groq.probe("gemini")
    assert {p["id"]: p["state"] for p in groq.status()}["gemini"] == "resting"
    statuses["m1"] = 200
    groq.probe("gemini")
    assert {p["id"]: p["state"] for p in groq.status()}["gemini"] == "ready"


def test_ollama_is_off_unless_its_address_is_set() -> None:
    from app.ai.client import provider_keys

    common = {"_env_file": None, "warehouse_password": "x", "app_db_password": "x"}
    on = Settings(
        ollama_url="http://localhost:11434/", llm_provider_order="ollama", **common
    )
    [state] = provider_keys(on)
    assert state.url == "http://localhost:11434/v1" and state.timeout > 30
    assert on.has_llm_keys()
    off = Settings(llm_provider_order="ollama", **common)
    assert provider_keys(off) == [] and not off.has_llm_keys()


@pytest.mark.parametrize(
    ("body", "rests", "busy"),
    [
        ({"error": "Daily limit reached on the Basic tier"}, 3600, True),
        ({"error": "Rate limit exceeded, 7 seconds between messages"}, 7, True),
        ({"error": "Invalid API key"}, 3600, False),
    ],
)
def test_a_403_that_names_a_limit_is_a_wait_not_a_broken_key(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, str], rests: int, busy: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=body)

    monkeypatch.setattr(
        client_module.httpx,
        "Client",
        lambda *a, **k: REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=5),
    )
    settings = Settings(
        _env_file=None,
        literouter_api_key="l1",
        llm_provider_order="literouter",
        warehouse_password="x",
        app_db_password="x",
    )
    groq = GroqClient(settings)
    with pytest.raises(LLMBusy if busy else httpx.HTTPStatusError):
        ask_as(groq, None)
    [item] = groq.status()
    assert item["state"] == "resting"
    assert item["back_in_seconds"] <= rests and item["back_in_seconds"] >= rests - 10

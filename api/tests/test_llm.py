import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from pydantic import BaseModel

import llm
from config import get_settings

Handler = Callable[[httpx.Request], httpx.Response]


class Answer(BaseModel):
    value: int


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def llm_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Two keys, both models set, fresh limiter/rotation, and recorded (skipped) sleeps."""
    monkeypatch.setenv("GROQ_API_KEY_1", "key-a")
    monkeypatch.setenv("GROQ_API_KEY_2", "key-b")
    monkeypatch.setenv("LLM_MODEL_LARGE", "model-large")
    monkeypatch.setenv("LLM_MODEL_SMALL", "model-small")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "_key_index", 0)
    monkeypatch.setattr(llm, "_bucket", llm.TokenBucket(6000, 100))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    yield sleeps
    get_settings.cache_clear()


def ok(content: str, tokens: int = 10) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}},
    )


def sequence(responses: list[httpx.Response], seen: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[len(seen) - 1]

    return handler


async def call(handler: Handler, tier: llm.Tier = "small") -> tuple[Answer, int]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await llm.complete_json(client, tier, "sys", "user", Answer)


@pytest.mark.anyio
async def test_success_routes_model_and_json_mode() -> None:
    seen: list[httpx.Request] = []
    result, tokens = await call(sequence([ok('{"value": 3}', 42)], seen), "large")
    assert result.value == 3 and tokens == 42
    body = json.loads(seen[0].content)
    assert body["model"] == "model-large"
    assert body["response_format"] == {"type": "json_object"}
    assert seen[0].headers["Authorization"] == "Bearer key-a"


@pytest.mark.anyio
async def test_small_tier_model() -> None:
    seen: list[httpx.Request] = []
    await call(sequence([ok('{"value": 1}')], seen), "small")
    assert json.loads(seen[0].content)["model"] == "model-small"


@pytest.mark.anyio
async def test_429_rotates_key_and_honors_retry_after(llm_env: list[float]) -> None:
    seen: list[httpx.Request] = []
    responses = [httpx.Response(429, headers={"Retry-After": "7"}), ok('{"value": 1}')]
    result, _ = await call(sequence(responses, seen))
    assert result.value == 1
    assert [r.headers["Authorization"] for r in seen] == ["Bearer key-a", "Bearer key-b"]
    assert llm_env == [7.0]


@pytest.mark.anyio
async def test_5xx_retried_then_gives_up(llm_env: list[float]) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(llm.LLMRequestError) as err:
        await call(sequence([httpx.Response(503)] * 3, seen))
    assert len(seen) == llm.MAX_TRIES == 3
    assert err.value.status == 503
    assert len(llm_env) == 2  # backoff between tries only
    assert 1.0 <= llm_env[0] < 2.0 and 2.0 <= llm_env[1] < 3.0


@pytest.mark.anyio
async def test_5xx_then_success() -> None:
    seen: list[httpx.Request] = []
    result, _ = await call(sequence([httpx.Response(500), ok('{"value": 2}')], seen))
    assert result.value == 2 and len(seen) == 2


@pytest.mark.anyio
async def test_timeout_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return ok('{"value": 5}')

    result, _ = await call(handler)
    assert result.value == 5 and calls == 2


@pytest.mark.anyio
async def test_400_not_retried() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(llm.LLMRequestError) as err:
        await call(sequence([httpx.Response(400, text="bad"), ok('{"value": 1}')], seen))
    assert err.value.status == 400 and len(seen) == 1


@pytest.mark.anyio
async def test_invalid_json_retried_once_tokens_summed() -> None:
    seen: list[httpx.Request] = []
    result, tokens = await call(sequence([ok("not json", 15), ok('{"value": 9}', 20)], seen))
    assert result.value == 9 and tokens == 35 and len(seen) == 2


@pytest.mark.anyio
async def test_invalid_twice_raises_with_tokens() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(llm.LLMValidationError) as err:
        await call(sequence([ok('{"value": "x"}', 11), ok("{}", 12)], seen))
    assert err.value.tokens == 23 and len(seen) == 2


@pytest.mark.anyio
async def test_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY_1", "")
    monkeypatch.setenv("GROQ_API_KEY_2", "")
    get_settings.cache_clear()
    with pytest.raises(llm.LLMConfigError):
        await call(sequence([], []))


@pytest.mark.anyio
async def test_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL_SMALL", "")
    get_settings.cache_clear()
    with pytest.raises(llm.LLMConfigError):
        await call(sequence([], []), "small")


@pytest.mark.anyio
async def test_token_bucket_waits_when_empty(llm_env: list[float]) -> None:
    bucket = llm.TokenBucket(60, 1)  # 1 request/s, burst 1
    await bucket.acquire()
    await bucket.acquire()
    assert len(llm_env) == 1 and 0.9 < llm_env[0] <= 1.0


@pytest.mark.anyio
async def test_any_transport_error_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("peer closed", request=request)
        return ok('{"value": 6}')

    result, _ = await call(handler)
    assert result.value == 6 and calls == 2


def json_validate_failed() -> httpx.Response:
    body = {"error": {"message": "Failed to generate JSON", "code": "json_validate_failed"}}
    return httpx.Response(400, json=body)


@pytest.mark.anyio
async def test_json_validate_failed_retried_once() -> None:
    seen: list[httpx.Request] = []
    result, tokens = await call(sequence([json_validate_failed(), ok('{"value": 4}', 7)], seen))
    assert result.value == 4 and tokens == 7 and len(seen) == 2


@pytest.mark.anyio
async def test_json_validate_failed_twice_is_validation_error() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(llm.LLMValidationError) as err:
        await call(sequence([ok("bad", 9), json_validate_failed()], seen))
    assert err.value.tokens == 9 and len(seen) == 2


@pytest.mark.anyio
async def test_request_error_carries_tokens() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(llm.LLMRequestError) as err:
        await call(sequence([ok("bad", 13), httpx.Response(401, text="no")], seen))
    assert err.value.status == 401 and err.value.tokens == 13
